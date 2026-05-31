"""Structured observability hooks for the unified Study Lab coach."""

import logging
from typing import Any, Dict

from Logic.agent_event_bus import event_bus

logger = logging.getLogger("ai_educator.coach.observability")


class CoachObservability:
    def emit(self, session_id: str, event_type: str, **data: Any) -> None:
        event_bus.emit("coach", event_type, data, session_id=session_id)
        logger.info("[COACH_OBSERVABILITY] %s %s", event_type, data)

    def snapshot(
        self,
        query: Dict[str, Any],
        retrieval: Dict[str, Any],
        plan: Dict[str, Any],
        quality: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "query": query,
            "retrieval": retrieval,
            "plan": plan,
            "quality": quality,
        }


coach_observability = CoachObservability()
