"""Controlled gateway for deterministic coach tools."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List

from Logic.agent_runtime import normalize_agent_role

from .tool_registry import ToolRegistry, coach_tool_registry


def _compact_text(value: Any, limit: int = 700) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _safe_payload(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        try:
            return _safe_payload(value.to_dict())
        except Exception:
            pass
    if isinstance(value, dict):
        return {str(key): _safe_payload(item) for key, item in list(value.items())[:24]}
    if isinstance(value, list):
        return [_safe_payload(item) for item in value[:12]]
    if isinstance(value, str):
        return _compact_text(value, 900)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _compact_text(value, 300)


class ToolGateway:
    """Stable execution boundary for backend-selected tools."""

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.registry = registry or coach_tool_registry
        self._local = threading.local()

    def begin_turn(self, turn_id: str) -> None:
        self._local.turn_id = turn_id
        self._local.records = []

    def describe(self) -> Dict[str, Dict[str, str]]:
        return self.registry.describe()

    def records(self) -> List[Dict[str, Any]]:
        return list(getattr(self._local, "records", []) or [])

    def run(
        self,
        name: str,
        *,
        agent_name: str = "tool_gateway",
        task: str = "",
        fail_open: bool = True,
        **kwargs: Any,
    ) -> Any:
        started_at = time.perf_counter()
        agent = normalize_agent_role(agent_name or "tool_gateway")
        tool = self.registry.get(name)
        safety_rule = tool.safety_rule if tool else ""
        try:
            result = self.registry.run(name, **kwargs)
            self._record(
                name=name,
                agent_name=agent,
                task=task,
                status="success",
                started_at=started_at,
                input_payload=kwargs,
                result_payload=result,
                safety_rule=safety_rule,
            )
            return result
        except Exception as exc:
            error_payload = {
                "used": False,
                "tool_failed": True,
                "tool_name": name,
                "error": _compact_text(exc, 300),
            }
            self._record(
                name=name,
                agent_name=agent,
                task=task,
                status="error",
                started_at=started_at,
                input_payload=kwargs,
                result_payload=error_payload,
                safety_rule=safety_rule,
                error=exc,
            )
            if fail_open:
                return error_payload
            raise

    def _record(
        self,
        *,
        name: str,
        agent_name: str,
        task: str,
        status: str,
        started_at: float,
        input_payload: Dict[str, Any],
        result_payload: Any,
        safety_rule: str,
        error: Any = "",
    ) -> None:
        records = list(getattr(self._local, "records", []) or [])
        records.append({
            "name": str(name or "tool"),
            "tool_name": str(name or "tool"),
            "agent_name": agent_name,
            "gateway_task": _compact_text(task or name, 240),
            "status": status,
            "latency_ms": round((time.perf_counter() - started_at) * 1000),
            "input": _safe_payload(input_payload),
            "result": _safe_payload(result_payload),
            "safety_rule": _compact_text(safety_rule, 300),
            "error": _compact_text(error, 300),
        })
        self._local.records = records[-64:]


tool_gateway = ToolGateway()
