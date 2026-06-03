# ================= SAFE IMPORT FIX =================
import os
import sys
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# ================= IMPORTS =================
import json
import logging
import re
import time
import uuid
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from hashlib import sha256
from threading import Lock
from typing import Any, Dict, List, Optional, Generator

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from database import SessionLocal, check_db_health, engine
from models import (
    AICoachDailySignal,
    AICoachMemory,
    AICoachProfile,
    Base,
    SessionDetail,
    TestHistory,
    UserProgress,
)
from schemas import (
    AutonomousStudyRequest,
    AutonomousStudyResponse,
    CoachBootstrapRequest,
    CoachChatRequest,
    CoachDailySignalResponse,
    CoachDashboardResponse,
    CoachMemoryResponse,
    CoachProfileResponse,
    ProgressResponse,
    ProgressUpdate,
    TestHistoryCreate,
    TestHistoryResponse,
)

from Logic.agent_event_bus import event_bus
from Logic.agent_router import get_agent_registry, route_to_agent
from Logic.analytics_engine import get_user_analytics, update_topic_performance
from Logic.autonomous_study_loop import run_autonomous_study_loop
from Logic.observability_store import (
    get_latest_observability_version,
    get_observability_events_since,
    get_observability_summary,
    get_recent_observability_events,
    persist_event_from_bus,
)
from Logic.agents.coach_agent import (
    get_or_create_coach,
    run_daily_learning_cycle,
    coach_agent,
    coach_agent_stream,          # <-- NEW
)
from Logic.section_doubt import (
    generate_structured_mcqs,
    generate_structured_probable_questions,
    reset_conversation,
    section_doubt,
)
from Logic.knowledge_graph import knowledge_graph
from Logic.tools.artifact_generator import (
    ARTIFACT_DATA_NOT_AVAILABLE,
    available_artifact_sections,
    generate_study_artifacts,
)

# ── Groq client for casual CEO chats ──
import groq

# ── Generic LLM chat (Groq low-latency fallback) ─────────
def generic_llm_chat(system_prompt: str, user_message: str, agent_id: str = "unknown") -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        logger.error("GROQ_API_KEY not set – cannot use casual chat.")
        return "Casual chat is not configured on the server. Please set GROQ_API_KEY."

    try:
        client = groq.Client(api_key=api_key)
        response = client.chat.completions.create(
            model=os.getenv(
                "GROQ_CASUAL_MODEL",
                os.getenv("GROQ_FAST_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
            ),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.9,
            max_tokens=400,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.exception("Groq LLM call failed")
        return f"I'm having trouble responding right now. ({agent_id})"


try:
    import firebase_admin
    from firebase_admin import auth as firebase_auth
    from firebase_admin import credentials
except Exception as firebase_import_error:
    firebase_admin = None
    firebase_auth = None
    credentials = None
    FIREBASE_IMPORT_ERROR = firebase_import_error
else:
    FIREBASE_IMPORT_ERROR = None


# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(message)s",
)

logger = logging.getLogger("ai_educator.main")


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


ALLOWED_ORIGINS = env_list(
    "ALLOWED_ORIGINS",
    [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://agentifyai.in",
        "https://www.agentifyai.in",
    ],
)
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

QUOTA_LIMITS = {
    "coach": AI_DAILY_QUOTA_PER_USER,
    "exam": EXAM_DAILY_QUOTA_PER_USER,
    "artifact": ARTIFACT_DAILY_QUOTA_PER_USER,
    "agent": AI_DAILY_QUOTA_PER_USER,
}


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._events: Dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        if limit <= 0:
            return True, 0
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._events[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, int(window_seconds - (now - bucket[0])))
                return False, retry_after
            bucket.append(now)
            return True, 0


class DailyQuotaStore:
    def __init__(self) -> None:
        self._counts: Dict[str, int] = defaultdict(int)
        self._lock = Lock()

    def consume(self, user_id: str, quota_name: str, limit: int) -> tuple[bool, int]:
        if limit <= 0:
            return True, 0
        day = date.today().isoformat()
        safe_user = sha256(str(user_id).encode("utf-8")).hexdigest()[:16]
        key = f"{day}:{quota_name}:{safe_user}"
        with self._lock:
            current = self._counts[key]
            if current >= limit:
                return False, current
            self._counts[key] = current + 1
            return True, self._counts[key]


rate_limiter = SlidingWindowLimiter()
daily_quotas = DailyQuotaStore()

# ================= CREATE TABLES =================
Base.metadata.create_all(bind=engine)


def ensure_session_telemetry_columns() -> None:
    """Backfill telemetry columns until the production Alembic pass lands."""
    ddl_by_column = {
        "started_at": "TIMESTAMP",
        "completed_at": "TIMESTAMP",
        "response_latency_ms": "INTEGER DEFAULT 0",
        "hint_count": "INTEGER DEFAULT 0",
        "retry_count": "INTEGER DEFAULT 0",
        "confidence_before": "FLOAT",
        "confidence_after": "FLOAT",
    }
    try:
        inspector = inspect(engine)
        if "test_history" not in inspector.get_table_names():
            return
        existing = {column["name"] for column in inspector.get_columns("test_history")}
        missing = [(name, ddl) for name, ddl in ddl_by_column.items() if name not in existing]
        if not missing:
            return
        with engine.begin() as conn:
            for name, ddl in missing:
                conn.execute(text(f"ALTER TABLE test_history ADD COLUMN {name} {ddl}"))
        logger.info("DATABASE: Added session telemetry columns: %s", ", ".join(name for name, _ in missing))
    except Exception as exc:
        logger.warning("DATABASE: Session telemetry column check skipped: %s", exc)


ensure_session_telemetry_columns()
event_bus.set_sink(persist_event_from_bus)

# ================= APP INIT =================
app = FastAPI(title="AI Educator Backend - Agentic v2.0 + Secure Admin + Coach API")

# ================= CORS =================
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Response-Time-ms", "Retry-After"],
)


