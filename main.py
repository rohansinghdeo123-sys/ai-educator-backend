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
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from hashlib import sha256
from threading import Lock
from typing import Any, Dict, List, Optional, Generator

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import distinct, func, inspect, text
from sqlalchemy.orm import Session

from database import SessionLocal, check_db_health, engine
from models import (
    AdminAuditLog,
    AgentChatMemory,
    AICoachDailySignal,
    AICoachInteraction,
    AICoachMemory,
    AICoachProfile,
    Base,
    ContentChapter,
    ContentChunk,
    ContentIngestionJob,
    DailyQuotaUsage,
    ModelToolTrace,
    ObservabilityEvent,
    SessionDetail,
    TestHistory,
    TopicPerformance,
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
from Logic.content_pipeline import (
    RAW_NCERT_DIR,
    approve_chapter,
    chapter_report,
    generate_concepts_for_chapter,
    import_concepts_for_chapter,
    ingest_pdf_folder,
    list_chapters as list_content_chapters,
    publish_chapter,
    serialize_chapter,
)
from Logic.tools.artifact_generator import (
    ARTIFACT_DATA_NOT_AVAILABLE,
    available_artifact_sections,
    generate_study_artifacts,
)
from Logic.coach.settings import coach_settings

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
BACKEND_FOUNDER_ADMIN_EMAILS = parse_csv_env("BACKEND_FOUNDER_ADMIN_EMAILS") or BACKEND_ADMIN_EMAILS


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


def is_founder_admin(decoded_token: Dict[str, Any]) -> bool:
    email = str(decoded_token.get("email", "")).lower()
    return bool(email and email in BACKEND_FOUNDER_ADMIN_EMAILS and is_backend_admin(decoded_token))


def require_founder_admin(
    decoded_token: Dict[str, Any] = Depends(verify_firebase_user),
) -> Dict[str, Any]:
    if not is_founder_admin(decoded_token):
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


def session_id_belongs_to_user(session_id: str, user_id: str) -> bool:
    session = str(session_id or "").strip()
    uid = str(user_id or "").strip()
    if not session or not uid:
        return False

    owned_prefixes = (
        f"coach-{uid}-",
        f"coach_{uid}_",
        f"revision-{uid}-",
        f"exam-{uid}-",
        f"probable-{uid}-",
        f"autonomous-{uid}-",
        f"widget-{uid}",
    )
    owned_exact = {
        uid,
        f"coach-{uid}",
        f"coach_{uid}",
        f"widget-{uid}",
    }

    return session in owned_exact or any(session.startswith(prefix) for prefix in owned_prefixes)


def require_owned_study_session(session_id: str, decoded_token: Dict[str, Any]) -> str:
    user_id = require_authenticated_user_id(decoded_token)
    if is_backend_admin(decoded_token):
        return user_id
    if session_id_belongs_to_user(session_id, user_id):
        return user_id

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not allowed for this study session",
    )


def consume_persistent_daily_quota(user_id: str, quota_name: str, limit: int) -> tuple[bool, int]:
    if limit <= 0:
        return True, 0

    quota_date = date.today()
    user_hash = sha256(str(user_id).encode("utf-8")).hexdigest()[:16]
    quota_key = f"{quota_date.isoformat()}:{quota_name}:{user_hash}"
    db = SessionLocal()
    try:
        usage = db.query(DailyQuotaUsage).filter(DailyQuotaUsage.quota_key == quota_key).first()
        if usage is None:
            usage = DailyQuotaUsage(
                quota_key=quota_key,
                user_hash=user_hash,
                quota_name=quota_name,
                quota_date=quota_date,
                count=1,
            )
            db.add(usage)
            db.commit()
            return True, 1

        current = int(usage.count or 0)
        if current >= limit:
            return False, current

        usage.count = current + 1
        usage.updated_at = datetime.utcnow()
        db.commit()
        return True, usage.count
    except Exception as exc:
        db.rollback()
        logger.warning("QUOTA: Persistent quota unavailable, using in-memory fallback: %s", exc)
        return daily_quotas.consume(user_id, quota_name, limit)
    finally:
        db.close()


def enforce_user_quota(user_id: str, quota_name: str) -> None:
    limit = QUOTA_LIMITS.get(quota_name, AI_DAILY_QUOTA_PER_USER)
    allowed, used = consume_persistent_daily_quota(user_id, quota_name, limit)
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
    question: str = Field(min_length=1, max_length=2500)
    section_id: str = Field(min_length=1, max_length=160)
    session_id: str = Field(min_length=1, max_length=220)
    mode: str = Field(default="revision", max_length=40)
    difficulty: str = Field(default="medium", max_length=40)
    subject: Optional[str] = Field(default=None, max_length=120)
    chapter: Optional[str] = Field(default=None, max_length=180)
    topic: Optional[str] = Field(default=None, max_length=180)
    system_guardrail: Optional[str] = Field(default=None, max_length=8000)
    strict_grounding: bool = False
    retrieval_required: bool = False
    fallback_to_general_knowledge: bool = True
    required_not_found_response: Optional[str] = Field(default=None, max_length=500)


class ResetRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=160)
    user_id: Optional[str] = None


class CoachConversationPatch(BaseModel):
    title: Optional[str] = Field(default=None, max_length=72)
    pinned: Optional[bool] = None
    archived: Optional[bool] = None
    titleLocked: Optional[bool] = None


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


class ContentIngestFolderRequest(BaseModel):
    root_path: Optional[str] = Field(default=None, max_length=600)
    replace_existing_extraction: bool = True


class ContentConceptImportRequest(BaseModel):
    concepts: Any
    replace_existing: bool = True


class ContentGenerateConceptsRequest(BaseModel):
    replace_existing: bool = True
    max_batch_chars: int = Field(default=9000, ge=2500, le=16000)


class AdminAuditRequest(BaseModel):
    action: str = Field(min_length=2, max_length=120)
    target_type: str = Field(default="console", max_length=80)
    target_id: str = Field(default="", max_length=220)
    status: str = Field(default="success", max_length=40)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AdminActionRequest(BaseModel):
    action: str
    target_type: str = ""
    target_id: str = ""
    confirmed: bool = False
    payload: Dict[str, Any] = Field(default_factory=dict)


class GenerateMCQRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=180)
    section_id: Optional[str] = Field(default=None, max_length=160)
    session_id: str = Field(default="exam-session", min_length=1, max_length=220)
    difficulty: str = Field(default="medium", max_length=40)
    count: int = Field(default=5, ge=1, le=10)
    subject: Optional[str] = Field(default=None, max_length=120)
    chapter: Optional[str] = Field(default=None, max_length=180)
    system_guardrail: Optional[str] = Field(default=None, max_length=8000)
    strict_grounding: bool = False
    retrieval_required: bool = False
    fallback_to_general_knowledge: bool = True
    required_not_found_response: Optional[str] = Field(default=None, max_length=500)
    include_source: bool = False
    require_four_options: bool = True
    require_explanation: bool = True


class GenerateProbableRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=180)
    section_id: Optional[str] = Field(default=None, max_length=160)
    session_id: str = Field(default="probable-session", min_length=1, max_length=220)
    difficulty: str = Field(default="medium", max_length=40)
    subject: Optional[str] = Field(default=None, max_length=120)
    chapter: Optional[str] = Field(default=None, max_length=180)
    system_guardrail: Optional[str] = Field(default=None, max_length=8000)
    strict_grounding: bool = False
    retrieval_required: bool = False
    fallback_to_general_knowledge: bool = True
    required_not_found_response: Optional[str] = Field(default=None, max_length=500)
    include_source: bool = False


class ArtifactGenerateRequest(BaseModel):
    section_id: str = Field(min_length=1, max_length=160)
    topic: Optional[str] = Field(default=None, max_length=180)
    artifact_type: str = Field(default="auto", max_length=50)
    subject: Optional[str] = Field(default=None, max_length=120)
    chapter: Optional[str] = Field(default=None, max_length=180)
    system_guardrail: Optional[str] = Field(default=None, max_length=8000)
    strict_grounding: bool = False
    retrieval_required: bool = False
    fallback_to_general_knowledge: bool = True
    required_not_found_response: Optional[str] = Field(default=None, max_length=500)


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
    replay_data = test.details.replay_data if getattr(test, "details", None) else {}
    replay_question_count = 0
    if isinstance(replay_data, dict):
        questions_payload = replay_data.get("questions")
        if isinstance(questions_payload, list):
            replay_question_count = len(questions_payload)

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
        "has_replay": bool(replay_question_count),
        "replay_question_count": replay_question_count,
        "replay_data": replay_data or {},
    }


ADMIN_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SUPPORTED_ADMIN_ACTIONS = {
    "clear_temp_cache",
    "export_data_report",
    "refresh_snapshot",
}
CONFIRMED_ADMIN_ACTIONS = {
    "clear_temp_cache",
    "clear_temp_chat_state",
    "disable_user",
    "promote_model_version",
    "refresh_vector_index",
    "reindex_material",
    "reset_user_session",
}


def _admin_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _admin_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _admin_iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


def _safe_json_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _query_count(query: Any) -> int:
    try:
        return _admin_int(query.count())
    except Exception:
        logger.exception("Admin count query failed")
        return 0


def _sum_scalar(db: Session, model: Any, column: Any, *filters: Any) -> float:
    try:
        query = db.query(func.sum(column))
        for item in filters:
            query = query.filter(item)
        return _admin_float(query.scalar())
    except Exception:
        logger.exception("Admin sum query failed for %s", model)
        return 0.0


def _admin_identity(current_admin: Dict[str, Any]) -> Dict[str, str]:
    return {
        "uid": str(current_admin.get("uid") or ""),
        "email": str(current_admin.get("email") or ""),
        "phone": str(current_admin.get("phone_number") or ""),
    }


