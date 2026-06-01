"""Structured observability hooks for the unified Study Lab coach."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import logging
import time
from typing import Any, Dict, List

from Logic.agent_event_bus import event_bus

logger = logging.getLogger("ai_educator.coach.observability")


@dataclass
class CoachTurnTrace:
    turn_id: str
    session_id: str
    started_at: float = field(default_factory=time.perf_counter)
    phase_started_at: float = field(default_factory=time.perf_counter)
    phases_ms: Dict[str, int] = field(default_factory=dict)
    tools: List[Dict[str, Any]] = field(default_factory=list)
    fallbacks: List[Dict[str, Any]] = field(default_factory=list)
    memory_layers: List[str] = field(default_factory=list)

    def mark_phase(self, phase: str) -> None:
        now = time.perf_counter()
        if self.phases_ms:
            previous = next(reversed(self.phases_ms))
            if self.phases_ms[previous] < 0:
                self.phases_ms[previous] = round((now - self.phase_started_at) * 1000)
        self.phases_ms[phase] = -1
        self.phase_started_at = now

    def finish(self) -> int:
        now = time.perf_counter()
        if self.phases_ms:
            previous = next(reversed(self.phases_ms))
            if self.phases_ms[previous] < 0:
                self.phases_ms[previous] = round((now - self.phase_started_at) * 1000)
        return round((now - self.started_at) * 1000)

    def record_tool(self, name: str, **data: Any) -> None:
        self.tools.append({"name": name, **data})

    def record_fallback(self, reason: str, **data: Any) -> None:
        self.fallbacks.append({"reason": reason, **data})

    def snapshot(self) -> Dict[str, Any]:
        snapshot = asdict(self)
        snapshot.pop("started_at", None)
        snapshot.pop("phase_started_at", None)
        return snapshot


class CoachObservability:
    def start_turn(self, turn_id: str, session_id: str) -> CoachTurnTrace:
        trace = CoachTurnTrace(turn_id=turn_id, session_id=session_id)
        trace.mark_phase("received")
        self.emit(session_id, "task_start", turn_id=turn_id, task="Coach tutor turn started")
        return trace

    def emit(self, session_id: str, event_type: str, **data: Any) -> None:
        event_bus.emit("coach", event_type, data, session_id=session_id)
        logger.info("[COACH_OBSERVABILITY] %s %s", event_type, data)

    def snapshot(
        self,
        query: Dict[str, Any],
        retrieval: Dict[str, Any],
        plan: Dict[str, Any],
        quality: Dict[str, Any],
        trace: CoachTurnTrace | None = None,
        model_calls: List[Dict[str, Any]] | None = None,
        mastery_signal: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        return {
            "query": query,
            "retrieval": retrieval,
            "plan": plan,
            "quality": quality,
            "trace": trace.snapshot() if trace else {},
            "model_calls": model_calls or [],
            "mastery_signal": mastery_signal or {},
        }


coach_observability = CoachObservability()
