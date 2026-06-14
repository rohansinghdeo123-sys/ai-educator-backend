"""Tests for the legacy (section-AI / revision / exam / tutor) pipeline upgrades:

- Tutor doubt memory is now persisted in the agent_chat_memory table
  (survives restart, consistent across instances) instead of a process dict.
- Non-coach agent runs get a durable cost/quality trace via persist_agent_trace.
"""

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models import ModelToolTrace  # noqa: F401  (ensures table is registered)


def _make_session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


class TutorDbMemoryTests(unittest.TestCase):
    def setUp(self):
        self.Session = _make_session_factory()

    def test_memory_persists_and_reloads_in_order(self):
        import Logic.agents.tutor_agent as tutor

        with patch.object(tutor, "SessionLocal", self.Session):
            tutor._save_turn("sess-1", "what is an alkane?", "A saturated hydrocarbon.")
            tutor._save_turn("sess-1", "and an alkene?", "It has a C=C double bond.")
            memory = tutor._load_session_memory("sess-1")

        self.assertEqual([m["role"] for m in memory], ["user", "assistant", "user", "assistant"])
        self.assertEqual(memory[0]["content"], "what is an alkane?")
        self.assertEqual(memory[-1]["content"], "It has a C=C double bond.")

    def test_memory_is_isolated_per_session(self):
        import Logic.agents.tutor_agent as tutor

        with patch.object(tutor, "SessionLocal", self.Session):
            tutor._save_turn("sess-a", "qa", "aa")
            tutor._save_turn("sess-b", "qb", "ab")
            self.assertEqual(len(tutor._load_session_memory("sess-a")), 2)
            self.assertEqual(len(tutor._load_session_memory("sess-b")), 2)

    def test_reset_clears_only_target_session(self):
        import Logic.agents.tutor_agent as tutor

        with patch.object(tutor, "SessionLocal", self.Session):
            tutor._save_turn("sess-a", "qa", "aa")
            tutor._save_turn("sess-b", "qb", "ab")
            tutor.reset_tutor_session("sess-a")
            self.assertEqual(tutor._load_session_memory("sess-a"), [])
            self.assertEqual(len(tutor._load_session_memory("sess-b")), 2)

    def test_load_returns_only_last_n_turns(self):
        import Logic.agents.tutor_agent as tutor

        with patch.object(tutor, "SessionLocal", self.Session):
            for index in range(8):
                tutor._save_turn("sess-1", f"q{index}", f"a{index}")
            memory = tutor._load_session_memory("sess-1", limit=4)

        self.assertEqual(len(memory), 4)
        # Most recent rows, still in chronological order.
        self.assertEqual(memory[-1]["content"], "a7")


class AgentTraceTests(unittest.TestCase):
    def setUp(self):
        self.Session = _make_session_factory()

    def test_persist_agent_trace_writes_turn_and_model_rows(self):
        import Logic.observability_store as obs

        with patch.object(obs, "SessionLocal", self.Session):
            result = obs.persist_agent_trace(
                agent="revision",
                turn_id="run_abc",
                session_id="sess-1",
                status="success",
                latency_ms=1234,
                quality={"score": 0.8, "passed": True},
                model_calls=[
                    {
                        "role": "tutor",
                        "provider": "groq",
                        "model": "m1",
                        "status": "success",
                        "latency_ms": 900,
                        "estimated_input_tokens": 100,
                        "estimated_output_tokens": 200,
                        "estimated_cost_usd": 0.0012,
                    }
                ],
                metadata={"mode": "summary", "intent": "revision"},
            )

        self.assertEqual(result["model_calls"], 1)

        db = self.Session()
        try:
            rows = db.query(ModelToolTrace).filter(ModelToolTrace.turn_id == "run_abc").all()
            self.assertEqual(sorted(r.trace_type for r in rows), ["model", "turn"])
            turn = next(r for r in rows if r.trace_type == "turn")
            self.assertEqual(turn.name, "revision_turn")
            self.assertEqual(turn.session_id, "sess-1")
            self.assertAlmostEqual(turn.estimated_cost_usd, 0.0012, places=6)
            self.assertEqual(turn.estimated_input_tokens, 100)
            self.assertTrue(turn.metadata_json.get("quality", {}).get("passed"))
            self.assertEqual(turn.metadata_json.get("mode"), "summary")
        finally:
            db.close()

    def test_persist_agent_trace_swallows_session_failure(self):
        import Logic.observability_store as obs

        def boom():
            raise RuntimeError("db unavailable")

        with patch.object(obs, "SessionLocal", boom):
            result = obs.persist_agent_trace(agent="exam", turn_id="t1")

        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
