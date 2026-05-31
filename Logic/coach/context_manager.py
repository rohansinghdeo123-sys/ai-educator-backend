"""Compact context assembly for the unified Study Lab coach."""

from typing import Any, Dict, Iterable

from .models import QueryUnderstanding, RetrievalResult


def _trim(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def build_compact_context(
    query: QueryUnderstanding,
    retrieval: RetrievalResult | Dict[str, Any],
    recent_messages: Iterable[Dict[str, Any]] = (),
    memory_summary: str = "",
    student_state: Dict[str, Any] | None = None,
    lesson_memory: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if isinstance(retrieval, dict):
        retrieval = RetrievalResult(
            context=str(retrieval.get("context") or ""),
            section_id=str(retrieval.get("section_id") or ""),
            source=str(retrieval.get("source") or ""),
            paragraphs_found=int(retrieval.get("paragraphs_found") or 0),
            keywords_used=list(retrieval.get("keywords_used") or []),
            scope=dict(retrieval.get("scope") or {}),
            supported=bool(retrieval.get("supported", bool(retrieval.get("context")))),
            error=str(retrieval.get("error") or ""),
        )

    messages = []
    for item in list(recent_messages)[-6:]:
        if not isinstance(item, dict):
            continue
        messages.append({
            "role": str(item.get("role") or "message"),
            "content": _trim(item.get("content"), 280),
        })

    return {
        "query": query.to_dict(),
        "scope": retrieval.scope,
        "retrieval": {
            "source": retrieval.source,
            "section_id": retrieval.section_id,
            "paragraphs_found": retrieval.paragraphs_found,
            "supported": retrieval.supported,
            "context_excerpt": _trim(retrieval.context, 900),
        },
        "recent_messages": messages,
        "memory_summary": _trim(memory_summary, 900),
        "lesson_memory": lesson_memory or {},
        "student_state": student_state or {},
    }
