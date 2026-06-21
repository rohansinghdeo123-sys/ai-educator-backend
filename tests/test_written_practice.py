"""Route-level tests for written-answer practice + weakness tracking (LLM mocked)."""

import json
import os
import unittest
import uuid
from unittest.mock import patch

os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "true")
os.environ["DATABASE_URL"] = ""

from fastapi.testclient import TestClient

import main
from app.security import verify_firebase_user
from Logic.exam import agents

QUESTION_JSON = {
    "question_text": "Explain the mole concept with an example.",
    "question_type": "long_answer",
    "marks_total": 5,
    "topic": "Mole concept",
    "command_word": "explain",
    "expected_points": ["mole is a counting unit", "equal to Avogadro number", "used for amount of substance"],
}

EVAL_JSON = {
    "marks_awarded": 3,
    "marks_total": 5,
    "covered_points": ["mole is a counting unit"],
    "missing_points": ["equal to Avogadro number"],
    "incorrect_points": [],
    "weak_explanation_areas": ["units"],
    "presentation_feedback": "Use clear steps.",
    "teacher_feedback": "Good start, add more detail.",
    "model_answer": "A mole is ...",
    "improve_to_full_marks": "State Avogadro's number explicitly.",
    "rubric_scores": {"concept_accuracy": 0.6, "exam_presentation": 0.3},
    "next_question_suggestion": "Define molarity.",
    "weakness_tags": [{"topic": "Mole concept", "weakness_type": "missing_key_points", "note": "Avogadro missing"}],
}


def fake_complete(role, messages, complexity="balanced", **kwargs):
    task = kwargs.get("task", "")
    if "generate_written_question" in task:
        return json.dumps(QUESTION_JSON)
    if "evaluate_written_answer" in task:
        return json.dumps(EVAL_JSON)
    return "{}"


class WrittenPracticeRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        cls.client.__enter__()
        suffix = uuid.uuid4().hex[:8]
        cls.student = {"uid": f"wp-stu-{suffix}", "email": "stu@example.com"}
        cls.other = {"uid": f"wp-other-{suffix}", "email": "other@example.com"}

    @classmethod
    def tearDownClass(cls):
        main.app.dependency_overrides.clear()
        cls.client.__exit__(None, None, None)

    def setUp(self):
        self._patcher = patch.object(agents.model_gateway, "complete", fake_complete)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        main.app.dependency_overrides.clear()

    def _login(self, token):
        main.app.dependency_overrides[verify_firebase_user] = lambda: token

    def _start(self):
        return self.client.post(
            "/exam/written-practice/start",
            json={"subject": "Chemistry", "chapter_name": "Mole concept", "topic": "Mole concept", "marks_focus": "5"},
        ).json()["id"]

    def test_requires_auth(self):
        self.assertIn(self.client.post("/exam/written-practice/start", json={}).status_code, (401, 503))

    def test_full_flow(self):
        self._login(self.student)
        session_id = self._start()

        # generate a question — expected points must NOT be exposed
        q = self.client.post(
            "/exam/written-practice/question",
            json={"session_id": session_id, "use_syllabus_grounding": False},
        )
        self.assertEqual(q.status_code, 200, q.text)
        qbody = q.json()
        attempt_id = qbody["attempt_id"]
        self.assertEqual(qbody["question_text"], QUESTION_JSON["question_text"])
        self.assertNotIn("expected_points", qbody)

        # submit an answer
        s = self.client.post(
            "/exam/written-practice/submit",
            json={"attempt_id": attempt_id, "answer": "A mole is a counting unit.", "use_syllabus_grounding": False},
        )
        self.assertEqual(s.status_code, 200, s.text)
        sbody = s.json()
        self.assertEqual(sbody["feedback"]["marks_awarded"], 3.0)
        self.assertEqual(sbody["feedback"]["score_percentage"], 60.0)
        self.assertIn("equal to Avogadro number", sbody["feedback"]["missing_points"])
        self.assertGreaterEqual(sbody["weaknesses_updated"], 1)

        # fetch feedback again
        fb = self.client.get(f"/exam/written-practice/attempts/{attempt_id}/feedback")
        self.assertEqual(fb.status_code, 200)
        self.assertEqual(fb.json()["marks_total"], 5.0)

        # history + session detail
        hist = self.client.get("/exam/written-practice/history")
        self.assertGreaterEqual(hist.json()["total"], 1)
        detail = self.client.get(f"/exam/written-practice/sessions/{session_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.json()["attempts"]), 1)

        # weakness report populated
        report = self.client.get("/exam/student-weakness-report")
        self.assertGreaterEqual(report.json()["total"], 1)
        by_topic = self.client.get("/exam/student-weakness-report/by-topic")
        self.assertGreaterEqual(by_topic.json()["total_topics"], 1)

        # recalculate rebuilds from stored feedback
        recalc = self.client.post("/exam/student-weakness-report/recalculate")
        self.assertEqual(recalc.status_code, 200)
        self.assertGreaterEqual(recalc.json()["attempts_processed"], 1)

    def test_ad_hoc_submit_without_generate(self):
        self._login(self.student)
        session_id = self._start()
        resp = self.client.post(
            "/exam/written-practice/submit",
            json={
                "session_id": session_id,
                "question_text": "Define a mole.",
                "marks_total": 2,
                "answer": "A mole is Avogadro number of particles.",
                "expected_points": ["Avogadro number", "amount of substance"],
                "use_syllabus_grounding": False,
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        # marks_total is authoritative from the question (2), not the LLM's echo (5).
        self.assertEqual(resp.json()["feedback"]["marks_total"], 2.0)
        # canned marks_awarded (3) is clamped to the question's marks_total (2).
        self.assertLessEqual(resp.json()["feedback"]["marks_awarded"], 2.0)

    def test_cross_user_isolation(self):
        self._login(self.student)
        session_id = self._start()
        q = self.client.post(
            "/exam/written-practice/question",
            json={"session_id": session_id, "use_syllabus_grounding": False},
        )
        attempt_id = q.json()["attempt_id"]

        self._login(self.other)
        self.assertEqual(self.client.get(f"/exam/written-practice/sessions/{session_id}").status_code, 404)
        self.assertEqual(
            self.client.get(f"/exam/written-practice/attempts/{attempt_id}/feedback").status_code, 404
        )
        self.assertEqual(
            self.client.post(
                "/exam/written-practice/submit",
                json={"attempt_id": attempt_id, "answer": "x", "use_syllabus_grounding": False},
            ).status_code,
            404,
        )

    def test_empty_answer_rejected_by_validation(self):
        self._login(self.student)
        session_id = self._start()
        resp = self.client.post(
            "/exam/written-practice/submit",
            json={"session_id": session_id, "question_text": "Q", "marks_total": 2, "answer": ""},
        )
        self.assertEqual(resp.status_code, 422)  # answer min_length=1


if __name__ == "__main__":
    unittest.main()
