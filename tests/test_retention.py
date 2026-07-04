import os
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "true")
os.environ["DATABASE_URL"] = ""

from database import SessionLocal
from models import AgentChatMemory, AgentRuntimeRun, AgentRuntimeStep, ObservabilityEvent
from services.retention_service import prune_telemetry

MARK = "retentiontest"


def _old(days: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self._cleanup()

    def tearDown(self):
        self._cleanup()
        self.db.close()

    def _cleanup(self):
        self.db.query(AgentChatMemory).filter(AgentChatMemory.session_id.like(f"{MARK}%")).delete(
            synchronize_session=False
        )
        self.db.query(ObservabilityEvent).filter(ObservabilityEvent.session_id.like(f"{MARK}%")).delete(
            synchronize_session=False
        )
        self.db.query(AgentRuntimeStep).filter(AgentRuntimeStep.run_id.like(f"{MARK}%")).delete(
            synchronize_session=False
        )
        self.db.query(AgentRuntimeRun).filter(AgentRuntimeRun.run_id.like(f"{MARK}%")).delete(
            synchronize_session=False
        )
        self.db.commit()

    def test_expired_rows_are_pruned_and_fresh_rows_survive(self):
        self.db.add(AgentChatMemory(session_id=f"{MARK}-old", role="user", content="x", timestamp=_old(120)))
        self.db.add(AgentChatMemory(session_id=f"{MARK}-new", role="user", content="x", timestamp=_old(1)))
        self.db.add(ObservabilityEvent(session_id=f"{MARK}-old", created_at=_old(60)))
        self.db.add(ObservabilityEvent(session_id=f"{MARK}-new", created_at=_old(1)))
        self.db.add(AgentRuntimeRun(run_id=f"{MARK}-old-run", started_at=_old(60)))
        self.db.add(AgentRuntimeStep(run_id=f"{MARK}-old-run", step_name="s"))
        self.db.add(AgentRuntimeRun(run_id=f"{MARK}-new-run", started_at=_old(1)))
        self.db.commit()

        prune_telemetry(self.db)

        chat_left = [
            row.session_id
            for row in self.db.query(AgentChatMemory).filter(AgentChatMemory.session_id.like(f"{MARK}%"))
        ]
        self.assertEqual(chat_left, [f"{MARK}-new"])
        events_left = [
            row.session_id
            for row in self.db.query(ObservabilityEvent).filter(ObservabilityEvent.session_id.like(f"{MARK}%"))
        ]
        self.assertEqual(events_left, [f"{MARK}-new"])
        runs_left = [
            row.run_id
            for row in self.db.query(AgentRuntimeRun).filter(AgentRuntimeRun.run_id.like(f"{MARK}%"))
        ]
        self.assertEqual(runs_left, [f"{MARK}-new-run"])
        steps_left = (
            self.db.query(AgentRuntimeStep).filter(AgentRuntimeStep.run_id.like(f"{MARK}%")).count()
        )
        self.assertEqual(steps_left, 0)

    def test_zero_window_disables_group(self):
        self.db.add(AgentChatMemory(session_id=f"{MARK}-old", role="user", content="x", timestamp=_old(120)))
        self.db.commit()
        os.environ["CHAT_MEMORY_RETENTION_DAYS"] = "0"
        try:
            prune_telemetry(self.db)
        finally:
            os.environ.pop("CHAT_MEMORY_RETENTION_DAYS", None)
        survivors = (
            self.db.query(AgentChatMemory).filter(AgentChatMemory.session_id.like(f"{MARK}%")).count()
        )
        self.assertEqual(survivors, 1)


if __name__ == "__main__":
    unittest.main()
