# Logic/agent_router.py

"""
Backward-compatible router facade.

Legacy code still imports route_to_agent() from this module. The actual routing
now lives in Logic.agentic_orchestrator so every request goes through the same
agent registry, workflow resolver, and telemetry envelope.
"""

from Logic.agentic_orchestrator import (
    classify_intent,
    execute_agentic_request,
    get_agent_registry,
    resolve_agent_route,
)


def route_to_agent(request, db=None) -> dict:
    return execute_agentic_request(request, db=db)
