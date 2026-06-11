"""Admin console, audit, content pipeline, agent registry, intelligence, and commands."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.request_models import (
    AdminActionRequest,
    AdminAuditRequest,
    AgentCommandRequest,
    AgentMessageRequest,
    ContentConceptImportRequest,
    ContentGenerateConceptsRequest,
    ContentIngestFolderRequest,
)
from app.security import require_admin, require_founder_admin
from app.serializers import normalize_topic, serialize_audit_log
from database import get_db
from Logic.agent_event_bus import event_bus
from Logic.agent_router import get_agent_registry, route_to_agent
from Logic.agents.coach_agent import coach_agent
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
from Logic.observability_store import (
    get_latest_observability_version,
    get_observability_events_since,
    get_observability_summary,
    get_recent_observability_events,
)
from models import AdminAuditLog
from services.admin_intelligence import (
    CONFIRMED_ADMIN_ACTIONS,
    _admin_float,
    _admin_iso,
    build_admin_console_payload,
    build_admin_data_registry,
    build_admin_model_registry,
    build_admin_overview,
    build_admin_students,
    build_admin_system_health,
    build_admin_traces,
)
from services.admin_service import (
    generic_llm_chat,
    record_admin_audit,
    record_admin_audit_simple,
)

router = APIRouter(tags=["admin"])


@router.get("/admin/me")
def admin_me(current_admin: Dict[str, Any] = Depends(require_admin)):
    return {
        "uid": current_admin.get("uid"),
        "email": current_admin.get("email"),
        "phone": current_admin.get("phone_number"),
        "role": "admin",
        "verified": True,
    }


@router.get("/admin/console")
def admin_console(
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_founder_admin),
):
    return build_admin_console_payload(db)


@router.get("/admin/audit")
def admin_audit(
    limit: int = Query(default=80, ge=1, le=300),
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_founder_admin),
):
    rows = db.query(AdminAuditLog).order_by(AdminAuditLog.id.desc()).limit(limit).all()
    return {"audit": [serialize_audit_log(row) for row in rows]}


@router.post("/admin/audit")
def admin_record_audit(
    payload: AdminAuditRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: Dict[str, Any] = Depends(require_founder_admin),
):
    row = record_admin_audit(
        db,
        current_admin=current_admin,
        action=payload.action,
        target_type=payload.target_type,
        target_id=payload.target_id,
        status_value=payload.status,
        metadata=payload.metadata,
        request=request,
    )
    return {"audit": serialize_audit_log(row), "status": "recorded"}


@router.get("/admin/content/folder-contract")
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


@router.post("/admin/content/ingest-folder")
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
        record_admin_audit(
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


@router.get("/admin/content/chapters")
def admin_content_chapters(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    return {"chapters": list_content_chapters(db, status=status_filter)}


@router.get("/admin/content/report/{chapter_id}")
def admin_content_report(
    chapter_id: int,
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    try:
        return chapter_report(db, chapter_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/admin/content/import-concepts/{chapter_id}")
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
        record_admin_audit(
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


@router.post("/admin/content/generate-json/{chapter_id}")
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
        record_admin_audit(
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


@router.post("/admin/content/approve/{chapter_id}")
def admin_content_approve(
    chapter_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: Dict[str, Any] = Depends(require_admin),
):
    try:
        chapter = approve_chapter(db, chapter_id, approved_by=str(current_admin.get("uid") or "admin"))
        db.commit()
        record_admin_audit(
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


@router.post("/admin/content/publish/{chapter_id}")
def admin_content_publish(
    chapter_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: Dict[str, Any] = Depends(require_admin),
):
    try:
        chapter = publish_chapter(db, chapter_id, published_by=str(current_admin.get("uid") or "admin"))
        db.commit()
        record_admin_audit(
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


@router.get("/admin/overview")
def admin_overview(
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    return build_admin_overview(db)


@router.get("/admin/agents")
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


@router.get("/admin/agent-registry")
def admin_get_agent_registry(_current_admin: Dict[str, Any] = Depends(require_admin)):
    return {
        "agents": get_agent_registry(),
        "system": event_bus.get_system_stats(),
    }


@router.get("/admin/agents/{agent_id}")
def admin_get_agent(
    agent_id: str,
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    agent = event_bus.get_agent(agent_id)

    if not agent:
        return {"error": f"Agent '{agent_id}' not found"}

    return agent


@router.get("/admin/traces")
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


@router.get("/admin/data-registry")
def admin_data_registry(
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    return build_admin_data_registry(db)


@router.get("/admin/model-registry")
def admin_model_registry(
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    return build_admin_model_registry(db)


@router.get("/admin/students")
def admin_students(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    return build_admin_students(db, limit=limit)


@router.get("/admin/system-health")
def admin_system_health(
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    return build_admin_system_health(db)


@router.get("/admin/audit-logs")
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


@router.post("/admin/action")
def admin_action(
    request: AdminActionRequest,
    db: Session = Depends(get_db),
    current_admin: Dict[str, Any] = Depends(require_admin),
):
    normalized_action = normalize_topic(request.action)
    if normalized_action in CONFIRMED_ADMIN_ACTIONS and not request.confirmed:
        record_admin_audit_simple(
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
        record_admin_audit_simple(
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
        record_admin_audit_simple(
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
    record_admin_audit_simple(
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


@router.get("/admin/events")
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


@router.get("/admin/poll")
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


@router.get("/admin/stats")
def admin_get_stats(
    db: Session = Depends(get_db),
    _current_admin: Dict[str, Any] = Depends(require_admin),
):
    stats = event_bus.get_system_stats()
    stats["observability"] = get_observability_summary(db)
    return stats


@router.post("/admin/command")
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
    record_admin_audit(
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


@router.post("/admin/message")
def admin_send_message(
    request: AgentMessageRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    current_admin: Dict[str, Any] = Depends(require_admin),
):
    record_admin_audit_simple(
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
            import logging

            logging.getLogger("ai_educator.routers.admin").error(f"Generic LLM chat failed: {e}")
            answer = f"I'm having trouble responding right now. (agent: {request.agent_id})"

        record_admin_audit(
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

    record_admin_audit(
        db,
        current_admin=current_admin,
        action="agent_message",
        target_type="agent",
        target_id=request.agent_id,
        metadata={"mode": request.mode or "study", "session_id": request.session_id},
        request=http_request,
    )
    return result
