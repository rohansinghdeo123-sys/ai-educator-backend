"""Tests for the admin content-ingestion report endpoint + shared builder."""

import os
import unittest

os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "true")
os.environ["DATABASE_URL"] = ""

from fastapi.testclient import TestClient

import main
from app import config
from app.security import verify_firebase_user
from database import SessionLocal
from services.content_report_service import build_content_report

ADMIN = {"uid": "admin-1", "email": "admin@example.com"}
STUDENT = {"uid": "student-1", "email": "student@example.com"}


class ContentReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        cls.client.__enter__()
        # Register the test admin email on the live config set (read at call time
        # by is_backend_admin), independent of import order.
        config.BACKEND_ADMIN_EMAILS.add("admin@example.com")

    @classmethod
    def tearDownClass(cls):
        config.BACKEND_ADMIN_EMAILS.discard("admin@example.com")
        main.app.dependency_overrides.clear()
        cls.client.__exit__(None, None, None)

    def tearDown(self):
        main.app.dependency_overrides.clear()

    def _login(self, token):
        main.app.dependency_overrides[verify_firebase_user] = lambda: token

    def test_report_requires_auth(self):
        self.assertIn(self.client.get("/admin/content/ingestion-report").status_code, (401, 503))

    def test_non_admin_gets_404(self):
        self._login(STUDENT)
        self.assertEqual(self.client.get("/admin/content/ingestion-report").status_code, 404)

    def test_admin_gets_report_with_expected_shape(self):
        self._login(ADMIN)
        resp = self.client.get("/admin/content/ingestion-report")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        for key in ("generated_at", "totals", "by_status", "by_class", "by_subject", "chapters"):
            self.assertIn(key, body)
        for key in ("chapters", "pages", "concepts", "chunks", "embedded_chunks"):
            self.assertIn(key, body["totals"])

    def test_status_filter_and_full_flag(self):
        self._login(ADMIN)
        resp = self.client.get("/admin/content/ingestion-report?status=published&full=true")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status_filter"], "published")
        for ch in resp.json()["chapters"]:
            self.assertEqual(ch["status"], "published")
            # full=true never truncates the concept list
            self.assertFalse(ch.get("concepts_truncated"))

    def test_builder_matches_endpoint(self):
        # The shared builder (used by the CLI) returns the same totals as the API.
        self._login(ADMIN)
        api_totals = self.client.get("/admin/content/ingestion-report").json()["totals"]
        db = SessionLocal()
        try:
            builder_totals = build_content_report(db)["totals"]
        finally:
            db.close()
        self.assertEqual(api_totals, builder_totals)


if __name__ == "__main__":
    unittest.main()
