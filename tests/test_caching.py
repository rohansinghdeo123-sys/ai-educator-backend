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
    class _User:
        def __init__(self, uid, disabled=False):
            self.uid = uid
            self.display_name = f"Name {uid}"
            self.email = f"{uid}@example.com"
            self.disabled = disabled

    class _Page:
        def __init__(self, users, next_page_token=None):
            self.users = users
            self.next_page_token = next_page_token

    def __init__(self, user_ids, disabled_ids=None, page_size=1000, fail=False):
        self.calls = 0
        self.user_ids = list(user_ids)
        self.disabled_ids = set(disabled_ids or [])
        self.page_size = page_size
        self.fail = fail

    def list_users(self, page_token=None, max_results=1000):
        self.calls += 1
        if self.fail:
            raise RuntimeError("Firebase directory unavailable")

        start = int(page_token or 0)
        size = min(max_results, self.page_size)
        selected = self.user_ids[start:start + size]
        next_index = start + len(selected)
        next_page_token = str(next_index) if next_index < len(self.user_ids) else None
        return self._Page(
            [
                self._User(user_id, disabled=user_id in self.disabled_ids)
                for user_id in selected
            ],
            next_page_token=next_page_token,
        )


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
        fake_auth = FakeFirebaseAuth(self.USER_IDS)
        with patch.object(leaderboard_service.security, "firebase_ready", return_value=True), patch.object(
            leaderboard_service.security, "firebase_auth", fake_auth
        ):
            leaderboard_service.build_leaderboard(self.db)
            leaderboard_service.build_leaderboard(self.db)
        self.assertEqual(fake_auth.calls, 1)

    def test_cache_does_not_widen_email_visibility(self):
        fake_auth = FakeFirebaseAuth(self.USER_IDS)
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

    def test_includes_every_active_firebase_user_across_pages(self):
        directory_users = [
            *self.USER_IDS,
            *[f"lb-directory-user-{index}" for index in range(12)],
        ]
        disabled_user = directory_users[-1]
        fake_auth = FakeFirebaseAuth(
            directory_users,
            disabled_ids={disabled_user},
            page_size=4,
        )

        with patch.object(leaderboard_service.security, "firebase_ready", return_value=True), patch.object(
            leaderboard_service.security, "firebase_auth", fake_auth
        ):
            rows = leaderboard_service.build_leaderboard(
                self.db,
                {"uid": self.USER_IDS[0], "name": "Current Student"},
            )

        row_ids = {row["user_id"] for row in rows}
        self.assertEqual(row_ids, set(directory_users) - {disabled_user})
        self.assertGreater(fake_auth.calls, 1)
        self.assertEqual(rows[0]["user_id"], self.USER_IDS[1])
        zero_progress = next(row for row in rows if row["user_id"] == "lb-directory-user-0")
        self.assertEqual(zero_progress["xp"], 0)
        self.assertEqual(zero_progress["streak"], 0)
        self.assertEqual(zero_progress["total_tests"], 0)

    def test_falls_back_to_all_progress_rows_when_firebase_is_unavailable(self):
        fallback_ids = [f"lb-fallback-user-{index}" for index in range(12)]
        self.db.add_all(
            [
                UserProgress(user_id=user_id, xp=index, streak=index % 3)
                for index, user_id in enumerate(fallback_ids)
            ]
        )
        self.db.commit()

        fake_auth = FakeFirebaseAuth([], fail=True)
        try:
            with patch.object(leaderboard_service.security, "firebase_ready", return_value=True), patch.object(
                leaderboard_service.security, "firebase_auth", fake_auth
            ):
                rows = leaderboard_service.build_leaderboard(self.db)

            row_ids = {row["user_id"] for row in rows}
            self.assertTrue(set(fallback_ids).issubset(row_ids))
            self.assertGreaterEqual(len(rows), 12)
        finally:
            self.db.query(UserProgress).filter(UserProgress.user_id.in_(fallback_ids)).delete(
                synchronize_session=False
            )
            self.db.commit()


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
