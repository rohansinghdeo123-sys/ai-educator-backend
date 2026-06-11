"""Generic agent dispatch and conversation reset endpoints."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.request_models import ResetRequest, SectionAIRequest
from app.security import (
    enforce_user_quota,
    is_backend_admin,
    require_authenticated_user_id,
    require_owned_study_session,
    require_same_user_or_admin,
    session_id_belongs_to_user,
    verify_firebase_user,
)
from database import get_db
from Logic.agent_router import route_to_agent
from Logic.section_doubt import reset_conversation

router = APIRouter(tags=["agent"])


@router.post("/agent")
def agent_endpoint(
    request: SectionAIRequest,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    user_id = require_owned_study_session(request.session_id, current_user)
    enforce_user_quota(user_id, "agent")
    return route_to_agent(request, db=db)


@router.post("/reset-chat")
def reset_chat(
    request: ResetRequest,
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    token_uid = require_authenticated_user_id(current_user)
    target_uid = request.user_id or token_uid
    if request.user_id:
        require_same_user_or_admin(request.user_id, current_user)

    if not is_backend_admin(current_user) and not session_id_belongs_to_user(request.session_id, target_uid):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Session reset is allowed only for your own learning session.",
        )

    reset_conversation(request.session_id)
    return {"status": "cleared", "message": "Agent memory reset successfully"}
