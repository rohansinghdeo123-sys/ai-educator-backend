"""Service layer for the student exam-weakness tracker.

Weakness signals are derived deterministically from written-answer evaluations
(see ``Logic.exam.agents.derive_weaknesses``) and upserted here so the same
weakness accumulates a frequency count and evidence over time.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from models import StudentExamWeakness, WrittenAnswerAttempt, WrittenAnswerFeedback
from Logic.exam import agents

logger = logging.getLogger("ai_educator.services.exam_weakness")

MAX_EVIDENCE = 8


def _utcnow() -> datetime:
    return datetime.utcnow()


def record_weaknesses(
    db: Session,
    user_id: str,
    signals: List[Dict[str, Any]],
    *,
    class_level: str = "",
    subject: str = "",
    chapter_name: str = "",
    chapter_id: Optional[int] = None,
    commit: bool = True,
) -> int:
    """Upsert weakness signals for a user. Returns how many were processed."""
    processed = 0
    for signal in signals or []:
        weakness_type = str(signal.get("weakness_type") or "").strip().lower()
        if not weakness_type:
            continue
        topic = str(signal.get("topic") or chapter_name or "").strip()
        existing = (
            db.query(StudentExamWeakness)
            .filter(
                StudentExamWeakness.user_id == user_id,
                StudentExamWeakness.subject == (subject or ""),
                StudentExamWeakness.topic == topic,
                StudentExamWeakness.weakness_type == weakness_type,
            )
            .first()
        )
        evidence = [str(e) for e in (signal.get("evidence") or []) if str(e).strip()]
        if existing:
            existing.frequency_count = int(existing.frequency_count or 0) + 1
            existing.last_seen_at = _utcnow()
            existing.weakness_summary = signal.get("weakness_summary") or existing.weakness_summary
            if signal.get("improvement_suggestion"):
                existing.improvement_suggestion = signal["improvement_suggestion"]
            merged = list(existing.evidence_json or []) + evidence
            existing.evidence_json = merged[-MAX_EVIDENCE:]
            existing.updated_at = _utcnow()
        else:
            db.add(
                StudentExamWeakness(
                    user_id=user_id,
                    class_level=class_level or "",
                    subject=subject or "",
                    chapter_id=chapter_id,
                    chapter_name=chapter_name or "",
                    topic=topic,
                    weakness_type=weakness_type,
                    weakness_summary=signal.get("weakness_summary") or "",
                    evidence_json=evidence[-MAX_EVIDENCE:],
                    frequency_count=1,
                    last_seen_at=_utcnow(),
                    improvement_suggestion=signal.get("improvement_suggestion") or "",
                )
            )
        processed += 1
    if commit:
        db.commit()
    return processed


def weakness_report(
    db: Session,
    user_id: str,
    *,
    subject: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    query = db.query(StudentExamWeakness).filter(StudentExamWeakness.user_id == user_id)
    if subject:
        query = query.filter(StudentExamWeakness.subject == subject)
    total = query.count()
    rows = (
        query.order_by(
            StudentExamWeakness.frequency_count.desc(),
            StudentExamWeakness.last_seen_at.desc(),
        )
        .offset(max(0, offset))
        .limit(max(1, min(limit, 300)))
        .all()
    )
    return {"total": total, "weaknesses": [serialize_weakness(row) for row in rows]}


def weakness_by_topic(db: Session, user_id: str) -> Dict[str, Any]:
    rows = db.query(StudentExamWeakness).filter(StudentExamWeakness.user_id == user_id).all()
    groups: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = (row.topic or "General").strip() or "General"
        bucket = groups.setdefault(
            key,
            {
                "topic": key,
                "subject": row.subject or "",
                "total_frequency": 0,
                "_types": Counter(),
                "latest_suggestion": "",
                "_last_seen": None,
            },
        )
        bucket["total_frequency"] += int(row.frequency_count or 0)
        if row.weakness_type:
            bucket["_types"][row.weakness_type] += int(row.frequency_count or 0)
        last_seen = row.last_seen_at
        if last_seen and (bucket["_last_seen"] is None or last_seen >= bucket["_last_seen"]):
            bucket["_last_seen"] = last_seen
            if row.improvement_suggestion:
                bucket["latest_suggestion"] = row.improvement_suggestion

    topics = [
        {
            "topic": bucket["topic"],
            "subject": bucket["subject"],
            "total_frequency": bucket["total_frequency"],
            "weakness_types": [t for t, _ in bucket["_types"].most_common()],
            "latest_suggestion": bucket["latest_suggestion"],
        }
        for bucket in groups.values()
    ]
    topics.sort(key=lambda t: t["total_frequency"], reverse=True)
    return {"total_topics": len(topics), "topics": topics}


def recalculate(db: Session, user_id: str) -> Dict[str, Any]:
    """Rebuild the weakness table from all of this user's evaluated attempts.

    Reconstructs an evaluation payload from each stored feedback row and re-derives
    weakness signals, so the report reflects the full written-practice history.
    """
    db.query(StudentExamWeakness).filter(StudentExamWeakness.user_id == user_id).delete(
        synchronize_session=False
    )

    rows = (
        db.query(WrittenAnswerFeedback, WrittenAnswerAttempt)
        .join(WrittenAnswerAttempt, WrittenAnswerFeedback.attempt_id == WrittenAnswerAttempt.id)
        .filter(WrittenAnswerFeedback.user_id == user_id)
        .order_by(WrittenAnswerFeedback.id.asc())
        .all()
    )

    processed = 0
    for feedback, attempt in rows:
        evaluation = {
            "marks_awarded": feedback.marks_awarded,
            "marks_total": feedback.marks_total,
            "missing_points": list(feedback.missing_points_json or []),
            "incorrect_points": list(feedback.incorrect_points_json or []),
            "weak_explanation_areas": list(feedback.weak_explanation_json or []),
            "rubric_scores": dict(feedback.rubric_scores_json or {}),
            "improve_to_full_marks": feedback.improve_to_full_marks or "",
            "weakness_tags": [],
        }
        signals = agents.derive_weaknesses(
            evaluation=evaluation,
            subject=attempt.subject or "",
            class_level=attempt.class_level or "",
            chapter_name=attempt.chapter_name or "",
            topic=attempt.topic or "",
        )
        processed += record_weaknesses(
            db,
            user_id,
            signals,
            class_level=attempt.class_level or "",
            subject=attempt.subject or "",
            chapter_name=attempt.chapter_name or "",
            chapter_id=attempt.chapter_id,
            commit=False,
        )
    db.commit()
    return {"attempts_processed": len(rows), "signals_recorded": processed}


def serialize_weakness(row: StudentExamWeakness) -> Dict[str, Any]:
    return {
        "id": row.id,
        "class_level": row.class_level or "",
        "subject": row.subject or "",
        "chapter_id": row.chapter_id,
        "chapter_name": row.chapter_name or "",
        "topic": row.topic or "",
        "weakness_type": row.weakness_type or "",
        "weakness_summary": row.weakness_summary or "",
        "evidence": list(row.evidence_json or []),
        "frequency_count": row.frequency_count or 0,
        "last_seen_at": row.last_seen_at,
        "improvement_suggestion": row.improvement_suggestion or "",
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
