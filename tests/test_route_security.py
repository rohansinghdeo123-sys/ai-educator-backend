import os
import unittest

os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "true")
os.environ["DATABASE_URL"] = ""

from fastapi.testclient import TestClient

import main
from app.security import verify_firebase_user

STUDENT = {"uid": "student-1", "email": "student@example.com"}


class RouteSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        main.app.dependency_overrides.clear()
        cls.client.__exit__(None, None, None)

    def tearDown(self):
        main.app.dependency_overrides.clear()

    def _login(self, token=STUDENT):
        main.app.dependency_overrides[verify_firebase_user] = lambda: token

    def test_health_endpoints_are_public(self):
        self.assertEqual(self.client.get("/health/live").status_code, 200)
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_protected_routes_require_auth(self):
        for method, path in [
            ("get", "/get-progress/u1"),
            ("get", "/coach/conversations/u1"),
            ("post", "/coach/chat"),
            ("get", "/leaderboard"),
            ("get", "/admin/me"),
            ("get", "/admin/console"),
            ("get", "/admin/prompts"),
        ]:
            resp = getattr(self.client, method)(path, **({"json": {}} if method == "post" else {}))
            self.assertIn(resp.status_code, (401, 503), f"{path} -> {resp.status_code}")

    def test_non_admin_gets_404_on_admin_routes(self):
        self._login()
        for path in ["/admin/me", "/admin/console", "/admin/overview", "/admin/prompts", "/admin/students"]:
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_cross_user_access_forbidden(self):
        self._login()
        self.assertEqual(self.client.get("/get-progress/other-user").status_code, 403)
        self.assertEqual(self.client.get("/coach/conversations/other-user").status_code, 403)
        self.assertEqual(self.client.get("/sessions/other-user").status_code, 403)

    def test_same_user_access_allowed(self):
        self._login()
        resp = self.client.get("/get-progress/student-1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["user_id"], "student-1")

    def test_foreign_study_session_forbidden(self):
        self._login()
        resp = self.client.post("/section-ai", json={
            "question": "What is matter?",
            "section_id": "matter_definition",
            "session_id": "coach-someone-else-abc",
        })
        self.assertEqual(resp.status_code, 403)

    def test_client_progress_overwrite_disabled_by_default(self):
        self._login()
        resp = self.client.post("/update-progress", json={
            "user_id": "student-1", "total_tests": 1, "total_questions": 1,
            "total_correct": 1, "xp": 99999, "streak": 1,
        })
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
