# Logic/agent_event_bus.py

"""
AGENT EVENT BUS — Centralized Telemetry & Monitoring System

This is the nervous system of the admin panel. Every agent action,
tool call, state transition, and error flows through this bus.

Architecture:
- Singleton EventBus collects all agent events
- In-memory ring buffer stores recent events (last 1000)
- Agent registry tracks status, health, and current tasks
- Versioned events for efficient HTTP polling (no WebSocket needed)

Usage in agents:
    from Logic.agent_event_bus import event_bus
    event_bus.emit("tutor", "step", {"step": "retrieval", "message": "Searching KB..."})
"""

import threading
import logging
from datetime import datetime
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

logger = logging.getLogger("ai_educator.event_bus")


# =====================================================
# EVENT MODEL
# =====================================================
@dataclass
class AgentEvent:
    """A single event emitted by an agent or tool."""
    version: int               # Auto-incrementing version number for polling
    timestamp: str
    agent_id: str              # "tutor", "revision", "exam", "planner", "orchestrator"
    event_type: str            # "step", "tool_call", "error", "state_change", "metric", "task_start", "task_complete"
    data: dict                 # Flexible payload
    session_id: str = ""
    severity: str = "info"     # "info", "warning", "error", "critical"

    def to_dict(self) -> dict:
        return asdict(self)


