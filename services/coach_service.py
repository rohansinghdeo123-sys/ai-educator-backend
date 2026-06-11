"""Database access for coach conversation history."""

from __future__ import annotations

from typing import List

from sqlalchemy.orm import Session

from app.security import session_id_belongs_to_user
from app.serializers import interaction_session_id
from models import AICoachInteraction, AICoachProfile


def conversation_rows_for_user(
    db: Session,
    coach: AICoachProfile,
    user_id: str,
    limit: int = 400,
) -> List[AICoachInteraction]:
    rows = (
        db.query(AICoachInteraction)
        .filter(AICoachInteraction.coach_id == coach.coach_id)
        .filter(AICoachInteraction.user_id == user_id)
        .order_by(AICoachInteraction.id.desc())
        .limit(limit)
        .all()
    )
    return [
        row for row in rows
        if interaction_session_id(row)
        and session_id_belongs_to_user(interaction_session_id(row), user_id)
    ]
