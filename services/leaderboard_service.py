"""Leaderboard assembly from onboarded student profiles and their progress.

Names come only from the student's onboarding profile (the name entered at
signup) — the Firebase login name is never used. Only students with real
activity are ranked, the board is capped at the top N, and each row carries the
student's position within their own class alongside the overall rank.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app import security
from models import UserProfile, UserProgress

LEADERBOARD_SIZE = 20


def _anonymous_name(user_id: str) -> str:
    """Neutral fallback when a student has no profile name yet (never Firebase)."""
    public_suffix = sha256(user_id.encode("utf-8")).hexdigest()[:6].upper()
    return f"Student {public_suffix}"


def _has_activity(progress: UserProgress) -> bool:
    return bool(
        (progress.xp or 0) > 0
        or (progress.streak or 0) > 0
        or (progress.total_tests or 0) > 0
    )


def build_leaderboard(
    db: Session,
    decoded_token: Optional[Dict[str, Any]] = None,
    limit: int = LEADERBOARD_SIZE,
):
    token_uid = str((decoded_token or {}).get("uid", "")).strip()
    admin_view = bool(decoded_token and security.is_backend_admin(decoded_token))

    profiles_by_user = {
        row.user_id: row
        for row in db.query(UserProfile).all()
        if row.user_id
    }

    rows = []
    for progress in db.query(UserProgress).all():
        user_id = progress.user_id
        if not user_id or not _has_activity(progress):
            continue

        account_profile = profiles_by_user.get(user_id)
        display_name = str(
            (account_profile.display_name if account_profile else "") or ""
        ).strip()
        if not display_name:
            display_name = _anonymous_name(user_id)

        # Emails are private: only the student themselves or an admin sees them.
        email = None
        if admin_view or user_id == token_uid:
            email = (account_profile.email if account_profile else "") or None
            if not email and user_id == token_uid:
                email = str((decoded_token or {}).get("email") or "").strip() or None

        rows.append(
            {
                "user_id": user_id,
                "display_name": display_name,
                "email": email,
                "class_level": account_profile.class_level if account_profile else "",
                "xp": int(progress.xp or 0),
                "streak": int(progress.streak or 0),
                "total_tests": int(progress.total_tests or 0),
            }
        )

    rows.sort(
        key=lambda row: (
            -row["xp"],
            -row["streak"],
            -row["total_tests"],
            row["display_name"].casefold(),
            row["user_id"],
        )
    )

    # Per-class position is computed over the full ranked field; the overall
    # board is then capped to the top N.
    class_positions: Dict[str, int] = {}
    ranked_rows = []
    for rank, row in enumerate(rows, start=1):
        class_level = str(row.get("class_level") or "")
        class_rank = None
        if class_level:
            class_positions[class_level] = class_positions.get(class_level, 0) + 1
            class_rank = class_positions[class_level]
        ranked_rows.append({"rank": rank, "class_rank": class_rank, **row})

    if limit and limit > 0:
        ranked_rows = ranked_rows[:limit]
    return ranked_rows
