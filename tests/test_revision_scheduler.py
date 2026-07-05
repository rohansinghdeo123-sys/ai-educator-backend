import os
import unittest
import uuid
from datetime import datetime, timedelta

os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "true")
os.environ["DATABASE_URL"] = ""

from fastapi.testclient import TestClient

import main
from app.security import verify_firebase_user
from database import SessionLocal
from models import TopicPerformance
from services.revision_scheduler import build_revision_queue, score_topic, summarize_queue


class _Row:
    def __init__(self, **kwargs):
        self.topic = kwargs.get("topic", "Topic")
        self.attempts = kwargs.get("attempts", 10)
        self.correct = kwargs.get("correct", 8)
        self.weak = kwargs.get("weak", False)
        self.trend_score = kwargs.get("trend_score", 0.0)
        self.last_practiced = kwargs.get("last_practiced")


NOW = datetime(2026, 7, 5, 12, 0, 0)


class RevisionSchedulerLogicTests(unittest.TestCase):
    def test_zero_attempts_rows_are_skipped(self):
        queue = build_revision_queue([_Row(attempts=0)], now=NOW)
        self.assertEqual(queue, [])

    def test_overdue_topic_outranks_fresh_topic(self):
        stale = _Row(topic="Thermodynamics", last_practiced=NOW - timedelta(days=20))
        fresh = _Row(topic="Alkanes", last_practiced=NOW - timedelta(hours=2))
        queue = build_revision_queue([fresh, stale], now=NOW)
        self.assertEqual(queue[0]["topic"], "Thermodynamics")
        self.assertEqual(queue[0]["bucket"], "overdue")
        self.assertEqual(queue[1]["bucket"], "fresh")
        self.assertGreater(queue[0]["priority"], queue[1]["priority"])

    def test_weak_topic_gets_priority_boost_and_strengthen_bucket(self):
        weak = _Row(
            topic="Mole Concept",
            attempts=10,
            correct=4,
            weak=True,
            last_practiced=NOW - timedelta(hours=6),
        )
        strong = _Row(
            topic="Atomic Structure",
            attempts=10,
            correct=9,
            last_practiced=NOW - timedelta(hours=6),
        )
        queue = build_revision_queue([strong, weak], now=NOW)
        self.assertEqual(queue[0]["topic"], "Mole Concept")
        self.assertEqual(queue[0]["bucket"], "strengthen")
        self.assertEqual(queue[0]["suggested_mode"], "deep_review")

    def test_never_practiced_timestamp_treated_as_overdue(self):
        entry = score_topic(
            topic="Equilibrium",
            attempts=12,
            correct=9,
            weak=False,
            trend_score=0.0,
            last_practiced=None,
            now=NOW,
        )
        self.assertEqual(entry["bucket"], "overdue")
        self.assertLess(entry["retention_estimate"], 0.2)

    def test_retention_and_priority_are_bounded(self):
        entry = score_topic(
            topic="X",
            attempts=100,
            correct=0,
            weak=True,
            trend_score=-30.0,
            last_practiced=NOW - timedelta(days=365),
            now=NOW,
        )
        self.assertGreaterEqual(entry["retention_estimate"], 0.0)
        self.assertLessEqual(entry["priority"], 100.0)

    def test_declining_trend_adds_priority(self):
        base = dict(
            topic="Bonding",
            attempts=10,
            correct=8,
            weak=False,
            last_practiced=NOW - timedelta(days=3),
            now=NOW,
        )
        steady = score_topic(trend_score=0.0, **base)
        declining = score_topic(trend_score=-15.0, **base)
        self.assertGreater(declining["priority"], steady["priority"])

    def test_limit_is_respected_and_clamped(self):
        rows = [
            _Row(topic=f"T{i}", last_practiced=NOW - timedelta(days=i)) for i in range(30)
        ]
        self.assertEqual(len(build_revision_queue(rows, now=NOW, limit=5)), 5)
        self.assertEqual(len(build_revision_queue(rows, now=NOW, limit=999)), 25)

    def test_summary_counts_and_empty_message(self):
        empty = summarize_queue([])
        self.assertIsNone(empty["top_pick"])
        self.assertIn("No revision data yet", empty["message"])

        queue = build_revision_queue(
            [_Row(topic="Thermo", last_practiced=NOW - timedelta(days=20))], now=NOW
        )
        summary = summarize_queue(queue)
        self.assertEqual(summary["overdue"], 1)
        self.assertIn("Thermo", summary["message"])


class RevisionQueueRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        main.app.dependency_overrides.clear()
        cls.client.__exit__(None, None, None)

    def setUp(self):
        self.uid = f"revq-{uuid.uuid4().hex[:8]}"
        main.app.dependency_overrides[verify_firebase_user] = lambda: {
            "uid": self.uid,
            "email": "revq@example.com",
        }
        self.db = SessionLocal()

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.db.query(TopicPerformance).filter(TopicPerformance.user_id == self.uid).delete(
            synchronize_session=False
        )
        self.db.commit()
        self.db.close()

    def _seed(self, topic, attempts, correct, days_ago, weak=False):
        self.db.add(
            TopicPerformance(
                user_id=self.uid,
                topic=topic,
                attempts=attempts,
                correct=correct,
                weak=weak,
                trend_score=0.0,
                last_practiced=datetime.utcnow() - timedelta(days=days_ago),
            )
        )
        self.db.commit()

    def test_queue_orders_overdue_first(self):
        self._seed("Thermodynamics", 12, 9, days_ago=15)
        self._seed("Alkanes", 12, 11, days_ago=0)

        response = self.client.get(f"/revision/queue/{self.uid}")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["queue"][0]["topic"], "Thermodynamics")
        self.assertEqual(body["queue"][0]["bucket"], "overdue")
        self.assertEqual(body["summary"]["overdue"], 1)
        self.assertTrue(body["queue"][0]["reason"])

    def test_empty_queue_for_new_user(self):
        response = self.client.get(f"/revision/queue/{self.uid}")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["queue"], [])
        self.assertIn("No revision data yet", body["summary"]["message"])

    def test_cannot_read_another_users_queue(self):
        response = self.client.get("/revision/queue/someone-else")
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
