import os
import unittest
from unittest.mock import patch

os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "true")
os.environ["DATABASE_URL"] = ""

from fastapi.testclient import TestClient

import main
import routers.admin as admin_router
from app.security import require_founder_admin
from database import SessionLocal
from models import UserProgress
from services import leaderboard_service
from services.ttl_cache import TTLCache


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class TTLCacheTests(unittest.TestCase):
    def test_serves_cached_value_within_ttl(self):
        clock = FakeClock()
        cache = TTLCache(clock=clock)
        calls = []
        builder = lambda: calls.append(1) or "value"

        self.assertEqual(cache.get_or_build("k", 30, builder), "value")
        clock.now += 29
        self.assertEqual(cache.get_or_build("k", 30, builder), "value")
        self.assertEqual(len(calls), 1)

    def test_rebuilds_after_expiry(self):
        clock = FakeClock()
        cache = TTLCache(clock=clock)
        calls = []
        builder = lambda: calls.append(1) or len(calls)

        self.assertEqual(cache.get_or_build("k", 30, builder), 1)
        clock.now += 31
        self.assertEqual(cache.get_or_build("k", 30, builder), 2)

    def test_invalidate_forces_rebuild(self):
        cache = TTLCache()
        calls = []
        builder = lambda: calls.append(1) or len(calls)

        cache.get_or_build("k", 300, builder)
        cache.invalidate("k")
        self.assertEqual(cache.get_or_build("k", 300, builder), 2)

    def test_eviction_respects_max_entries(self):
        cache = TTLCache(max_entries=2)
        for index in range(5):
            cache.get_or_build(f"k{index}", 300, lambda: index)
        self.assertLessEqual(len(cache._entries), 2)


class FakeFirebaseAuth:
    class UidIdentifier:
        def __init__(self, uid):
            self.uid = uid

    class _User:
        def __init__(self, uid):
            self.uid = uid
            self.display_name = f"Name {uid}"
            self.email = f"{uid}@example.com"

    class _Result:
        def __init__(self, users):
            self.users = users

    def __init__(self):
        self.calls = 0

    def get_users(self, identifiers):
        self.calls += 1
        return self._Result([self._User(identifier.uid) for identifier in identifiers])


class LeaderboardCacheTests(unittest.TestCase):
    USER_IDS = ["lb-cache-user-1", "lb-cache-user-2"]

    def setUp(self):
        leaderboard_service._firebase_lookup_cache.clear()
        self.db = SessionLocal()
        for index, user_id in enumerate(self.USER_IDS):
            self.db.add(UserProgress(user_id=user_id, xp=100000 + index))
        self.db.commit()

    def tearDown(self):
        self.db.query(UserProgress).filter(UserProgress.user_id.in_(self.USER_IDS)).delete(
            synchronize_session=False
        )
        self.db.commit()
        self.db.close()
        leaderboard_service._firebase_lookup_cache.clear()

    def test_firebase_lookup_is_cached_across_calls(self):
        fake_auth = FakeFirebaseAuth()
        with patch.object(leaderboard_service.security, "firebase_ready", return_value=True), patch.object(
            leaderboard_service.security, "firebase_auth", fake_auth
        ):
            leaderboard_service.build_leaderboard(self.db)
            leaderboard_service.build_leaderboard(self.db)
        self.assertEqual(fake_auth.calls, 1)

    def test_cache_does_not_widen_email_visibility(self):
        fake_auth = FakeFirebaseAuth()
        with patch.object(leaderboard_service.security, "firebase_ready", return_value=True), patch.object(
            leaderboard_service.security, "firebase_auth", fake_auth
        ):
            own_view = leaderboard_service.build_leaderboard(
                self.db, {"uid": self.USER_IDS[0], "email": "x@example.com"}
            )
            stranger_view = leaderboard_service.build_leaderboard(
                self.db, {"uid": "someone-else", "email": "y@example.com"}
            )

        own_rows = {row["user_id"]: row for row in own_view}
        stranger_rows = {row["user_id"]: row for row in stranger_view}
        self.assertIsNotNone(own_rows[self.USER_IDS[0]]["email"])
        self.assertIsNone(stranger_rows[self.USER_IDS[0]]["email"])
        # Both views came from one cached Firebase lookup.
        self.assertEqual(fake_auth.calls, 1)


class AdminConsoleCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        cls.client.__enter__()
        main.app.dependency_overrides[require_founder_admin] = lambda: {
            "uid": "founder",
            "email": "founder@example.com",
        }

    @classmethod
    def tearDownClass(cls):
        main.app.dependency_overrides.clear()
        cls.client.__exit__(None, None, None)

    def setUp(self):
        admin_router.admin_console_cache.clear()

    def tearDown(self):
        admin_router.admin_console_cache.clear()

    def test_console_payload_is_cached_between_polls(self):
        calls = []

        def fake_payload(db):
            calls.append(1)
            return {"build": len(calls)}

        with patch.object(admin_router, "build_admin_console_payload", fake_payload):
            first = self.client.get("/admin/console")
            second = self.client.get("/admin/console")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), {"build": 1})
        self.assertEqual(second.json(), {"build": 1})
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
