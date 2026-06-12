"""Persistence helpers for the controlled agent runtime.

Steps, handoffs, messages, and tool calls are buffered in memory per run and
written in a single commit by ``complete_agent_run`` (or
``flush_agent_runtime`` on failure paths). The coach turn engine records a
dozen telemetry rows per turn; committing each one individually put 10+
synchronous Postgres round trips between the student and their answer.
Only ``start_agent_run`` still commits eagerly so crashed turns stay visible.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import threading
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from models import (
    AgentRuntimeHandoff,
    AgentRuntimeMessage,
    AgentRuntimeRun,
    AgentRuntimeStep,
    AgentRuntimeToolCall,
)

from .contracts import normalize_agent_role, normalize_handoff_status
from .state import AgentMessage, AgentState

logger = logging.getLogger("ai_educator.agent_runtime")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _compact_text(value: Any, limit: int = 1000) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _commit_or_rollback(db: Session, action: str) -> bool:
    try:
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        logger.warning("Could not persist agent runtime %s: %s", action, exc)
        return False


_PENDING_LOCK = threading.Lock()
_PENDING_ROWS: Dict[str, List[Any]] = {}
_PENDING_STEP_ORDER: Dict[str, int] = {}
_MAX_PENDING_RUNS = 64


def _queue_pending(run_id: str, rows: Iterable[Any]) -> None:
    with _PENDING_LOCK:
        bucket = _PENDING_ROWS.setdefault(run_id, [])
        bucket.extend(rows)
        # Abandoned runs (process killed mid-turn) must not grow the buffer
        # without bound; evict the oldest runs' telemetry.
        while len(_PENDING_ROWS) > _MAX_PENDING_RUNS:
            evicted = next(iter(_PENDING_ROWS))
            _PENDING_ROWS.pop(evicted, None)
            _PENDING_STEP_ORDER.pop(evicted, None)


def _drain_pending(run_id: str) -> List[Any]:
    with _PENDING_LOCK:
        _PENDING_STEP_ORDER.pop(run_id, None)
        return _PENDING_ROWS.pop(run_id, [])


def _next_step_order(run_id: str) -> int:
    with _PENDING_LOCK:
        order = _PENDING_STEP_ORDER.get(run_id, 0) + 1
        _PENDING_STEP_ORDER[run_id] = order
        return order


def flush_agent_runtime(db: Session, run_id: str) -> int:
    """Write any buffered telemetry rows for a run in a single commit."""
    rows = _drain_pending(run_id)
    if not rows:
        return 0
    try:
        db.add_all(rows)
        if not _commit_or_rollback(db, "buffered records"):
            return 0
        return len(rows)
    except Exception as exc:
        db.rollback()
        logger.warning("Could not flush agent runtime buffer for %s: %s", run_id, exc)
        return 0


def start_agent_run(
    db: Session,
    *,
    state: AgentState,
    workflow_name: str = "study_coach_turn",
    lead_agent: str = "lead_coach_orchestrator",
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[AgentRuntimeRun]:
    """Create or refresh the durable envelope for a runtime run."""
    try:
        run = db.query(AgentRuntimeRun).filter(AgentRuntimeRun.run_id == state.turn_id).first()
        if run is None:
            run = AgentRuntimeRun(
                run_id=state.turn_id,
                turn_id=state.turn_id,
                user_id=state.user_id,
                session_id=state.session_id,
                workflow_name=workflow_name,
                lead_agent=normalize_agent_role(lead_agent),
                mode=state.selected_mode,
                intent=state.detected_intent,
                status="running",
                started_at=_now(),
                grounding_status=state.grounding_status,
                state_json=state.to_trace_dict(),
                metadata_json=metadata or {},
            )
            db.add(run)
        else:
            run.status = "running"
            run.lead_agent = normalize_agent_role(lead_agent)
            run.intent = state.detected_intent
            run.grounding_status = state.grounding_status
            run.state_json = state.to_trace_dict()
            run.metadata_json = {**_safe_dict(run.metadata_json), **(metadata or {})}
        if not _commit_or_rollback(db, "run start"):
            return None
        db.refresh(run)
        return run
    except Exception as exc:
        db.rollback()
        logger.warning("Could not start agent runtime run: %s", exc)
        return None


def record_agent_step(
    db: Session,
    *,
    run_id: str,
    step_name: str,
    agent_name: str = "",
    status: str = "success",
    input_data: Optional[Dict[str, Any]] = None,
    output_data: Optional[Dict[str, Any]] = None,
    latency_ms: int = 0,
    error: Any = "",
) -> Optional[AgentRuntimeStep]:
    try:
        completed_at = _now()
        started_at = completed_at
        if latency_ms > 0:
            started_at = completed_at - timedelta(milliseconds=latency_ms)
        step = AgentRuntimeStep(
            run_id=run_id,
            step_name=step_name,
            agent_name=normalize_agent_role(agent_name) if agent_name else "",
            status=status,
            step_order=_next_step_order(run_id),
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=max(0, int(latency_ms or 0)),
            input_json=input_data or {},
            output_json=output_data or {},
            error=_compact_text(error, 1000),
        )
        _queue_pending(run_id, [step])
        return step
    except Exception as exc:
        logger.warning("Could not record agent runtime step %s: %s", step_name, exc)
        return None


def record_agent_messages(
    db: Session,
    *,
    run_id: str,
    messages: Iterable[AgentMessage],
) -> int:
    try:
        rows = []
        for message in list(messages or []):
            payload = message.to_dict()
            rows.append(
                AgentRuntimeMessage(
                    run_id=run_id,
                    sender_agent=str(payload.get("sender_agent") or ""),
                    receiver_agent=str(payload.get("receiver_agent") or ""),
                    message_type=str(payload.get("message_type") or ""),
                    task=str(payload.get("task") or ""),
                    confidence=float(payload.get("confidence") or 0.0),
                    required_action=str(payload.get("required_action") or ""),
                    evidence_json=_safe_dict(payload.get("evidence")),
                    result_json=_safe_dict(payload.get("result")),
                )
            )
        if rows:
            _queue_pending(run_id, rows)
        return len(rows)
    except Exception as exc:
        logger.warning("Could not record agent runtime messages: %s", exc)
        return 0


def record_agent_tool_calls(
    db: Session,
    *,
    run_id: str,
    tools: Iterable[Dict[str, Any]],
    agent_name: str = "lead_coach_orchestrator",
) -> int:
    try:
        rows = []
        for tool in list(tools or []):
            tool_name = str(tool.get("name") or tool.get("tool_name") or "tool")
            output_payload = _safe_dict(tool.get("output") or tool.get("result") or tool.get("output_json"))
            if not output_payload:
                output_payload = {
                    key: value
                    for key, value in tool.items()
                    if key not in {"name", "tool_name", "agent_name", "status", "latency_ms", "input", "input_json", "error"}
                }
            rows.append(
                AgentRuntimeToolCall(
                    run_id=run_id,
                    tool_name=tool_name,
                    agent_name=normalize_agent_role(tool.get("agent_name") or agent_name or ""),
                    status=str(tool.get("status") or "success"),
                    completed_at=_now(),
                    latency_ms=int(tool.get("latency_ms") or 0),
                    input_json=_safe_dict(tool.get("input") or tool.get("input_json")),
                    output_json=output_payload,
                    error=_compact_text(tool.get("error"), 1000),
                )
            )
        if rows:
            _queue_pending(run_id, rows)
        return len(rows)
    except Exception as exc:
        logger.warning("Could not record agent runtime tool calls: %s", exc)
        return 0


def record_agent_handoff(
    db: Session,
    *,
    run_id: str,
    from_agent: str,
    to_agent: str,
    reason: str,
    status: str = "completed",
    input_data: Optional[Dict[str, Any]] = None,
    result_data: Optional[Dict[str, Any]] = None,
) -> Optional[AgentRuntimeHandoff]:
    try:
        handoff = AgentRuntimeHandoff(
            run_id=run_id,
            from_agent=normalize_agent_role(from_agent),
            to_agent=normalize_agent_role(to_agent),
            reason=_compact_text(reason, 1000),
            status=normalize_handoff_status(status),
            input_json=input_data or {},
            result_json=result_data or {},
        )
        _queue_pending(run_id, [handoff])
        return handoff
    except Exception as exc:
        logger.warning("Could not record agent runtime handoff: %s", exc)
        return None


def complete_agent_run(
    db: Session,
    *,
    state: AgentState,
    status: str = "success",
    latency_ms: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[AgentRuntimeRun]:
    pending_rows = _drain_pending(state.turn_id)
    try:
        run = db.query(AgentRuntimeRun).filter(AgentRuntimeRun.run_id == state.turn_id).first()
        if run is None:
            run = AgentRuntimeRun(
                run_id=state.turn_id,
                turn_id=state.turn_id,
                user_id=state.user_id,
                session_id=state.session_id,
                workflow_name="study_coach_turn",
                lead_agent=normalize_agent_role("lead_coach_orchestrator"),
                started_at=_now(),
            )
            db.add(run)
        if pending_rows:
            db.add_all(pending_rows)
        run.status = status
        run.intent = state.detected_intent
        run.mode = state.selected_mode
        run.completed_at = _now()
        run.latency_ms = max(0, int(latency_ms or 0))
        run.confidence_score = float(state.confidence_score or 0.0)
        run.grounding_status = state.grounding_status
        run.final_answer_excerpt = _compact_text(state.final_answer, 1200)
        run.state_json = state.to_trace_dict(include_answer=True)
        run.metadata_json = {**_safe_dict(run.metadata_json), **(metadata or {})}
        if not _commit_or_rollback(db, "run completion"):
            return None
        db.refresh(run)
        return run
    except Exception as exc:
        db.rollback()
        logger.warning("Could not complete agent runtime run: %s", exc)
        return None


def runtime_summary(db: Session, run_id: str) -> Dict[str, Any]:
    """Return a compact runtime summary for tests/admin surfaces."""
    run = db.query(AgentRuntimeRun).filter(AgentRuntimeRun.run_id == run_id).first()
    if run is None:
        return {}
    return {
        "run_id": run.run_id,
        "status": run.status,
        "intent": run.intent,
        "mode": run.mode,
        "latency_ms": run.latency_ms,
        "confidence_score": run.confidence_score,
        "grounding_status": run.grounding_status,
        "steps": db.query(AgentRuntimeStep).filter(AgentRuntimeStep.run_id == run_id).count(),
        "messages": db.query(AgentRuntimeMessage).filter(AgentRuntimeMessage.run_id == run_id).count(),
        "tool_calls": db.query(AgentRuntimeToolCall).filter(AgentRuntimeToolCall.run_id == run_id).count(),
        "handoffs": db.query(AgentRuntimeHandoff).filter(AgentRuntimeHandoff.run_id == run_id).count(),
    }