# =====================================================
# AGENT REGISTRY — Tracks live status of each agent
# =====================================================
@dataclass
class AgentStatus:
    """Live status of a single agent."""
    agent_id: str
    display_name: str
    status: str = "idle"           # "idle", "running", "failed", "paused"
    health: str = "healthy"        # "healthy", "warning", "critical"
    current_task: str = ""
    last_activity: str = ""
    total_requests: int = 0
    total_errors: int = 0
    total_success: int = 0
    avg_latency_ms: float = 0.0
    last_quality_score: float = 0.0
    _latencies: list = field(default_factory=list, repr=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("_latencies", None)
        total = self.total_success + self.total_errors
        d["success_rate"] = round((self.total_success / total * 100) if total > 0 else 100, 1)
        return d


# =====================================================
# EVENT BUS — Singleton (Thread-safe, no async needed)
# =====================================================
class AgentEventBus:
    """
    Centralized event bus for agent monitoring.
    
    - Collects events from all agents
    - Maintains agent registry (status, health, metrics)
    - Versioned events for efficient HTTP polling
    - Stores last 1000 events in ring buffer
    - Fully thread-safe (no asyncio, no WebSocket)
    """

    def __init__(self, max_events: int = 1000):
        self._events: deque[AgentEvent] = deque(maxlen=max_events)
        self._agents: dict[str, AgentStatus] = {}
        self._version_counter: int = 0
        self._lock = threading.Lock()
        self._sink: Optional[Callable[[AgentEvent], None]] = None

        # Register default agents
        self._register_default_agents()

    def set_sink(self, sink: Callable[[AgentEvent], None] | None) -> None:
        """Attach an optional durable persistence sink for emitted events."""
        self._sink = sink

    def _register_default_agents(self):
        """Register all known agents with their default status."""
        defaults = [
            ("orchestrator", "Supervisor Orchestrator"),
            ("coach", "Personal AI Coach"),
            ("tutor", "Subject Tutor"),
            ("revision", "Revision Specialist"),
            ("exam", "Exam Generator"),
            ("planner", "Study Planner"),
        ]
        for agent_id, display_name in defaults:
            self._agents[agent_id] = AgentStatus(
                agent_id=agent_id,
                display_name=display_name,
                last_activity=datetime.now().isoformat(),
            )

    # =====================================================
    # EVENT EMISSION
    # =====================================================
    def emit(
        self,
        agent_id: str,
        event_type: str,
        data: dict = None,
        session_id: str = "",
        severity: str = "info",
    ):
        """
        Emit an event from an agent.
        
        Args:
            agent_id: Which agent is emitting ("tutor", "revision", etc.)
            event_type: Type of event ("step", "tool_call", "error", etc.)
            data: Event payload
            session_id: Optional session identifier
            severity: "info", "warning", "error", "critical"
        """
        if data is None:
            data = {}

        with self._lock:
            self._version_counter += 1
            version = self._version_counter

            event = AgentEvent(
                version=version,
                timestamp=datetime.now().isoformat(),
                agent_id=agent_id,
                event_type=event_type,
                data=data,
                session_id=session_id,
                severity=severity,
            )

            # Store in ring buffer
            self._events.append(event)

            # Update agent registry
            self._update_agent_status(agent_id, event)

        # Log it (outside lock)
        log_msg = f"[EVENT_BUS] [{agent_id.upper()}] {event_type}: {data.get('message', data.get('step', ''))}"
        if severity == "error":
            logger.error(log_msg)
        elif severity == "warning":
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

        if self._sink:
            try:
                self._sink(event)
            except Exception as exc:
                logger.warning("Event sink failed for %s/%s: %s", agent_id, event_type, exc)

    def _update_agent_status(self, agent_id: str, event: AgentEvent):
        """Update the agent registry based on the event. Must be called with lock held."""
        if agent_id not in self._agents:
            self._agents[agent_id] = AgentStatus(
                agent_id=agent_id,
                display_name=agent_id.title() + " Agent",
            )

        agent = self._agents[agent_id]
        agent.last_activity = event.timestamp

        if event.event_type == "task_start":
            agent.status = "running"
            agent.current_task = event.data.get("task", "Processing...")
            agent.total_requests += 1

        elif event.event_type == "task_complete":
            agent.status = "idle"
            agent.current_task = ""
            agent.total_success += 1
            latency = event.data.get("latency_ms", 0)
            if latency > 0:
                agent._latencies.append(latency)
                if len(agent._latencies) > 50:
                    agent._latencies = agent._latencies[-50:]
                agent.avg_latency_ms = round(
                    sum(agent._latencies) / len(agent._latencies), 1
                )
            quality = event.data.get("quality_score", 0)
            if quality > 0:
                agent.last_quality_score = quality
            agent.health = "healthy"

        elif event.event_type == "error":
            agent.total_errors += 1
            agent.health = "warning" if agent.total_errors < 5 else "critical"
            if event.severity == "critical":
                agent.status = "failed"
                agent.health = "critical"

        elif event.event_type == "state_change":
            new_state = event.data.get("state", "")
            if new_state:
                agent.status = new_state

    # =====================================================
    # POLLING INTERFACE — Versioned event retrieval
    # =====================================================
    def get_events_since(self, since_version: int) -> list[dict]:
        """
        Get all events with version > since_version.
        This is the core of the polling system.
        
        The frontend calls this every 1-2 seconds with the last
        version it received, and gets back only NEW events.
        """
        with self._lock:
            return [
                e.to_dict() for e in self._events
                if e.version > since_version
            ]

    def get_latest_version(self) -> int:
        """Get the current version counter."""
        return self._version_counter

    # =====================================================
    # QUERY INTERFACE (for REST API)
    # =====================================================
    def get_all_agents(self) -> list[dict]:
        """Get status of all registered agents."""
        with self._lock:
            return [agent.to_dict() for agent in self._agents.values()]

    def get_agent(self, agent_id: str) -> dict | None:
        """Get status of a specific agent."""
        with self._lock:
            agent = self._agents.get(agent_id)
            return agent.to_dict() if agent else None

    def get_recent_events(
        self,
        limit: int = 50,
        agent_id: str = None,
        severity: str = None,
        event_type: str = None,
    ) -> list[dict]:
        """Get recent events with optional filters."""
        with self._lock:
            events = list(self._events)

        events.reverse()  # Most recent first

        if agent_id:
            events = [e for e in events if e.agent_id == agent_id]
        if severity:
            events = [e for e in events if e.severity == severity]
        if event_type:
            events = [e for e in events if e.event_type == event_type]

        return [e.to_dict() for e in events[:limit]]

    def get_system_stats(self) -> dict:
        """Get overall system statistics."""
        with self._lock:
            total_requests = sum(a.total_requests for a in self._agents.values())
            total_errors = sum(a.total_errors for a in self._agents.values())
            total_success = sum(a.total_success for a in self._agents.values())
            active_agents = sum(1 for a in self._agents.values() if a.status == "running")

        return {
            "total_agents": len(self._agents),
            "active_agents": active_agents,
            "total_requests": total_requests,
            "total_success": total_success,
            "total_errors": total_errors,
            "success_rate": round((total_success / total_requests * 100) if total_requests > 0 else 100, 1),
            "total_events_buffered": len(self._events),
            "event_version": self._version_counter,
            "uptime_status": "operational",
        }

    def send_command(self, agent_id: str, command: str, payload: dict = None) -> dict:
        """Send a command to an agent (restart, pause, resume, etc.)."""
        if agent_id not in self._agents:
            return {"success": False, "error": f"Agent '{agent_id}' not found"}

        agent = self._agents[agent_id]

        if command == "restart":
            agent.status = "idle"
            agent.health = "healthy"
            agent.total_errors = 0
            agent.current_task = ""
            self.emit(agent_id, "state_change", {
                "state": "idle",
                "message": f"Agent {agent_id} restarted by admin",
                "command": "restart",
            })
            return {"success": True, "message": f"Agent '{agent_id}' restarted"}

        elif command == "pause":
            agent.status = "paused"
            self.emit(agent_id, "state_change", {
                "state": "paused",
                "message": f"Agent {agent_id} paused by admin",
                "command": "pause",
            })
            return {"success": True, "message": f"Agent '{agent_id}' paused"}

        elif command == "resume":
            agent.status = "idle"
            self.emit(agent_id, "state_change", {
                "state": "idle",
                "message": f"Agent {agent_id} resumed by admin",
                "command": "resume",
            })
            return {"success": True, "message": f"Agent '{agent_id}' resumed"}

        elif command == "clear_memory":
            self.emit(agent_id, "state_change", {
                "state": agent.status,
                "message": f"Agent {agent_id} memory cleared by admin",
                "command": "clear_memory",
            })
            return {"success": True, "message": f"Agent '{agent_id}' memory cleared"}

        else:
            return {"success": False, "error": f"Unknown command: {command}"}


# =====================================================
# GLOBAL SINGLETON
# =====================================================
event_bus = AgentEventBus()