def client_rate_key(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    client_host = forwarded or (request.client.host if request.client else "unknown")
    auth_header = request.headers.get("authorization") or ""
    if auth_header:
        token_hash = sha256(auth_header.encode("utf-8")).hexdigest()[:16]
        return f"auth:{token_hash}:{request.url.path}"
    return f"ip:{client_host}:{request.url.path}"


def minute_limit_for_path(path: str) -> int:
    if path.startswith("/admin"):
        return ADMIN_RATE_LIMIT_PER_MINUTE
    if path in AI_RATE_LIMIT_PATHS or path.startswith("/coach/autonomous-study"):
        return AI_RATE_LIMIT_PER_MINUTE
    return RATE_LIMIT_PER_MINUTE


@app.middleware("http")
async def production_guardrails(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = request_id

    if RATE_LIMIT_ENABLED and request.method.upper() != "OPTIONS":
        limit = minute_limit_for_path(request.url.path)
        allowed, retry_after = rate_limiter.allow(client_rate_key(request), limit, 60)
        if not allowed:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "detail": "Too many requests. Please slow down and try again.",
                    "request_id": request_id,
                },
                headers={"X-Request-ID": request_id, "Retry-After": str(retry_after)},
            )

    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Request failed | request_id=%s path=%s", request_id, request.url.path)
        raise

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time-ms"] = str(round((time.perf_counter() - started) * 1000, 2))
    return response


# ================= FIREBASE ADMIN =================
FIREBASE_ADMIN_READY = False
FIREBASE_ADMIN_ERROR: Optional[str] = None


def parse_csv_env(name: str) -> set[str]:
    return {
        item.strip().lower()
        for item in os.getenv(name, "").split(",")
        if item.strip()
    }


BACKEND_ADMIN_EMAILS = parse_csv_env("BACKEND_ADMIN_EMAILS")
BACKEND_ADMIN_UIDS = parse_csv_env("BACKEND_ADMIN_UIDS")
BACKEND_ADMIN_PHONES = parse_csv_env("BACKEND_ADMIN_PHONES")


def initialize_firebase_admin() -> None:
    global FIREBASE_ADMIN_READY, FIREBASE_ADMIN_ERROR

    if firebase_admin is None or credentials is None:
        FIREBASE_ADMIN_READY = False
        FIREBASE_ADMIN_ERROR = f"firebase_admin import failed: {FIREBASE_IMPORT_ERROR}"
        logger.warning(FIREBASE_ADMIN_ERROR)
        return

    try:
        if firebase_admin._apps:
            FIREBASE_ADMIN_READY = True
            FIREBASE_ADMIN_ERROR = None
            return

        service_account = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
        project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()
        app_options = {"projectId": project_id} if project_id else None

        if service_account:
            if service_account.startswith("{"):
                cred = credentials.Certificate(json.loads(service_account))
            else:
                cred = credentials.Certificate(service_account)

            firebase_admin.initialize_app(cred, app_options)
        elif os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            cred = credentials.Certificate(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
            firebase_admin.initialize_app(cred, app_options)
        else:
            firebase_admin.initialize_app(options=app_options)

        FIREBASE_ADMIN_READY = True
        FIREBASE_ADMIN_ERROR = None
        logger.info("Firebase Admin initialized successfully")
    except Exception as exc:
        FIREBASE_ADMIN_READY = False
        FIREBASE_ADMIN_ERROR = str(exc)
        logger.warning("Firebase Admin not initialized: %s", FIREBASE_ADMIN_ERROR)


initialize_firebase_admin()


def get_bearer_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    scheme, _, token = authorization.partition(" ")

    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header",
        )

    return token.strip()


def verify_firebase_user(
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    if not FIREBASE_ADMIN_READY or firebase_auth is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Firebase Admin is not configured on backend",
        )

    token = get_bearer_token(authorization)

    try:
        return firebase_auth.verify_id_token(token, check_revoked=True)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Firebase token",
        )


def has_admin_claim(decoded_token: Dict[str, Any]) -> bool:
    if decoded_token.get("admin") is True:
        return True

    if decoded_token.get("role") == "admin":
        return True

    roles = decoded_token.get("roles")
    return isinstance(roles, list) and "admin" in roles


def is_backend_admin(decoded_token: Dict[str, Any]) -> bool:
    if has_admin_claim(decoded_token):
        return True

    uid = str(decoded_token.get("uid", "")).lower()
    email = str(decoded_token.get("email", "")).lower()
    phone = str(decoded_token.get("phone_number", "")).lower()

    return (
        uid in BACKEND_ADMIN_UIDS
        or email in BACKEND_ADMIN_EMAILS
        or phone in BACKEND_ADMIN_PHONES
    )


def require_admin(
    decoded_token: Dict[str, Any] = Depends(verify_firebase_user),
) -> Dict[str, Any]:
    if not is_backend_admin(decoded_token):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    return decoded_token


def require_same_user_or_admin(
    user_id: str,
    decoded_token: Dict[str, Any],
) -> None:
    token_uid = str(decoded_token.get("uid", "")).strip()
    target_uid = str(user_id or "").strip()

    if token_uid and (token_uid == target_uid or is_backend_admin(decoded_token)):
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not allowed for this user",
    )


def require_authenticated_user_id(decoded_token: Dict[str, Any]) -> str:
    token_uid = str(decoded_token.get("uid", "")).strip()

    if not token_uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user id missing from token",
        )

    return token_uid


def enforce_user_quota(user_id: str, quota_name: str) -> None:
    limit = QUOTA_LIMITS.get(quota_name, AI_DAILY_QUOTA_PER_USER)
    allowed, used = daily_quotas.consume(user_id, quota_name, limit)
    if allowed:
        return
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Daily {quota_name} quota reached. Please continue after the quota resets.",
        headers={"Retry-After": "86400"},
    )


# ================= DATABASE =================
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Knowledge Graph loading (absolute path) ─────────────────────────────
import os as _os
_BASE_DIR = _os.path.dirname(_os.path.abspath(__file__))   # <-- one level up = backend/
_JSON_PATH = _os.path.join(_BASE_DIR, "data", "Chapters", "basic_concepts_of_chemistry.json")

