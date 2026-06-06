"""Lead Coach orchestration decisions for a single student turn."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


def _append(values: List[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


@dataclass(frozen=True)
class LeadCoachDecision:
    primary_agent: str
    agent_sequence: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    safety_gates: List[str] = field(default_factory=list)
    statuses: List[str] = field(default_factory=list)
    student_goal: str = ""
    route_reason: str = ""
    retrieval_policy: str = "none"
    answer_format: str = "concept"
    socratic: bool = False
    direct_answer: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_lead_coach_decision(
    *,
    query: Any,
    answer_format: Dict[str, Any],
    orchestration_plan: Dict[str, Any],
    retrieval_policy: str,
    strict_grounding: bool,
    material_supported: bool,
    attachment_summary: Dict[str, Any] | None = None,
    mastery_profile: Dict[str, Any] | None = None,
) -> LeadCoachDecision:
    """Build the explicit agent route for the current coach turn."""
    tools = list(orchestration_plan.get("tools") or [])
    statuses = list(orchestration_plan.get("statuses") or [])
    attachment_summary = attachment_summary or {}
    mastery_profile = mastery_profile or {}
    answer_format_id = str(answer_format.get("id") or getattr(query, "answer_format", "") or "concept")
    answer_label = str(answer_format.get("label") or answer_format_id.replace("_", " ").title())

    agents: List[str] = []
    safety_gates: List[str] = []
    _append(agents, "lead_coach_orchestrator")
    _append(agents, "intent_profiler")

    if getattr(query, "is_conversational", False):
        _append(agents, "conversation_responder")
        if mastery_profile.get("route") not in {None, "", "baseline"} or getattr(query, "needs_memory", False):
            _append(agents, "memory_mastery_engine")
        return LeadCoachDecision(
            primary_agent="lead_coach_orchestrator",
            agent_sequence=agents,
            tools=[],
            safety_gates=[],
            statuses=[],
            student_goal="Give a short natural conversation reply without reopening the previous lesson.",
            route_reason=(
                f"Scenario profile routed intent '{getattr(query, 'intent', 'conversation')}' "
                "to the conversation responder."
            ),
            retrieval_policy="none",
            answer_format="conversation",
            socratic=False,
            direct_answer=False,
        )

    if retrieval_policy != "none" or "knowledge_search" in tools or attachment_summary.get("has_material"):
        _append(agents, "context_retriever")
    if any(tool not in {"knowledge_search", "attachment_reader", "answer_verifier"} for tool in tools):
        _append(agents, "tool_gateway")
    _append(agents, "tutor_model")

    if not getattr(query, "is_conversational", False):
        _append(agents, "answer_reviewer")
        _append(agents, "quality_verifier")
    if mastery_profile.get("route") not in {None, "", "baseline"} or getattr(query, "needs_memory", True):
        _append(agents, "memory_mastery_engine")

    if strict_grounding:
        _append(safety_gates, "strict_grounding")
    if strict_grounding and not material_supported:
        _append(safety_gates, "required_material_missing")
    if "answer_verifier" in tools:
        _append(safety_gates, "answer_verifier")
    if "safety_review" in tools:
        _append(safety_gates, "safety_review")
    if answer_format_id:
        _append(safety_gates, "student_friendly_format")

    route_reason = (
        f"Intent '{getattr(query, 'intent', 'general')}' uses {retrieval_policy} retrieval, "
        f"{len(tools)} selected tools, and {answer_format_id} answer format."
    )
    student_goal = (
        f"Give a clear, calm {answer_label.lower()} response that matches the student's current need."
    )

    return LeadCoachDecision(
        primary_agent="lead_coach_orchestrator",
        agent_sequence=agents,
        tools=tools,
        safety_gates=safety_gates,
        statuses=statuses,
        student_goal=student_goal,
        route_reason=route_reason,
        retrieval_policy=retrieval_policy,
        answer_format=answer_format_id,
        socratic=bool(orchestration_plan.get("socratic")),
        direct_answer=bool(orchestration_plan.get("direct_answer")),
    )
