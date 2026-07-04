import os
import unittest
import uuid

os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "true")
os.environ["DATABASE_URL"] = ""

from fastapi.testclient import TestClient

import main
from app.security import verify_firebase_user
from database import SessionLocal
from models import SessionDetail, TestHistory, TopicPerformance, UserProgress


class ServerXpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        main.app.dependency_overrides.clear()
        cls.client.__exit__(None, None, None)

    def setUp(self):
        self.uid = f"xptest-{uuid.uuid4().hex[:8]}"
        main.app.dependency_overrides[verify_firebase_user] = lambda: {
            "uid": self.uid,
            "email": "xp@example.com",
        }
        self.db = SessionLocal()

    def tearDown(self):
        main.app.dependency_overrides.clear()
        test_ids = [row.id for row in self.db.query(TestHistory).filter(TestHistory.user_id == self.uid)]
        if test_ids:
            self.db.query(SessionDetail).filter(SessionDetail.test_id.in_(test_ids)).delete(
                synchronize_session=False
            )
        for model in (TestHistory, TopicPerformance, UserProgress):
            self.db.query(model).filter(model.user_id == self.uid).delete(synchronize_session=False)
        self.db.commit()
        self.db.close()

    def test_inflated_client_xp_is_ignored(self):
        response = self.client.post(
            "/submit-session",
            json={
                "user_id": self.uid,
                "topic": "alkanes",
                "score": 3,
                "total_questions": 5,
                "xp_earned": 99999,
                "session_type": "study_exam",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["session"]["xp"], 30)
        progress = self.db.query(UserProgress).filter(UserProgress.user_id == self.uid).first()
        self.assertEqual(progress.xp, 30)

    def test_score_is_clamped_before_xp(self):
        response = self.client.post(
            "/submit-session",
            json={
                "user_id": self.uid,
                "topic": "alkanes",
                "score": 50,
                "total_questions": 5,
                "session_type": "study_exam",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["session"]["xp"], 50)

    def test_save_test_also_computes_xp_server_side(self):
        response = self.client.post(
            "/save-test",
            json={
                "user_id": self.uid,
                "topic": "alkanes",
                "score": 4,
                "total_questions": 5,
                "xp_earned": 12345,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["session"]["xp"], 40)


if __name__ == "__main__":
    unittest.main()
