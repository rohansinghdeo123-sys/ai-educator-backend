"""Service layer for written-answer practice: sessions, question generation,
teacher-style evaluation, and attempt history.

Expected marking points are stored on the attempt but never serialized back to
the student before they answer, so they cannot peek at the marking scheme.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from Logic.exam import agents
from models import (
    WrittenAnswerAttempt,
    WrittenAnswerFeedback,
    WrittenPracticeSession,
)
from services import exam_weakness_service

logger = logging.getLogger("ai_educator.services.written_practice")


class WrittenPracticeError(ValueError):
    """Raised for invalid written-practice requests."""


def _utcnow() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def start_session(
    db: Session,
    user_id: str,
    *,
    class_level: str = "",
    subject: str = "",
    chapter_name: str = "",
    chapter_id: Optional[int] = None,
    topic: str = "",
    marks_focus: str = "",
) -> WrittenPracticeSession:
    session = WrittenPracticeSession(
        user_id=user_id,
        class_level=class_level or "",
        subject=subject or "",
        chapter_id=chapter_id,
        chapter_name=chapter_name or "",
        topic=topic or "",
        marks_focus=str(marks_focus or ""),
        session_status="active",
        started_at=_utcnow(),
        metadata_json={},
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_owned_session(db: Session, user_id: str, session_id: int) -> Optional[WrittenPracticeSession]:
    return (
        db.query(WrittenPracticeSession)
        .filter(WrittenPracticeSession.id == session_id, WrittenPracticeSession.user_id == user_id)
        .first()
    )


def get_owned_attempt(db: Session, user_id: str, attempt_id: int) -> Optional[WrittenAnswerAttempt]:
    return (
        db.query(WrittenAnswerAttempt)
        .filter(WrittenAnswerAttempt.id == attempt_id, WrittenAnswerAttempt.user_id == user_id)
        .first()
    )


def _attempt_count(db: Session, session_id: int) -> int:
    return db.query(WrittenAnswerAttempt).filter(WrittenAnswerAttempt.session_id == session_id).count()


def _grounding_context(session: WrittenPracticeSession, topic: str, use_grounding: bool) -> str:
    if not use_grounding:
        return ""
    return agents.build_reference_context(
        section_id=topic or session.chapter_name or session.subject or "general",
        query=topic or session.chapter_name or session.subject,
        subject=session.subject or "",
        chapter=session.chapter_name or "",
    )


# ---------------------------------------------------------------------------
# Question generation
# ---------------------------------------------------------------------------
def generate_question(
    db: Session,
    user_id: str,
    session: WrittenPracticeSession,
    *,
    topic: Optional[str] = None,
    marks_focus: Optional[str] = None,
    question_type: Optional[str] = None,
    use_syllabus_grounding: bool = True,
) -> WrittenAnswerAttempt:
    effective_topic = (topic or session.topic or "").strip()
    effective_marks = (marks_focus or session.marks_focus or "").strip()
    augment = _grounding_context(session, effective_topic, use_syllabus_grounding)

    result = agents.generate_written_question(
        class_level=session.class_level or "",
        subject=session.subject or "",
        chapter_name=session.chapter_name or "",
        topic=effective_topic,
        marks_focus=effective_marks,
        question_type=question_type or "",
        augment_context=augment,
    )

    attempt = WrittenAnswerAttempt(
        session_id=session.id,
        user_id=user_id,
        question_text=result["question_text"],
        question_type=result["question_type"],
        marks_total=float(result["marks_total"] or 0),
        student_answer="",
        expected_points_json=result["expected_points"],
        class_level=session.class_level or "",
        subject=session.subject or "",
        chapter_id=session.chapter_id,
        chapter_name=session.chapter_name or "",
        topic=result.get("topic") or effective_topic,
        evaluation_status="awaiting_answer",
    )
    # Stash command_word for the response without a dedicated column.
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    attempt._command_word = result.get("command_word", "")  # type: ignore[attr-defined]
    return attempt


# ---------------------------------------------------------------------------
# Submission + evaluation
# ---------------------------------------------------------------------------
def submit_answer(
    db: Session,
    user_id: str,
    *,
    answer: str,
    attempt_id: Optional[int] = None,
    session_id: Optional[int] = None,
    question_text: Optional[str] = None,
    question_type: Optional[str] = None,
    marks_total: Optional[float] = None,
    topic: Optional[str] = None,
    expected_points: Optional[List[str]] = None,
    use_syllabus_grounding: bool = True,
) -> Tuple[WrittenAnswerAttempt, WrittenAnswerFeedback, int]:
    """Grade a written answer. ``attempt_id`` grades a previously generated
    question; otherwise an ad-hoc attempt is created under ``session_id``."""
    if attempt_id is not None:
        attempt = get_owned_attempt(db, user_id, attempt_id)
        if attempt is None:
            raise WrittenPracticeError("Attempt not found.")
        session = get_owned_session(db, user_id, attempt.session_id) if attempt.session_id else None
    else:
        if session_id is None or not (question_text or "").strip():
            raise WrittenPracticeError(
                "Provide an attempt_id, or a session_id with a question_text to grade."
            )
        session = get_owned_session(db, user_id, session_id)
        if session is None:
            raise WrittenPracticeError("Practice session not found.")
        attempt = WrittenAnswerAttempt(
            session_id=session.id,
            user_id=user_id,
            question_text=question_text or "",
            question_type=question_type or "descriptive",
            marks_total=float(marks_total or 0),
            expected_points_json=list(expected_points or []),
            class_level=session.class_level or "",
            subject=session.subject or "",
            chapter_id=session.chapter_id,
            chapter_name=session.chapter_name or "",
            topic=(topic or session.topic or "").strip(),
            evaluation_status="awaiting_answer",
        )
        db.add(attempt)
        db.flush()

    attempt.student_answer = answer
    attempt.submitted_at = _utcnow()
    attempt.evaluation_status = "evaluating"

    augment = ""
    if use_syllabus_grounding:
        augment = agents.build_reference_context(
            section_id=attempt.topic or attempt.chapter_name or attempt.subject or "general",
            query=attempt.topic or attempt.chapter_name or attempt.subject,
            subject=attempt.subject or "",
            chapter=attempt.chapter_name or "",
        )

    evaluation = agents.evaluate_written_answer(
        question_text=attempt.question_text or "",
        question_type=attempt.question_type or "",
        marks_total=float(attempt.marks_total or 0),
        student_answer=answer,
        expected_points=list(attempt.expected_points_json or []),
        class_level=attempt.class_level or "",
        subject=attempt.subject or "",
        chapter_name=attempt.chapter_name or "",
        topic=attempt.topic or "",
        augment_context=augment,
    )

    marks_total_value = float(evaluation.get("marks_total") or attempt.marks_total or 0)
    marks_awarded = float(evaluation.get("marks_awarded") or 0)
    score_pct = round((marks_awarded / marks_total_value) * 100, 2) if marks_total_value > 0 else 0.0

    # One feedback row per attempt: replace any prior evaluation.
    db.query(WrittenAnswerFeedback).filter(
        WrittenAnswerFeedback.attempt_id == attempt.id
    ).delete(synchronize_session=False)
    feedback = WrittenAnswerFeedback(
        attempt_id=attempt.id,
        user_id=user_id,
        marks_awarded=marks_awarded,
        marks_total=marks_total_value,
        score_percentage=score_pct,
        covered_points_json=evaluation.get("covered_points", []),
        missing_points_json=evaluation.get("missing_points", []),
        incorrect_points_json=evaluation.get("incorrect_points", []),
        weak_explanation_json=evaluation.get("weak_explanation_areas", []),
        presentation_feedback=evaluation.get("presentation_feedback", ""),
        teacher_feedback=evaluation.get("teacher_feedback", ""),
        model_answer=evaluation.get("model_answer", ""),
        improve_to_full_marks=evaluation.get("improve_to_full_marks", ""),
        rubric_scores_json=evaluation.get("rubric_scores", {}),
        next_question_suggestion=evaluation.get("next_question_suggestion", ""),
    )
    db.add(feedback)
    attempt.evaluation_status = "evaluated"

    signals = agents.derive_weaknesses(
        evaluation=evaluation,
        subject=attempt.subject or "",
        class_level=attempt.class_level or "",
        chapter_name=attempt.chapter_name or "",
        topic=attempt.topic or "",
    )
    weaknesses_updated = exam_weakness_service.record_weaknesses(
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
    db.refresh(attempt)
    db.refresh(feedback)
    return attempt, feedback, weaknesses_updated


def get_attempt_feedback(db: Session, attempt: WrittenAnswerAttempt) -> Optional[WrittenAnswerFeedback]:
    return (
        db.query(WrittenAnswerFeedback)
        .filter(WrittenAnswerFeedback.attempt_id == attempt.id)
        .order_by(WrittenAnswerFeedback.id.desc())
        .first()
    )


def list_history(
    db: Session,
    user_id: str,
    *,
    subject: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[int, List[Dict[str, Any]]]:
    query = db.query(WrittenAnswerAttempt).filter(WrittenAnswerAttempt.user_id == user_id)
    if subject:
        query = query.filter(WrittenAnswerAttempt.subject == subject)
    total = query.count()
    attempts = (
        query.order_by(WrittenAnswerAttempt.id.desc())
        .offset(max(0, offset))
        .limit(max(1, min(limit, 200)))
        .all()
    )
    feedback_by_attempt = _feedback_map(db, [a.id for a in attempts])
    return total, [serialize_attempt_summary(a, feedback_by_attempt.get(a.id)) for a in attempts]


def session_detail(db: Session, user_id: str, session: WrittenPracticeSession) -> Dict[str, Any]:
    attempts = (
        db.query(WrittenAnswerAttempt)
        .filter(WrittenAnswerAttempt.session_id == session.id)
        .order_by(WrittenAnswerAttempt.id.asc())
        .all()
    )
    feedback_by_attempt = _feedback_map(db, [a.id for a in attempts])
    return {
        "session": serialize_session(session, attempt_count=len(attempts)),
        "attempts": [serialize_attempt_summary(a, feedback_by_attempt.get(a.id)) for a in attempts],
    }


def _feedback_map(db: Session, attempt_ids: List[int]) -> Dict[int, WrittenAnswerFeedback]:
    if not attempt_ids:
        return {}
    rows = (
        db.query(WrittenAnswerFeedback)
        .filter(WrittenAnswerFeedback.attempt_id.in_(attempt_ids))
        .all()
    )
    return {row.attempt_id: row for row in rows}


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------
def serialize_session(session: WrittenPracticeSession, *, attempt_count: int = 0) -> Dict[str, Any]:
    return {
        "id": session.id,
        "class_level": session.class_level or "",
        "subject": session.subject or "",
        "chapter_id": session.chapter_id,
        "chapter_name": session.chapter_name or "",
        "topic": session.topic or "",
        "marks_focus": session.marks_focus or "",
        "session_status": session.session_status or "",
        "started_at": session.started_at,
        "completed_at": session.completed_at,
        "attempt_count": attempt_count,
    }


def serialize_question(attempt: WrittenAnswerAttempt) -> Dict[str, Any]:
    return {
        "attempt_id": attempt.id,
        "session_id": attempt.session_id,
        "question_text": attempt.question_text or "",
        "question_type": attempt.question_type or "",
        "marks_total": float(attempt.marks_total or 0),
        "topic": attempt.topic or "",
        "command_word": getattr(attempt, "_command_word", ""),
        "evaluation_status": attempt.evaluation_status or "",
    }


def serialize_feedback(attempt: WrittenAnswerAttempt, feedback: WrittenAnswerFeedback) -> Dict[str, Any]:
    return {
        "attempt_id": attempt.id,
        "question_text": attempt.question_text or "",
        "question_type": attempt.question_type or "",
        "student_answer": attempt.student_answer or "",
        "marks_awarded": feedback.marks_awarded or 0.0,
        "marks_total": feedback.marks_total or 0.0,
        "score_percentage": feedback.score_percentage or 0.0,
        "covered_points": list(feedback.covered_points_json or []),
        "missing_points": list(feedback.missing_points_json or []),
        "incorrect_points": list(feedback.incorrect_points_json or []),
        "weak_explanation": list(feedback.weak_explanation_json or []),
        "presentation_feedback": feedback.presentation_feedback or "",
        "teacher_feedback": feedback.teacher_feedback or "",
        "model_answer": feedback.model_answer or "",
        "improve_to_full_marks": feedback.improve_to_full_marks or "",
        "rubric_scores": dict(feedback.rubric_scores_json or {}),
        "next_question_suggestion": feedback.next_question_suggestion or "",
        "created_at": feedback.created_at,
    }


def serialize_attempt_summary(
    attempt: WrittenAnswerAttempt, feedback: Optional[WrittenAnswerFeedback]
) -> Dict[str, Any]:
    return {
        "id": attempt.id,
        "session_id": attempt.session_id,
        "question_text": attempt.question_text or "",
        "question_type": attempt.question_type or "",
        "marks_total": float(attempt.marks_total or 0),
        "marks_awarded": (feedback.marks_awarded if feedback else None),
        "score_percentage": (feedback.score_percentage if feedback else None),
        "evaluation_status": attempt.evaluation_status or "",
        "topic": attempt.topic or "",
        "subject": attempt.subject or "",
        "submitted_at": attempt.submitted_at,
        "created_at": attempt.created_at,
    }
