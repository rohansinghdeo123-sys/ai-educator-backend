"""Cost-aware, provider-neutral model routing for Study Lab coach calls."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
import threading
import time
from typing import Any, Dict, Iterable, Iterator, List

from groq import Groq

from .costing import estimate_messages_tokens, estimate_model_cost_usd, estimate_text_tokens
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
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    estimated_cost_usd: float = 0.0


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
        if role == "vision":
            return coach_settings.vision_model
        if role == "profiler" or complexity == "fast":
            return coach_settings.fast_model
        if role == "reviewer":
            return coach_settings.review_model
        return coach_settings.tutor_model

    def _candidate_models(self, role: str, complexity: str) -> List[str]:
        primary = self.model_for(role, complexity=complexity)
        if role == "vision":
            return [primary]
        candidates = [primary]
        if coach_settings.fallback_model and coach_settings.fallback_model not in candidates:
            candidates.append(coach_settings.fallback_model)
        return candidates[: max(1, coach_settings.llm_max_attempts)]

    def _record_call(
        self,
        *,
        role: str,
        model: str,
        mode: str,
        status: str,
        started_at: float,
        attempt: int,
        input_tokens: int,
        output_text: Any = "",
        fallback: bool = False,
        error: str = "",
    ) -> None:
        output_tokens = estimate_text_tokens(output_text)
        self._record(ModelCallRecord(
            role=role,
            model=model,
            provider=coach_settings.provider,
            mode=mode,
            status=status,
            latency_ms=round((time.perf_counter() - started_at) * 1000),
            attempt=attempt,
            fallback=fallback,
            error=error[:240],
            estimated_input_tokens=input_tokens,
            estimated_output_tokens=output_tokens,
            estimated_cost_usd=estimate_model_cost_usd(model, input_tokens, output_tokens),
        ))

    @staticmethod
    def _chunk_text(chunk: Any) -> str:
        try:
            return chunk.choices[0].delta.content or ""
        except Exception:
            return ""

    def complete(
        self,
        role: str,
        messages: Iterable[Dict[str, Any]],
        complexity: str = "balanced",
        **kwargs: Any,
    ) -> str:
        last_error: Exception | None = None
        message_rows = list(messages)
        input_tokens = estimate_messages_tokens(message_rows)
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
                self._record_call(
                    role=role,
                    model=model,
                    mode="complete",
                    status="success",
                    started_at=started_at,
                    attempt=attempt,
                    input_tokens=input_tokens,
                    output_text=content,
                    fallback=attempt > 1,
                )
                return content
            except Exception as exc:
                last_error = exc
                self._record_call(
                    role=role,
                    model=model,
                    mode="complete",
                    status="error",
                    started_at=started_at,
                    attempt=attempt,
                    input_tokens=input_tokens,
                    fallback=attempt > 1,
                    error=str(exc),
                )
        raise last_error or RuntimeError(f"No model route was available for role '{role}'.")

    def stream(
        self,
        role: str,
        messages: Iterable[Dict[str, Any]],
        complexity: str = "balanced",
        **kwargs: Any,
    ) -> Iterator[Any]:
        message_rows = list(messages)
        input_tokens = estimate_messages_tokens(message_rows)
        last_error: Exception | None = None
        emitted_chunks = 0

        for attempt, model in enumerate(self._candidate_models(role, complexity), start=1):
            started_at = time.perf_counter()
            output_parts: list[str] = []
            try:
                response = self._client().chat.completions.create(
                    model=model,
                    messages=message_rows,
                    stream=True,
                    **kwargs,
                )
                for chunk in response:
                    emitted_chunks += 1
                    chunk_text = self._chunk_text(chunk)
                    if chunk_text:
                        output_parts.append(chunk_text)
                    yield chunk
                self._record_call(
                    role=role,
                    model=model,
                    mode="stream",
                    status="success",
                    started_at=started_at,
                    attempt=attempt,
                    input_tokens=input_tokens,
                    output_text="".join(output_parts),
                    fallback=attempt > 1,
                )
                return
            except Exception as exc:
                last_error = exc
                self._record_call(
                    role=role,
                    model=model,
                    mode="stream",
                    status="error",
                    started_at=started_at,
                    attempt=attempt,
                    input_tokens=input_tokens,
                    output_text="".join(output_parts),
                    fallback=attempt > 1,
                    error=str(exc),
                )
                if emitted_chunks:
                    raise
        raise last_error or RuntimeError(f"No streaming model route was available for role '{role}'.")


llm_router = LLMRouter()
