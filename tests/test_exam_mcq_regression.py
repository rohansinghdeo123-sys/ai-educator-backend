"""Regression: the existing MCQ / probable-question flow must keep working after
the exam-intelligence routers are added, and the new routes must be registered."""

import os
import unittest
import uuid
from unittest.mock import patch

os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "true")
os.environ["DATABASE_URL"] = ""

from fastapi.testclient import TestClient

import main
import routers.study as study_router
from app.security import verify_firebase_user

CANNED_MCQS = {
    "topic": "mole concept",
    "section_id": "mole_concept",
    "difficulty": "medium",
    "questions": [
        {"id": "Q1", "question": "What is a mole?", "options": ["A. unit", "B. atom", "C. ion", "D. bond"],
         "correct": "A", "explanation": "A mole is a counting unit.", "source": "mole_concept"},
    ],
}


class McqRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        cls.client.__enter__()
        cls.uid = f"mcq-stu-{uuid.uuid4().hex[:8]}"
        cls.student = {"uid": cls.uid, "email": "stu@example.com"}

    @classmethod
    def tearDownClass(cls):
        main.app.dependency_overrides.clear()
        cls.client.__exit__(None, None, None)

    def tearDown(self):
        main.app.dependency_overrides.clear()

    def _login(self):
        main.app.dependency_overrides[verify_firebase_user] = lambda: self.student

    def test_generate_mcqs_still_works(self):
        self._login()
        session_id = f"exam-{self.uid}-1"
        with patch.object(study_router, "generate_structured_mcqs", lambda **kwargs: CANNED_MCQS):
            resp = self.client.post(
                "/generate-mcqs",
                json={"topic": "mole concept", "section_id": "mole_concept", "session_id": session_id},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(len(resp.json()["questions"]), 1)

    def test_mcq_route_still_enforces_ownership(self):
        self._login()
        # A session owned by someone else must still be rejected.
        resp = self.client.post(
            "/generate-mcqs",
            json={"topic": "x", "section_id": "x", "session_id": "exam-someone-else-1"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_new_exam_routes_are_registered(self):
        paths = {route.path for route in main.app.routes}
        for expected in (
            "/exam/papers/upload",
            "/exam/pattern/analyze",
            "/exam/probable-questions/generate",
            "/exam/written-practice/submit",
            "/exam/student-weakness-report",
        ):
            self.assertIn(expected, paths)
        # Existing routes are still present.
        for existing in ("/generate-mcqs", "/generate-probable-questions", "/section-ai"):
            self.assertIn(existing, paths)


if __name__ == "__main__":
    unittest.main()
