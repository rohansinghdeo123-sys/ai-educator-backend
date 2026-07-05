"""Spaced-repetition revision queue built from existing TopicPerformance data.

Pure scheduling logic: no DB access, no LLM calls. The router queries
TopicPerformance rows and passes them in, so the math stays unit-testable.

Model: each topic gets an estimated memory half-life ("stability") that grows
with practice volume and accuracy. Estimated retention decays along
0.5 ** (days_since_practiced / stability); low retention, low accuracy, and
weak/declining topics float to the top of the queue.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

MAX_QUEUE_SIZE = 25
DEFAULT_QUEUE_SIZE = 10

_BUCKET_ORDER = {"overdue": 0, "due": 1, "strengthen": 2, "fresh": 3}


def _stability_days(attempts: int, accuracy_fraction: float, weak: bool) -> float:
    """Estimated memory half-life in days for a topic."""
    stability = 1.0 + min(attempts, 40) * 0.15 * (0.4 + accuracy_fraction)
    if weak:
        stability *= 0.5
    return max(1.0, min(stability, 14.0))


def _bucket_for(days_since: float, stability: float, accuracy_fraction: float) -> str:
    if days_since >= 1.5 * stability:
        return "overdue"
    if days_since >= 0.75 * stability:
        return "due"
    if accuracy_fraction < 0.6:
        return "strengthen"
    return "fresh"


def _suggestion_for(bucket: str, accuracy_fraction: float) -> tuple[str, int]:
    if accuracy_fraction < 0.5:
        return "deep_review", 15
    if bucket == "overdue":
        return "quick_recall", 12
    if bucket in ("due", "strengthen"):
        return "practice_mcq", 10
    return "practice_mcq", 5


def _reason_for(bucket: str, days_since: float, accuracy_pct: float, declining: bool) -> str:
    days = int(round(days_since))
    if bucket == "overdue":
        base = f"Not practiced in {days} day{'s' if days != 1 else ''} — memory has likely faded."
    elif bucket == "due":
        base = "Coming up on its forgetting curve — a short review now locks it in."
    elif bucket == "strengthen":
        base = f"Accuracy is {accuracy_pct:.0f}% — needs strengthening before it's exam-ready."
    else:
        base = "Fresh in memory — a light touch keeps it that way."
    if declining and bucket != "strengthen":
        base += " Recent scores dipped, so it deserves priority."
    return base


def score_topic(
    *,
    topic: str,
    attempts: int,
    correct: int,
    weak: bool,
    trend_score: float,
    last_practiced: Optional[datetime],
    now: datetime,
) -> Optional[Dict[str, Any]]:
    """Score a single topic for revision priority. Returns None for unusable rows."""
    if attempts <= 0:
        return None

    accuracy_fraction = max(0.0, min(correct / attempts, 1.0))
    stability = _stability_days(attempts, accuracy_fraction, weak)

    if last_practiced is None:
        days_since = stability * 3.0
    else:
        days_since = max(0.0, (now - last_practiced).total_seconds() / 86400.0)

    retention = 0.5 ** (days_since / stability)
    declining = trend_score < 0

    priority = (1.0 - retention) * 70.0 + (1.0 - accuracy_fraction) * 30.0
    if weak:
        priority += 12.0
    if declining:
        priority += 6.0
    priority = round(min(priority, 100.0), 1)

    bucket = _bucket_for(days_since, stability, accuracy_fraction)
    mode, minutes = _suggestion_for(bucket, accuracy_fraction)

    return {
        "topic": topic,
        "bucket": bucket,
        "priority": priority,
        "retention_estimate": round(retention, 3),
        "days_since_practiced": round(days_since, 1),
        "accuracy": round(accuracy_fraction * 100.0, 1),
        "attempts": attempts,
        "weak": bool(weak),
        "declining": declining,
        "suggested_mode": mode,
        "suggested_minutes": minutes,
        "reason": _reason_for(bucket, days_since, accuracy_fraction * 100.0, declining),
    }


def build_revision_queue(
    topics: Iterable[Any],
    now: Optional[datetime] = None,
    limit: int = DEFAULT_QUEUE_SIZE,
) -> List[Dict[str, Any]]:
    """Build an ordered revision queue from TopicPerformance-shaped rows.

    Accepts ORM rows or any objects with topic/attempts/correct/weak/
    trend_score/last_practiced attributes. Orders by bucket urgency, then
    priority score descending.
    """
    now = now or datetime.utcnow()
    limit = max(1, min(int(limit), MAX_QUEUE_SIZE))

    entries: List[Dict[str, Any]] = []
    for row in topics:
        entry = score_topic(
            topic=str(getattr(row, "topic", "") or "").strip() or "General",
            attempts=int(getattr(row, "attempts", 0) or 0),
            correct=int(getattr(row, "correct", 0) or 0),
            weak=bool(getattr(row, "weak", False)),
            trend_score=float(getattr(row, "trend_score", 0.0) or 0.0),
            last_practiced=getattr(row, "last_practiced", None),
            now=now,
        )
        if entry is not None:
            entries.append(entry)

    entries.sort(key=lambda e: (_BUCKET_ORDER[e["bucket"]], -e["priority"], e["topic"]))
    return entries[:limit]


def summarize_queue(queue: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate counts and a friendly headline for the queue."""
    counts = {"overdue": 0, "due": 0, "strengthen": 0, "fresh": 0}
    for entry in queue:
        counts[entry["bucket"]] += 1

    top_pick = queue[0] if queue else None
    if top_pick is None:
        message = "No revision data yet — finish a practice session to build your queue."
    elif counts["overdue"]:
        message = (
            f"{counts['overdue']} topic{'s' if counts['overdue'] != 1 else ''} overdue — "
            f"start with {top_pick['topic']}."
        )
    elif counts["due"] or counts["strengthen"]:
        message = f"Good timing: review {top_pick['topic']} to stay ahead of forgetting."
    else:
        message = "Everything is fresh — a light recall session keeps your streak strong."

    return {**counts, "top_pick": top_pick, "message": message}
