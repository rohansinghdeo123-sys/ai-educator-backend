"""Admin console aggregation, audit logging, and the casual-mode LLM helper."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, time as datetime_time, timedelta
from typing import Any, Dict, Optional

from fastapi import Request
from sqlalchemy import func
from sqlalchemy.orm import Session

import groq

from app import security
from app.serializers import (
    interaction_session_id,
    serialize_audit_log,
    student_payload,
    trace_payload,
)
from database import check_db_health
from Logic.agent_event_bus import event_bus
from Logic.observability_store import (
    get_observability_summary,
    get_recent_observability_events,
)
from models import (
    AICoachInteraction,
    AdminAuditLog,
    ContentChapter,
    ContentIngestionJob,
    DailyQuotaUsage,
    ModelToolTrace,
    ObservabilityEvent,
    TestHistory,
    UserProgress,
)

logger = logging.getLogger("ai_educator.services.admin")


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


def record_admin_audit(
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


def build_admin_console_payload(db: Session) -> Dict[str, Any]:
    today_start = _start_of_today()
    cutoff_24h = datetime.utcnow() - timedelta(hours=24)

    interactions_today = db.query(AICoachInteraction).filter(AICoachInteraction.created_at >= today_start)
    interactions_24h = db.query(AICoachInteraction).filter(AICoachInteraction.created_at >= cutoff_24h)
    tests_today = db.query(TestHistory).filter(TestHistory.date == date.today())
    traces_24h = db.query(ModelToolTrace).filter(ModelToolTrace.created_at >= cutoff_24h)
    events_24h = db.query(ObservabilityEvent).filter(ObservabilityEvent.created_at >= cutoff_24h)

    active_session_ids = {
        interaction_session_id(row)
        for row in interactions_today.order_by(AICoachInteraction.id.desc()).limit(2500).all()
        if interaction_session_id(row)
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
    agent_rows = event_bus.get_all_agents()

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
            "auth_status": "configured" if security.firebase_ready() else "not_configured",
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
                {"name": "Firebase Auth", "status": "configured" if security.firebase_ready() else "not_configured"},
                {"name": "LLM provider", "status": "configured" if os.getenv("GROQ_API_KEY") else "not_configured"},
                {"name": "RAG/content index", "status": "ready" if published_count else "waiting_for_approved_content"},
                {"name": "Storage", "status": "ready" if os.path.isdir(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")) else "missing"},
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
        "students": [student_payload(row) for row in recent_students],
        "traces": [trace_payload(row) for row in recent_traces],
        "events": recent_events,
        "audit": [serialize_audit_log(row) for row in recent_audits],
    }
