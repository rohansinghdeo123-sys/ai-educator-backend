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
from models import UserProfile, UserProgress
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


class LeaderboardTests(unittest.TestCase):
    """The leaderboard ranks active students by XP, names them from the signup
    profile (never Firebase), shows full names, and caps at the top N."""

    SEEDED_IDS = ["lb-user-1", "lb-user-2", "lb-user-3", "lb-inactive"]

    def setUp(self):
        self.db = SessionLocal()
        self._cleanup()
        self.db.add_all(
            [
                UserProgress(user_id="lb-user-1", xp=300, streak=4, total_tests=5),
                UserProgress(user_id="lb-user-2", xp=150, streak=2, total_tests=3),
                UserProgress(user_id="lb-inactive", xp=0, streak=0, total_tests=0),
            ]
        )
        self.db.add_all(
            [
                UserProfile(user_id="lb-user-1", email="a@example.com", display_name="Aarav Sharma"),
                UserProfile(user_id="lb-user-2", email="b@example.com", display_name="Diya Patel"),
            ]
        )
        self.db.commit()

    def _cleanup(self):
        self.db.query(UserProgress).filter(
            UserProgress.user_id.in_(self.SEEDED_IDS)
        ).delete(synchronize_session=False)
        self.db.query(UserProfile).filter(
            UserProfile.user_id.in_(self.SEEDED_IDS)
        ).delete(synchronize_session=False)
        self.db.commit()

    def tearDown(self):
        self._cleanup()
        self.db.close()

    def test_full_signup_names_not_firebase_or_masked(self):
        rows = leaderboard_service.build_leaderboard(self.db, limit=0)
        by_id = {row["user_id"]: row for row in rows}
        # Full name from the profile — not "Aarav S." and not a Firebase name.
        self.assertEqual(by_id["lb-user-1"]["display_name"], "Aarav Sharma")
        self.assertEqual(by_id["lb-user-2"]["display_name"], "Diya Patel")

    def test_excludes_inactive_students(self):
        rows = leaderboard_service.build_leaderboard(self.db, limit=0)
        ids = {row["user_id"] for row in rows}
        self.assertNotIn("lb-inactive", ids)

    def test_ranks_by_xp_descending(self):
        rows = leaderboard_service.build_leaderboard(self.db, limit=0)
        seeded = [row for row in rows if row["user_id"] in {"lb-user-1", "lb-user-2"}]
        self.assertEqual(seeded[0]["user_id"], "lb-user-1")
        self.assertEqual(seeded[1]["user_id"], "lb-user-2")
        self.assertLess(seeded[0]["rank"], seeded[1]["rank"])

    def test_anonymous_name_when_profile_missing(self):
        self.db.add(UserProgress(user_id="lb-user-3", xp=80, streak=1, total_tests=1))
        self.db.commit()
        rows = leaderboard_service.build_leaderboard(self.db, limit=0)
        row = next(item for item in rows if item["user_id"] == "lb-user-3")
        self.assertTrue(row["display_name"].startswith("Student "))

    def test_email_visible_only_to_self_or_admin(self):
        own = leaderboard_service.build_leaderboard(
            self.db, {"uid": "lb-user-1", "email": "a@example.com"}, limit=0
        )
        stranger = leaderboard_service.build_leaderboard(
            self.db, {"uid": "lb-user-2", "email": "b@example.com"}, limit=0
        )
        own_rows = {row["user_id"]: row for row in own}
        stranger_rows = {row["user_id"]: row for row in stranger}
        self.assertEqual(own_rows["lb-user-1"]["email"], "a@example.com")
        self.assertIsNone(stranger_rows["lb-user-1"]["email"])

    def test_caps_at_top_twenty(self):
        cap_ids = [f"lb-cap-{index}" for index in range(25)]
        self.db.add_all(
            [
                UserProgress(user_id=user_id, xp=10000 + index, streak=1, total_tests=1)
                for index, user_id in enumerate(cap_ids)
            ]
        )
        self.db.commit()
        try:
            rows = leaderboard_service.build_leaderboard(self.db, limit=20)
            self.assertEqual(len(rows), 20)
            self.assertEqual(rows[0]["rank"], 1)
            self.assertEqual(rows[-1]["rank"], 20)
        finally:
            self.db.query(UserProgress).filter(
                UserProgress.user_id.in_(cap_ids)
            ).delete(synchronize_session=False)
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
