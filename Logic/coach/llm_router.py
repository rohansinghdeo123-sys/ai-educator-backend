"""Cost-aware, provider-neutral model routing for Study Lab coach calls."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
import threading
import time
from typing import Any, Dict, Iterable, Iterator, List

from groq import Groq

from .settings import coach_settings


@dataclass
class ModelCallRecord:
    role: str
    model: str
    provider: str
    mode: str
    status: str
    latency_ms: int
    attempt: int
    fallback: bool = False
    error: str = ""


class LLMRouter:
    """Route cheap tasks cheaply, retry transient failures, and retain a safe trace."""

    def __init__(self) -> None:
        self._groq = None
        self._local = threading.local()

    def _client(self) -> Groq:
        if self._groq is None:
            api_key = os.getenv("GROQ_API_KEY")
            if not api_key:
                raise RuntimeError("GROQ_API_KEY is not configured.")
            self._groq = Groq(api_key=api_key, timeout=coach_settings.llm_timeout_seconds)
        return self._groq

    def begin_turn(self, turn_id: str) -> None:
        self._local.turn_id = turn_id
        self._local.records = []

    def records(self) -> List[Dict[str, Any]]:
        return [asdict(record) for record in list(getattr(self._local, "records", []))]

    def _record(self, record: ModelCallRecord) -> None:
        records = list(getattr(self._local, "records", []))
        records.append(record)
        self._local.records = records[-24:]

    def model_for(self, role: str, complexity: str = "balanced") -> str:
        if role == "profiler" or complexity == "fast":
            return coach_settings.fast_model
        if role == "reviewer":
            return coach_settings.review_model
        return coach_settings.tutor_model

    def _candidate_models(self, role: str, complexity: str) -> List[str]:
        primary = self.model_for(role, complexity=complexity)
        candidates = [primary]
        if coach_settings.fallback_model and coach_settings.fallback_model not in candidates:
            candidates.append(coach_settings.fallback_model)
        return candidates[: max(1, coach_settings.llm_max_attempts)]

    def complete(
        self,
        role: str,
        messages: Iterable[Dict[str, str]],
        complexity: str = "balanced",
        **kwargs: Any,
    ) -> str:
        last_error: Exception | None = None
        message_rows = list(messages)
        for attempt, model in enumerate(self._candidate_models(role, complexity), start=1):
            started_at = time.perf_counter()
            try:
                response = self._client().chat.completions.create(
                    model=model,
                    messages=message_rows,
                    stream=False,
                    **kwargs,
                )
                content = (response.choices[0].message.content or "").strip()
                self._record(ModelCallRecord(
                    role=role,
                    model=model,
                    provider=coach_settings.provider,
                    mode="complete",
                    status="success",
                    latency_ms=round((time.perf_counter() - started_at) * 1000),
                    attempt=attempt,
                    fallback=attempt > 1,
                ))
                return content
            except Exception as exc:
                last_error = exc
                self._record(ModelCallRecord(
                    role=role,
                    model=model,
                    provider=coach_settings.provider,
                    mode="complete",
                    status="error",
                    latency_ms=round((time.perf_counter() - started_at) * 1000),
                    attempt=attempt,
                    fallback=attempt > 1,
                    error=str(exc)[:240],
                ))
        raise last_error or RuntimeError(f"No model route was available for role '{role}'.")

    def stream(
        self,
        role: str,
        messages: Iterable[Dict[str, str]],
        complexity: str = "balanced",
        **kwargs: Any,
    ) -> Iterator[Any]:
        message_rows = list(messages)
        last_error: Exception | None = None
        emitted_chunks = 0

        for attempt, model in enumerate(self._candidate_models(role, complexity), start=1):
            started_at = time.perf_counter()
            try:
                response = self._client().chat.completions.create(
                    model=model,
                    messages=message_rows,
                    stream=True,
                    **kwargs,
                )
                for chunk in response:
                    emitted_chunks += 1
                    yield chunk
                self._record(ModelCallRecord(
                    role=role,
                    model=model,
                    provider=coach_settings.provider,
                    mode="stream",
                    status="success",
                    latency_ms=round((time.perf_counter() - started_at) * 1000),
                    attempt=attempt,
                    fallback=attempt > 1,
                ))
                return
            except Exception as exc:
                last_error = exc
                self._record(ModelCallRecord(
                    role=role,
                    model=model,
                    provider=coach_settings.provider,
                    mode="stream",
                    status="error",
                    latency_ms=round((time.perf_counter() - started_at) * 1000),
                    attempt=attempt,
                    fallback=attempt > 1,
                    error=str(exc)[:240],
                ))
                if emitted_chunks:
                    raise
        raise last_error or RuntimeError(f"No streaming model route was available for role '{role}'.")


llm_router = LLMRouter()
