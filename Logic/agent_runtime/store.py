"""Persistence helpers for the controlled agent runtime."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
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


def _next_step_order(db: Session, run_id: str) -> int:
    try:
        count = db.query(AgentRuntimeStep).filter(AgentRuntimeStep.run_id == run_id).count()
        return int(count or 0) + 1
    except Exception:
        return 1


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
            step_order=_next_step_order(db, run_id),
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=max(0, int(latency_ms or 0)),
            input_json=input_data or {},
            output_json=output_data or {},
            error=_compact_text(error, 1000),
        )
        db.add(step)
        if not _commit_or_rollback(db, f"step {step_name}"):
            return None
        db.refresh(step)
        return step
    except Exception as exc:
        db.rollback()
        logger.warning("Could not record agent runtime step %s: %s", step_name, exc)
        return None


def record_agent_messages(
    db: Session,
    *,
    run_id: str,
    messages: Iterable[AgentMessage],
) -> int:
    stored = 0
    try:
        for message in list(messages or []):
            payload = message.to_dict()
            db.add(
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
            stored += 1
        if stored and not _commit_or_rollback(db, "messages"):
            return 0
        return stored
    except Exception as exc:
        db.rollback()
        logger.warning("Could not record agent runtime messages: %s", exc)
        return 0


def record_agent_tool_calls(
    db: Session,
    *,
    run_id: str,
    tools: Iterable[Dict[str, Any]],
    agent_name: str = "lead_coach_orchestrator",
) -> int:
    stored = 0
    try:
        for tool in list(tools or []):
            tool_name = str(tool.get("name") or tool.get("tool_name") or "tool")
            output_payload = _safe_dict(tool.get("output") or tool.get("result") or tool.get("output_json"))
            if not output_payload:
                output_payload = {
                    key: value
                    for key, value in tool.items()
                    if key not in {"name", "tool_name", "agent_name", "status", "latency_ms", "input", "input_json", "error"}
                }
            db.add(
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
            stored += 1
        if stored and not _commit_or_rollback(db, "tool calls"):
            return 0
        return stored
    except Exception as exc:
        db.rollback()
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
        db.add(handoff)
        if not _commit_or_rollback(db, f"handoff {from_agent}->{to_agent}"):
            return None
        db.refresh(handoff)
        return handoff
    except Exception as exc:
        db.rollback()
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
