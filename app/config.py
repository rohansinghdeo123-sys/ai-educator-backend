"""Environment-backed configuration for the AI Educator backend.

All values are read once at import time. `.env` is loaded by ``main`` before this
module is imported, so ``os.getenv`` here observes the configured environment.
"""

from __future__ import annotations

import os
from typing import Dict, List


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def env_list(name: str, default: List[str]) -> List[str]:
    raw = os.getenv(name)
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def parse_csv_env(name: str) -> set[str]:
    return {
        item.strip().lower()
        for item in os.getenv(name, "").split(",")
        if item.strip()
    }


# ================= CORS =================
ALLOWED_ORIGINS = env_list(
    "ALLOWED_ORIGINS",
    [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://agentifyai.in",
        "https://www.agentifyai.in",
    ],
)

# ================= RATE LIMITS & QUOTAS =================
RATE_LIMIT_ENABLED = env_bool("RATE_LIMIT_ENABLED", True)
RATE_LIMIT_PER_MINUTE = env_int("RATE_LIMIT_PER_MINUTE", 120)
AI_RATE_LIMIT_PER_MINUTE = env_int("AI_RATE_LIMIT_PER_MINUTE", 24)
ADMIN_RATE_LIMIT_PER_MINUTE = env_int("ADMIN_RATE_LIMIT_PER_MINUTE", 180)
AI_DAILY_QUOTA_PER_USER = env_int("AI_DAILY_QUOTA_PER_USER", 180)
EXAM_DAILY_QUOTA_PER_USER = env_int("EXAM_DAILY_QUOTA_PER_USER", 80)
ARTIFACT_DAILY_QUOTA_PER_USER = env_int("ARTIFACT_DAILY_QUOTA_PER_USER", 40)

AI_RATE_LIMIT_PATHS = {
    "/section-ai",
    "/generate-mcqs",
    "/generate-probable-questions",
    "/artifacts/generate",
    "/agent",
    "/coach/chat",
    "/coach/chat/stream",
}

QUOTA_LIMITS: Dict[str, int] = {
    "coach": AI_DAILY_QUOTA_PER_USER,
    "exam": EXAM_DAILY_QUOTA_PER_USER,
    "artifact": ARTIFACT_DAILY_QUOTA_PER_USER,
    "agent": AI_DAILY_QUOTA_PER_USER,
}

# ================= ADMIN ALLOWLISTS =================
BACKEND_ADMIN_EMAILS = parse_csv_env("BACKEND_ADMIN_EMAILS")
BACKEND_ADMIN_UIDS = parse_csv_env("BACKEND_ADMIN_UIDS")
BACKEND_ADMIN_PHONES = parse_csv_env("BACKEND_ADMIN_PHONES")
BACKEND_FOUNDER_ADMIN_EMAILS = parse_csv_env("BACKEND_FOUNDER_ADMIN_EMAILS") or BACKEND_ADMIN_EMAILS
