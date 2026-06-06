"""Controlled communication contracts for backend agent workflows."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any, Dict


AGENT_ROLES = {
    "api",
    "orchestrator",
    "coach",
    "tutor",
    "revision",
    "exam",
    "planner",
    "lead_coach_orchestrator",
    "intent_profiler",
    "conversation_responder",
    "response_planner",
    "context_retriever",
    "tool_gateway",
    "tutor_model",
    "answer_reviewer",
    "quality_verifier",
    "memory_mastery_engine",
    "autonomous_mission_planner",
    "mission_executor",
    "reflection_agent",
    "unknown_agent",
}

MESSAGE_TYPES = {
    "request_received",
    "profile_result",
    "conversation_result",
    "response_plan",
    "context_result",
    "tool_results",
    "draft_result",
    "review_result",
    "verification_result",
    "memory_update",
    "handoff_request",
    "handoff_result",
    "status",
    "error",
}

HANDOFF_STATUSES = {
    "requested",
    "accepted",
    "completed",
    "success",
    "skipped",
    "fallback",
    "needs_review",
    "failed",
}


def _slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _compact_text(value: Any, limit: int = 1000) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def clamp_confidence(value: Any) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 3)
    except (TypeError, ValueError):
        return 0.0


def normalize_agent_role(value: Any, fallback: str = "unknown_agent") -> str:
    candidate = _slug(value)
    if candidate in AGENT_ROLES:
        return candidate
    safe_fallback = _slug(fallback) or "unknown_agent"
    return safe_fallback if safe_fallback in AGENT_ROLES else "unknown_agent"


def normalize_message_type(value: Any, fallback: str = "status") -> str:
    candidate = _slug(value)
    if candidate in MESSAGE_TYPES:
        return candidate
    safe_fallback = _slug(fallback) or "status"
    return safe_fallback if safe_fallback in MESSAGE_TYPES else "status"


def normalize_handoff_status(value: Any, fallback: str = "requested") -> str:
    candidate = _slug(value)
    if candidate in HANDOFF_STATUSES:
        return candidate
    safe_fallback = _slug(fallback) or "requested"
    return safe_fallback if safe_fallback in HANDOFF_STATUSES else "requested"


@dataclass
class AgentHandoff:
    """Structured handoff packet passed between backend agent roles."""

    from_agent: str
    to_agent: str
    reason: str
    status: str = "requested"
    input_payload: Dict[str, Any] = field(default_factory=dict)
    result_payload: Dict[str, Any] = field(default_factory=dict)
    required_action: str = ""
    confidence: float = 0.0

    def __post_init__(self) -> None:
        self.from_agent = normalize_agent_role(self.from_agent)
        self.to_agent = normalize_agent_role(self.to_agent)
        self.status = normalize_handoff_status(self.status)
        self.reason = _compact_text(self.reason, 1000)
        self.required_action = _compact_text(self.required_action, 500)
        self.input_payload = _safe_dict(self.input_payload)
        self.result_payload = _safe_dict(self.result_payload)
        self.confidence = clamp_confidence(self.confidence)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_agent_handoff(
    *,
    from_agent: str,
    to_agent: str,
    reason: str,
    status: str = "requested",
    input_payload: Dict[str, Any] | None = None,
    result_payload: Dict[str, Any] | None = None,
    required_action: str = "",
    confidence: float = 0.0,
) -> AgentHandoff:
    return AgentHandoff(
        from_agent=from_agent,
        to_agent=to_agent,
        reason=reason,
        status=status,
        input_payload=input_payload or {},
        result_payload=result_payload or {},
        required_action=required_action,
        confidence=confidence,
    )
