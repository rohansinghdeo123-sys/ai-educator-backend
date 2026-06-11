"""Application startup/shutdown wiring via a FastAPI lifespan context manager.

All side effects that previously ran at module import time (table creation, the
telemetry-column shim, event-bus sink wiring, Firebase initialization, and the
knowledge-graph load) now run here, once, when the ASGI server starts the app.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from sqlalchemy import inspect, text

import models  # noqa: F401  (ensures all tables are registered on Base.metadata)
from app import security
from app.telemetry import init_telemetry, shutdown_telemetry
from database import Base, engine
from Logic.agent_event_bus import event_bus
from Logic.knowledge_graph import knowledge_graph
from Logic.observability_store import persist_event_from_bus

logger = logging.getLogger("ai_educator.lifespan")

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_KNOWLEDGE_GRAPH_PATH = os.path.join(_BACKEND_DIR, "data", "Chapters", "basic_concepts_of_chemistry.json")


def _ensure_session_telemetry_columns() -> None:
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


def _load_knowledge_graph() -> None:
    logger.info("Loading knowledge graph from: %s", _KNOWLEDGE_GRAPH_PATH)
    try:
        knowledge_graph.load_chapter(_KNOWLEDGE_GRAPH_PATH, "basic-concepts-of-chemistry")
        logger.info(
            "Knowledge graph loaded: basic-concepts-of-chemistry (%d concepts)",
            len(knowledge_graph.concepts),
        )
    except Exception as exc:
        logger.warning("Could not load basic-concepts-of-chemistry chapter: %s", exc)


@asynccontextmanager
async def lifespan(app):
    # ── startup ──────────────────────────────────────────────────────────
    # In production Alembic is the single schema authority (run `alembic
    # upgrade head` on deploy); create_all is a dev/test convenience only.
    app_env = (os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or os.getenv("ENV") or "").lower()
    default_auto = "false" if app_env in {"prod", "production", "staging"} else "true"
    if os.getenv("AUTO_CREATE_TABLES", default_auto).strip().lower() in {"1", "true", "yes", "on"}:
        Base.metadata.create_all(bind=engine)
        _ensure_session_telemetry_columns()
    else:
        logger.info("AUTO_CREATE_TABLES disabled; relying on Alembic migrations.")
    event_bus.set_sink(persist_event_from_bus)
    security.initialize_firebase_admin()
    _load_knowledge_graph()
    init_telemetry()

    from services.job_queue import job_queue

    job_queue.start()

    yield

    # ── shutdown ─────────────────────────────────────────────────────────
    job_queue.stop()
    shutdown_telemetry()
    engine.dispose()
