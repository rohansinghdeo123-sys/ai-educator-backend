"""Weekly Rival Challenge endpoints for the Dashboard."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.security import require_same_user_or_admin, verify_firebase_user
from database import get_db
from services.rival_service import build_weekly_challenge

router = APIRouter(tags=["rivals"])


@router.get("/rivals/weekly-challenge/{user_id}")
def weekly_challenge(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    """The full living battle payload: rival, weekly progress, countdown,
    missions, rival activity, battle status, and rewards.

    Reading is also the lifecycle driver: the first request of a new week
    resolves last week's battle (awarding XP once) and generates the fresh
    performance-based pairings.
    """
    require_same_user_or_admin(user_id, current_user)
    return build_weekly_challenge(db, user_id)
