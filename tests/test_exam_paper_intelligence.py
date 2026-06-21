"""Route-level tests for paper upload + pattern intelligence (LLM mocked)."""

import json
import os
import shutil
import unittest
import uuid
from unittest.mock import patch

os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "true")
os.environ["DATABASE_URL"] = ""

from fastapi.testclient import TestClient

import main
from app.security import verify_firebase_user
from Logic.exam import agents
from services.exam_paper_service import UPLOAD_ROOT

ANALYZER_JSON = {
    "paper_title": "Unit Test 1",
    "exam_type": "unit_test",
    "questions": [
        {"question_number": "1", "section_name": "A", "question_text": "Define mole.", "marks": 2,
         "question_type": "short_answer", "intent": "definition", "difficulty": "easy", "topic": "Mole concept",
         "concept_tags": ["mole"], "confidence": 0.9},
        {"question_number": "2", "section_name": "A", "question_text": "Explain the mole concept.", "marks": 3,
         "question_type": "short_answer", "intent": "explanation", "difficulty": "medium", "topic": "Mole concept",
         "confidence": 0.8},
        {"question_number": "3", "section_name": "B", "question_text": "Calculate molarity.", "marks": 5,
         "question_type": "numerical", "intent": "numerical", "difficulty": "hard", "topic": "Concentration",
         "confidence": 0.7},
    ],
    "analysis": {"pattern_style": "school_style", "pattern_summary": "Mixed short and long answers."},
    "confidence": 0.85,
}

PROBABLE_JSON = {
    "probable_questions": [
        {"id": "P1", "question": "Explain the mole concept with an example.", "marks": 3,
         "question_type": "short_answer", "intent": "explanation", "topic": "Mole concept", "priority": "high",
         "based_on": "repeated concept"},
    ],
    "priority_topics": [{"topic": "Mole concept", "reason": "asked twice", "weight": "high"}],
    "strategy_summary": "Focus on the mole concept first.",
    "confidence": 0.7,
}


def fake_complete(role, messages, complexity="balanced", **kwargs):
    task = kwargs.get("task", "")
    if "analyze_exam_paper" in task:
        return json.dumps(ANALYZER_JSON)
    if "probable" in task:
        return json.dumps(PROBABLE_JSON)
    return "{}"


class ExamPaperRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        cls.client.__enter__()
        suffix = uuid.uuid4().hex[:8]
        cls.student = {"uid": f"exam-stu-{suffix}", "email": "stu@example.com"}
        cls.other = {"uid": f"exam-other-{suffix}", "email": "other@example.com"}

    @classmethod
    def tearDownClass(cls):
        main.app.dependency_overrides.clear()
        cls.client.__exit__(None, None, None)
        # Remove on-disk upload artifacts created during the tests.
        shutil.rmtree(UPLOAD_ROOT, ignore_errors=True)

    def setUp(self):
        self._patcher = patch.object(agents.model_gateway, "complete", fake_complete)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        main.app.dependency_overrides.clear()

    def _login(self, token):
        main.app.dependency_overrides[verify_firebase_user] = lambda: token

    def _upload(self, *, name="paper.txt", content=b"Q1. Define mole. (2)\nQ2. Explain mole. (3)",
                ctype="text/plain", subject="Chemistry", chapter="Mole concept", exam_type="unit_test"):
        return self.client.post(
            "/exam/papers/upload",
            files={"file": (name, content, ctype)},
            data={"subject": subject, "chapter_name": chapter, "exam_type": exam_type, "class_level": "Class 11"},
        )

    # ---- auth ----
    def test_upload_requires_auth(self):
        resp = self.client.post("/exam/papers/upload", files={"file": ("p.txt", b"x", "text/plain")})
        self.assertIn(resp.status_code, (401, 503))

    # ---- happy path upload + analysis ----
    def test_upload_text_paper_analyzes(self):
        self._login(self.student)
        resp = self._upload()
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["questions_extracted"], 3)
        self.assertEqual(body["paper"]["parse_status"], "analyzed")
        self.assertEqual(body["analysis"]["total_questions"], 3)
        # observed marks aggregate: 2 + 3 + 5
        self.assertEqual(body["analysis"]["total_marks"], 10.0)
        self.assertIn("mole concept", body["analysis"]["repeated_concepts"])

    def test_paper_lifecycle_and_ownership(self):
        self._login(self.student)
        paper_id = self._upload().json()["paper"]["id"]

        # questions endpoint
        q = self.client.get(f"/exam/papers/{paper_id}/questions")
        self.assertEqual(q.status_code, 200)
        self.assertEqual(q.json()["count"], 3)
        self.assertEqual(q.json()["questions"][0]["marks"], 2.0)

        # analysis endpoint
        a = self.client.get(f"/exam/papers/{paper_id}/analysis")
        self.assertEqual(a.status_code, 200)
        self.assertEqual(a.json()["parse_status"], "analyzed")

        # list shows it
        lst = self.client.get("/exam/papers")
        self.assertGreaterEqual(lst.json()["total"], 1)

        # another user cannot see it -> 404 (existence not leaked)
        self._login(self.other)
        self.assertEqual(self.client.get(f"/exam/papers/{paper_id}").status_code, 404)
        self.assertEqual(self.client.get(f"/exam/papers/{paper_id}/questions").status_code, 404)
        self.assertEqual(self.client.delete(f"/exam/papers/{paper_id}").status_code, 404)

    def test_reanalyze(self):
        self._login(self.student)
        paper_id = self._upload().json()["paper"]["id"]
        resp = self.client.post(f"/exam/papers/{paper_id}/reanalyze", json={"subject": "Physics"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["paper"]["subject"], "Physics")
        self.assertEqual(resp.json()["questions_extracted"], 3)

    def test_unsupported_type_rejected(self):
        self._login(self.student)
        resp = self.client.post(
            "/exam/papers/upload",
            files={"file": ("paper.docx", b"PKstuff", "application/msword")},
        )
        self.assertEqual(resp.status_code, 400)

    def test_image_upload_needs_ocr(self):
        self._login(self.student)
        resp = self._upload(name="scan.png", content=b"\x89PNG\r\n", ctype="image/png")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["paper"]["parse_status"], "needs_ocr")
        self.assertEqual(resp.json()["questions_extracted"], 0)

    def test_oversize_rejected(self):
        self._login(self.student)
        with patch("app.config.EXAM_UPLOAD_MAX_BYTES", 1024):
            big = b"a" * 4096
            resp = self.client.post(
                "/exam/papers/upload",
                files={"file": ("big.txt", big, "text/plain")},
            )
        self.assertEqual(resp.status_code, 413)

    # ---- pattern + probable ----
    def test_pattern_and_probable_flow(self):
        self._login(self.student)
        self._upload()
        self._upload()  # two analyzed papers

        # aggregate pattern
        pat = self.client.post("/exam/pattern/analyze", json={"subject": "Chemistry"})
        self.assertEqual(pat.status_code, 200, pat.text)
        analysis_id = pat.json()["id"]
        self.assertGreaterEqual(pat.json()["total_questions"], 6)

        # summary + grouped
        self.assertEqual(self.client.get("/exam/pattern/summary").status_code, 200)
        self.assertEqual(self.client.get("/exam/pattern/by-subject").status_code, 200)
        self.assertEqual(self.client.get("/exam/pattern/by-chapter").status_code, 200)

        # probable from stored analysis (grounding off to avoid DB content lookups)
        gen = self.client.post(
            "/exam/probable-questions/generate",
            json={"analysis_id": analysis_id, "use_syllabus_grounding": False},
        )
        self.assertEqual(gen.status_code, 200, gen.text)
        set_id = gen.json()["id"]
        self.assertTrue(gen.json()["probable_questions"])
        self.assertIn("not a prediction or guarantee", gen.json()["disclaimer"])

        # fetch + list
        self.assertEqual(self.client.get(f"/exam/probable-questions/{set_id}").status_code, 200)
        self.assertGreaterEqual(self.client.get("/exam/probable-questions").json()["total"], 1)

        # other user cannot read the set
        self._login(self.other)
        self.assertEqual(self.client.get(f"/exam/probable-questions/{set_id}").status_code, 404)

    def test_probable_unknown_analysis_id_is_404(self):
        self._login(self.student)
        resp = self.client.post(
            "/exam/probable-questions/generate",
            json={"analysis_id": 999999, "use_syllabus_grounding": False},
        )
        self.assertEqual(resp.status_code, 404)

    def test_pattern_analyze_without_papers_is_400(self):
        # A brand-new user with no analyzed papers.
        fresh = {"uid": f"exam-fresh-{uuid.uuid4().hex[:8]}", "email": "f@example.com"}
        self._login(fresh)
        resp = self.client.post("/exam/pattern/analyze", json={})
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
