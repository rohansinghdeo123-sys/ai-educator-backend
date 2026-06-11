"""Liveness, readiness, and public health endpoints."""

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app import config, security
from database import check_db_health
from Logic.knowledge_graph import knowledge_graph
from Logic.tools.artifact_generator import available_artifact_sections

router = APIRouter(tags=["health"])


@router.get("/health/live")
def liveness_probe():
    return {"status": "ok", "service": "agentifyai-backend"}


@router.get("/health/ready")
def readiness_probe():
    db_ready = check_db_health()
    firebase_ready = bool(security.firebase_ready())
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


@router.get("/health")
def health_check():
    return {
        "status": "online" if check_db_health() else "degraded",
        "version": "2.5.0-production-guardrails",
        "service": "agentifyai-backend",
        "request_ids": True,
        "rate_limits": config.RATE_LIMIT_ENABLED,
        "cors_origins_configured": len(config.ALLOWED_ORIGINS),
    }
