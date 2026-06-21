"""Written-answer practice and student-weakness endpoints.

All routes require authentication and are scoped to the caller; another user's
session, attempt, or feedback is reported as 404.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.exam_schemas import (
    WeaknessByTopicResponse,
    WeaknessReportResponse,
    WrittenFeedbackOut,
    WrittenHistoryResponse,
    WrittenQuestionOut,
    WrittenQuestionRequest,
    WrittenSessionDetailResponse,
    WrittenSessionOut,
    WrittenStartRequest,
    WrittenSubmitRequest,
    WrittenSubmitResponse,
)
from app.security import (
    enforce_user_quota,
    require_authenticated_user_id,
    verify_firebase_user,
)
from database import get_db
from services import exam_weakness_service as weakness_service
from services import written_practice_service as practice_service

logger = logging.getLogger("ai_educator.routers.written_practice")

router = APIRouter(tags=["exam-intelligence"])

SESSION_NOT_FOUND = "Practice session not found."
ATTEMPT_NOT_FOUND = "Attempt not found."


# =========================================================
# WRITTEN PRACTICE
# =========================================================
@router.post("/exam/written-practice/start", response_model=WrittenSessionOut)
def start_practice(
    payload: WrittenStartRequest,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    user_id = require_authenticated_user_id(current_user)
    session = practice_service.start_session(
        db,
        user_id,
        class_level=payload.class_level or "",
        subject=payload.subject or "",
        chapter_name=payload.chapter_name or "",
        chapter_id=payload.chapter_id,
        topic=payload.topic or "",
        marks_focus=payload.marks_focus or "",
    )
    return practice_service.serialize_session(session, attempt_count=0)


@router.post("/exam/written-practice/question", response_model=WrittenQuestionOut)
def generate_practice_question(
    payload: WrittenQuestionRequest,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    user_id = require_authenticated_user_id(current_user)
    enforce_user_quota(user_id, "written_practice")
    session = practice_service.get_owned_session(db, user_id, payload.session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=SESSION_NOT_FOUND)
    attempt = practice_service.generate_question(
        db,
        user_id,
        session,
        topic=payload.topic,
        marks_focus=payload.marks_focus,
        question_type=payload.question_type,
        use_syllabus_grounding=payload.use_syllabus_grounding,
    )
    return practice_service.serialize_question(attempt)


@router.post("/exam/written-practice/submit", response_model=WrittenSubmitResponse)
def submit_practice_answer(
    payload: WrittenSubmitRequest,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    user_id = require_authenticated_user_id(current_user)
    enforce_user_quota(user_id, "written_practice")
    try:
        attempt, feedback, weaknesses_updated = practice_service.submit_answer(
            db,
            user_id,
            answer=payload.answer,
            attempt_id=payload.attempt_id,
            session_id=payload.session_id,
            question_text=payload.question_text,
            question_type=payload.question_type,
            marks_total=payload.marks_total,
            topic=payload.topic,
            expected_points=payload.expected_points,
            use_syllabus_grounding=payload.use_syllabus_grounding,
        )
    except practice_service.WrittenPracticeError as exc:
        # "not found" maps to 404; everything else is a bad request.
        message = str(exc)
        if "not found" in message.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message) from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message) from exc

    return {
        "attempt_id": attempt.id,
        "feedback": practice_service.serialize_feedback(attempt, feedback),
        "weaknesses_updated": weaknesses_updated,
    }


@router.get("/exam/written-practice/history", response_model=WrittenHistoryResponse)
def written_history(
    subject: Optional[str] = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    user_id = require_authenticated_user_id(current_user)
    total, attempts = practice_service.list_history(db, user_id, subject=subject, limit=limit, offset=offset)
    return {"total": total, "attempts": attempts}


@router.get("/exam/written-practice/sessions/{session_id}", response_model=WrittenSessionDetailResponse)
def written_session_detail(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    user_id = require_authenticated_user_id(current_user)
    session = practice_service.get_owned_session(db, user_id, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=SESSION_NOT_FOUND)
    return practice_service.session_detail(db, user_id, session)


@router.get("/exam/written-practice/attempts/{attempt_id}/feedback", response_model=WrittenFeedbackOut)
def written_attempt_feedback(
    attempt_id: int,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    user_id = require_authenticated_user_id(current_user)
    attempt = practice_service.get_owned_attempt(db, user_id, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ATTEMPT_NOT_FOUND)
    feedback = practice_service.get_attempt_feedback(db, attempt)
    if feedback is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This attempt has not been evaluated yet.",
        )
    return practice_service.serialize_feedback(attempt, feedback)


# =========================================================
# STUDENT WEAKNESS REPORT
# =========================================================
@router.get("/exam/student-weakness-report", response_model=WeaknessReportResponse)
def student_weakness_report(
    subject: Optional[str] = Query(default=None, max_length=120),
    limit: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    user_id = require_authenticated_user_id(current_user)
    return weakness_service.weakness_report(db, user_id, subject=subject, limit=limit, offset=offset)


@router.get("/exam/student-weakness-report/by-topic", response_model=WeaknessByTopicResponse)
def student_weakness_by_topic(
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    user_id = require_authenticated_user_id(current_user)
    return weakness_service.weakness_by_topic(db, user_id)


@router.post("/exam/student-weakness-report/recalculate")
def recalculate_weakness_report(
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    user_id = require_authenticated_user_id(current_user)
    return weakness_service.recalculate(db, user_id)