logger.info("Loading knowledge graph from: %s", _JSON_PATH)

try:
    knowledge_graph.load_chapter(_JSON_PATH, "basic-concepts-of-chemistry")
    logger.info("Knowledge graph loaded: basic-concepts-of-chemistry (%d concepts)",
                len(knowledge_graph.concepts))
except Exception as e:
    logger.warning("Could not load basic-concepts-of-chemistry chapter: %s", e)


# ================= REQUEST MODELS =================
class SectionAIRequest(BaseModel):
    question: str
    section_id: str
    session_id: str
    mode: str = "revision"
    difficulty: str = "medium"
    subject: Optional[str] = None
    chapter: Optional[str] = None
    topic: Optional[str] = None
    system_guardrail: Optional[str] = None
    strict_grounding: bool = False
    retrieval_required: bool = False
    fallback_to_general_knowledge: bool = True
    required_not_found_response: Optional[str] = None


class ResetRequest(BaseModel):
    session_id: str


class AgentCommandRequest(BaseModel):
    agent_id: str
    command: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class AgentMessageRequest(BaseModel):
    agent_id: str
    message: str
    section_id: str = "alkanes"
    session_id: str = "admin"
    mode: Optional[str] = None
    system_message: Optional[str] = None


class GenerateMCQRequest(BaseModel):
    topic: str
    section_id: Optional[str] = None
    session_id: str = "exam-session"
    difficulty: str = "medium"
    count: int = Field(default=5, ge=1, le=10)
    subject: Optional[str] = None
    chapter: Optional[str] = None
    system_guardrail: Optional[str] = None
    strict_grounding: bool = False
    retrieval_required: bool = False
    fallback_to_general_knowledge: bool = True
    required_not_found_response: Optional[str] = None
    include_source: bool = False
    require_four_options: bool = True
    require_explanation: bool = True


class GenerateProbableRequest(BaseModel):
    topic: str
    section_id: Optional[str] = None
    session_id: str = "probable-session"
    difficulty: str = "medium"
    subject: Optional[str] = None
    chapter: Optional[str] = None
    system_guardrail: Optional[str] = None
    strict_grounding: bool = False
    retrieval_required: bool = False
    fallback_to_general_knowledge: bool = True
    required_not_found_response: Optional[str] = None
    include_source: bool = False


class ArtifactGenerateRequest(BaseModel):
    section_id: str
    topic: Optional[str] = None
    artifact_type: str = "auto"
    subject: Optional[str] = None
    chapter: Optional[str] = None
    system_guardrail: Optional[str] = None
    strict_grounding: bool = False
    retrieval_required: bool = False
    fallback_to_general_knowledge: bool = True
    required_not_found_response: Optional[str] = None


class SubmitSessionRequest(BaseModel):
    user_id: str
    topic: str
    subject: str = "Chemistry"
    score: int = Field(ge=0)
    total_questions: int = Field(gt=0)
    xp_earned: Optional[int] = None
    time_spent_seconds: int = Field(default=0, ge=0)
    focus_score: float = Field(default=0.0, ge=0, le=100)
    session_type: str = "exam"
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    response_latency_ms: int = Field(default=0, ge=0)
    hint_count: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    confidence_before: Optional[float] = Field(default=None, ge=0, le=100)
    confidence_after: Optional[float] = Field(default=None, ge=0, le=100)
    replay_data: Optional[Dict[str, Any]] = None


# ================= HELPERS =================
def normalize_topic(topic: str) -> str:
    cleaned = (topic or "unknown").strip().lower()
    cleaned = re.sub(r"[^a-z0-9]+", "_", cleaned).strip("_")
    aliases = {
        "basic_concepts_of_chemistry": "matter_definition",
        "basic_concept_of_chemistry": "matter_definition",
        "matter": "matter_definition",
        "hydrocarbon": "alkanes",
        "hydrocarbons": "alkanes",
        "aromatic_hydrocarbons": "aromatics",
    }
    return aliases.get(cleaned, cleaned)


