"""Modular services used by the unified Study Lab coach."""

from .answer_repair import AnswerRepairDecision, decide_answer_repair, mark_repair_applied
from .context_manager import build_compact_context
from .attachments import prepare_attachments
from .llm_router import llm_router
from .lead_orchestrator import LeadCoachDecision, build_lead_coach_decision
from .model_gateway import ModelGateway, model_gateway
from .mastery_store import build_mastery_signal, build_student_memory_update, persist_mastery_signal
from .mastery_engine import build_active_mastery_profile
from .growth_loop import GrowthEvaluation, evaluate_turn_growth
from .observability import coach_observability
from .quality_scorer import score_coach_answer
from .query_understanding import understand_query
from .react_loop import build_coach_plan
from .retriever import grounded_retriever
from .retrieval_gate import RetrievalGateDecision, evaluate_retrieval_gate
from .response_planner import (
    ResponsePlannerOutput,
    build_response_plan,
    build_response_plan_instruction,
)
from .settings import coach_settings
from .source_metadata import build_source_bundle
from .tool_registry import coach_tool_registry
from .tool_gateway import ToolGateway, tool_gateway
from .unified_orchestrator import build_orchestration_plan, format_orchestration_prompt
from .turn_engine import (
    build_adaptive_answer_blocks,
    parse_semantic_event,
    resolve_hybrid_query,
    semantic_event,
)

__all__ = [
    "build_coach_plan",
    "AnswerRepairDecision",
    "decide_answer_repair",
    "mark_repair_applied",
    "build_active_mastery_profile",
    "build_compact_context",
    "coach_observability",
    "coach_settings",
    "coach_tool_registry",
    "ToolGateway",
    "tool_gateway",
    "build_orchestration_plan",
    "build_source_bundle",
    "format_orchestration_prompt",
    "GrowthEvaluation",
    "evaluate_turn_growth",
    "grounded_retriever",
    "RetrievalGateDecision",
    "evaluate_retrieval_gate",
    "ResponsePlannerOutput",
    "build_response_plan",
    "build_response_plan_instruction",
    "LeadCoachDecision",
    "build_lead_coach_decision",
    "llm_router",
    "ModelGateway",
    "model_gateway",
    "build_mastery_signal",
    "build_student_memory_update",
    "persist_mastery_signal",
    "prepare_attachments",
    "score_coach_answer",
    "build_adaptive_answer_blocks",
    "parse_semantic_event",
    "resolve_hybrid_query",
    "semantic_event",
    "understand_query",
]
