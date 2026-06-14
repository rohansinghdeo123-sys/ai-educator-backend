"""Canonical backend-backed user profile helpers."""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import AICoachProfile, UserProfile, UserProgress


def token_email(decoded_token: Dict[str, Any]) -> str:
    return str(decoded_token.get("email") or "").strip().lower()


def token_display_name(decoded_token: Dict[str, Any]) -> str:
    return " ".join(str(decoded_token.get("name") or "").split())[:80]


def get_user_profile(db: Session, user_id: str) -> Optional[UserProfile]:
    return db.query(UserProfile).filter(UserProfile.user_id == user_id).first()


def get_or_create_user_profile(
    db: Session,
    user_id: str,
    decoded_token: Optional[Dict[str, Any]] = None,
) -> UserProfile:
    profile = get_user_profile(db, user_id)
    email = token_email(decoded_token or {})
    changed = False

    if not profile:
        profile = UserProfile(
            user_id=user_id,
            email=email,
            display_name=token_display_name(decoded_token or {}),
            class_level="",
            onboarding_completed=False,
        )
        db.add(profile)
        changed = True
    elif email and profile.email != email:
        profile.email = email
        changed = True

    progress = (
        db.query(UserProgress).filter(UserProgress.user_id == user_id).first()
    )
    if not progress:
        db.add(UserProgress(user_id=user_id))
        changed = True

    if changed:
        try:
            db.commit()
            db.refresh(profile)
        except IntegrityError:
            db.rollback()
            profile = get_user_profile(db, user_id)
            if profile is None:
                raise

    return profile


def update_user_profile(
    db: Session,
    profile: UserProfile,
    *,
    display_name: Optional[str] = None,
    class_level: Optional[str] = None,
    onboarding_completed: Optional[bool] = None,
) -> UserProfile:
    if display_name is not None:
        profile.display_name = display_name
        coach = (
            db.query(AICoachProfile)
            .filter(AICoachProfile.user_id == profile.user_id)
            .first()
        )
        if coach:
            coach.student_display_name = display_name
    if class_level is not None:
        profile.class_level = class_level
    if onboarding_completed is not None:
        profile.onboarding_completed = onboarding_completed

    db.commit()
    db.refresh(profile)
    return profile


def profile_learning_context(db: Session, user_id: str) -> Dict[str, str]:
    profile = get_user_profile(db, user_id)
    if not profile:
        return {}
    return {
        "display_name": profile.display_name or "",
        "class_level": profile.class_level or "",
    }


def privacy_safe_display_name(value: str) -> str:
    parts = [part for part in " ".join((value or "").split()).split(" ") if part]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0].upper()}."
