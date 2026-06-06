"""Typed shared state for backend-controlled agent workflows.

This module is intentionally lightweight. It gives the Coach and future
Autonomous Mission runtime one controlled state object without forcing a broad
rewrite of existing endpoints.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional

from .contracts import clamp_confidence, normalize_agent_role, normalize_message_type


MAX_TEXT_FIELD_CHARS = 1200
MAX_CONTEXT_EXCERPT_CHARS = 900


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compact_text(value: Any, limit: int = MAX_TEXT_FIELD_CHARS) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _confidence_from_quality(value: Any) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 3)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class AgentMessage:
    """Controlled internal message between backend agent roles."""

    sender_agent: str
    receiver_agent: str
    message_type: str
    task: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    required_action: str = ""
    result: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        self.sender_agent = normalize_agent_role(self.sender_agent)
        self.receiver_agent = normalize_agent_role(self.receiver_agent)
        self.message_type = normalize_message_type(self.message_type)
        self.evidence = _safe_dict(self.evidence)
        self.result = _safe_dict(self.result)
        self.confidence = clamp_confidence(self.confidence)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["task"] = _compact_text(data.get("task"), 500)
        data["required_action"] = _compact_text(data.get("required_action"), 500)
        data["confidence"] = clamp_confidence(data.get("confidence"))
        return data


@dataclass
class AgentState:
    """One shared state object for a single coach or mission turn."""

    request_id: str
    turn_id: str
    user_id: str
    session_id: str
    raw_user_input: str
    normalized_question: str
    selected_mode: str = "coach"
    raw_message: str = ""
    original_message: str = ""
    conversation_history: Dict[str, Any] = field(default_factory=dict)
    detected_intent: str = "general"
    answer_format: str = "concept"
    detected_topic: str = ""
    student_level: str = ""
    student_confusion_signal: str = ""
    student_preferred_style: str = ""
    retrieval_policy: str = "none"
    retrieved_context: str = ""
    retrieved_source_scores: Dict[str, Any] = field(default_factory=dict)
    uploaded_image_context: str = ""
    attachment_summary: Dict[str, Any] = field(default_factory=dict)
    tool_outputs: Dict[str, Any] = field(default_factory=dict)
    agent_messages: List[AgentMessage] = field(default_factory=list)
    tutor_draft: str = ""
    misconception_notes: List[str] = field(default_factory=list)
    verification_result: Dict[str, Any] = field(default_factory=dict)
    reviewer_notes: str = ""
    final_answer: str = ""
    confidence_score: float = 0.0
    grounding_status: str = "not_required"
    memory_update: Dict[str, Any] = field(default_factory=dict)
    analytics_event: Dict[str, Any] = field(default_factory=dict)
    next_best_action: str = ""
    follow_up_suggestions: List[str] = field(default_factory=list)
    error_state: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)

    def touch(self) -> None:
        self.updated_at = _utc_now()

    def add_message(
        self,
        *,
        sender_agent: str,
        receiver_agent: str,
        message_type: str,
        task: str = "",
        evidence: Optional[Dict[str, Any]] = None,
        confidence: float = 0.0,
        required_action: str = "",
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.agent_messages.append(
            AgentMessage(
                sender_agent=sender_agent,
                receiver_agent=receiver_agent,
                message_type=message_type,
                task=task,
                evidence=evidence or {},
                confidence=confidence,
                required_action=required_action,
                result=result or {},
            )
        )
        self.agent_messages = self.agent_messages[-32:]
        self.touch()

    def apply_query(self, query: Any, answer_format: Any = None) -> None:
        self.detected_intent = str(getattr(query, "intent", self.detected_intent) or self.detected_intent)
        format_value = (
            (answer_format or {}).get("id")
            if isinstance(answer_format, dict)
            else getattr(query, "answer_format", None)
        )
        self.answer_format = str(format_value or self.answer_format)
        self.retrieval_policy = str(getattr(query, "retrieval_policy", self.retrieval_policy) or self.retrieval_policy)
        self.grounding_status = "required" if bool(getattr(query, "requires_grounding", False)) else (
            "optional" if self.retrieval_policy == "optional" else "not_required"
        )
        self.metadata["query"] = query.to_dict() if hasattr(query, "to_dict") else {}
        self.touch()

    def apply_conversation_context(self, context: Dict[str, Any]) -> None:
        self.conversation_history = {
            "is_follow_up": bool(context.get("is_follow_up")),
            "last_student_question": _compact_text(context.get("last_student_question"), 500),
            "recent_thread_excerpt": _compact_text(context.get("recent_thread"), 900),
            "has_durable_memory": bool(str(context.get("durable_memory") or "").strip()),
        }
        self.touch()

    def apply_scope(self, scope: Dict[str, Any]) -> None:
        self.detected_topic = str(
            scope.get("topic")
            or scope.get("section_id")
            or scope.get("chapter")
            or self.detected_topic
            or ""
        ).strip()
        self.metadata["scope"] = dict(scope or {})
        self.touch()

    def apply_attachments(self, bundle: Any) -> None:
        multimodal = _safe_dict(getattr(bundle, "multimodal", {}))
        self.uploaded_image_context = _compact_text(getattr(bundle, "vision_summary", ""), 900)
        self.attachment_summary = {
            "images": int(getattr(bundle, "image_count", 0) or 0),
            "documents": int(getattr(bundle, "document_count", 0) or 0),
            "warnings": list(getattr(bundle, "warnings", []) or [])[:6],
            "has_material": bool(getattr(bundle, "has_material", False)),
            "multimodal": {
                "confidence": multimodal.get("confidence", 0),
                "math_lines": len(_safe_list(multimodal.get("math_lines"))),
                "formulas": len(_safe_list(multimodal.get("formulas"))),
                "diagram_specs": len(_safe_list(multimodal.get("diagram_specs"))),
            },
        }
        self.touch()

    def apply_retrieval(self, material: Optional[Dict[str, Any]], policy: str, supported: bool) -> None:
        material = material or {}
        context = str(material.get("context") or "")
        self.retrieval_policy = policy or self.retrieval_policy
        self.retrieved_context = context
        self.retrieved_source_scores = {
            "section_id": str(material.get("section_id") or ""),
            "source": str(material.get("source") or ""),
            "paragraphs_found": int(material.get("paragraphs_found") or 0),
            "supported": bool(supported),
            "keywords_used": list(material.get("keywords_used") or [])[:12],
        }
        self.grounding_status = (
            "grounded" if supported and context.strip()
            else "missing_required_source" if policy == "required" and not supported
            else "not_required" if policy == "none"
            else "optional_no_source"
        )
        self.touch()

    def apply_tools(self, outputs: Dict[str, Any], tools: List[str]) -> None:
        self.tool_outputs = {
            name: _summarize_tool_output(outputs.get(name))
            for name in list(tools or [])
            if name in outputs
        }
        self.metadata["selected_tools"] = list(tools or [])
        self.touch()

    def apply_answer(
        self,
        *,
        draft: str = "",
        final_answer: str = "",
        reviewer_notes: str = "",
        next_best_action: str = "",
    ) -> None:
        if draft:
            self.tutor_draft = draft
        if final_answer:
            self.final_answer = final_answer
        if reviewer_notes:
            self.reviewer_notes = reviewer_notes
        if next_best_action:
            self.next_best_action = next_best_action
        self.touch()

    def apply_quality(self, quality: Any, verification: Optional[Dict[str, Any]] = None) -> None:
        quality_dict = quality.to_dict() if hasattr(quality, "to_dict") else _safe_dict(quality)
        score = quality_dict.get("score", 0)
        self.confidence_score = _confidence_from_quality(score)
        self.verification_result = {
            "quality": quality_dict,
            "tool_verification": verification or {},
        }
        if quality_dict.get("issues"):
            self.misconception_notes = [
                str(item) for item in list(quality_dict.get("issues") or [])[:8]
            ]
        self.touch()

    def apply_memory_and_analytics(
        self,
        *,
        mastery_signal: Optional[Dict[str, Any]] = None,
        analytics_snapshot: Optional[Dict[str, Any]] = None,
        recommendation: str = "",
    ) -> None:
        self.memory_update = {
            "mastery_signal": mastery_signal or {},
            "recommendation_saved": bool(recommendation),
        }
        self.analytics_event = {
            "has_progress": bool(_safe_dict(analytics_snapshot).get("progress")),
            "weak_topic_count": len(_safe_list(_safe_dict(analytics_snapshot).get("weak_topics"))),
            "strong_topic_count": len(_safe_list(_safe_dict(analytics_snapshot).get("strong_topics"))),
        }
        if recommendation:
            self.next_best_action = recommendation
        self.touch()

    def apply_error(self, *, stage: str, error: Any) -> None:
        self.error_state = {
            "stage": stage,
            "message": _compact_text(error, 500),
        }
        self.touch()

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["agent_messages"] = [message.to_dict() for message in self.agent_messages]
        return data

    def to_trace_dict(self, *, include_answer: bool = False) -> Dict[str, Any]:
        """Return safe metadata for traces and API payloads."""
        data = self.to_dict()
        data["raw_user_input"] = _compact_text(data.get("raw_user_input"), 500)
        data["raw_message"] = _compact_text(data.get("raw_message"), 500)
        data["original_message"] = _compact_text(data.get("original_message"), 500)
        data["retrieved_context_excerpt"] = _compact_text(
            data.pop("retrieved_context", ""),
            MAX_CONTEXT_EXCERPT_CHARS,
        )
        data["uploaded_image_context"] = _compact_text(data.get("uploaded_image_context"), 500)
        data["tutor_draft"] = _compact_text(data.get("tutor_draft"), 500)
        if include_answer:
            data["final_answer"] = _compact_text(data.get("final_answer"), 900)
        else:
            data.pop("final_answer", None)
        return data


def _summarize_tool_output(value: Any) -> Any:
    if isinstance(value, dict):
        summary: Dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(item, str):
                summary[key] = _compact_text(item, 500)
            elif isinstance(item, list):
                summary[key] = item[:8]
            elif isinstance(item, dict):
                summary[key] = {
                    sub_key: _compact_text(sub_value, 300) if isinstance(sub_value, str) else sub_value
                    for sub_key, sub_value in list(item.items())[:12]
                }
            else:
                summary[key] = item
        return summary
    if isinstance(value, str):
        return _compact_text(value, 700)
    return value


def build_initial_agent_state(
    *,
    request: Any,
    turn_id: str,
    user_id: str,
    session_id: str,
    question: str,
    mode: str,
    query: Any = None,
    answer_format: Any = None,
    adaptive_context: Optional[Dict[str, Any]] = None,
) -> AgentState:
    adaptive_context = adaptive_context or {}
    student_state = _safe_dict(adaptive_context.get("student_state"))
    strategy = _safe_dict(adaptive_context.get("adaptive_strategy"))
    learning_context = _safe_dict(adaptive_context.get("learning_context"))
    state = AgentState(
        request_id=turn_id,
        turn_id=turn_id,
        user_id=str(user_id or "anonymous"),
        session_id=str(session_id or ""),
        raw_user_input=str(question or ""),
        normalized_question=_compact_text(question, 2000),
        selected_mode=str(mode or "coach"),
        raw_message=str(getattr(request, "raw_message", "") or ""),
        original_message=str(getattr(request, "original_message", "") or ""),
        student_level=str(
            student_state.get("level")
            or learning_context.get("student_level")
            or ""
        ),
        student_confusion_signal=str(
            student_state.get("emotional_state")
            or student_state.get("confusion_signal")
            or ""
        ),
        student_preferred_style=str(
            strategy.get("answer_style")
            or learning_context.get("preferred_style")
            or ""
        ),
        metadata={
            "has_adaptive_signals": bool(adaptive_context.get("has_signals")),
            "mentor_directive_used": bool(adaptive_context.get("mentor_directive")),
            "system_guardrail_used": bool(adaptive_context.get("system_guardrail")),
        },
    )
    if query is not None:
        state.apply_query(query, answer_format)
    state.add_message(
        sender_agent="api",
        receiver_agent="lead_coach_orchestrator",
        message_type="request_received",
        task="Normalize the student request and choose the safest learning route.",
        confidence=1.0,
    )
    return state
