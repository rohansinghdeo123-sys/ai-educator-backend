"""Modular services used by the unified Study Lab coach."""

from .context_manager import build_compact_context
from .llm_router import llm_router
from .observability import coach_observability
from .quality_scorer import score_coach_answer
from .query_understanding import understand_query
from .react_loop import build_coach_plan
from .retriever import grounded_retriever
from .settings import coach_settings
from .tool_registry import coach_tool_registry
from .turn_engine import (
    build_adaptive_answer_blocks,
    parse_semantic_event,
    resolve_hybrid_query,
    semantic_event,
)

__all__ = [
    "build_coach_plan",
    "build_compact_context",
    "coach_observability",
    "coach_settings",
    "coach_tool_registry",
    "grounded_retriever",
    "llm_router",
    "score_coach_answer",
    "build_adaptive_answer_blocks",
    "parse_semantic_event",
    "resolve_hybrid_query",
    "semantic_event",
    "understand_query",
]
