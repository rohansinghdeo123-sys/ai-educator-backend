"""Authenticated account profile and onboarding endpoints."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.security import require_authenticated_user_id, verify_firebase_user
from database import get_db
from schemas import UserProfileResponse, UserProfileUpdate
from services.profile_service import get_or_create_user_profile, update_user_profile

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("/me", response_model=UserProfileResponse)
def profile_me(
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    user_id = require_authenticated_user_id(current_user)
    return get_or_create_user_profile(db, user_id, current_user)


@router.patch("/me", response_model=UserProfileResponse)
def update_profile_me(
    payload: UserProfileUpdate,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    user_id = require_authenticated_user_id(current_user)
    profile = get_or_create_user_profile(db, user_id, current_user)
    fields = payload.model_fields_set
    final_display_name = (
        payload.display_name if "display_name" in fields else profile.display_name
    )
    if payload.onboarding_completed is True and not str(final_display_name or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A valid display name is required before onboarding can be completed.",
        )
    return update_user_profile(
        db,
        profile,
        display_name=payload.display_name if "display_name" in fields else None,
        class_level=payload.class_level if "class_level" in fields else None,
        onboarding_completed=(
            payload.onboarding_completed if "onboarding_completed" in fields else None
        ),
    )
