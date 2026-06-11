"""Progress, streak, and test-history persistence."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.serializers import normalize_topic, utc_naive
from Logic.analytics_engine import update_topic_performance
from models import SessionDetail, TestHistory, UserProgress


def get_or_create_progress(db: Session, user_id: str) -> UserProgress:
    user = db.query(UserProgress).filter(UserProgress.user_id == user_id).first()

    if user:
        return user

    user = UserProgress(
        user_id=user_id,
        total_tests=0,
        total_questions=0,
        total_correct=0,
        xp=0,
        streak=0,
        last_active_date=None,
        focus_score=0.0,
        consistency_index=0.0,
        learning_efficiency=0.0,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user


def apply_streak(user: UserProgress) -> None:
    today = date.today()

    if user.last_active_date:
        difference = (today - user.last_active_date).days

        if difference == 0:
            pass
        elif difference == 1:
            user.streak += 1
        else:
            user.streak = 1
    else:
        user.streak = 1

    user.last_active_date = today


def create_test_history(
    db: Session,
    user_id: str,
    topic: str,
    score: int,
    total_questions: int,
    xp_earned: int,
    time_spent_seconds: int = 0,
    focus_score: float = 0.0,
    session_type: str = "exam",
    replay_data: Optional[Dict[str, Any]] = None,
    started_at: Optional[datetime] = None,
    completed_at: Optional[datetime] = None,
    response_latency_ms: int = 0,
    hint_count: int = 0,
    retry_count: int = 0,
    confidence_before: Optional[float] = None,
    confidence_after: Optional[float] = None,
) -> TestHistory:
    correct = max(0, min(score, total_questions))
    accuracy_rate = round((correct / total_questions) * 100, 2) if total_questions else 0.0
    started_at = utc_naive(started_at)
    completed_at = utc_naive(completed_at)
    measured_seconds = max(0, time_spent_seconds)
    if measured_seconds == 0 and started_at and completed_at:
        measured_seconds = max(0, int((completed_at - started_at).total_seconds()))

    test = TestHistory(
        user_id=user_id,
        date=date.today(),
        topic=normalize_topic(topic),
        score=correct,
        total_questions=total_questions,
        xp_earned=xp_earned,
        time_spent_seconds=measured_seconds,
        accuracy_rate=accuracy_rate,
        focus_score=max(0.0, min(100.0, focus_score)),
        session_type=session_type or "exam",
        started_at=started_at,
        completed_at=completed_at,
        response_latency_ms=max(0, int(response_latency_ms or 0)),
        hint_count=max(0, int(hint_count or 0)),
        retry_count=max(0, int(retry_count or 0)),
        confidence_before=confidence_before,
        confidence_after=confidence_after,
    )

    db.add(test)
    db.flush()

    if replay_data is not None:
        detail = SessionDetail(
            test_id=test.id,
            replay_data=replay_data,
        )
        db.add(detail)

    update_topic_performance(
        db=db,
        user_id=user_id,
        topic=normalize_topic(topic),
        correct_answers=correct,
        total_questions=total_questions,
        time_spent=measured_seconds,
    )

    db.refresh(test)
    return test
