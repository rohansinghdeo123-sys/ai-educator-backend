"""Telemetry and chat-memory retention.

These tables grow on every tutor turn and would grow forever on a small
managed Postgres. Pruning runs at app startup (free-tier dynos restart
often, so this fires at least daily in practice) and is safe to run any
time — it only deletes rows older than the retention windows.

Windows are env-tunable:
  CHAT_MEMORY_RETENTION_DAYS   (default 90)  — agent_chat_memory
  TELEMETRY_RETENTION_DAYS     (default 45)  — observability/traces/runtime
Set a window to 0 to disable pruning for that group.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict

from sqlalchemy.orm import Session

from models import (
    AgentChatMemory,
    AgentRuntimeHandoff,
    AgentRuntimeMessage,
    AgentRuntimeRun,
    AgentRuntimeStep,
    AgentRuntimeToolCall,
    ModelToolTrace,
    ObservabilityEvent,
)

logger = logging.getLogger("ai_educator.services.retention")

_RUN_DELETE_BATCH = 500


def _days(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _cutoff(days: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)


def prune_telemetry(db: Session) -> Dict[str, int]:
    """Delete expired telemetry rows. Returns per-table delete counts."""
    deleted: Dict[str, int] = {}

    chat_days = _days("CHAT_MEMORY_RETENTION_DAYS", 90)
    if chat_days:
        deleted["agent_chat_memory"] = (
            db.query(AgentChatMemory)
            .filter(AgentChatMemory.timestamp < _cutoff(chat_days))
            .delete(synchronize_session=False)
        )

    telemetry_days = _days("TELEMETRY_RETENTION_DAYS", 45)
    if telemetry_days:
        cutoff = _cutoff(telemetry_days)
        deleted["observability_events"] = (
            db.query(ObservabilityEvent)
            .filter(ObservabilityEvent.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        deleted["model_tool_traces"] = (
            db.query(ModelToolTrace)
            .filter(ModelToolTrace.created_at < cutoff)
            .delete(synchronize_session=False)
        )

        # Runtime children hang off run_id strings, so expire whole runs in
        # batches to keep the trace tree consistent.
        total_runs = 0
        while True:
            run_ids = [
                row.run_id
                for row in db.query(AgentRuntimeRun.run_id)
                .filter(AgentRuntimeRun.started_at < cutoff)
                .limit(_RUN_DELETE_BATCH)
                .all()
            ]
            if not run_ids:
                break
            for child in (
                AgentRuntimeStep,
                AgentRuntimeMessage,
                AgentRuntimeToolCall,
                AgentRuntimeHandoff,
            ):
                db.query(child).filter(child.run_id.in_(run_ids)).delete(synchronize_session=False)
            total_runs += (
                db.query(AgentRuntimeRun)
                .filter(AgentRuntimeRun.run_id.in_(run_ids))
                .delete(synchronize_session=False)
            )
            db.commit()
        deleted["agent_runtime_runs"] = total_runs

    db.commit()
    removed = {table: count for table, count in deleted.items() if count}
    if removed:
        logger.info("RETENTION: pruned %s", removed)
    return deleted


def prune_telemetry_safely(session_factory) -> None:
    """Startup wrapper: retention must never block or crash boot."""
    db = session_factory()
    try:
        prune_telemetry(db)
    except Exception as exc:
        db.rollback()
        logger.warning("RETENTION: prune skipped: %s", exc)
    finally:
        db.close()
