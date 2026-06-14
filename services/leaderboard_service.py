"""Leaderboard assembly across the complete active Firebase student directory."""

from __future__ import annotations

import logging
from hashlib import sha256
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app import security
from models import UserProfile, UserProgress
from services.profile_service import privacy_safe_display_name
from services.ttl_cache import TTLCache

logger = logging.getLogger("ai_educator.services.leaderboard")

FIREBASE_DIRECTORY_TTL_SECONDS = 30.0
FIREBASE_DIRECTORY_CACHE_KEY = "active-firebase-users"
_firebase_lookup_cache = TTLCache(max_entries=4)


def _fetch_firebase_profiles() -> Tuple[bool, Dict[str, Dict[str, Optional[str]]]]:
    """Return every active Firebase profile, following all directory pages."""
    profiles: Dict[str, Dict[str, Optional[str]]] = {}
    page_token: Optional[str] = None

    try:
        while True:
            page = security.firebase_auth.list_users(
                page_token=page_token,
                max_results=1000,
            )
            for firebase_user in page.users:
                if getattr(firebase_user, "disabled", False):
                    continue
                profiles[firebase_user.uid] = {
                    "display_name": firebase_user.display_name or None,
                    "email": firebase_user.email or None,
                }

            page_token = getattr(page, "next_page_token", None)
            if not page_token:
                break
    except Exception:
        logger.warning("Failed to list Firebase users for leaderboard", exc_info=True)
        return False, {}

    return True, profiles


def _cached_firebase_profiles() -> Tuple[bool, Dict[str, Dict[str, Optional[str]]]]:
    return _firebase_lookup_cache.get_or_build(
        FIREBASE_DIRECTORY_CACHE_KEY,
        FIREBASE_DIRECTORY_TTL_SECONDS,
        _fetch_firebase_profiles,
    )


def _public_display_name(
    user_id: str,
    account_profile: Optional[UserProfile],
    firebase_profile: Optional[Dict[str, Optional[str]]],
    decoded_token: Optional[Dict[str, Any]],
    admin_view: bool,
) -> str:
    display_name = str(
        (account_profile.display_name if account_profile else "")
        or (firebase_profile or {}).get("display_name")
        or ""
    ).strip()
    token_uid = str((decoded_token or {}).get("uid") or "").strip()
    if not display_name and user_id == token_uid:
        display_name = str((decoded_token or {}).get("name") or "").strip()
    if display_name:
        return display_name if admin_view else privacy_safe_display_name(display_name)

    public_suffix = sha256(user_id.encode("utf-8")).hexdigest()[:6].upper()
    return f"Student {public_suffix}"


def build_leaderboard(
    db: Session,
    decoded_token: Optional[Dict[str, Any]] = None,
):
    token_uid = str((decoded_token or {}).get("uid", "")).strip()
    admin_view = bool(decoded_token and security.is_backend_admin(decoded_token))

    progress_by_user = {
        row.user_id: row
        for row in db.query(UserProgress).all()
        if row.user_id
    }
    profiles_by_user = {
        row.user_id: row
        for row in db.query(UserProfile).all()
        if row.user_id
    }
    directory_available = False
    firebase_users: Dict[str, Dict[str, Optional[str]]] = {}
    if security.firebase_ready() and security.firebase_auth:
        directory_available, firebase_users = _cached_firebase_profiles()

    # A successful Firebase directory read is authoritative and excludes
    # deleted/disabled accounts. If it is temporarily unavailable, retain a
    # complete leaderboard from every progress row instead of returning none.
    user_ids = set(firebase_users if directory_available else progress_by_user)
    if not directory_available:
        user_ids.update(profiles_by_user)
    if token_uid:
        user_ids.add(token_uid)

    rows = []
    for user_id in user_ids:
        progress = progress_by_user.get(user_id)
        firebase_profile = firebase_users.get(user_id)
        account_profile = profiles_by_user.get(user_id)
        email = None
        if admin_view or user_id == token_uid:
            email = (account_profile.email if account_profile else "") or (firebase_profile or {}).get("email")
            if not email and user_id == token_uid:
                email = str((decoded_token or {}).get("email") or "").strip() or None

        rows.append(
            {
                "user_id": user_id,
                "display_name": _public_display_name(
                    user_id,
                    account_profile,
                    firebase_profile,
                    decoded_token,
                    admin_view,
                ),
                "email": email,
                "class_level": account_profile.class_level if account_profile else "",
                "xp": int(progress.xp or 0) if progress else 0,
                "streak": int(progress.streak or 0) if progress else 0,
                "total_tests": int(progress.total_tests or 0) if progress else 0,
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

    class_positions: Dict[str, int] = {}
    ranked_rows = []
    for rank, row in enumerate(rows, start=1):
        class_level = str(row.get("class_level") or "")
        class_rank = None
        if class_level:
            class_positions[class_level] = class_positions.get(class_level, 0) + 1
            class_rank = class_positions[class_level]
        ranked_rows.append({"rank": rank, "class_rank": class_rank, **row})
    return ranked_rows
