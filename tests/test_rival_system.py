import os
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "true")
os.environ["DATABASE_URL"] = ""

from fastapi.testclient import TestClient

import main
from app.security import verify_firebase_user
from database import SessionLocal
from models import RivalPairing, TestHistory, TopicPerformance, UserProfile, UserProgress
from services import rival_service
from services.rival_service import (
    WEEKLY_TIE_XP,
    WEEKLY_WIN_XP,
    build_weekly_challenge,
    generate_week_pairings,
    monthly_exam_rankings,
    resolve_finished_weeks,
    week_start_for,
)

PREFIX = "rivaltest-"
U1, U2, U3, U4, U5 = (f"{PREFIX}u{i}" for i in range(1, 6))
ALL_USERS = [U1, U2, U3, U4, U5]


def _seed_user(db, user_id, xp=0, last_active=None):
    db.add(UserProfile(user_id=user_id, display_name=f"Student {user_id[-2:]}", class_level="Class 11"))
    db.add(UserProgress(user_id=user_id, xp=xp, streak=1, last_active_date=last_active or date.today()))


def _seed_exam(db, user_id, score, total, xp, on_date=None, topic="alkanes", session_type="exam"):
    db.add(
        TestHistory(
            user_id=user_id,
            date=on_date or date.today(),
            topic=topic,
            score=score,
            total_questions=total,
            xp_earned=xp,
            accuracy_rate=round(score / total * 100, 2) if total else 0,
            session_type=session_type,
            completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )


class RivalSystemBase(unittest.TestCase):
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
        self._cleanup()

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self._cleanup()
        self.db.close()

    def _cleanup(self):
        self.db.query(RivalPairing).filter(
            (RivalPairing.user_id.like(f"{PREFIX}%"))
            | (RivalPairing.rival_user_id.like(f"{PREFIX}%"))
        ).delete(synchronize_session=False)
        for model in (TestHistory, TopicPerformance, UserProfile, UserProgress):
            self.db.query(model).filter(model.user_id.like(f"{PREFIX}%")).delete(
                synchronize_session=False
            )
        self.db.commit()


class MonthlyRankingTests(RivalSystemBase):
    def test_rankings_are_deterministic_and_performance_ordered(self):
        _seed_user(self.db, U1, xp=500)
        _seed_user(self.db, U2, xp=400)
        _seed_user(self.db, U3, xp=300)
        _seed_exam(self.db, U1, 9, 10, 90)   # 90% accuracy
        _seed_exam(self.db, U2, 7, 10, 70)   # 70%
        _seed_exam(self.db, U3, 4, 10, 40)   # 40%
        self.db.commit()

        first = monthly_exam_rankings(self.db)
        second = monthly_exam_rankings(self.db)
        self.assertEqual(
            [row["user_id"] for row in first], [row["user_id"] for row in second]
        )

        ours = [row["user_id"] for row in first if row["user_id"].startswith(PREFIX)]
        self.assertEqual(ours, [U1, U2, U3])

    def test_ties_break_by_lifetime_xp_then_user_id(self):
        _seed_user(self.db, U1, xp=100)
        _seed_user(self.db, U2, xp=300)
        _seed_exam(self.db, U1, 8, 10, 80)
        _seed_exam(self.db, U2, 8, 10, 80)
        self.db.commit()

        ours = [
            row["user_id"]
            for row in monthly_exam_rankings(self.db)
            if row["user_id"].startswith(PREFIX)
        ]
        self.assertEqual(ours, [U2, U1])  # same score, higher XP first

    def test_inactive_students_are_excluded(self):
        stale = date.today() - timedelta(days=30)
        _seed_user(self.db, U1, xp=500, last_active=stale)
        _seed_exam(self.db, U1, 9, 10, 90, on_date=stale)
        self.db.commit()

        ours = [
            row["user_id"]
            for row in monthly_exam_rankings(self.db)
            if row["user_id"].startswith(PREFIX)
        ]
        self.assertEqual(ours, [])


class PairingTests(RivalSystemBase):
    def _controlled_rankings(self, users):
        return [
            {
                "user_id": uid,
                "score": 90 - index * 10,
                "monthly_accuracy": 90 - index * 10,
                "monthly_exams": 3,
                "lifetime_xp": 100,
                "rank": index + 1,
            }
            for index, uid in enumerate(users)
        ]

    def test_adjacent_rank_pairing_is_symmetric(self):
        for uid in (U1, U2, U3, U4):
            _seed_user(self.db, uid)
        self.db.commit()
        week = week_start_for()

        with patch.object(
            rival_service, "monthly_exam_rankings",
            return_value=self._controlled_rankings([U1, U2, U3, U4]),
        ), patch.object(rival_service, "_newcomer_pool", return_value=[]):
            generate_week_pairings(self.db, week)

        rows = {
            row.user_id: row
            for row in self.db.query(RivalPairing)
            .filter(RivalPairing.week_start == week, RivalPairing.user_id.like(f"{PREFIX}%"))
            .all()
        }
        self.assertEqual(rows[U1].rival_user_id, U2)
        self.assertEqual(rows[U2].rival_user_id, U1)
        self.assertEqual(rows[U3].rival_user_id, U4)
        self.assertEqual(rows[U4].rival_user_id, U3)
        self.assertTrue(all(row.status == "active" for row in rows.values()))

    def test_odd_student_out_is_gracefully_unmatched(self):
        for uid in (U1, U2, U3):
            _seed_user(self.db, uid)
        self.db.commit()
        week = week_start_for()

        with patch.object(
            rival_service, "monthly_exam_rankings",
            return_value=self._controlled_rankings([U1, U2, U3]),
        ), patch.object(rival_service, "_newcomer_pool", return_value=[]):
            generate_week_pairings(self.db, week)

        row = (
            self.db.query(RivalPairing)
            .filter(RivalPairing.week_start == week, RivalPairing.user_id == U3)
            .first()
        )
        self.assertIsNotNone(row)
        self.assertEqual(row.status, "unmatched")
        self.assertIsNone(row.rival_user_id)

    def test_pairings_are_fixed_for_the_week(self):
        for uid in (U1, U2):
            _seed_user(self.db, uid)
        self.db.commit()
        week = week_start_for()

        with patch.object(
            rival_service, "monthly_exam_rankings",
            return_value=self._controlled_rankings([U1, U2]),
        ), patch.object(rival_service, "_newcomer_pool", return_value=[]):
            generate_week_pairings(self.db, week)
            # Second run must not rewrite or duplicate existing pairings.
            generate_week_pairings(self.db, week)

        rows = (
            self.db.query(RivalPairing)
            .filter(RivalPairing.week_start == week, RivalPairing.user_id == U1)
            .all()
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].rival_user_id, U2)


