"""Modular services used by the unified Study Lab coach."""

from .context_manager import build_compact_context
from .attachments import prepare_attachments
from .llm_router import llm_router
from .mastery_store import build_mastery_signal, persist_mastery_signal
from .mastery_engine import build_active_mastery_profile
from .observability import coach_observability
from .quality_scorer import score_coach_answer
from .query_understanding import understand_query
from .react_loop import build_coach_plan
from .retriever import grounded_retriever
from .settings import coach_settings
from .source_metadata import build_source_bundle
from .tool_registry import coach_tool_registry
from .unified_orchestrator import build_orchestration_plan, format_orchestration_prompt
from .turn_engine import (
    build_adaptive_answer_blocks,
    parse_semantic_event,
    resolve_hybrid_query,
    semantic_event,
)

__all__ = [
    "build_coach_plan",
    "build_active_mastery_profile",
    "build_compact_context",
    "coach_observability",
    "coach_settings",
    "coach_tool_registry",
    "build_orchestration_plan",
    "build_source_bundle",
    "format_orchestration_prompt",
    "grounded_retriever",
    "llm_router",
    "build_mastery_signal",
    "persist_mastery_signal",
    "prepare_attachments",
    "score_coach_answer",
    "build_adaptive_answer_blocks",
    "parse_semantic_event",
    "resolve_hybrid_query",
    "semantic_event",
    "understand_query",
]
