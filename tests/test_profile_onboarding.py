import os
import unittest

os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "true")
os.environ["DATABASE_URL"] = ""

from fastapi.testclient import TestClient

import main
from app.security import verify_firebase_user
from database import SessionLocal
from models import UserProfile, UserProgress
from services import leaderboard_service


class ProfileOnboardingTests(unittest.TestCase):
    USER_ID = "profile-onboarding-user"
    OTHER_USER_ID = "profile-onboarding-peer"

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        main.app.dependency_overrides.clear()
        cls.client.__exit__(None, None, None)

    def setUp(self):
        self.db = SessionLocal()
        self.db.query(UserProfile).filter(
            UserProfile.user_id.in_([self.USER_ID, self.OTHER_USER_ID])
        ).delete(synchronize_session=False)
        self.db.query(UserProgress).filter(
            UserProgress.user_id.in_([self.USER_ID, self.OTHER_USER_ID])
        ).delete(synchronize_session=False)
        self.db.commit()
        main.app.dependency_overrides[verify_firebase_user] = lambda: {
            "uid": self.USER_ID,
            "email": "Student@Example.com",
            "name": "Rohan Singh",
        }

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.db.query(UserProfile).filter(
            UserProfile.user_id.in_([self.USER_ID, self.OTHER_USER_ID])
        ).delete(synchronize_session=False)
        self.db.query(UserProgress).filter(
            UserProgress.user_id.in_([self.USER_ID, self.OTHER_USER_ID])
        ).delete(synchronize_session=False)
        self.db.commit()
        self.db.close()

    def test_get_creates_incomplete_profile_from_verified_identity(self):
        response = self.client.get("/profile/me")
        self.assertEqual(response.status_code, 200)
        profile = response.json()
        self.assertEqual(profile["user_id"], self.USER_ID)
        self.assertEqual(profile["email"], "student@example.com")
        self.assertEqual(profile["display_name"], "Rohan Singh")
        self.assertEqual(profile["class_level"], "")
        self.assertFalse(profile["onboarding_completed"])
        progress = (
            self.db.query(UserProgress)
            .filter(UserProgress.user_id == self.USER_ID)
            .first()
        )
        self.assertIsNotNone(progress)

    def test_patch_is_retry_safe_and_keeps_token_email_authoritative(self):
        payload = {
            "display_name": "  Rohan   Singh Deo  ",
            "class_level": "Class 11",
            "onboarding_completed": True,
        }
        first = self.client.patch("/profile/me", json=payload)
        second = self.client.patch("/profile/me", json=payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        profile = second.json()
        self.assertEqual(profile["display_name"], "Rohan Singh Deo")
        self.assertEqual(profile["class_level"], "Class 11")
        self.assertTrue(profile["onboarding_completed"])
        self.assertEqual(profile["email"], "student@example.com")

    def test_patch_rejects_invalid_name_and_class(self):
        bad_name = self.client.patch("/profile/me", json={"display_name": "1"})
        bad_class = self.client.patch("/profile/me", json={"class_level": "College"})
        self.assertEqual(bad_name.status_code, 422)
        self.assertEqual(bad_class.status_code, 422)

    def test_phone_profile_cannot_complete_without_name(self):
        main.app.dependency_overrides[verify_firebase_user] = lambda: {
            "uid": self.USER_ID,
            "phone_number": "+910000000000",
        }
        response = self.client.patch(
            "/profile/me",
            json={"class_level": "Class 10", "onboarding_completed": True},
        )
        self.assertEqual(response.status_code, 422)

    def test_leaderboard_uses_full_signup_names_and_class_rank(self):
        self.db.add_all(
            [
                UserProfile(
                    user_id=self.USER_ID,
                    email="student@example.com",
                    display_name="Rohan Singh",
                    class_level="Class 11",
                    onboarding_completed=True,
                ),
                UserProfile(
                    user_id=self.OTHER_USER_ID,
                    email="peer@example.com",
                    display_name="Ananya Sharma",
                    class_level="Class 11",
                    onboarding_completed=True,
                ),
                UserProgress(user_id=self.USER_ID, xp=100, streak=1, total_tests=2),
                UserProgress(user_id=self.OTHER_USER_ID, xp=200, streak=2, total_tests=3),
            ]
        )
        self.db.commit()

        rows = leaderboard_service.build_leaderboard(
            self.db,
            {"uid": self.USER_ID, "email": "student@example.com"},
        )
        selected = {
            row["user_id"]: row
            for row in rows
            if row["user_id"] in {self.USER_ID, self.OTHER_USER_ID}
        }

        self.assertEqual(selected[self.OTHER_USER_ID]["display_name"], "Ananya Sharma")
        self.assertEqual(selected[self.USER_ID]["display_name"], "Rohan Singh")
        self.assertEqual(selected[self.OTHER_USER_ID]["class_rank"], 1)
        self.assertEqual(selected[self.USER_ID]["class_rank"], 2)


if __name__ == "__main__":
    unittest.main()