class WeeklyResolutionTests(RivalSystemBase):
    def _make_last_week_pairing(self):
        last_week = week_start_for() - timedelta(days=7)
        _seed_user(self.db, U1, xp=1000)
        _seed_user(self.db, U2, xp=1000)
        for me, other in ((U1, U2), (U2, U1)):
            self.db.add(
                RivalPairing(
                    week_start=last_week,
                    user_id=me,
                    rival_user_id=other,
                    status="active",
                )
            )
        _seed_exam(self.db, U1, 9, 10, 90, on_date=last_week + timedelta(days=2))
        _seed_exam(self.db, U2, 5, 10, 50, on_date=last_week + timedelta(days=2))
        self.db.commit()
        return last_week

    def test_winner_gets_reward_exactly_once(self):
        self._make_last_week_pairing()

        resolved = resolve_finished_weeks(self.db, U1)
        self.assertEqual(resolved.outcome, "won")
        self.assertEqual(resolved.reward_xp, WEEKLY_WIN_XP)

        # Idempotent: a second resolve (from either side) changes nothing.
        self.assertIsNone(resolve_finished_weeks(self.db, U1))
        self.assertIsNone(resolve_finished_weeks(self.db, U2))

        winner = self.db.query(UserProgress).filter(UserProgress.user_id == U1).first()
        loser = self.db.query(UserProgress).filter(UserProgress.user_id == U2).first()
        self.assertEqual(winner.xp, 1000 + WEEKLY_WIN_XP)
        self.assertEqual(loser.xp, 1000)

        mirror = (
            self.db.query(RivalPairing)
            .filter(RivalPairing.user_id == U2, RivalPairing.resolved.is_(True))
            .first()
        )
        self.assertEqual(mirror.outcome, "lost")

    def test_tie_rewards_both_sides(self):
        last_week = week_start_for() - timedelta(days=7)
        _seed_user(self.db, U1, xp=100)
        _seed_user(self.db, U2, xp=100)
        for me, other in ((U1, U2), (U2, U1)):
            self.db.add(
                RivalPairing(week_start=last_week, user_id=me, rival_user_id=other, status="active")
            )
        _seed_exam(self.db, U1, 5, 10, 60, on_date=last_week + timedelta(days=1))
        _seed_exam(self.db, U2, 6, 10, 60, on_date=last_week + timedelta(days=1))
        self.db.commit()

        resolved = resolve_finished_weeks(self.db, U1)
        self.assertEqual(resolved.outcome, "tied")
        for uid in (U1, U2):
            progress = self.db.query(UserProgress).filter(UserProgress.user_id == uid).first()
            self.assertEqual(progress.xp, 100 + WEEKLY_TIE_XP)


class ChallengeEndpointTests(RivalSystemBase):
    def _login(self, uid):
        main.app.dependency_overrides[verify_firebase_user] = lambda: {
            "uid": uid,
            "email": f"{uid}@example.com",
        }

    def test_endpoint_returns_full_challenge_shape(self):
        _seed_user(self.db, U1, xp=200)
        _seed_user(self.db, U2, xp=180)
        _seed_exam(self.db, U1, 8, 10, 80)
        _seed_exam(self.db, U2, 7, 10, 70)
        self.db.add(
            TopicPerformance(user_id=U1, topic="alkanes", attempts=10, correct=4, weak=True)
        )
        self.db.commit()
        self._login(U1)

        response = self.client.get(f"/rivals/weekly-challenge/{U1}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        for key in ("week", "me", "battle", "missions", "reward"):
            self.assertIn(key, payload)
        self.assertGreater(payload["week"]["seconds_remaining"], 0)
        self.assertGreaterEqual(len(payload["missions"]), 3)
        self.assertEqual(payload["reward"]["win_xp"], WEEKLY_WIN_XP)
        self.assertIn(payload["battle"]["status"], {"leading", "trailing", "tied", "unmatched"})
        # Missions are living data: the session mission must already be done.
        session_mission = next(m for m in payload["missions"] if m["id"] == "daily_session")
        self.assertTrue(session_mission["completed"])

    def test_endpoint_rejects_other_users(self):
        self._login(U2)
        response = self.client.get(f"/rivals/weekly-challenge/{U1}")
        self.assertEqual(response.status_code, 403)

    def test_new_user_with_no_data_gets_graceful_solo_view(self):
        _seed_user(self.db, U5)
        self.db.commit()
        self._login(U5)

        response = self.client.get(f"/rivals/weekly-challenge/{U5}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn(payload["battle"]["status"], {"leading", "trailing", "tied", "unmatched"})
        self.assertGreaterEqual(len(payload["missions"]), 3)


if __name__ == "__main__":
    unittest.main()
