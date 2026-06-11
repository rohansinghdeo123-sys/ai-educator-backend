"""Admin audit logging and the casual-mode LLM helper.

The large admin-console aggregation logic lives in ``admin_intelligence``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from fastapi import Request
from sqlalchemy.orm import Session

import groq

from models import AdminAuditLog

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
    """Full audit row including request IP and user-agent context."""
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


def record_admin_audit_simple(
    db: Session,
    current_admin: Dict[str, Any],
    *,
    action: str,
    target_type: str = "",
    target_id: str = "",
    status_value: str = "success",
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Lightweight audit row used by action/message endpoints (no request context)."""
    identity = {
        "uid": str(current_admin.get("uid") or ""),
        "email": str(current_admin.get("email") or ""),
        "phone": str(current_admin.get("phone_number") or ""),
    }
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
