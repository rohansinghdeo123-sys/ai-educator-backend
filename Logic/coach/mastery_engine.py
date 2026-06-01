"""Active mastery decisions derived from compact Coach memories."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, Dict, Iterable


def _normalized(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _topic_from_scope(scope: Dict[str, Any], anchors: Iterable[str]) -> str:
    for value in (scope.get("topic"), scope.get("section_id"), scope.get("chapter")):
        topic = _normalized(value)
        if topic and topic not in {"general", "open", "any", "open_tutor_topic"}:
            return topic
    return "_".join(_normalized(item) for item in list(anchors)[:3] if _normalized(item)) or "open_concept"


def _parse_date(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def build_active_mastery_profile(
    memories: Iterable[Any],
    scope: Dict[str, Any],
    anchors: Iterable[str] = (),
    now: datetime | None = None,
) -> Dict[str, Any]:
    """Recommend a teaching adjustment without pretending chat signals are exam scores."""
    current_topic = _topic_from_scope(scope, anchors)
    concept_rows = []
    for memory in memories or []:
        metadata = getattr(memory, "metadata_json", {}) or {}
        if not isinstance(metadata, dict) or not metadata.get("topic"):
            continue
        concept_rows.append(metadata)

    matching = next((row for row in concept_rows if row.get("topic") == current_topic), None)
    weakest = max(concept_rows, key=lambda row: int(row.get("support_count") or 0), default={})
    row = matching or {}
    observations = int(row.get("observations") or 0)
    support_count = int(row.get("support_count") or 0)
    average_confidence = float(row.get("average_confidence") or 0.0)

    if support_count >= 2:
        route = "simplify_and_reinforce"
        directive = "Use simpler language, one worked example, and one small understanding check."
        interval_days = 1
    elif observations >= 3 and average_confidence >= 78:
        route = "increase_difficulty"
        directive = "Keep the explanation concise and add one slightly harder application or edge case."
        interval_days = 7
    elif observations:
        route = "steady_progression"
        directive = "Teach clearly, connect to the previous concept, and close with one useful checkpoint."
        interval_days = 3
    else:
        route = "baseline"
        directive = "Infer the suitable depth from the student's question and answer naturally."
        interval_days = 3

    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    last_observed = _parse_date(row.get("last_observed_at"))
    revision_due_on = (last_observed + timedelta(days=interval_days)) if last_observed else None
    return {
        "topic": str(row.get("topic") or current_topic),
        "route": route,
        "directive": directive,
        "observations": observations,
        "support_count": support_count,
        "average_confidence": round(average_confidence, 1),
        "weak_topic": str(weakest.get("topic") or ""),
        "revision_due": bool(revision_due_on and revision_due_on <= now),
        "revision_due_on": revision_due_on.date().isoformat() if revision_due_on else "",
        "revision_interval_days": interval_days,
    }
