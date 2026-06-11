"""Leaderboard assembly with optional Firebase display-name enrichment."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app import security
from models import UserProgress
from services.ttl_cache import TTLCache

logger = logging.getLogger("ai_educator.services.leaderboard")

FIREBASE_LOOKUP_TTL_SECONDS = 60.0
_firebase_lookup_cache = TTLCache(max_entries=32)


def _fetch_firebase_profiles(uids: Sequence[str]) -> Dict[str, Dict[str, Optional[str]]]:
    """uid -> {display_name, email} for the given users, via one batch call."""
    profiles: Dict[str, Dict[str, Optional[str]]] = {}
    try:
        # firebase_admin.auth.get_users (plural) fetches up to 100 UIDs in one call
        auth_result = security.firebase_auth.get_users(
            [security.firebase_auth.UidIdentifier(uid) for uid in uids]
        )
        for firebase_user in auth_result.users:
            profiles[firebase_user.uid] = {
                "display_name": firebase_user.display_name or None,
                "email": firebase_user.email or None,
            }
    except Exception:
        logger.warning("Failed to batch fetch Firebase users for leaderboard")
    return profiles


def _cached_firebase_profiles(uids: Tuple[str, ...]) -> Dict[str, Dict[str, Optional[str]]]:
    return _firebase_lookup_cache.get_or_build(
        uids,
        FIREBASE_LOOKUP_TTL_SECONDS,
        lambda: _fetch_firebase_profiles(uids),
    )


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

    # The Firebase round trip is the expensive part of this endpoint, and the
    # top-10 changes slowly, so the lookup is cached briefly. Email visibility
    # is decided per request below, so the cache never widens what a caller
    # is allowed to see.
    firebase_users: Dict[str, Dict[str, Optional[str]]] = {}
    if users and security.firebase_ready() and security.firebase_auth:
        firebase_users = _cached_firebase_profiles(tuple(user.user_id for user in users))

    for rank, user in enumerate(users, start=1):
        fb_user = firebase_users.get(user.user_id)
        display_name = None
        email = None
        if fb_user:
            display_name = fb_user["display_name"]
            if admin_view or user.user_id == token_uid:
                email = fb_user["email"]

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
