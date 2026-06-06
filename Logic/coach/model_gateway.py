"""Agent-facing gateway for model calls.

The low-level LLMRouter owns provider fallback, cost estimates, streaming, and
budget checks. This gateway adds the agent/task metadata that autonomous
workflows need without changing the router contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
import uuid
from typing import Any, Dict, Iterable, Iterator, List

from Logic.agent_runtime import normalize_agent_role

from .llm_router import LLMRouter, llm_router


DEFAULT_AGENT_BY_ROLE = {
    "profiler": "intent_profiler",
    "vision": "context_retriever",
    "tutor": "tutor_model",
    "reviewer": "answer_reviewer",
}


@dataclass(frozen=True)
class ModelGatewayTask:
    role: str
    agent_name: str
    task: str
    student_visible: bool = False
    safety_tier: str = "standard"
    call_id: str = ""

    def metadata(self) -> Dict[str, Any]:
        return {
            "gateway_call_id": self.call_id,
            "gateway_role": self.role,
            "gateway_agent": self.agent_name,
            "gateway_task": self.task,
            "student_visible": self.student_visible,
            "safety_tier": self.safety_tier,
        }


class ModelGateway:
    """Stable model access point for backend agents."""

    def __init__(self, router: LLMRouter | None = None) -> None:
        self.router = router or llm_router
        self._local = threading.local()

    def begin_turn(self, turn_id: str) -> None:
        self.router.begin_turn(turn_id)
        self._local.record_annotations = {}

    def records(self) -> List[Dict[str, Any]]:
        annotations = dict(getattr(self._local, "record_annotations", {}) or {})
        result: List[Dict[str, Any]] = []
        for index, record in enumerate(self.router.records()):
            enriched = dict(record)
            enriched.update(annotations.get(index, {}))
            result.append(enriched)
        return result

    def model_for(self, role: str, complexity: str = "balanced") -> str:
        return self.router.model_for(role, complexity=complexity)

    def complete(
        self,
        role: str,
        messages: Iterable[Dict[str, Any]],
        complexity: str = "balanced",
        *,
        agent_name: str = "",
        task: str = "",
        student_visible: bool = False,
        safety_tier: str = "standard",
        **kwargs: Any,
    ) -> str:
        gateway_task = self._task(
            role=role,
            agent_name=agent_name,
            task=task,
            student_visible=student_visible,
            safety_tier=safety_tier,
        )
        start_index = len(self.router.records())
        try:
            return self.router.complete(role, messages, complexity=complexity, **kwargs)
        finally:
            self._annotate_new_records(start_index, gateway_task)

    def stream(
        self,
        role: str,
        messages: Iterable[Dict[str, Any]],
        complexity: str = "balanced",
        *,
        agent_name: str = "",
        task: str = "",
        student_visible: bool = True,
        safety_tier: str = "standard",
        **kwargs: Any,
    ) -> Iterator[Any]:
        gateway_task = self._task(
            role=role,
            agent_name=agent_name,
            task=task,
            student_visible=student_visible,
            safety_tier=safety_tier,
        )
        start_index = len(self.router.records())

        def _wrapped() -> Iterator[Any]:
            try:
                for chunk in self.router.stream(role, messages, complexity=complexity, **kwargs):
                    yield chunk
            finally:
                self._annotate_new_records(start_index, gateway_task)

        return _wrapped()

    def _task(
        self,
        *,
        role: str,
        agent_name: str,
        task: str,
        student_visible: bool,
        safety_tier: str,
    ) -> ModelGatewayTask:
        role_value = str(role or "tutor").strip().lower()
        agent = normalize_agent_role(agent_name or DEFAULT_AGENT_BY_ROLE.get(role_value) or "unknown_agent")
        return ModelGatewayTask(
            role=role_value,
            agent_name=agent,
            task=str(task or role_value or "model_call")[:240],
            student_visible=bool(student_visible),
            safety_tier=str(safety_tier or "standard")[:80],
            call_id=f"model_call_{uuid.uuid4().hex[:12]}",
        )

    def _annotate_new_records(self, start_index: int, task: ModelGatewayTask) -> None:
        annotations = dict(getattr(self._local, "record_annotations", {}) or {})
        end_index = len(self.router.records())
        for index in range(start_index, end_index):
            annotations[index] = task.metadata()
        self._local.record_annotations = annotations


model_gateway = ModelGateway()