def record_admin_audit(
    db: Session,
    current_admin: Dict[str, Any],
    *,
    action: str,
    target_type: str = "",
    target_id: str = "",
    status_value: str = "success",
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    identity = _admin_identity(current_admin)
    try:
        db.add(
            AdminAuditLog(
                actor_uid=identity["uid"],
                actor_email=identity["email"],
                action=action,
                target_type=target_type,
                target_id=target_id,
                status=status_value,
                metadata_json=metadata or {},
            )
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("Could not persist admin audit log for %s: %s", action, exc)


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    for suffix in ("B", "KB", "MB", "GB"):
        if size < 1024 or suffix == "GB":
            return f"{size:.1f} {suffix}" if suffix != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


def _paragraph_chunks(text_value: str) -> List[str]:
    return [
        item.strip()
        for item in re.split(r"\n\s*\n+", text_value or "")
        if item.strip()
    ]


def _iter_data_files() -> List[Dict[str, Any]]:
    files: List[Dict[str, Any]] = []
    if not os.path.isdir(ADMIN_DATA_DIR):
        return files

    for root, _dirs, names in os.walk(ADMIN_DATA_DIR):
        for name in names:
            path = os.path.join(root, name)
            rel_path = os.path.relpath(path, ADMIN_DATA_DIR).replace("\\", "/")
            try:
                stat = os.stat(path)
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    content = handle.read()
                chunks = _paragraph_chunks(content)
                concepts = 0
                if name.lower().endswith(".json"):
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, list):
                            concepts = len(parsed)
                    except json.JSONDecodeError:
                        pass
                files.append(
                    {
                        "path": rel_path,
                        "bytes": int(stat.st_size),
                        "text_chars": len(content),
                        "chunks": concepts or len(chunks),
                        "low_quality_chunks": sum(1 for item in chunks if len(item) < 40),
                        "concepts": concepts,
                        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "status": "ready",
                    }
                )
            except Exception as exc:
                files.append(
                    {
                        "path": rel_path,
                        "bytes": 0,
                        "text_chars": 0,
                        "chunks": 0,
                        "low_quality_chunks": 0,
                        "concepts": 0,
                        "modified_at": None,
                        "status": "failed",
                        "error": str(exc)[:240],
                    }
                )
    return sorted(files, key=lambda item: item["path"])


def _recent_turn_traces(db: Session, limit: int = 500) -> List[ModelToolTrace]:
    return (
        db.query(ModelToolTrace)
        .filter(ModelToolTrace.trace_type == "turn")
        .order_by(ModelToolTrace.created_at.desc(), ModelToolTrace.id.desc())
        .limit(limit)
        .all()
    )


def _trace_quality_summary(db: Session) -> Dict[str, Any]:
    turns = _recent_turn_traces(db)
    if not turns:
        return {
            "samples": 0,
            "quality_score": None,
            "grounded_answer_rate": None,
            "retrieval_success_rate": None,
        }

    quality_values: List[float] = []
    grounded_total = 0
    grounded_success = 0
    retrieval_total = 0
    retrieval_success = 0

    for row in turns:
        metadata = _safe_json_dict(row.metadata_json)
        quality = _safe_json_dict(metadata.get("quality"))
        retrieval = _safe_json_dict(metadata.get("retrieval"))

        score = quality.get("score") or quality.get("overall_score")
        if score is None and "passed" in quality:
            score = 100 if quality.get("passed") else 0
        if score is not None:
            quality_values.append(_admin_float(score))

        if retrieval.get("policy") or retrieval.get("source") or retrieval.get("section_id"):
            retrieval_total += 1
            if _admin_int(retrieval.get("paragraphs_found")) > 0 or retrieval.get("supported") is True:
                retrieval_success += 1

        if quality or retrieval:
            grounded_total += 1
            if quality.get("passed", True) and (
                not retrieval or _admin_int(retrieval.get("paragraphs_found")) > 0 or retrieval.get("supported") is True
            ):
                grounded_success += 1

    return {
        "samples": len(turns),
        "quality_score": round(sum(quality_values) / len(quality_values), 1) if quality_values else None,
        "grounded_answer_rate": round((grounded_success / grounded_total) * 100, 1) if grounded_total else None,
        "retrieval_success_rate": round((retrieval_success / retrieval_total) * 100, 1) if retrieval_total else None,
    }


def build_admin_data_registry(db: Session) -> Dict[str, Any]:
    files = _iter_data_files()
    total_bytes = sum(_admin_int(item.get("bytes")) for item in files)
    total_text_chars = sum(_admin_int(item.get("text_chars")) for item in files)
    total_chunks = sum(_admin_int(item.get("chunks")) for item in files)
    total_concepts = sum(_admin_int(item.get("concepts")) for item in files)
    failed_files = [item for item in files if item.get("status") == "failed"]
    low_quality_chunks = sum(_admin_int(item.get("low_quality_chunks")) for item in files)
    modified_times = [
        str(item.get("modified_at"))
        for item in files
        if item.get("modified_at")
    ]
    quality = _trace_quality_summary(db)

    source_coverage: Dict[str, int] = defaultdict(int)
    for item in files:
        parts = str(item.get("path") or "").split("/")
        subject = parts[0] if parts else "data"
        source_coverage[subject] += _admin_int(item.get("chunks"))

    extraction_percent = 100 if files and not failed_files else 0 if not files else round(((len(files) - len(failed_files)) / len(files)) * 100, 1)
    chunking_percent = 100 if total_chunks > 0 else 0

    return {
        "summary": {
            "study_materials": len(files),
            "total_file_size_bytes": total_bytes,
            "total_file_size_label": _format_bytes(total_bytes),
            "total_extracted_text_chars": total_text_chars,
            "chapters": len(knowledge_graph.list_chapters()),
            "topics": total_concepts or len(available_artifact_sections()),
            "chunks": total_chunks,
            "embeddings": 0,
            "vector_index_size_bytes": 0,
            "vector_index_size_label": "0 B",
            "last_indexed_time": max(modified_times) if modified_times else None,
            "failed_files": len(failed_files),
            "low_quality_chunks": low_quality_chunks,
        },
        "progress": {
            "extraction_complete": extraction_percent,
            "chunking_complete": chunking_percent,
            "embedding_complete": None,
            "index_health": None,
            "retrieval_success": quality["retrieval_success_rate"],
        },
        "source_coverage": [
            {"source": key, "chunks": value}
            for key, value in sorted(source_coverage.items(), key=lambda item: item[0])
        ],
        "files": files,
        "failed_files": failed_files,
        "vector_index": {
            "available": False,
            "status": "not_configured",
            "todo": "Connect a vector index service before reporting embeddings or index size.",
        },
        "notes": [
            "File sizes, text sizes, chunks, chapters, and topics are computed from backend data files.",
            "Embeddings and vector index size are intentionally not estimated because no vector index backend is present in this repository.",
        ],
    }


def _format_trace_row(row: ModelToolTrace) -> Dict[str, Any]:
    return {
        "id": row.id,
        "created_at": _admin_iso(row.created_at),
        "user_id": row.user_id or "",
        "session_id": row.session_id or "",
        "turn_id": row.turn_id or "",
        "trace_type": row.trace_type or "",
        "name": row.name or "",
        "provider": row.provider or "",
        "model": row.model or "",
        "status": row.status or "unknown",
        "latency_ms": _admin_int(row.latency_ms),
        "estimated_input_tokens": _admin_int(row.estimated_input_tokens),
        "estimated_output_tokens": _admin_int(row.estimated_output_tokens),
        "estimated_cost_usd": round(_admin_float(row.estimated_cost_usd), 8),
        "metadata": row.metadata_json or {},
    }


def _timeline_for_trace_group(rows: List[ModelToolTrace]) -> List[Dict[str, Any]]:
    ordered = sorted(rows, key=lambda item: item.created_at or datetime.utcnow())
    timeline: List[Dict[str, Any]] = []
    turn_row = next((row for row in ordered if row.trace_type == "turn"), None)
    turn_metadata = _safe_json_dict(turn_row.metadata_json if turn_row else {})
    query = _safe_json_dict(turn_metadata.get("query"))
    retrieval = _safe_json_dict(turn_metadata.get("retrieval"))
    quality = _safe_json_dict(turn_metadata.get("quality"))

    if query:
        timeline.append(
            {
                "step": "user_query",
                "label": "User query",
                "status": "success",
                "detail": query.get("normalized") or query.get("question") or query.get("intent") or "Query captured",
            }
        )
    if query.get("intent"):
        timeline.append(
            {
                "step": "intent_detection",
                "label": "Intent detection",
                "status": "success",
                "detail": str(query.get("intent")),
            }
        )
    if retrieval:
        paragraphs = _admin_int(retrieval.get("paragraphs_found"))
        timeline.append(
            {
                "step": "retrieval",
                "label": "Retrieval",
                "status": "success" if paragraphs > 0 else "empty",
                "detail": f"{paragraphs} chunks from {retrieval.get('source') or 'study data'}",
            }
        )
        timeline.append(
            {
                "step": "source_check",
                "label": "Source check",
                "status": "success" if retrieval.get("supported", paragraphs > 0) else "warning",
                "detail": retrieval.get("section_id") or retrieval.get("policy") or "Grounding checked",
            }
        )

    for row in ordered:
        if row.trace_type == "model":
            timeline.append(
                {
                    "step": "model_call",
                    "label": row.name or "Model call",
                    "status": row.status or "unknown",
                    "detail": row.model or row.provider or "Model not recorded",
                    "latency_ms": _admin_int(row.latency_ms),
                }
            )
        elif row.trace_type == "tool":
            timeline.append(
                {
                    "step": "tool_call",
                    "label": row.name or "Tool call",
                    "status": row.status or "unknown",
                    "detail": str(_safe_json_dict(row.metadata_json).get("summary") or "Tool executed"),
                    "latency_ms": _admin_int(row.latency_ms),
                }
            )

    if quality:
        timeline.append(
            {
                "step": "quality_validation",
                "label": "Quality validation",
                "status": "success" if quality.get("passed", True) else "needs_review",
                "detail": ", ".join(str(item) for item in quality.get("issues", [])[:2]) or "Quality checked",
            }
        )

    if turn_row:
        timeline.append(
            {
                "step": "final_answer",
                "label": "Final answer",
                "status": turn_row.status or "unknown",
                "detail": f"{_admin_int(turn_row.latency_ms)} ms end-to-end",
                "latency_ms": _admin_int(turn_row.latency_ms),
            }
        )

    return timeline


def build_admin_traces(
    db: Session,
    *,
    limit: int = 40,
    trace_type: Optional[str] = None,
    status_filter: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    query = db.query(ModelToolTrace)
    if trace_type:
        query = query.filter(ModelToolTrace.trace_type == trace_type)
    if status_filter:
        query = query.filter(ModelToolTrace.status == status_filter)
    if user_id:
        query = query.filter(ModelToolTrace.user_id == user_id)

    rows = (
        query.order_by(ModelToolTrace.created_at.desc(), ModelToolTrace.id.desc())
        .limit(limit)
        .all()
    )
    grouped: Dict[str, List[ModelToolTrace]] = defaultdict(list)
    for row in rows:
        grouped[row.turn_id or f"{row.trace_type}-{row.id}"].append(row)

    return {
        "rows": [_format_trace_row(row) for row in rows],
        "runs": [
            {
                "turn_id": turn_id,
                "created_at": _admin_iso(max((row.created_at for row in group if row.created_at), default=None)),
                "status": next((row.status for row in group if row.trace_type == "turn"), group[0].status if group else "unknown"),
                "user_id": next((row.user_id for row in group if row.user_id), ""),
                "session_id": next((row.session_id for row in group if row.session_id), ""),
                "total_latency_ms": sum(_admin_int(row.latency_ms) for row in group if row.trace_type != "route"),
                "estimated_cost_usd": round(sum(_admin_float(row.estimated_cost_usd) for row in group), 8),
                "models": sorted({row.model for row in group if row.model}),
                "timeline": _timeline_for_trace_group(group),
                "rows": [_format_trace_row(row) for row in sorted(group, key=lambda item: item.created_at or datetime.utcnow())],
            }
            for turn_id, group in grouped.items()
        ],
        "summary": get_observability_summary(db),
    }


def build_admin_model_registry(db: Session) -> Dict[str, Any]:
    quality = _trace_quality_summary(db)
    model_rows = (
        db.query(
            ModelToolTrace.provider,
            ModelToolTrace.model,
            func.count(ModelToolTrace.id),
            func.avg(ModelToolTrace.latency_ms),
            func.sum(ModelToolTrace.estimated_cost_usd),
        )
        .filter(ModelToolTrace.trace_type == "model")
        .group_by(ModelToolTrace.provider, ModelToolTrace.model)
        .all()
    )
    versions = [
        {
            "version": f"{provider or 'provider'}:{model or 'model'}",
            "provider": provider or "",
            "model": model or "",
            "samples": _admin_int(samples),
            "avg_latency_ms": round(_admin_float(avg_latency), 1),
            "estimated_cost_usd": round(_admin_float(cost), 8),
            "status": "observed",
            "quality_score": quality["quality_score"],
        }
        for provider, model, samples, avg_latency, cost in model_rows
    ]

    live_model = coach_settings.tutor_model or coach_settings.fast_model
    live_provider = coach_settings.provider
    for item in versions:
        if item["model"] == live_model or (not item["model"] and item["provider"] == live_provider):
            item["status"] = "live"

    return {
        "current": {
            "llm_provider": live_provider,
            "llm_model": live_model,
            "fast_model": coach_settings.fast_model,
            "review_model": coach_settings.review_model,
            "embedding_model": os.getenv("EMBEDDING_MODEL") or None,
            "rag_index_version": hashlib_data_version(),
            "prompt_version": os.getenv("PROMPT_VERSION", "agentic-control-plane-v1"),
            "agent_workflow_version": os.getenv("AGENT_WORKFLOW_VERSION", "agentic-control-plane-v1"),
            "deployment_version": os.getenv("RENDER_GIT_COMMIT", "2.5.0-production-guardrails"),
            "quality_score": quality["quality_score"],
            "latency_ms": get_observability_summary(db).get("avg_model_latency_ms"),
            "failure_rate": model_failure_rate(db),
            "grounded_answer_rate": quality["grounded_answer_rate"],
            "samples": quality["samples"],
        },
        "versions": versions,
        "unsupported_actions": [
            "promote_model_version requires a deployment/version management backend before it can be enabled.",
        ],
    }


def hashlib_data_version() -> str:
    digest = sha256()
    for item in _iter_data_files():
        digest.update(str(item.get("path", "")).encode("utf-8"))
        digest.update(str(item.get("bytes", "")).encode("utf-8"))
        digest.update(str(item.get("modified_at", "")).encode("utf-8"))
    return f"data-{digest.hexdigest()[:12]}"


def model_failure_rate(db: Session) -> Optional[float]:
    total = _query_count(db.query(ModelToolTrace).filter(ModelToolTrace.trace_type == "model"))
    if not total:
        return None
    failures = _query_count(
        db.query(ModelToolTrace).filter(
            ModelToolTrace.trace_type == "model",
            ModelToolTrace.status.notin_(["success", "skipped"]),
        )
    )
    return round((failures / total) * 100, 1)


def build_admin_overview(db: Session) -> Dict[str, Any]:
    now = datetime.utcnow()
    today = date.today()
    today_start = datetime.combine(today, datetime.min.time())
    quality = _trace_quality_summary(db)
    data_registry = build_admin_data_registry(db)

    progress_users = db.query(UserProgress).all()
    user_ids = {user.user_id for user in progress_users if user.user_id}
    for (user_id_value,) in db.query(distinct(TestHistory.user_id)).all():
        if user_id_value:
            user_ids.add(user_id_value)
    for (user_id_value,) in db.query(distinct(AICoachProfile.user_id)).all():
        if user_id_value:
            user_ids.add(user_id_value)

    total_questions = sum(_admin_int(user.total_questions) for user in progress_users)
    total_correct = sum(_admin_int(user.total_correct) for user in progress_users)
    total_tests = sum(_admin_int(user.total_tests) for user in progress_users)

    active_today_ids = {
        user.user_id
        for user in progress_users
        if user.user_id and user.last_active_date == today
    }
    active_today_ids.update(
        user_id
        for (user_id,) in db.query(distinct(TestHistory.user_id)).filter(TestHistory.date == today).all()
        if user_id
    )
    active_today_ids.update(
        user_id
        for (user_id,) in db.query(distinct(AICoachInteraction.user_id))
        .filter(AICoachInteraction.created_at >= today_start)
        .all()
        if user_id
    )

    active_sessions = _query_count(
        db.query(distinct(ModelToolTrace.session_id)).filter(
            ModelToolTrace.created_at >= now - timedelta(hours=1),
            ModelToolTrace.session_id.isnot(None),
            ModelToolTrace.session_id != "",
        )
    )
    if active_sessions == 0:
        active_sessions = _query_count(
            db.query(distinct(AgentChatMemory.session_id)).filter(
                AgentChatMemory.timestamp >= now - timedelta(hours=1),
                AgentChatMemory.session_id.isnot(None),
                AgentChatMemory.session_id != "",
            )
        )

    observed = get_observability_summary(db)
    failed_requests = _query_count(
        db.query(ObservabilityEvent).filter(ObservabilityEvent.severity.in_(["error", "critical"]))
    )
    total_cost = _sum_scalar(db, ModelToolTrace, ModelToolTrace.estimated_cost_usd)
    total_input_tokens = _sum_scalar(db, ModelToolTrace, ModelToolTrace.estimated_input_tokens)
    total_output_tokens = _sum_scalar(db, ModelToolTrace, ModelToolTrace.estimated_output_tokens)

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "users": {
            "total": len(user_ids),
            "active_today": len(active_today_ids),
            "active_sessions": active_sessions,
        },
        "learning": {
            "questions_asked": _query_count(db.query(AICoachInteraction).filter(AICoachInteraction.role == "user")),
            "revision_generations": _query_count(
                db.query(ObservabilityEvent).filter(
                    ObservabilityEvent.agent_id == "revision",
                    ObservabilityEvent.event_type == "task_complete",
                )
            ),
            "exam_generations": _query_count(
                db.query(ObservabilityEvent).filter(
                    ObservabilityEvent.agent_id == "exam",
                    ObservabilityEvent.event_type == "task_complete",
                )
            ),
            "mcqs_generated": _query_count(
                db.query(ObservabilityEvent).filter(
                    ObservabilityEvent.agent_id == "exam",
                    ObservabilityEvent.summary.ilike("%mcq%"),
                )
            ),
            "mcqs_attempted": total_questions,
            "exam_attempts": total_tests,
            "avg_accuracy": round((total_correct / total_questions) * 100, 1) if total_questions else None,
            "grounded_answer_rate": quality["grounded_answer_rate"],
        },
        "operations": {
            "failed_requests": failed_requests,
            "avg_latency_ms": observed.get("avg_turn_latency_ms") or observed.get("avg_model_latency_ms"),
            "model_calls": observed.get("model_calls"),
            "tool_calls": observed.get("tool_calls"),
            "turns": observed.get("turns"),
            "estimated_input_tokens": _admin_int(total_input_tokens),
            "estimated_output_tokens": _admin_int(total_output_tokens),
            "estimated_cost_usd": round(_admin_float(total_cost), 8),
        },
        "data": {
            "total_study_data_indexed_bytes": data_registry["summary"]["total_file_size_bytes"],
            "total_study_data_indexed_label": data_registry["summary"]["total_file_size_label"],
            "total_chunks": data_registry["summary"]["chunks"],
            "total_embeddings": data_registry["summary"]["embeddings"],
            "last_data_sync": data_registry["summary"]["last_indexed_time"],
            "last_index_build": None,
            "vector_index_status": data_registry["vector_index"]["status"],
        },
        "no_data": {
            "has_users": bool(user_ids),
            "has_traces": bool(quality["samples"]),
            "has_vector_index": False,
        },
    }


def build_admin_students(db: Session, limit: int = 50) -> Dict[str, Any]:
    users = (
        db.query(UserProgress)
        .order_by(func.coalesce(UserProgress.last_active_date, date(1970, 1, 1)).desc(), UserProgress.xp.desc())
        .limit(limit)
        .all()
    )
    rows: List[Dict[str, Any]] = []
    for user in users:
        sessions = (
            db.query(TestHistory)
            .filter(TestHistory.user_id == user.user_id)
            .order_by(TestHistory.id.desc())
            .limit(5)
            .all()
        )
        weak_topics = (
            db.query(TopicPerformance)
            .filter(TopicPerformance.user_id == user.user_id, TopicPerformance.weak == True)  # noqa: E712
            .order_by(TopicPerformance.last_practiced.desc())
            .limit(5)
            .all()
        )
        recent_question = (
            db.query(AICoachInteraction)
            .filter(AICoachInteraction.user_id == user.user_id, AICoachInteraction.role == "user")
            .order_by(AICoachInteraction.created_at.desc())
            .first()
        )
        rows.append(
            {
                "user_id": user.user_id,
                "sessions": _query_count(db.query(TestHistory).filter(TestHistory.user_id == user.user_id)),
                "recent_sessions": [format_test_session(session) for session in sessions],
                "recent_question": recent_question.message[:220] if recent_question and recent_question.message else "",
                "topics_studied": sorted({session.topic for session in sessions if session.topic}),
                "exam_attempts": _admin_int(user.total_tests),
                "accuracy": round(float(user.accuracy), 1),
                "weak_topics": [
                    {
                        "topic": item.topic,
                        "accuracy": round(float(item.accuracy), 1),
                        "attempts": _admin_int(item.attempts),
                    }
                    for item in weak_topics
                ],
                "xp": _admin_int(user.xp),
                "streak": _admin_int(user.streak),
                "last_activity": user.last_active_date.isoformat() if user.last_active_date else None,
            }
        )

    return {"students": rows, "total": _query_count(db.query(UserProgress))}


def build_admin_system_health(db: Session) -> Dict[str, Any]:
    started = time.perf_counter()
    database_ok = check_db_health()
    db_latency_ms = round((time.perf_counter() - started) * 1000, 1)
    data_dir_ok = os.path.isdir(ADMIN_DATA_DIR)
    llm_configured = bool(os.getenv("GROQ_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY"))

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "services": [
            {"name": "Backend", "status": "live", "detail": "FastAPI process responded"},
            {"name": "Database", "status": "ready" if database_ok else "error", "latency_ms": db_latency_ms},
            {"name": "Auth", "status": "ready" if FIREBASE_ADMIN_READY else "degraded", "detail": FIREBASE_ADMIN_ERROR or "Firebase Admin ready"},
            {"name": "LLM provider", "status": "configured" if llm_configured else "missing_config", "detail": coach_settings.provider},
            {"name": "Embedding provider", "status": "not_configured", "detail": "No embedding provider endpoint is configured"},
            {"name": "Vector index/RAG", "status": "source_only", "detail": "Retriever uses platform source files; no vector index is connected"},
            {"name": "Storage", "status": "ready" if data_dir_ok else "error", "detail": ADMIN_DATA_DIR},
            {"name": "Knowledge graph", "status": "ready" if knowledge_graph.list_chapters() else "empty", "detail": f"{len(knowledge_graph.concepts)} concepts loaded"},
        ],
        "environment": {
            "version": "2.5.0-production-guardrails",
            "python_env": os.getenv("ENVIRONMENT") or os.getenv("APP_ENV") or "local",
            "rate_limits": RATE_LIMIT_ENABLED,
            "cors_origins_configured": len(ALLOWED_ORIGINS),
        },
        "metrics": {
            "avg_api_latency_ms": get_observability_summary(db).get("avg_turn_latency_ms"),
            "error_rate": model_failure_rate(db),
            "queue_jobs": None,
        },
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


def _interaction_metadata(row: AICoachInteraction) -> Dict[str, Any]:
    return row.metadata_json if isinstance(row.metadata_json, dict) else {}


def _interaction_session_id(row: AICoachInteraction) -> str:
    return str(_interaction_metadata(row).get("session_id") or "").strip()


def _conversation_title_from(message: str) -> str:
    compact = " ".join(str(message or "").split())
    if not compact:
        return "New study chat"
    return compact[:54] + ("..." if len(compact) > 54 else "")


def _serialize_coach_interaction_message(row: AICoachInteraction) -> Dict[str, Any]:
    metadata = _interaction_metadata(row)
    role = "coach" if row.role == "assistant" else "user"
    payload: Dict[str, Any] = {
        "role": role,
        "content": row.message or "",
        "timestamp": row.created_at.strftime("%H:%M") if row.created_at else "",
    }
    if role == "coach":
        answer_blocks = metadata.get("answer_blocks")
        sources = metadata.get("sources")
        if isinstance(answer_blocks, list):
            payload["blocks"] = answer_blocks
        if isinstance(sources, dict):
            payload["sources"] = sources
        orchestration = metadata.get("orchestration") if isinstance(metadata.get("orchestration"), dict) else {}
        if "socratic" in orchestration:
            payload["socratic"] = bool(orchestration.get("socratic"))
    return payload


def _conversation_metadata(rows: List[AICoachInteraction]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for row in rows:
        metadata = _interaction_metadata(row)
        for key in ("conversation_title", "conversation_pinned", "conversation_archived", "conversation_title_locked"):
            if key in metadata:
                merged[key] = metadata[key]
    return merged


def _serialize_coach_conversation(session_id: str, rows: List[AICoachInteraction]) -> Dict[str, Any]:
    sorted_rows = sorted(rows, key=lambda item: item.id or 0)
    metadata = _conversation_metadata(sorted_rows)
    first_user = next((row for row in sorted_rows if row.role == "user" and row.message), sorted_rows[0])
    last_row = sorted_rows[-1]
    title = str(metadata.get("conversation_title") or _conversation_title_from(first_user.message))
    last_metadata = _interaction_metadata(last_row)
    learning_context = last_metadata.get("learning_context") if isinstance(last_metadata.get("learning_context"), dict) else {}
    return {
        "id": session_id.replace(f"coach-{last_row.user_id}-", "", 1) if session_id.startswith(f"coach-{last_row.user_id}-") else session_id,
        "sessionId": session_id,
        "title": title,
        "updatedAt": last_row.created_at.isoformat() if last_row.created_at else datetime.utcnow().isoformat(),
        "chapter": str(learning_context.get("selected_chapter") or "Open tutor"),
        "topic": str(learning_context.get("selected_topic") or "Any subject"),
        "messages": [_serialize_coach_interaction_message(row) for row in sorted_rows],
        "pinned": bool(metadata.get("conversation_pinned")),
        "archived": bool(metadata.get("conversation_archived")),
        "titleLocked": bool(metadata.get("conversation_title_locked")),
        "messageCount": len(sorted_rows),
    }


def _conversation_rows_for_user(
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
        if _interaction_session_id(row)
        and session_id_belongs_to_user(_interaction_session_id(row), user_id)
    ]


def _group_conversation_rows(rows: List[AICoachInteraction]) -> Dict[str, List[AICoachInteraction]]:
    grouped: Dict[str, List[AICoachInteraction]] = defaultdict(list)
    for row in rows:
        grouped[_interaction_session_id(row)].append(row)
    return grouped


def _session_id_from_conversation_id(user_id: str, conversation_id: str) -> str:
    raw = str(conversation_id or "").strip()
    if session_id_belongs_to_user(raw, user_id):
        return raw
    return f"coach-{user_id}-{raw}"


def _apply_conversation_patch(row: AICoachInteraction, patch: CoachConversationPatch) -> None:
    metadata = dict(_interaction_metadata(row))
    if patch.title is not None:
        metadata["conversation_title"] = patch.title.strip() or "New study chat"
    if patch.pinned is not None:
        metadata["conversation_pinned"] = bool(patch.pinned)
    if patch.archived is not None:
        metadata["conversation_archived"] = bool(patch.archived)
    if patch.titleLocked is not None:
        metadata["conversation_title_locked"] = bool(patch.titleLocked)
    row.metadata_json = metadata


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
    user_id = require_owned_study_session(request.session_id, current_user)
    enforce_user_quota(user_id, "coach")
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
    user_id = require_owned_study_session(request.session_id, current_user)
    enforce_user_quota(user_id, "exam")
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
    user_id = require_owned_study_session(request.session_id, current_user)
    enforce_user_quota(user_id, "exam")
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
    user_id = require_authenticated_user_id(current_user)
    enforce_user_quota(user_id, "artifact")
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
    user_id = require_owned_study_session(request.session_id, current_user)
    enforce_user_quota(user_id, "agent")
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


@app.get("/coach/conversations/{user_id}")
def coach_conversations(
    user_id: str,
    include_archived: bool = Query(default=True),
    limit: int = Query(default=40, ge=1, le=80),
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    require_same_user_or_admin(user_id, current_user)
    coach = get_or_create_coach(db=db, user_id=user_id)
    rows = _conversation_rows_for_user(db, coach, user_id)
    grouped = _group_conversation_rows(rows)
    conversations = [
        _serialize_coach_conversation(session_id, session_rows)
        for session_id, session_rows in grouped.items()
    ]
    if not include_archived:
        conversations = [item for item in conversations if not item.get("archived")]
    conversations.sort(key=lambda item: (bool(item.get("pinned")), item.get("updatedAt") or ""), reverse=True)
    return {
        "user_id": user_id,
        "conversations": conversations[:limit],
    }


@app.get("/coach/conversations/{user_id}/{conversation_id}")
def coach_conversation_detail(
    user_id: str,
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    require_same_user_or_admin(user_id, current_user)
    session_id = _session_id_from_conversation_id(user_id, conversation_id)
    if not is_backend_admin(current_user) and not session_id_belongs_to_user(session_id, user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed for this conversation")

    coach = get_or_create_coach(db=db, user_id=user_id)
    rows = [
        row for row in _conversation_rows_for_user(db, coach, user_id, limit=800)
        if _interaction_session_id(row) == session_id
    ]
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    return {
        "user_id": user_id,
        "conversation": _serialize_coach_conversation(session_id, rows),
    }


@app.patch("/coach/conversations/{user_id}/{conversation_id}")
def update_coach_conversation(
    user_id: str,
    conversation_id: str,
    patch: CoachConversationPatch,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    require_same_user_or_admin(user_id, current_user)
    session_id = _session_id_from_conversation_id(user_id, conversation_id)
    if not is_backend_admin(current_user) and not session_id_belongs_to_user(session_id, user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed for this conversation")

    coach = get_or_create_coach(db=db, user_id=user_id)
    rows = [
        row for row in _conversation_rows_for_user(db, coach, user_id, limit=800)
        if _interaction_session_id(row) == session_id
    ]
    if not rows:
        return {
            "user_id": user_id,
            "conversation": None,
            "status": "no_saved_messages",
        }
    for row in rows:
        _apply_conversation_patch(row, patch)
    db.commit()
    return {
        "user_id": user_id,
        "conversation": _serialize_coach_conversation(session_id, rows),
        "status": "updated",
    }


@app.delete("/coach/conversations/{user_id}/{conversation_id}")
def delete_coach_conversation(
    user_id: str,
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    require_same_user_or_admin(user_id, current_user)
    session_id = _session_id_from_conversation_id(user_id, conversation_id)
    if not is_backend_admin(current_user) and not session_id_belongs_to_user(session_id, user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed for this conversation")

    coach = get_or_create_coach(db=db, user_id=user_id)
    rows = [
        row for row in _conversation_rows_for_user(db, coach, user_id, limit=800)
        if _interaction_session_id(row) == session_id
    ]
    deleted = len(rows)
    for row in rows:
        db.delete(row)
    db.commit()
    return {
        "user_id": user_id,
        "session_id": session_id,
        "deleted": deleted,
        "status": "deleted",
    }


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
    current_user: Dict[str, Any] = Depends(verify_firebase_user),
):
    token_uid = require_authenticated_user_id(current_user)
    target_uid = request.user_id or token_uid
    if request.user_id:
        require_same_user_or_admin(request.user_id, current_user)

    if not is_backend_admin(current_user) and not session_id_belongs_to_user(request.session_id, target_uid):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Session reset is allowed only for your own learning session.",
        )

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


def _start_of_today() -> datetime:
    return datetime.combine(date.today(), datetime_time.min)


def _safe_count(query) -> int:
    try:
        return int(query.count() or 0)
    except Exception:
        return 0


def _safe_scalar(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _metric(value: Optional[float | int | str], *, source: str = "db", unit: str = "", note: str = "") -> Dict[str, Any]:
    return {
        "value": value,
        "source": source,
        "unit": unit,
        "note": note,
    }


def _serialize_audit_log(row: AdminAuditLog) -> Dict[str, Any]:
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "actor_uid": row.actor_uid or "",
        "actor_email": row.actor_email or "",
        "admin_uid": row.actor_uid or "",
        "admin_email": row.actor_email or "",
        "action": row.action or "",
        "target_type": row.target_type or "",
        "target_id": row.target_id or "",
        "status": row.status or "",
        "metadata": row.metadata_json or {},
    }


def _record_admin_audit(
    db: Session,
    *,
    current_admin: Dict[str, Any],
    action: str,
    target_type: str = "console",
    target_id: str = "",
    status_value: str = "success",
    metadata: Optional[Dict[str, Any]] = None,
    request: Optional[Request] = None,
) -> AdminAuditLog:
    row = AdminAuditLog(
        actor_uid=str(current_admin.get("uid") or ""),
        actor_email=str(current_admin.get("email") or "").lower(),
        action=str(action or "")[:120],
        target_type=str(target_type or "console")[:80],
        target_id=str(target_id or "")[:220],
        status=str(status_value or "success")[:40],
        ip_address=str(request.client.host if request and request.client else ""),
        user_agent=str(request.headers.get("user-agent") if request else "")[:500],
        metadata_json=metadata or {},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _trace_payload(row: ModelToolTrace) -> Dict[str, Any]:
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "turn_id": row.turn_id or "",
        "session_id": row.session_id or "",
        "user_id": row.user_id or "",
        "trace_type": row.trace_type or "",
        "name": row.name or "",
        "provider": row.provider or "",
        "model": row.model or "",
        "status": row.status or "",
        "latency_ms": int(row.latency_ms or 0),
        "estimated_input_tokens": int(row.estimated_input_tokens or 0),
        "estimated_output_tokens": int(row.estimated_output_tokens or 0),
        "estimated_cost_usd": float(row.estimated_cost_usd or 0.0),
        "metadata": row.metadata_json or {},
    }


def _student_payload(row: UserProgress) -> Dict[str, Any]:
    return {
        "user_id": row.user_id,
        "xp": int(row.xp or 0),
        "level": int(row.level or 1),
        "streak": int(row.streak or 0),
        "total_tests": int(row.total_tests or 0),
        "total_questions": int(row.total_questions or 0),
        "total_correct": int(row.total_correct or 0),
        "accuracy": round(float(row.accuracy or 0.0), 1),
        "last_active_date": row.last_active_date.isoformat() if row.last_active_date else None,
        "focus_score": round(float(row.focus_score or 0.0), 1),
        "consistency_index": round(float(row.consistency_index or 0.0), 1),
        "learning_efficiency": round(float(row.learning_efficiency or 0.0), 1),
    }


ADMIN_AGENT_DEFINITIONS = [
    {
        "agent_id": "orchestrator",
        "display_name": "Supervisor Orchestrator",
        "role": "Routes tasks, policies, traces, and handoffs.",
        "keywords": ["orchestrator", "router", "route", "gateway", "handoff"],
    },
    {
        "agent_id": "tutor",
        "display_name": "Subject Tutor",
        "role": "Answers doubts with grounded study context.",
        "keywords": ["tutor", "answer", "explain", "study", "doubt", "coach"],
    },
    {
        "agent_id": "revision",
        "display_name": "Revision Specialist",
        "role": "Generates notes, recall, summaries, and quick review.",
        "keywords": ["revision", "revise", "summary", "notes", "recall"],
    },
    {
        "agent_id": "exam",
        "display_name": "Exam Generator",
        "role": "Builds MCQs, tests, probable questions, and scoring.",
        "keywords": ["exam", "mcq", "test", "quiz", "question"],
    },
    {
        "agent_id": "planner",
        "display_name": "Study Planner",
        "role": "Chooses next best action from progress and weak topics.",
        "keywords": ["planner", "plan", "mission", "path", "next"],
    },
    {
        "agent_id": "coach",
        "display_name": "Personal AI Coach",
        "role": "Maintains continuity, memory, motivation, and progress.",
        "keywords": ["coach", "memory", "profile", "daily", "motivation"],
    },
]


def _agent_definition(agent_id: str) -> Dict[str, Any]:
    return next((item for item in ADMIN_AGENT_DEFINITIONS if item["agent_id"] == agent_id), {
        "agent_id": agent_id,
        "display_name": agent_id.replace("_", " ").title(),
        "role": "Observed runtime agent.",
        "keywords": [agent_id],
    })


def _agent_match_text(row: Any) -> str:
    metadata = _safe_json_dict(getattr(row, "metadata_json", {}) or getattr(row, "data_json", {}) or {})
    try:
        metadata_text = json.dumps(metadata, default=str)
    except TypeError:
        metadata_text = str(metadata)
    pieces = [
        getattr(row, "agent_id", ""),
        getattr(row, "event_type", ""),
        getattr(row, "summary", ""),
        getattr(row, "name", ""),
        getattr(row, "trace_type", ""),
        getattr(row, "provider", ""),
        getattr(row, "model", ""),
        metadata_text,
    ]
    return " ".join(str(piece or "") for piece in pieces).lower()


def _matches_agent(row: Any, agent_id: str) -> bool:
    text_value = _agent_match_text(row)
    definition = _agent_definition(agent_id)
    return any(str(keyword).lower() in text_value for keyword in definition.get("keywords", [agent_id]))


def _safe_created_at(row: Any) -> Optional[datetime]:
    value = getattr(row, "created_at", None) or getattr(row, "timestamp", None)
    return value if isinstance(value, datetime) else None


def _average(values: List[float]) -> Optional[float]:
    clean = [value for value in values if value is not None]
    return round(sum(clean) / len(clean), 3) if clean else None


def _quality_scores_from_traces(rows: List[ModelToolTrace]) -> List[float]:
    scores: List[float] = []
    for row in rows:
        metadata = _safe_json_dict(row.metadata_json)
        quality = _safe_json_dict(metadata.get("quality"))
        score = quality.get("score") or quality.get("overall_score")
        if score is None and "passed" in quality:
            score = 1.0 if quality.get("passed") else 0.0
        if score is not None:
            numeric = _admin_float(score)
            scores.append(numeric if numeric <= 1 else numeric / 100)
    return scores


def _agent_source_usage(rows: List[ModelToolTrace]) -> List[Dict[str, Any]]:
    usage: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        metadata = _safe_json_dict(row.metadata_json)
        retrieval = _safe_json_dict(metadata.get("retrieval"))
        source = (
            retrieval.get("source")
            or retrieval.get("section_id")
            or metadata.get("source")
            or metadata.get("section_id")
            or "runtime_trace"
        )
        key = str(source)
        if key not in usage:
            usage[key] = {"source": key, "chunks": 0, "rows": 0}
        usage[key]["rows"] += 1
        usage[key]["chunks"] += _admin_int(retrieval.get("paragraphs_found") or retrieval.get("chunks") or 0)
    return sorted(usage.values(), key=lambda item: (item["chunks"], item["rows"]), reverse=True)[:6]


def _agent_learning_signal(quality_delta: Optional[float], latency_delta_ms: Optional[float], errors: int, runs_24h: int) -> str:
    if runs_24h == 0:
        return "needs_data"
    if errors > 0 and quality_delta is not None and quality_delta < -0.02:
        return "regressing"
    if quality_delta is not None and quality_delta > 0.02:
        return "improving"
    if latency_delta_ms is not None and latency_delta_ms < -250:
        return "faster"
    return "stable"


def build_admin_agent_intelligence(db: Session, agent_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    now = datetime.utcnow()
    cutoff_24h = now - timedelta(hours=24)
    previous_cutoff = now - timedelta(hours=48)
    trace_rows = db.query(ModelToolTrace).order_by(ModelToolTrace.created_at.desc(), ModelToolTrace.id.desc()).limit(3500).all()
    event_rows = db.query(ObservabilityEvent).order_by(ObservabilityEvent.created_at.desc(), ObservabilityEvent.id.desc()).limit(3500).all()
    interaction_rows = db.query(AICoachInteraction).order_by(AICoachInteraction.created_at.desc(), AICoachInteraction.id.desc()).limit(2000).all()

    bus_agents = {str(row.get("agent_id") or row.get("id") or ""): row for row in agent_rows if isinstance(row, dict)}
    agent_ids = list(dict.fromkeys([item["agent_id"] for item in ADMIN_AGENT_DEFINITIONS] + list(bus_agents.keys())))
    enriched: List[Dict[str, Any]] = []

    for agent_id in agent_ids:
        if not agent_id:
            continue
        definition = _agent_definition(agent_id)
        bus_agent = bus_agents.get(agent_id, {})
        matched_traces = [row for row in trace_rows if _matches_agent(row, agent_id)]
        matched_events = [row for row in event_rows if _matches_agent(row, agent_id)]
        current_traces = [row for row in matched_traces if (_safe_created_at(row) or now) >= cutoff_24h]
        previous_traces = [
            row for row in matched_traces
            if previous_cutoff <= (_safe_created_at(row) or now) < cutoff_24h
        ]
        current_events = [row for row in matched_events if (_safe_created_at(row) or now) >= cutoff_24h]
        previous_events = [
            row for row in matched_events
            if previous_cutoff <= (_safe_created_at(row) or now) < cutoff_24h
        ]
        success_rows = [row for row in matched_traces if str(row.status or "").lower() in {"success", "skipped"}]
        error_rows = [row for row in matched_traces if str(row.status or "").lower() not in {"success", "skipped"}]
        latency_values = [_admin_float(row.latency_ms) for row in matched_traces if row.latency_ms is not None]
        current_latency = _average([_admin_float(row.latency_ms) for row in current_traces if row.latency_ms is not None])
        previous_latency = _average([_admin_float(row.latency_ms) for row in previous_traces if row.latency_ms is not None])
        quality_current = _average(_quality_scores_from_traces(current_traces))
        quality_previous = _average(_quality_scores_from_traces(previous_traces))

        if agent_id in {"coach", "tutor"} and quality_current is None:
            current_quality_rows = [
                float(row.quality_score or 0)
                for row in interaction_rows
                if row.created_at and row.created_at >= cutoff_24h and row.quality_score is not None and float(row.quality_score or 0) > 0
            ]
            quality_current = _average(current_quality_rows)
        if agent_id in {"coach", "tutor"} and quality_previous is None:
            previous_quality_rows = [
                float(row.quality_score or 0)
                for row in interaction_rows
                if row.created_at and previous_cutoff <= row.created_at < cutoff_24h and row.quality_score is not None and float(row.quality_score or 0) > 0
            ]
            quality_previous = _average(previous_quality_rows)

        quality_delta = round(quality_current - quality_previous, 3) if quality_current is not None and quality_previous is not None else None
        latency_delta = round(current_latency - previous_latency, 1) if current_latency is not None and previous_latency is not None else None
        total_requests = max(_admin_int(bus_agent.get("total_requests")), len(matched_traces) + len(matched_events))
        total_errors = max(_admin_int(bus_agent.get("total_errors")), len(error_rows))
        success_rate = round((len(success_rows) / len(matched_traces)) * 100, 1) if matched_traces else _admin_float(bus_agent.get("success_rate"))
        avg_latency = round(sum(latency_values) / len(latency_values), 1) if latency_values else _admin_float(bus_agent.get("avg_latency_ms"))
        last_activity = max(
            [item for item in [_safe_created_at(row) for row in matched_traces + matched_events] if item],
            default=None,
        )
        input_tokens = sum(_admin_int(row.estimated_input_tokens) for row in matched_traces)
        output_tokens = sum(_admin_int(row.estimated_output_tokens) for row in matched_traces)
        estimated_cost = round(sum(_admin_float(row.estimated_cost_usd) for row in matched_traces), 8)
        sessions = {row.session_id for row in matched_traces if row.session_id}
        model_calls = len([row for row in matched_traces if row.trace_type == "model"])
        tool_calls = len([row for row in matched_traces if row.trace_type == "tool"])
        turn_calls = len([row for row in matched_traces if row.trace_type == "turn"])
        memory_rows = 0
        if agent_id == "coach":
            memory_rows = _safe_count(db.query(AICoachMemory)) + _safe_count(db.query(AgentChatMemory))

        enriched.append({
            **bus_agent,
            "agent_id": agent_id,
            "display_name": str(bus_agent.get("display_name") or bus_agent.get("name") or definition["display_name"]),
            "role": str(bus_agent.get("role") or definition["role"]),
            "status": str(bus_agent.get("status") or ("observing" if total_requests else "not_reporting")),
            "health": str(bus_agent.get("health") or ("healthy" if total_errors == 0 and total_requests else "idle")),
            "current_task": str(bus_agent.get("current_task") or "Watching traces, data usage, model calls, and quality drift."),
            "last_activity": _admin_iso(last_activity) if last_activity else str(bus_agent.get("last_activity") or ""),
            "total_requests": total_requests,
            "total_errors": total_errors,
            "total_success": max(_admin_int(bus_agent.get("total_success")), len(success_rows)),
            "avg_latency_ms": avg_latency,
            "last_quality_score": quality_current if quality_current is not None else _admin_float(bus_agent.get("last_quality_score")),
            "success_rate": success_rate,
            "data_intake": {
                "trace_rows": len(matched_traces),
                "event_rows": len(matched_events),
                "new_trace_rows_24h": len(current_traces),
                "previous_trace_rows_24h": len(previous_traces),
                "new_event_rows_24h": len(current_events),
                "previous_event_rows_24h": len(previous_events),
                "model_calls": model_calls,
                "tool_calls": tool_calls,
                "turns": turn_calls,
                "sessions": len(sessions),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "estimated_cost_usd": estimated_cost,
                "memory_rows": memory_rows,
                "sources": _agent_source_usage(matched_traces),
            },
            "evolution": {
                "version": str(bus_agent.get("version") or f"observed-v{1 + min(8, total_requests // 250)}"),
                "runs_total": len(matched_traces),
                "runs_24h": len(current_traces),
                "runs_previous_24h": len(previous_traces),
                "events_24h": len(current_events),
                "quality_score_current": quality_current,
                "quality_score_previous": quality_previous,
                "quality_delta": quality_delta,
                "latency_current_ms": current_latency,
                "latency_previous_ms": previous_latency,
                "latency_delta_ms": latency_delta,
                "success_rate": success_rate,
                "learning_signal": _agent_learning_signal(quality_delta, latency_delta, len(error_rows), len(current_traces)),
                "trained_on_samples": len(matched_traces) + len(matched_events) + memory_rows,
                "new_data_rows": len(current_traces) + len(current_events),
                "historical_data_rows": max(0, len(matched_traces) + len(matched_events) - len(current_traces) - len(current_events)),
            },
        })

    return enriched


def build_admin_data_intake(
    db: Session,
    *,
    data_registry: Dict[str, Any],
    model_registry: Dict[str, Any],
    chapters: List[ContentChapter],
    recent_content_jobs: List[ContentIngestionJob],
) -> Dict[str, Any]:
    now = datetime.utcnow()
    cutoff_24h = now - timedelta(hours=24)
    cutoff_7d = now - timedelta(days=7)
    total_traces = _safe_count(db.query(ModelToolTrace))
    traces_24h = _safe_count(db.query(ModelToolTrace).filter(ModelToolTrace.created_at >= cutoff_24h))
    traces_7d = _safe_count(db.query(ModelToolTrace).filter(ModelToolTrace.created_at >= cutoff_7d))
    total_events = _safe_count(db.query(ObservabilityEvent))
    events_24h = _safe_count(db.query(ObservabilityEvent).filter(ObservabilityEvent.created_at >= cutoff_24h))
    events_7d = _safe_count(db.query(ObservabilityEvent).filter(ObservabilityEvent.created_at >= cutoff_7d))
    total_chunks = _safe_count(db.query(ContentChunk))
    summary = data_registry.get("summary", {})
    current_model = model_registry.get("current", {})
    total_input_tokens = int(_safe_scalar(db.query(func.sum(ModelToolTrace.estimated_input_tokens)).scalar()))
    total_output_tokens = int(_safe_scalar(db.query(func.sum(ModelToolTrace.estimated_output_tokens)).scalar()))
    total_cost = round(_safe_scalar(db.query(func.sum(ModelToolTrace.estimated_cost_usd)).scalar()), 8)
    latest_trace = db.query(ModelToolTrace).order_by(ModelToolTrace.created_at.desc(), ModelToolTrace.id.desc()).first()
    latest_event = db.query(ObservabilityEvent).order_by(ObservabilityEvent.created_at.desc(), ObservabilityEvent.id.desc()).first()

    return {
        "generated_at": now.isoformat(),
        "totals": {
            "study_materials": _admin_int(summary.get("study_materials")),
            "study_material_bytes": _admin_int(summary.get("total_file_size_bytes")),
            "study_material_label": summary.get("total_file_size_label") or "0 B",
            "content_chapters": len(chapters),
            "approved_chapters": len([chapter for chapter in chapters if chapter.status in {"approved", "published"}]),
            "content_chunks": total_chunks or _admin_int(summary.get("chunks")),
            "topics": _admin_int(summary.get("topics")),
            "trace_rows": total_traces,
            "event_rows": total_events,
            "model_calls": _safe_count(db.query(ModelToolTrace).filter(ModelToolTrace.trace_type == "model")),
            "tool_calls": _safe_count(db.query(ModelToolTrace).filter(ModelToolTrace.trace_type == "tool")),
            "turns": _safe_count(db.query(ModelToolTrace).filter(ModelToolTrace.trace_type == "turn")),
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "estimated_cost_usd": total_cost,
            "model_versions": len(model_registry.get("versions", [])),
            "training_samples": _admin_int(current_model.get("samples")) or total_traces,
        },
        "freshness": {
            "traces_24h": traces_24h,
            "traces_7d": traces_7d,
            "events_24h": events_24h,
            "events_7d": events_7d,
            "historical_traces": max(0, total_traces - traces_24h),
            "historical_events": max(0, total_events - events_24h),
            "latest_trace_at": _admin_iso(latest_trace.created_at) if latest_trace else None,
            "latest_event_at": _admin_iso(latest_event.created_at) if latest_event else None,
            "last_indexed_time": summary.get("last_indexed_time"),
        },
        "pipeline": {
            **data_registry.get("progress", {}),
            "rag_index_version": hashlib_data_version(),
            "prompt_version": current_model.get("prompt_version"),
            "agent_workflow_version": current_model.get("agent_workflow_version"),
        },
        "source_coverage": data_registry.get("source_coverage", []),
        "recent_jobs": [
            {
                "job_id": job.job_id,
                "job_type": job.job_type,
                "status": job.status,
                "source_path": job.source_path,
                "created_at": job.created_at.isoformat() if job.created_at else "",
                "summary": job.summary or {},
            }
            for job in recent_content_jobs
        ],
    }


def build_admin_console_payload(db: Session) -> Dict[str, Any]:
    today_start = _start_of_today()
    cutoff_24h = datetime.utcnow() - timedelta(hours=24)

    interactions_today = db.query(AICoachInteraction).filter(AICoachInteraction.created_at >= today_start)
    interactions_24h = db.query(AICoachInteraction).filter(AICoachInteraction.created_at >= cutoff_24h)
    tests_today = db.query(TestHistory).filter(TestHistory.date == date.today())
    traces_24h = db.query(ModelToolTrace).filter(ModelToolTrace.created_at >= cutoff_24h)
    events_24h = db.query(ObservabilityEvent).filter(ObservabilityEvent.created_at >= cutoff_24h)

    active_session_ids = {
        _interaction_session_id(row)
        for row in interactions_today.order_by(AICoachInteraction.id.desc()).limit(2500).all()
        if _interaction_session_id(row)
    }
    test_users_today = {
        user_id for (user_id,) in tests_today.with_entities(TestHistory.user_id).distinct().all() if user_id
    }
    coach_users_today = {
        user_id for (user_id,) in interactions_today.with_entities(AICoachInteraction.user_id).distinct().all() if user_id
    }

    total_questions_today = _safe_count(interactions_today.filter(AICoachInteraction.role == "user"))
    exam_generations_today = _safe_count(tests_today.filter(TestHistory.session_type == "exam"))
    mcq_attempted_today = int(tests_today.with_entities(func.sum(TestHistory.total_questions)).scalar() or 0)
    avg_accuracy_today = tests_today.with_entities(func.avg(TestHistory.accuracy_rate)).scalar()
    quota_today = int(
        db.query(func.sum(DailyQuotaUsage.count))
        .filter(DailyQuotaUsage.quota_date == date.today())
        .scalar()
        or 0
    )
    estimated_cost_24h = round(_safe_scalar(traces_24h.with_entities(func.sum(ModelToolTrace.estimated_cost_usd)).scalar()), 8)
    total_tokens_24h = int(
        _safe_scalar(traces_24h.with_entities(func.sum(ModelToolTrace.estimated_input_tokens + ModelToolTrace.estimated_output_tokens)).scalar())
    )
    avg_latency_24h = traces_24h.with_entities(func.avg(ModelToolTrace.latency_ms)).scalar()
    failures_24h = _safe_count(events_24h.filter(ObservabilityEvent.severity.in_(["error", "critical"]))) + _safe_count(
        traces_24h.filter(ModelToolTrace.status.notin_(["success", "skipped"]))
    )

    turn_traces = traces_24h.filter(ModelToolTrace.trace_type == "turn").order_by(ModelToolTrace.id.desc()).limit(500).all()
    grounded_candidates = [
        row for row in turn_traces
        if "retrieval" in (row.metadata_json or {}) or "ground" in json.dumps(row.metadata_json or {}).lower()
    ]
    grounded_rate = round((len(grounded_candidates) / len(turn_traces)) * 100, 1) if turn_traces else None

    quality_scores = [
        float(score)
        for (score,) in interactions_24h.with_entities(AICoachInteraction.quality_score).all()
        if score is not None and float(score or 0) > 0
    ]
    avg_quality = round(sum(quality_scores) / len(quality_scores), 3) if quality_scores else None
    low_quality_count = len([score for score in quality_scores if score < 0.65])
    slow_trace_count = _safe_count(traces_24h.filter(ModelToolTrace.latency_ms >= 7000))
    failed_mcq_events = _safe_count(
        events_24h
        .filter(ObservabilityEvent.severity.in_(["error", "critical"]))
        .filter(ObservabilityEvent.summary.ilike("%mcq%"))
    )
    missing_source_events = _safe_count(events_24h.filter(ObservabilityEvent.summary.ilike("%source%")))
    fallback_events = _safe_count(events_24h.filter(ObservabilityEvent.summary.ilike("%fallback%")))
    empty_retrieval_events = _safe_count(events_24h.filter(ObservabilityEvent.summary.ilike("%retrieval%")).filter(ObservabilityEvent.summary.ilike("%empty%")))

    chapters = db.query(ContentChapter).all()
    published_count = len([chapter for chapter in chapters if chapter.status in {"approved", "published"}])
    content_quality = round(
        sum(float(chapter.coverage_score or 0.0) for chapter in chapters) / len(chapters),
        3,
    ) if chapters else None
    recent_content_jobs = db.query(ContentIngestionJob).order_by(ContentIngestionJob.id.desc()).limit(8).all()

    system_stats = event_bus.get_system_stats()
    observability = get_observability_summary(db)
    data_registry = build_admin_data_registry(db)
    model_registry = build_admin_model_registry(db)
    agent_rows = build_admin_agent_intelligence(db, event_bus.get_all_agents())
    data_intake = build_admin_data_intake(
        db,
        data_registry=data_registry,
        model_registry=model_registry,
        chapters=chapters,
        recent_content_jobs=recent_content_jobs,
    )

    recent_traces = db.query(ModelToolTrace).order_by(ModelToolTrace.created_at.desc(), ModelToolTrace.id.desc()).limit(80).all()
    recent_students = db.query(UserProgress).order_by(UserProgress.last_active_date.desc(), UserProgress.xp.desc()).limit(20).all()
    recent_audits = db.query(AdminAuditLog).order_by(AdminAuditLog.id.desc()).limit(40).all()
    recent_events = get_recent_observability_events(db, limit=80)

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "environment": os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or os.getenv("ENV") or "development",
        "header": {
            "system_status": system_stats.get("uptime_status") or "online",
            "backend_ready": check_db_health(),
            "database_status": "ready" if check_db_health() else "degraded",
            "auth_status": "configured" if FIREBASE_ADMIN_READY else "not_configured",
            "llm_status": "configured" if os.getenv("GROQ_API_KEY") else "not_configured",
            "rag_status": "ready" if published_count else "waiting_for_approved_content",
            "last_sync_time": datetime.utcnow().isoformat(),
        },
        "overview": {
            "total_users": _metric(_safe_count(db.query(UserProgress)), source="user_progress"),
            "active_students_today": _metric(len(coach_users_today | test_users_today), source="coach_interactions+test_history"),
            "active_sessions": _metric(len(active_session_ids), source="coach_interaction_metadata"),
            "questions_asked": _metric(total_questions_today, source="ai_coach_interactions", unit="today"),
            "revision_generations": _metric(None, source="not_instrumented", note="Revision generation needs a dedicated event counter."),
            "exam_generations": _metric(exam_generations_today, source="test_history", unit="today"),
            "mcqs_attempted": _metric(mcq_attempted_today, source="test_history", unit="today"),
            "average_accuracy": _metric(round(float(avg_accuracy_today), 1) if avg_accuracy_today is not None else None, source="test_history", unit="%"),
            "api_llm_usage": _metric(quota_today, source="daily_quota_usage", unit="requests_today"),
            "llm_tokens": _metric(total_tokens_24h, source="model_tool_traces", unit="24h"),
            "llm_cost": _metric(estimated_cost_24h, source="model_tool_traces", unit="usd_24h"),
            "errors_failures": _metric(failures_24h, source="observability_events+model_tool_traces", unit="24h"),
            "avg_latency": _metric(round(float(avg_latency_24h), 1) if avg_latency_24h is not None else None, source="model_tool_traces", unit="ms_24h"),
            "grounded_answer_rate": _metric(grounded_rate, source="model_tool_traces", unit="%"),
        },
        "agents": agent_rows,
        "system": {
            "event_bus": system_stats,
            "observability": observability,
            "services": [
                {"name": "Backend live/ready", "status": "ready" if check_db_health() else "degraded"},
                {"name": "Database", "status": "ready" if check_db_health() else "degraded"},
                {"name": "Firebase Auth", "status": "configured" if FIREBASE_ADMIN_READY else "not_configured"},
                {"name": "LLM provider", "status": "configured" if os.getenv("GROQ_API_KEY") else "not_configured"},
                {"name": "RAG/content index", "status": "ready" if published_count else "waiting_for_approved_content"},
                {"name": "Storage", "status": "ready" if os.path.isdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")) else "missing"},
            ],
        },
        "quality": {
            "avg_quality_score": avg_quality,
            "low_quality_answers": low_quality_count,
            "hallucination_risk": missing_source_events + fallback_events,
            "missing_sources": missing_source_events,
            "failed_mcq_generation": failed_mcq_events,
            "empty_retrieval": empty_retrieval_events,
            "slow_responses": slow_trace_count,
            "fallback_used": fallback_events,
            "badges": {
                "grounded": len(grounded_candidates),
                "verified": len([score for score in quality_scores if score >= 0.82]),
                "needs_review": low_quality_count + missing_source_events,
                "failed": failures_24h,
                "fallback_used": fallback_events,
            },
        },
        "content": {
            "chapters_total": len(chapters),
            "approved_or_published": published_count,
            "coverage_score_avg": content_quality,
            "status_counts": {
                status_name: len([chapter for chapter in chapters if chapter.status == status_name])
                for status_name in sorted({chapter.status for chapter in chapters if chapter.status})
            },
            "recent_jobs": [
                {
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                    "status": job.status,
                    "source_path": job.source_path,
                    "created_at": job.created_at.isoformat() if job.created_at else "",
                    "summary": job.summary or {},
                }
                for job in recent_content_jobs
            ],
        },
        "students": [_student_payload(row) for row in recent_students],
        "traces": [_trace_payload(row) for row in recent_traces],
        "events": recent_events,
        "audit": [_serialize_audit_log(row) for row in recent_audits],
        "data_intake": data_intake,
        "data_registry": data_registry,
        "model_registry": model_registry,
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
def admin_me(
    db: Session = Depends(get_db),
    current_admin: Dict[str, Any] = Depends(require_admin),
):
    record_admin_audit(
        db,
        current_admin,
        action="admin_login",
        status_value="success",
        metadata={"source": "admin_console"},
    )
    return {
        "uid": current_admin.get("uid"),
        "email": current_admin.get("email"),
        "phone": current_admin.get("phone_number"),
        "role": "admin",
        "verified": True,
    }

@app.get("/admin/console")
def admin_console(
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_founder_admin),
):
    return build_admin_console_payload(db)


@app.get("/admin/audit")
def admin_audit_logs(
    limit: int = Query(default=80, ge=1, le=300),
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_founder_admin),
):
    rows = db.query(AdminAuditLog).order_by(AdminAuditLog.id.desc()).limit(limit).all()
    return {"audit": [_serialize_audit_log(row) for row in rows]}


@app.post("/admin/audit")
def admin_record_audit(
    payload: AdminAuditRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: Dict[str, Any] = Depends(require_founder_admin),
):
    row = _record_admin_audit(
        db,
        current_admin=current_admin,
        action=payload.action,
        target_type=payload.target_type,
        target_id=payload.target_id,
        status_value=payload.status,
        metadata=payload.metadata,
        request=request,
    )
    return {"audit": _serialize_audit_log(row), "status": "recorded"}


@app.get("/admin/content/folder-contract")
def admin_content_folder_contract(_current_admin: Dict[str, Any] = Depends(require_admin)):
    RAW_NCERT_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "default_root": str(RAW_NCERT_DIR),
        "expected_structure": [
            "backend/data/raw/ncert/class_10/science/chapter_01_chemical_reactions.pdf",
            "backend/data/raw/ncert/class_11/chemistry/chapter_01_some_basic_concepts_of_chemistry.pdf",
            "backend/data/raw/ncert/class_12/physics/chapter_02_electrostatic_potential.pdf",
        ],
        "source_of_truth": "NCERT PDF text",
        "approval_rule": "Only approved or published chapters are used by Study Lab retrieval.",
        "statuses": [
            "uploaded",
            "indexed",
            "json_generated",
            "validated",
            "needs_review",
            "approved",
            "published",
            "failed",
        ],
    }


@app.post("/admin/content/ingest-folder")
def admin_content_ingest_folder(
    payload: ContentIngestFolderRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: Dict[str, Any] = Depends(require_admin),
):
    try:
        result = ingest_pdf_folder(
            db,
            root_path=payload.root_path,
            replace=payload.replace_existing_extraction,
        )
        _record_admin_audit(
            db,
            current_admin=current_admin,
            action="content_ingest_folder",
            target_type="content_folder",
            target_id=payload.root_path or str(RAW_NCERT_DIR),
            metadata={"replace_existing_extraction": payload.replace_existing_extraction},
            request=request,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/admin/content/chapters")
def admin_content_chapters(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    return {"chapters": list_content_chapters(db, status=status_filter)}


@app.get("/admin/content/report/{chapter_id}")
def admin_content_report(
    chapter_id: int,
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    try:
        return chapter_report(db, chapter_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/admin/content/import-concepts/{chapter_id}")
def admin_content_import_concepts(
    chapter_id: int,
    payload: ContentConceptImportRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: Dict[str, Any] = Depends(require_admin),
):
    try:
        chapter = import_concepts_for_chapter(
            db,
            chapter_id,
            payload.concepts,
            replace=payload.replace_existing,
        )
        db.commit()
        _record_admin_audit(
            db,
            current_admin=current_admin,
            action="content_import_concepts",
            target_type="chapter",
            target_id=str(chapter_id),
            metadata={"concept_count": len(payload.concepts), "replace_existing": payload.replace_existing},
            request=request,
        )
        return serialize_chapter(chapter)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/admin/content/generate-json/{chapter_id}")
def admin_content_generate_json(
    chapter_id: int,
    payload: ContentGenerateConceptsRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: Dict[str, Any] = Depends(require_admin),
):
    try:
        chapter = generate_concepts_for_chapter(
            db,
            chapter_id,
            replace=payload.replace_existing,
            max_batch_chars=payload.max_batch_chars,
        )
        db.commit()
        _record_admin_audit(
            db,
            current_admin=current_admin,
            action="content_generate_json",
            target_type="chapter",
            target_id=str(chapter_id),
            metadata={"replace_existing": payload.replace_existing, "max_batch_chars": payload.max_batch_chars},
            request=request,
        )
        return serialize_chapter(chapter)
    except RuntimeError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, json.JSONDecodeError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/admin/content/approve/{chapter_id}")
def admin_content_approve(
    chapter_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: Dict[str, Any] = Depends(require_admin),
):
    try:
        chapter = approve_chapter(db, chapter_id, approved_by=str(current_admin.get("uid") or "admin"))
        db.commit()
        _record_admin_audit(
            db,
            current_admin=current_admin,
            action="content_approve_chapter",
            target_type="chapter",
            target_id=str(chapter_id),
            request=request,
        )
        return serialize_chapter(chapter)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/admin/content/publish/{chapter_id}")
def admin_content_publish(
    chapter_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: Dict[str, Any] = Depends(require_admin),
):
    try:
        chapter = publish_chapter(db, chapter_id, published_by=str(current_admin.get("uid") or "admin"))
        db.commit()
        _record_admin_audit(
            db,
            current_admin=current_admin,
            action="content_publish_chapter",
            target_type="chapter",
            target_id=str(chapter_id),
            request=request,
        )
        return serialize_chapter(chapter)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/admin/overview")
def admin_overview(
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    return build_admin_overview(db)


@app.get("/admin/agents")
def admin_get_agents(
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    traces = build_admin_traces(db, limit=120)
    recent_by_agent: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for event in get_recent_observability_events(db, limit=200):
        recent_by_agent[str(event.get("agent_id") or "unknown")].append(event)

    agents = []
    for agent in event_bus.get_all_agents():
        agent_id = str(agent.get("agent_id") or "")
        events_for_agent = recent_by_agent.get(agent_id, [])[:8]
        agent["recent_events"] = events_for_agent
        agent["recent_runs"] = [
            run
            for run in traces["runs"]
            if any(agent_id in str(row.get("name") or "") for row in run.get("rows", []))
        ][:5]
        agent["data_source"] = (
            next((event.get("data", {}).get("source") for event in events_for_agent if isinstance(event.get("data"), dict) and event.get("data", {}).get("source")), "")
            or "event_bus"
        )
        agent["estimated_cost_usd"] = round(
            sum(_admin_float(run.get("estimated_cost_usd")) for run in agent["recent_runs"]),
            8,
        )
        agents.append(agent)

    return {
        "agents": agents,
        "system": event_bus.get_system_stats(),
        "observability": get_observability_summary(db),
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


@app.get("/admin/traces")
def admin_get_traces(
    limit: int = Query(default=80, ge=1, le=300),
    trace_type: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    user_id: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    return build_admin_traces(
        db,
        limit=limit,
        trace_type=trace_type,
        status_filter=status_filter,
        user_id=user_id,
    )


@app.get("/admin/data-registry")
def admin_data_registry(
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    return build_admin_data_registry(db)


@app.get("/admin/model-registry")
def admin_model_registry(
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    return build_admin_model_registry(db)


@app.get("/admin/students")
def admin_students(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    return build_admin_students(db, limit=limit)


@app.get("/admin/system-health")
def admin_system_health(
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    return build_admin_system_health(db)


@app.get("/admin/audit-logs")
def admin_audit_logs(
    limit: int = Query(default=80, ge=1, le=300),
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    rows = (
        db.query(AdminAuditLog)
        .order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "logs": [
            {
                "id": row.id,
                "created_at": _admin_iso(row.created_at),
                "actor_uid": row.actor_uid or "",
                "actor_email": row.actor_email or "",
                "admin_uid": row.actor_uid or "",
                "admin_email": row.actor_email or "",
                "action": row.action or "",
                "target_type": row.target_type or "",
                "target_id": row.target_id or "",
                "status": row.status or "",
                "metadata": row.metadata_json or {},
            }
            for row in rows
        ],
    }


@app.post("/admin/action")
def admin_action(
    request: AdminActionRequest,
    db: Session = Depends(get_db),
    current_admin: Dict[str, Any] = Depends(require_admin),
):
    normalized_action = normalize_topic(request.action)
    if normalized_action in CONFIRMED_ADMIN_ACTIONS and not request.confirmed:
        record_admin_audit(
            db,
            current_admin,
            action=normalized_action,
            target_type=request.target_type,
            target_id=request.target_id,
            status_value="blocked_confirmation_required",
            metadata={"payload": request.payload},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confirmation is required for this admin action.",
        )

    if normalized_action == "export_data_report":
        record_admin_audit(
            db,
            current_admin,
            action=normalized_action,
            target_type=request.target_type,
            target_id=request.target_id,
            status_value="success",
            metadata={"payload": request.payload},
        )
        return {
            "status": "success",
            "message": "Data report generated from current backend state.",
            "report": {
                "overview": build_admin_overview(db),
                "data_registry": build_admin_data_registry(db),
                "model_registry": build_admin_model_registry(db),
                "system_health": build_admin_system_health(db),
            },
        }

    if normalized_action == "clear_temp_cache":
        record_admin_audit(
            db,
            current_admin,
            action=normalized_action,
            target_type=request.target_type or "backend",
            target_id=request.target_id,
            status_value="success",
            metadata={"payload": request.payload, "note": "No durable study/user data was deleted."},
        )
        return {
            "status": "success",
            "message": "Temporary cache clear recorded. No durable study/user data was deleted.",
        }

    status_value = "unsupported"
    record_admin_audit(
        db,
        current_admin,
        action=normalized_action,
        target_type=request.target_type,
        target_id=request.target_id,
        status_value=status_value,
        metadata={
            "payload": request.payload,
            "todo": "Backend workflow not implemented yet; action intentionally not executed.",
        },
    )
    return {
        "status": status_value,
        "message": "This action is not wired to a safe backend workflow yet.",
        "todo": "Implement the backend worker or service connector before enabling execution.",
    }


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
    http_request: Request,
    db: Session = Depends(get_db),
    current_admin: Dict[str, Any] = Depends(require_admin),
):
    result = event_bus.send_command(
        agent_id=request.agent_id,
        command=request.command,
        payload=request.payload,
    )
    _record_admin_audit(
        db,
        current_admin=current_admin,
        action=f"agent_{request.command}",
        target_type="agent",
        target_id=request.agent_id,
        status_value="success" if result.get("success") else "failed",
        metadata={"payload": request.payload, "result": result},
        request=http_request,
    )
    return result


# ╔══════════════════════════════════════════════════════════════╗
# ║  UPDATED /admin/message  – Casual mode for CEO chats       ║
# ╚══════════════════════════════════════════════════════════════╝
@app.post("/admin/message")
def admin_send_message(
    request: AgentMessageRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    current_admin: Dict[str, Any] = Depends(require_admin),
):
    record_admin_audit(
        db,
        current_admin,
        action="agent_message",
        target_type="agent",
        target_id=request.agent_id,
        status_value="requested",
        metadata={"mode": request.mode or "study", "session_id": request.session_id},
    )
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

        _record_admin_audit(
            db,
            current_admin=current_admin,
            action="agent_message",
            target_type="agent",
            target_id=request.agent_id,
            metadata={"mode": request.mode, "session_id": request.session_id},
            request=http_request,
        )
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

    _record_admin_audit(
        db,
        current_admin=current_admin,
        action="agent_message",
        target_type="agent",
        target_id=request.agent_id,
        metadata={"mode": request.mode or "study", "session_id": request.session_id},
        request=http_request,
    )
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
    allow_client_overwrite = os.getenv("ALLOW_CLIENT_PROGRESS_OVERWRITE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if not allow_client_overwrite and not is_backend_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Direct progress overwrite is disabled. Submit completed sessions instead.",
        )

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