def get_or_create_progress(db: Session, user_id: str) -> UserProgress:
    user = db.query(UserProgress).filter(UserProgress.user_id == user_id).first()

    if user:
        return user

    user = UserProgress(
        user_id=user_id,
        total_tests=0,
        total_questions=0,
        total_correct=0,
        xp=0,
        streak=0,
        last_active_date=None,
        focus_score=0.0,
        consistency_index=0.0,
        learning_efficiency=0.0,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user


def apply_streak(user: UserProgress):
    today = date.today()

    if user.last_active_date:
        difference = (today - user.last_active_date).days

        if difference == 0:
            pass
        elif difference == 1:
            user.streak += 1
        else:
            user.streak = 1
    else:
        user.streak = 1

    user.last_active_date = today


def progress_payload(user: UserProgress) -> Dict[str, Any]:
    return {
        "user_id": user.user_id,
        "total_tests": int(user.total_tests or 0),
        "total_questions": int(user.total_questions or 0),
        "total_correct": int(user.total_correct or 0),
        "xp": int(user.xp or 0),
        "streak": int(user.streak or 0),
        "level": int(user.level),
        "accuracy": round(float(user.accuracy), 1),
        "focus_score": round(float(user.focus_score or 0), 1),
        "consistency_index": round(float(user.consistency_index or 0), 1),
        "learning_efficiency": round(float(user.learning_efficiency or 0), 1),
    }


def utc_naive(value: Optional[datetime]) -> Optional[datetime]:
    if not value:
        return None
    if value.tzinfo:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def iso_or_none(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def format_test_session(test: TestHistory) -> Dict[str, Any]:
    questions = int(test.total_questions or 0)
    correct = int(test.score or 0)
    seconds = int(test.time_spent_seconds or 0)
    duration_minutes = round(seconds / 60)

    if seconds > 0 and duration_minutes == 0:
        duration_minutes = 1

    accuracy = round((correct / questions) * 100) if questions else 0
    session_date = test.date.isoformat() if test.date else None
    timestamp = (
        datetime.combine(test.date, datetime.min.time()).isoformat()
        if test.date
        else None
    )
    started_at = getattr(test, "started_at", None)
    completed_at = getattr(test, "completed_at", None)
    confidence_before = getattr(test, "confidence_before", None)
    confidence_after = getattr(test, "confidence_after", None)
    confidence_change = (
        round(float(confidence_after) - float(confidence_before), 1)
        if confidence_before is not None and confidence_after is not None
        else None
    )

    return {
        "id": str(test.id),
        "subject": "Chemistry",
        "topic": test.topic or "unknown",
        "duration": duration_minutes,
        "questions": questions,
        "correct": correct,
        "xp": int(test.xp_earned or 0),
        "focusScore": round(float(test.focus_score or 0)),
        "date": session_date,
        "timestamp": timestamp,
        "status": "completed",
        "performance": accuracy,
        "time_spent_seconds": seconds,
        "accuracy_rate": round(float(test.accuracy_rate or accuracy), 1),
        "focus_score": round(float(test.focus_score or 0), 1),
        "session_type": test.session_type or "exam",
        "started_at": iso_or_none(started_at),
        "completed_at": iso_or_none(completed_at),
        "startedAt": iso_or_none(started_at),
        "completedAt": iso_or_none(completed_at),
        "response_latency_ms": int(getattr(test, "response_latency_ms", 0) or 0),
        "responseLatencyMs": int(getattr(test, "response_latency_ms", 0) or 0),
        "hint_count": int(getattr(test, "hint_count", 0) or 0),
        "hintCount": int(getattr(test, "hint_count", 0) or 0),
        "retry_count": int(getattr(test, "retry_count", 0) or 0),
        "retryCount": int(getattr(test, "retry_count", 0) or 0),
        "confidence_before": confidence_before,
        "confidence_after": confidence_after,
        "confidenceBefore": confidence_before,
        "confidenceAfter": confidence_after,
        "confidence_change": confidence_change,
        "confidenceChange": confidence_change,
    }


def serialize_coach_profile(coach: AICoachProfile) -> Dict[str, Any]:
    return {
        "coach_id": coach.coach_id,
        "user_id": coach.user_id,
        "coach_name": coach.coach_name,
        "coach_tone": coach.coach_tone,
        "coach_style": coach.coach_style,
        "coach_status": coach.coach_status,
        "student_display_name": coach.student_display_name,
        "target_exam": coach.target_exam,
        "target_exam_date": coach.target_exam_date,
        "preferred_subjects": coach.preferred_subjects or [],
        "weak_topics_snapshot": coach.weak_topics_snapshot or [],
        "strengths_snapshot": coach.strengths_snapshot or [],
        "active_goals": coach.active_goals or [],
        "motivation_profile": coach.motivation_profile or {},
        "study_preferences": coach.study_preferences or {},
        "long_term_summary": coach.long_term_summary or "",
        "daily_strategy": coach.daily_strategy or "",
        "next_best_action": coach.next_best_action or "",
        "last_learning_cycle_at": coach.last_learning_cycle_at,
        "last_interaction_at": coach.last_interaction_at,
        "created_at": coach.created_at,
        "updated_at": coach.updated_at,
    }


def serialize_coach_memory(memory: AICoachMemory) -> Dict[str, Any]:
    return {
        "id": memory.id,
        "coach_id": memory.coach_id,
        "user_id": memory.user_id,
        "memory_type": memory.memory_type,
        "title": memory.title,
        "summary": memory.summary,
        "importance": memory.importance,
        "confidence": memory.confidence,
        "source": memory.source,
        "metadata_json": memory.metadata_json or {},
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
    }


def serialize_daily_signal(signal: Optional[AICoachDailySignal]) -> Optional[Dict[str, Any]]:
    if not signal:
        return None

    return {
        "user_id": signal.user_id,
        "coach_id": signal.coach_id,
        "signal_date": signal.signal_date,
        "sessions_count": signal.sessions_count,
        "questions_attempted": signal.questions_attempted,
        "accuracy": signal.accuracy,
        "focus_score": signal.focus_score,
        "xp_earned": signal.xp_earned,
        "weakest_topics": signal.weakest_topics or [],
        "strongest_topics": signal.strongest_topics or [],
        "recommendation": signal.recommendation,
        "risk_level": signal.risk_level,
    }


def create_test_history(
    db: Session,
    user_id: str,
    topic: str,
    score: int,
    total_questions: int,
    xp_earned: int,
    time_spent_seconds: int = 0,
    focus_score: float = 0.0,
    session_type: str = "exam",
    replay_data: Optional[Dict[str, Any]] = None,
    started_at: Optional[datetime] = None,
    completed_at: Optional[datetime] = None,
    response_latency_ms: int = 0,
    hint_count: int = 0,
    retry_count: int = 0,
    confidence_before: Optional[float] = None,
    confidence_after: Optional[float] = None,
) -> TestHistory:
    correct = max(0, min(score, total_questions))
    accuracy_rate = round((correct / total_questions) * 100, 2) if total_questions else 0.0
    started_at = utc_naive(started_at)
    completed_at = utc_naive(completed_at)
    measured_seconds = max(0, time_spent_seconds)
    if measured_seconds == 0 and started_at and completed_at:
        measured_seconds = max(0, int((completed_at - started_at).total_seconds()))

    test = TestHistory(
        user_id=user_id,
        date=date.today(),
        topic=normalize_topic(topic),
        score=correct,
        total_questions=total_questions,
        xp_earned=xp_earned,
        time_spent_seconds=measured_seconds,
        accuracy_rate=accuracy_rate,
        focus_score=max(0.0, min(100.0, focus_score)),
        session_type=session_type or "exam",
        started_at=started_at,
        completed_at=completed_at,
        response_latency_ms=max(0, int(response_latency_ms or 0)),
        hint_count=max(0, int(hint_count or 0)),
        retry_count=max(0, int(retry_count or 0)),
        confidence_before=confidence_before,
        confidence_after=confidence_after,
    )

    db.add(test)
    db.flush()

    if replay_data is not None:
        detail = SessionDetail(
            test_id=test.id,
            replay_data=replay_data,
        )
        db.add(detail)

    update_topic_performance(
        db=db,
        user_id=user_id,
        topic=normalize_topic(topic),
        correct_answers=correct,
        total_questions=total_questions,
        time_spent=measured_seconds,
    )

    db.refresh(test)
    return test


# =====================================================
# SECTION AI
# =====================================================
@app.post("/section-ai")
def section_ai(
    request: SectionAIRequest,
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    enforce_user_quota(require_authenticated_user_id(current_user), "coach")
    section_id = normalize_topic(request.section_id)
    answer = section_doubt(
        question=request.question,
        section_id=section_id,
        session_id=request.session_id,
        mode=request.mode,
        difficulty=request.difficulty,
        strict_grounding=request.strict_grounding or request.retrieval_required,
        required_not_found_response=request.required_not_found_response,
    )
    return {"answer": answer}


# =====================================================
# STRUCTURED EXAM GENERATION
# =====================================================
@app.post("/generate-mcqs")
def generate_mcqs(
    request: GenerateMCQRequest,
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    enforce_user_quota(require_authenticated_user_id(current_user), "exam")
    section_id = normalize_topic(request.section_id or request.topic)

    return generate_structured_mcqs(
        topic=request.topic,
        section_id=section_id,
        session_id=request.session_id,
        difficulty=request.difficulty,
        count=request.count,
        strict_grounding=request.strict_grounding or request.retrieval_required,
        required_not_found_response=request.required_not_found_response,
        include_source=request.include_source,
    )


@app.post("/generate-probable-questions")
def generate_probable_questions(
    request: GenerateProbableRequest,
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    enforce_user_quota(require_authenticated_user_id(current_user), "exam")
    section_id = normalize_topic(request.section_id or request.topic)

    return generate_structured_probable_questions(
        topic=request.topic,
        section_id=section_id,
        session_id=request.session_id,
        difficulty=request.difficulty,
        strict_grounding=request.strict_grounding or request.retrieval_required,
        required_not_found_response=request.required_not_found_response,
        include_source=request.include_source,
    )


@app.post("/artifacts/generate")
def generate_artifacts(
    request: ArtifactGenerateRequest,
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    enforce_user_quota(require_authenticated_user_id(current_user), "artifact")
    section_id = re.sub(
        r"[^a-z0-9]+",
        "_",
        (request.section_id or request.topic or "").strip().lower(),
    ).strip("_")
    try:
        return generate_study_artifacts(
            section_id=section_id,
            topic=request.topic,
            subject=request.subject,
            chapter=request.chapter,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ARTIFACT_DATA_NOT_AVAILABLE,
        ) from exc


# =====================================================
# AGENT ENDPOINT
# =====================================================
@app.post("/agent")
def agent_endpoint(
    request: SectionAIRequest,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    enforce_user_quota(require_authenticated_user_id(current_user), "agent")
    return route_to_agent(request, db=db)


# =====================================================
# PERSONAL AI COACH API
# =====================================================
@app.post("/coach/bootstrap", response_model=CoachProfileResponse)
def coach_bootstrap(
    payload: CoachBootstrapRequest,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    require_same_user_or_admin(payload.user_id, current_user)

    coach = get_or_create_coach(
        db=db,
        user_id=payload.user_id,
        student_display_name=payload.student_display_name,
        preferred_subjects=payload.preferred_subjects,
        target_exam=payload.target_exam,
        target_exam_date=payload.target_exam_date,
    )

    return CoachProfileResponse(**serialize_coach_profile(coach))


@app.get("/coach/{user_id}", response_model=CoachDashboardResponse)
def coach_dashboard(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    require_same_user_or_admin(user_id, current_user)

    coach = get_or_create_coach(db=db, user_id=user_id)

    memories = (
        db.query(AICoachMemory)
        .filter(AICoachMemory.coach_id == coach.coach_id)
        .order_by(AICoachMemory.importance.desc(), AICoachMemory.updated_at.desc())
        .limit(8)
        .all()
    )

    daily_signal = (
        db.query(AICoachDailySignal)
        .filter(AICoachDailySignal.coach_id == coach.coach_id)
        .order_by(AICoachDailySignal.id.desc())
        .first()
    )

    try:
        analytics_snapshot = get_user_analytics(db, user_id)
    except Exception:
        analytics_snapshot = {}

    return CoachDashboardResponse(
        profile=CoachProfileResponse(**serialize_coach_profile(coach)),
        memories=[CoachMemoryResponse(**serialize_coach_memory(memory)) for memory in memories],
        daily_signal=(
            CoachDailySignalResponse(**serialize_daily_signal(daily_signal))
            if daily_signal
            else None
        ),
        analytics_snapshot=analytics_snapshot,
    )


@app.post("/coach/chat")
def coach_chat(
    payload: CoachChatRequest,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    require_same_user_or_admin(payload.user_id, current_user)
    enforce_user_quota(payload.user_id, "coach")

    class CoachRequest:
        def __init__(self):
            self.user_id = payload.user_id
            self.question = (payload.original_message or payload.message).strip()
            self.raw_message = payload.message
            self.original_message = payload.original_message
            self.grounding_context_prompt = payload.grounding_context_prompt
            self.section_id = payload.section_id or payload.topic or payload.subject or "general"
            self.session_id = payload.session_id or f"coach-{payload.user_id}"
            self.mode = "coach"
            self.intent = payload.intent
            self.difficulty = "medium"
            self.subject = payload.subject
            self.chapter = payload.chapter
            self.topic = payload.topic
            self.mentor_directive = payload.mentor_directive
            self.system_guardrail = payload.system_guardrail
            self.strict_grounding = payload.strict_grounding
            self.retrieval_required = payload.retrieval_required
            self.fallback_to_general_knowledge = payload.fallback_to_general_knowledge
            self.required_not_found_response = payload.required_not_found_response
            self.student_state = payload.student_state
            self.adaptive_strategy = payload.adaptive_strategy
            self.learning_context = payload.learning_context
            self.attachments = [item.model_dump() for item in payload.attachments]
            self.direct_answer = payload.direct_answer
            self.socratic_mode = payload.socratic_mode

    result = coach_agent(CoachRequest(), db=db)
    return result


# ─── STREAMING ENDPOINT ────────────────────────────────────────────────────
@app.post("/coach/chat/stream")
async def coach_chat_stream(
    payload: CoachChatRequest,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    """
    Stream the coach's reply via Server‑Sent Events (SSE).
    The frontend will receive tokens one by one and display them
    as they arrive, creating a real‑time typing effect.
    """
    require_same_user_or_admin(payload.user_id, current_user)
    enforce_user_quota(payload.user_id, "coach")

    class CoachRequest:
        def __init__(self):
            self.user_id = payload.user_id
            self.question = (payload.original_message or payload.message).strip()
            self.raw_message = payload.message
            self.original_message = payload.original_message
            self.grounding_context_prompt = payload.grounding_context_prompt
            self.section_id = payload.section_id or payload.topic or payload.subject or "general"
            self.session_id = payload.session_id or f"coach-{payload.user_id}"
            self.mode = "coach"
            self.intent = payload.intent
            self.difficulty = "medium"
            self.subject = payload.subject
            self.chapter = payload.chapter
            self.topic = payload.topic
            self.mentor_directive = payload.mentor_directive
            self.system_guardrail = payload.system_guardrail
            self.strict_grounding = payload.strict_grounding
            self.retrieval_required = payload.retrieval_required
            self.fallback_to_general_knowledge = payload.fallback_to_general_knowledge
            self.required_not_found_response = payload.required_not_found_response
            self.student_state = payload.student_state
            self.adaptive_strategy = payload.adaptive_strategy
            self.learning_context = payload.learning_context
            self.attachments = [item.model_dump() for item in payload.attachments]
            self.direct_answer = payload.direct_answer
            self.socratic_mode = payload.socratic_mode

    def event_stream():
        for token in coach_agent_stream(CoachRequest(), db=db):
            # coach_agent_stream already returns complete SSE frames.
            yield token

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/coach/daily-learning/{user_id}", response_model=CoachDailySignalResponse)
def coach_daily_learning(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    require_same_user_or_admin(user_id, current_user)

    signal = run_daily_learning_cycle(db=db, user_id=user_id)
    return CoachDailySignalResponse(**serialize_daily_signal(signal))


@app.post("/coach/autonomous-study/{user_id}", response_model=AutonomousStudyResponse)
def coach_autonomous_study(
    user_id: str,
    payload: AutonomousStudyRequest,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    require_same_user_or_admin(user_id, current_user)
    enforce_user_quota(user_id, "coach")

    mission = run_autonomous_study_loop(
        db=db,
        user_id=user_id,
        current_topic=payload.current_topic,
        current_chapter=payload.current_chapter,
        subject=payload.subject,
        current_knowledge=payload.current_knowledge,
        learning_goal=payload.learning_goal,
        available_minutes=payload.available_minutes,
        exam_target=payload.exam_target,
        preferred_style=payload.preferred_style,
        prerequisite_confidence=payload.prerequisite_confidence,
    )
    return AutonomousStudyResponse(**mission)


# =====================================================
# RESET CHAT
# =====================================================
@app.post("/reset-chat")
def reset_chat(
    request: ResetRequest,
    _current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    reset_conversation(request.session_id)
    return {"status": "cleared", "message": "Agent memory reset successfully"}


# =====================================================
# HEALTH CHECK
# =====================================================
@app.get("/health/live")
def liveness_probe():
    return {"status": "ok", "service": "agentifyai-backend"}


@app.get("/health/ready")
def readiness_probe():
    db_ready = check_db_health()
    firebase_ready = bool(FIREBASE_ADMIN_READY)
    knowledge_ready = bool(knowledge_graph.list_chapters())
    artifact_ready = bool(available_artifact_sections())
    ready = db_ready and firebase_ready
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "status": "ready" if ready else "degraded",
            "database": db_ready,
            "firebase": firebase_ready,
            "knowledge_graph": knowledge_ready,
            "artifacts": artifact_ready,
            "version": "2.5.0-production-guardrails",
        },
    )


@app.get("/health")
def health_check():
    return {
        "status": "online" if check_db_health() else "degraded",
        "version": "2.5.0-production-guardrails",
        "service": "agentifyai-backend",
        "request_ids": True,
        "rate_limits": RATE_LIMIT_ENABLED,
        "cors_origins_configured": len(ALLOWED_ORIGINS),
    }


@app.get("/artifacts/catalog")
def artifact_catalog():
    return {
        "subject": "Chemistry",
        "available_sections": available_artifact_sections(),
        "message": "Artifacts are generated only from ingested platform study data.",
    }


# =====================================================
# ADMIN PANEL API - FIREBASE TOKEN + ROLE PROTECTED
# =====================================================
@app.get("/admin/me")
def admin_me(current_admin: Dict[str, Any] = Depends(require_admin)):
    return {
        "uid": current_admin.get("uid"),
        "email": current_admin.get("email"),
        "phone": current_admin.get("phone_number"),
        "role": "admin",
        "verified": True,
    }


@app.get("/admin/agents")
def admin_get_agents(_current_admin: Dict[str, Any] = Depends(require_admin)):
    return {
        "agents": event_bus.get_all_agents(),
        "system": event_bus.get_system_stats(),
    }


@app.get("/admin/agent-registry")
def admin_get_agent_registry(_current_admin: Dict[str, Any] = Depends(require_admin)):
    return {
        "agents": get_agent_registry(),
        "system": event_bus.get_system_stats(),
    }


@app.get("/admin/agents/{agent_id}")
def admin_get_agent(
    agent_id: str,
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    agent = event_bus.get_agent(agent_id)

    if not agent:
        return {"error": f"Agent '{agent_id}' not found"}

    return agent


@app.get("/admin/events")
def admin_get_events(
    limit: int = Query(default=50, le=500),
    agent_id: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    event_type: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    events = get_recent_observability_events(
        db,
        limit=limit,
        agent_id=agent_id,
        severity=severity,
        event_type=event_type,
    )
    durable = bool(events)
    if not events:
        events = event_bus.get_recent_events(
            limit=limit,
            agent_id=agent_id,
            severity=severity,
            event_type=event_type,
        )
    return {
        "events": events,
        "durable": durable,
        "total_buffered": len(event_bus._events),
        "observability": get_observability_summary(db),
    }


@app.get("/admin/poll")
def admin_poll(
    since: int = Query(default=0, description="Event version cursor. Pass 0 on first call."),
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    new_events = get_observability_events_since(db, since)
    if not new_events:
        new_events = event_bus.get_events_since(since)
    latest_version = max(event_bus.get_latest_version(), get_latest_observability_version(db))
    observability = get_observability_summary(db)
    system = event_bus.get_system_stats()
    system["observability"] = observability

    return {
        "events": new_events,
        "agents": event_bus.get_all_agents(),
        "system": system,
        "observability": observability,
        "version": latest_version,
    }


@app.get("/admin/stats")
def admin_get_stats(
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    stats = event_bus.get_system_stats()
    stats["observability"] = get_observability_summary(db)
    return stats


@app.post("/admin/command")
def admin_send_command(
    request: AgentCommandRequest,
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    return event_bus.send_command(
        agent_id=request.agent_id,
        command=request.command,
        payload=request.payload,
    )


# ╔══════════════════════════════════════════════════════════════╗
# ║  UPDATED /admin/message  – Casual mode for CEO chats       ║
# ╚══════════════════════════════════════════════════════════════╝
@app.post("/admin/message")
def admin_send_message(
    request: AgentMessageRequest,
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    if request.mode == "casual":
        agent_stats = event_bus.get_agent(request.agent_id) or {}
        recent_events = event_bus.get_recent_events(
            limit=5,
            agent_id=request.agent_id,
        )

        status = agent_stats.get("status", "unknown")
        health = agent_stats.get("health", "unknown")
        current_task = agent_stats.get("current_task") or "idle"
        total_requests = agent_stats.get("total_requests", 0)
        total_errors = agent_stats.get("total_errors", 0)
        total_success = agent_stats.get("total_success", 0)
        avg_latency = agent_stats.get("avg_latency_ms", 0)

        last_events = ""
        if recent_events:
            event_lines = []
            for ev in recent_events[:5]:
                timestamp = ev.get("timestamp", "")[:16]
                ev_type = ev.get("event_type", "event")
                data_preview = str(ev.get("data", {}))[:120]
                event_lines.append(f"  [{timestamp}] {ev_type}: {data_preview}")
            last_events = "\n".join(event_lines)

        role_descriptions = {
            "tutor": "You are the Tutor Agent. Your job is to help students learn chemistry concepts, answer their questions, and provide clear explanations.",
            "revision": "You are the Revision Agent. You generate intelligent revision summaries, key points, and deep explanations.",
            "exam": "You are the Exam Agent. You create MCQs, probable exam questions, and track student scores.",
            "planner": "You are the Planner Agent. You design personalised study plans and learning paths.",
            "coach": "You are the Personal AI Coach. You monitor student progress, provide daily strategies, and recommend next actions.",
        }
        role_text = role_descriptions.get(
            request.agent_id,
            f"You are the {request.agent_id} agent, a key member of the AI learning platform team.",
        )

        system_prompt = f"""
{role_text}
You are reporting to the CEO in a casual, professional tone as a trusted team member.

CURRENT STATUS:
- Status: {status}
- Health: {health}
- Current task: {current_task}
- Total requests processed: {total_requests}
- Successful: {total_success}
- Errors: {total_errors}
- Average latency: {avg_latency} ms

LAST RECENT EVENTS (newest first):
{last_events if last_events else "No recent events."}

When responding, ***only use the facts above*** to answer the CEO's question.
If the CEO asks about something not covered by the data, politely say that you don't have that information at the moment.
Be concise, but warm and proactive. Offer insights or suggestions where relevant.
"""

        enriched_message = (
            f"CEO says: {request.message}\n\n"
            "Provide a thoughtful, data-driven response based on the facts you have."
        )

        try:
            answer = generic_llm_chat(
                system_prompt=system_prompt,
                user_message=enriched_message,
                agent_id=request.agent_id,
            )
        except Exception as e:
            logger.error(f"Generic LLM chat failed: {e}")
            answer = f"I'm having trouble responding right now. (agent: {request.agent_id})"

        return {
            "answer": answer,
            "agent_id": request.agent_id,
            "mode": "casual",
            "session_id": request.session_id,
        }

    # ── STANDARD STUDY MODE ──────────────────────
    class AdminRequest:
        def __init__(self):
            self.question = request.message
            self.section_id = request.section_id
            self.session_id = f"admin_{request.agent_id}"
            self.mode = None
            self.difficulty = "medium"

    admin_req = AdminRequest()

    if request.agent_id == "tutor":
        from Logic.agents.tutor_agent import tutor_agent

        result = tutor_agent(admin_req)
    elif request.agent_id == "revision":
        from Logic.agents.revision_agent import revision_agent

        admin_req.mode = "summary"
        result = revision_agent(admin_req, revision_type="summary")
    elif request.agent_id == "exam":
        from Logic.agents.exam_agent import exam_agent

        result = exam_agent(admin_req, exam_type="mcq")
    elif request.agent_id == "planner":
        from Logic.agents.planner_agent import planner_agent

        result = planner_agent(admin_req, db)
    elif request.agent_id == "coach":
        result = coach_agent(admin_req, db=db)
    else:
        result = route_to_agent(admin_req, db=db)

    return result


# =====================================================
# PROGRESS API
# =====================================================
@app.post("/update-progress")
def update_progress(
    progress: ProgressUpdate,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    require_same_user_or_admin(progress.user_id, current_user)

    user = get_or_create_progress(db, progress.user_id)

    user.total_tests = progress.total_tests
    user.total_questions = progress.total_questions
    user.total_correct = progress.total_correct
    user.xp = progress.xp

    apply_streak(user)

    db.commit()
    db.refresh(user)

    return {
        "message": "Progress updated successfully",
        "progress": progress_payload(user),
        "streak": user.streak,
    }


@app.get("/get-progress/{user_id}", response_model=ProgressResponse)
def get_progress(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    require_same_user_or_admin(user_id, current_user)

    user = get_or_create_progress(db, user_id)
    payload = progress_payload(user)

    return ProgressResponse(**payload)


# =====================================================
# SESSION WRITE API
# =====================================================
@app.post("/submit-session")
def submit_session(
    payload: SubmitSessionRequest,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    require_same_user_or_admin(payload.user_id, current_user)

    if payload.total_questions <= 0:
        raise HTTPException(status_code=400, detail="total_questions must be greater than zero")

    correct = max(0, min(payload.score, payload.total_questions))
    xp_earned = payload.xp_earned if payload.xp_earned is not None else correct * 10

    test = create_test_history(
        db=db,
        user_id=payload.user_id,
        topic=payload.topic,
        score=correct,
        total_questions=payload.total_questions,
        xp_earned=xp_earned,
        time_spent_seconds=payload.time_spent_seconds,
        focus_score=payload.focus_score,
        session_type=payload.session_type,
        replay_data=payload.replay_data,
        started_at=payload.started_at,
        completed_at=payload.completed_at,
        response_latency_ms=payload.response_latency_ms,
        hint_count=payload.hint_count,
        retry_count=payload.retry_count,
        confidence_before=payload.confidence_before,
        confidence_after=payload.confidence_after,
    )

    user = get_or_create_progress(db, payload.user_id)
    user.total_tests += 1
    user.total_questions += payload.total_questions
    user.total_correct += correct
    user.xp += xp_earned
    user.focus_score = payload.focus_score

    apply_streak(user)

    db.commit()
    db.refresh(test)
    db.refresh(user)

    return {
        "message": "Session submitted successfully",
        "session": format_test_session(test),
        "progress": progress_payload(user),
    }


@app.post("/save-test")
def save_test(
    test: TestHistoryCreate,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    require_same_user_or_admin(test.user_id, current_user)

    topic = normalize_topic(test.topic)
    correct = int(test.score)
    total = int(test.total_questions)
    xp_earned = int(test.xp_earned)

    new_test = create_test_history(
        db=db,
        user_id=test.user_id,
        topic=topic,
        score=correct,
        total_questions=total,
        xp_earned=xp_earned,
        time_spent_seconds=int(test.time_spent_seconds or 0),
        focus_score=float(test.focus_score or 0),
        session_type=test.session_type or "exam",
        replay_data=test.replay_data,
        started_at=test.started_at,
        completed_at=test.completed_at,
        response_latency_ms=test.response_latency_ms,
        hint_count=test.hint_count,
        retry_count=test.retry_count,
        confidence_before=test.confidence_before,
        confidence_after=test.confidence_after,
    )

    db.commit()
    db.refresh(new_test)

    return {
        "message": "Test saved successfully",
        "topic": topic,
        "analytics_updated": True,
        "session": format_test_session(new_test),
    }


# =====================================================
# SESSION READ API
# =====================================================
@app.get("/sessions/{user_id}")
def get_sessions(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    require_same_user_or_admin(user_id, current_user)

    tests = (
        db.query(TestHistory)
        .filter(TestHistory.user_id == user_id)
        .order_by(TestHistory.id.desc())
        .all()
    )

    return {
        "user_id": user_id,
        "sessions": [format_test_session(test) for test in tests],
    }


@app.get("/test-history/{user_id}", response_model=List[TestHistoryResponse])
def get_test_history(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    require_same_user_or_admin(user_id, current_user)

    tests = (
        db.query(TestHistory)
        .filter(TestHistory.user_id == user_id)
        .order_by(TestHistory.date.asc(), TestHistory.id.asc())
        .all()
    )

    return tests


@app.get("/session-replay/{test_id}")
def get_session_replay(
    test_id: int,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    test = db.query(TestHistory).filter(TestHistory.id == test_id).first()

    if not test:
        raise HTTPException(status_code=404, detail="Session not found")

    require_same_user_or_admin(test.user_id, current_user)

    replay = test.details.replay_data if test.details else {}

    return {
        "id": test.id,
        "topic": test.topic,
        "date": test.date.isoformat() if test.date else None,
        "replay_data": replay,
    }


# =====================================================
# LEADERBOARD
# =====================================================
def build_leaderboard(
    db: Session,
    decoded_token: Optional[Dict[str, Any]] = None,
):
    token_uid = str((decoded_token or {}).get("uid", "")).strip()
    admin_view = bool(decoded_token and is_backend_admin(decoded_token))

    users = (
        db.query(UserProgress)
        .order_by(UserProgress.xp.desc())
        .limit(10)
        .all()
    )

    leaderboard_data = []

    # Batch fetch Firebase user info if possible
    firebase_users = {}
    if FIREBASE_ADMIN_READY and firebase_auth:
        uids = [user.user_id for user in users]
        try:
            # firebase_admin.auth.get_users (plural) fetches up to 100 UIDs in one call
            auth_result = firebase_auth.get_users([firebase_auth.UidIdentifier(uid) for uid in uids])
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


@app.get("/leaderboard")
def leaderboard(
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    return build_leaderboard(db, current_user)

# =====================================================
# ANALYTICS
# =====================================================
@app.get("/analytics/{user_id}")
def analytics(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    require_same_user_or_admin(user_id, current_user)
    return get_user_analytics(db, user_id)


@app.get("/dashboard/{user_id}")
def get_dashboard_data(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    require_same_user_or_admin(user_id, current_user)

    user = get_or_create_progress(db, user_id)

    tests = (
        db.query(TestHistory)
        .filter(TestHistory.user_id == user_id)
        .order_by(TestHistory.id.desc())
        .limit(50)
        .all()
    )

    return {
        "progress": progress_payload(user),
        "sessions": [format_test_session(test) for test in tests],
        "analytics": get_user_analytics(db, user_id),
        "leaderboard": build_leaderboard(db, current_user),
    }
