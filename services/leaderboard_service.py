"""Leaderboard assembly with optional Firebase display-name enrichment."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app import security
from models import UserProgress

logger = logging.getLogger("ai_educator.services.leaderboard")


def build_leaderboard(
    db: Session,
    decoded_token: Optional[Dict[str, Any]] = None,
):
    token_uid = str((decoded_token or {}).get("uid", "")).strip()
    admin_view = bool(decoded_token and security.is_backend_admin(decoded_token))

    users = (
        db.query(UserProgress)
        .order_by(UserProgress.xp.desc())
        .limit(10)
        .all()
    )

    leaderboard_data = []

    # Batch fetch Firebase user info if possible
    firebase_users = {}
    if security.firebase_ready() and security.firebase_auth:
        uids = [user.user_id for user in users]
        try:
            # firebase_admin.auth.get_users (plural) fetches up to 100 UIDs in one call
            auth_result = security.firebase_auth.get_users(
                [security.firebase_auth.UidIdentifier(uid) for uid in uids]
            )
            for firebase_user in auth_result.users:
                firebase_users[firebase_user.uid] = firebase_user
        except Exception:
            logger.warning("Failed to batch fetch Firebase users for leaderboard")

    for rank, user in enumerate(users, start=1):
        fb_user = firebase_users.get(user.user_id)
        display_name = None
        email = None
        if fb_user:
            display_name = fb_user.display_name or None
            if admin_view or user.user_id == token_uid:
                email = fb_user.email or None

        leaderboard_data.append(
            {
                "rank": rank,
                "user_id": user.user_id,
                "display_name": display_name,
                "email": email,
                "xp": int(user.xp or 0),
                "streak": int(user.streak or 0),
                "total_tests": int(user.total_tests or 0),
            }
        )

    return leaderboard_data
