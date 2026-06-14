"""Study endpoints: section AI, MCQ/probable generation, and artifacts."""

from __future__ import annotations

import re
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.request_models import (
    ArtifactGenerateRequest,
    GenerateMCQRequest,
    GenerateProbableRequest,
    SectionAIRequest,
)
from app.security import (
    enforce_user_quota,
    require_authenticated_user_id,
    require_owned_study_session,
    verify_firebase_user,
)
from app.serializers import normalize_topic
from database import get_db
from Logic.section_doubt import (
    generate_structured_mcqs,
    generate_structured_probable_questions,
    section_doubt,
)
from Logic.tools.artifact_generator import (
    ARTIFACT_DATA_NOT_AVAILABLE,
    available_artifact_sections,
    generate_study_artifacts,
)
from services.profile_service import profile_learning_context

router = APIRouter(tags=["study"])


@router.post("/section-ai")
def section_ai(
    request: SectionAIRequest,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    user_id = require_owned_study_session(request.session_id, current_user)
    enforce_user_quota(user_id, "coach")
    learner_profile = profile_learning_context(db, user_id)
    section_id = normalize_topic(request.section_id)
    answer = section_doubt(
        question=request.question,
        section_id=section_id,
        session_id=request.session_id,
        mode=request.mode,
        difficulty=request.difficulty,
        strict_grounding=request.strict_grounding or request.retrieval_required,
        required_not_found_response=request.required_not_found_response,
        class_level=learner_profile.get("class_level", ""),
    )
    return {"answer": answer}


@router.post("/generate-mcqs")
def generate_mcqs(
    request: GenerateMCQRequest,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    user_id = require_owned_study_session(request.session_id, current_user)
    enforce_user_quota(user_id, "exam")
    learner_profile = profile_learning_context(db, user_id)
    section_id = normalize_topic(request.section_id or request.topic)

    return generate_structured_mcqs(
        topic=request.topic,
        section_id=section_id,
        session_id=request.session_id,
        difficulty=request.difficulty,
        count=request.count,
        strict_grounding=request.strict_grounding or request.retrieval_required,
        required_not_found_response=request.required_not_found_response,
        include_source=request.include_source,
        class_level=learner_profile.get("class_level", ""),
    )


@router.post("/generate-probable-questions")
def generate_probable_questions(
    request: GenerateProbableRequest,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    user_id = require_owned_study_session(request.session_id, current_user)
    enforce_user_quota(user_id, "exam")
    learner_profile = profile_learning_context(db, user_id)
    section_id = normalize_topic(request.section_id or request.topic)

    return generate_structured_probable_questions(
        topic=request.topic,
        section_id=section_id,
        session_id=request.session_id,
        difficulty=request.difficulty,
        strict_grounding=request.strict_grounding or request.retrieval_required,
        required_not_found_response=request.required_not_found_response,
        include_source=request.include_source,
        class_level=learner_profile.get("class_level", ""),
    )


@router.post("/artifacts/generate")
def generate_artifacts(
    request: ArtifactGenerateRequest,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    user_id = require_authenticated_user_id(current_user)
    enforce_user_quota(user_id, "artifact")
    learner_profile = profile_learning_context(db, user_id)
    section_id = re.sub(
        r"[^a-z0-9]+",
        "_",
        (request.section_id or request.topic or "").strip().lower(),
    ).strip("_")
    try:
        result = generate_study_artifacts(
            section_id=section_id,
            topic=request.topic,
            subject=request.subject,
            chapter=request.chapter,
        )
        if isinstance(result, dict):
            result["class_level"] = learner_profile.get("class_level", "")
        return result
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ARTIFACT_DATA_NOT_AVAILABLE,
        ) from exc


@router.get("/artifacts/catalog")
def artifact_catalog():
    return {
        "subject": "Chemistry",
        "available_sections": available_artifact_sections(),
        "message": "Artifacts are generated only from ingested platform study data.",
    }
