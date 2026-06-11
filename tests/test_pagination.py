import os
import unittest

os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "true")
os.environ["DATABASE_URL"] = ""

from fastapi.testclient import TestClient

import main
from app.security import verify_firebase_user
from database import SessionLocal
from models import TestHistory, TopicPerformance, UserProgress
from services.progress_service import create_test_history

USER_ID = "pagination-tester"
TOKEN = {"uid": USER_ID, "email": "pagination@example.com"}
SEEDED = 7


class HistoryPaginationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        cls.client.__enter__()
        main.app.dependency_overrides[verify_firebase_user] = lambda: TOKEN
        cls._wipe_user_rows()
        db = SessionLocal()
        try:
            for index in range(SEEDED):
                create_test_history(
                    db,
                    user_id=USER_ID,
                    topic=f"topic-{index}",
                    score=index,
                    total_questions=10,
                    xp_earned=index * 10,
                )
            db.commit()
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        cls._wipe_user_rows()
        main.app.dependency_overrides.clear()
        cls.client.__exit__(None, None, None)

    @classmethod
    def _wipe_user_rows(cls):
        db = SessionLocal()
        try:
            db.query(TestHistory).filter(TestHistory.user_id == USER_ID).delete()
            db.query(TopicPerformance).filter(TopicPerformance.user_id == USER_ID).delete()
            db.query(UserProgress).filter(UserProgress.user_id == USER_ID).delete()
            db.commit()
        finally:
            db.close()

    def test_sessions_default_returns_all_with_total(self):
        resp = self.client.get(f"/sessions/{USER_ID}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total"], SEEDED)
        self.assertEqual(len(body["sessions"]), SEEDED)
        self.assertEqual(body["offset"], 0)

    def test_sessions_pagination_window(self):
        resp = self.client.get(f"/sessions/{USER_ID}?limit=3&offset=0")
        body = resp.json()
        self.assertEqual(len(body["sessions"]), 3)
        self.assertEqual(body["total"], SEEDED)

        # Most recent first; second page continues without overlap.
        first_page_topics = [s["topic"] for s in body["sessions"]]
        resp2 = self.client.get(f"/sessions/{USER_ID}?limit=3&offset=3")
        second_page_topics = [s["topic"] for s in resp2.json()["sessions"]]
        self.assertEqual(first_page_topics, ["topic_6", "topic_5", "topic_4"])
        self.assertEqual(second_page_topics, ["topic_3", "topic_2", "topic_1"])

    def test_sessions_limit_is_capped(self):
        resp = self.client.get(f"/sessions/{USER_ID}?limit=9999")
        self.assertEqual(resp.status_code, 422)

    def test_test_history_pagination_window(self):
        resp = self.client.get(f"/test-history/{USER_ID}?limit=2&offset=1")
        self.assertEqual(resp.status_code, 200)
        rows = resp.json()
        self.assertEqual(len(rows), 2)
        # Oldest-first ordering preserved for progress charts.
        self.assertEqual([row["topic"] for row in rows], ["topic_1", "topic_2"])

    def test_test_history_default_returns_all(self):
        resp = self.client.get(f"/test-history/{USER_ID}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), SEEDED)


if __name__ == "__main__":
    unittest.main()
