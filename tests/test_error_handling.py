import os
import unittest

os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "true")
os.environ["DATABASE_URL"] = ""

from fastapi.testclient import TestClient

import main

SECRET_MARKER = "secret-internal-detail-xyz"


@main.app.get("/_test/unhandled-error")
def _raise_unhandled():
    raise RuntimeError(f"boom {SECRET_MARKER}")


class GlobalExceptionHandlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app, raise_server_exceptions=False)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_unhandled_error_returns_sanitized_500(self):
        resp = self.client.get("/_test/unhandled-error")
        self.assertEqual(resp.status_code, 500)
        body = resp.json()
        self.assertIn("request_id", body)
        self.assertTrue(body["request_id"])
        self.assertNotIn(SECRET_MARKER, resp.text)
        self.assertNotIn("RuntimeError", resp.text)
        self.assertNotIn("Traceback", resp.text)

    def test_500_echoes_caller_request_id(self):
        resp = self.client.get(
            "/_test/unhandled-error", headers={"x-request-id": "req-abc-123"}
        )
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()["request_id"], "req-abc-123")
        self.assertEqual(resp.headers.get("x-request-id"), "req-abc-123")

    def test_500_carries_cors_headers_for_allowed_origin(self):
        resp = self.client.get(
            "/_test/unhandled-error", headers={"origin": "http://localhost:3000"}
        )
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(
            resp.headers.get("access-control-allow-origin"), "http://localhost:3000"
        )

    def test_500_omits_cors_headers_for_unknown_origin(self):
        resp = self.client.get(
            "/_test/unhandled-error", headers={"origin": "https://evil.example.com"}
        )
        self.assertEqual(resp.status_code, 500)
        self.assertIsNone(resp.headers.get("access-control-allow-origin"))


if __name__ == "__main__":
    unittest.main()
