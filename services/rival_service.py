"""Weekly Rival Challenge: performance-based matchmaking and living battle data.

Matchmaking rules (product contract):
- Rivals are NEVER random. Students are ranked by Monthly Exam performance
  (exam-type sessions in the last 30 days) and paired with the adjacent-rank
  student, so every match is between similar performers.
- A pairing is fixed for one ISO week (Monday 00:00 UTC boundary) and stored
  symmetrically — both students see the same match all week.
- On the first request of a new week, last week's battles are resolved
  (winner XP awarded, idempotently) and fresh pairings are computed from the
  latest rankings.
- Graceful degradation: students without monthly exam data are paired among
  themselves by lifetime XP ("newcomer" basis); students inactive for 14+ days
  are left out of the pool; an odd student out gets an "unmatched" week with
  self-improvement missions instead of a fake rival.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import RivalPairing, TestHistory, TopicPerformance, UserProfile, UserProgress
from services.profile_service import privacy_safe_display_name

logger = logging.getLogger("ai_educator.services.rival")

MONTHLY_WINDOW_DAYS = 30
INACTIVE_AFTER_DAYS = 14
EXAM_VOLUME_CAP = 8          # exams/month beyond this stop raising the seed score
WEEKLY_WIN_XP = 150
WEEKLY_TIE_XP = 50
BADGE_NAME = "Weekly Champion"


# ---------------------------------------------------------------------------
# Week boundaries
# ---------------------------------------------------------------------------

def week_start_for(day: Optional[date] = None) -> date:
    day = day or datetime.now(timezone.utc).date()
    return day - timedelta(days=day.weekday())


def week_end_utc(week_start: date) -> datetime:
    return datetime.combine(week_start + timedelta(days=7), time.min, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Monthly exam rankings
# ---------------------------------------------------------------------------

def _is_exam_type(session_type: str) -> bool:
    return "exam" in (session_type or "").lower()


def monthly_exam_rankings(db: Session, today: Optional[date] = None) -> List[Dict[str, Any]]:
    """Rank active students by monthly exam performance, deterministically.

    Seed score = monthly exam accuracy (70%) + exam volume factor (30%).
    Ties break by lifetime XP, then user_id, so reruns give identical order.
    """
    today = today or datetime.now(timezone.utc).date()
    window_start = today - timedelta(days=MONTHLY_WINDOW_DAYS)
    active_cutoff = today - timedelta(days=INACTIVE_AFTER_DAYS)

    progress_rows = {row.user_id: row for row in db.query(UserProgress).all() if row.user_id}

    recent_sessions = (
        db.query(TestHistory)
        .filter(TestHistory.date >= window_start)
        .all()
    )
    last_session_date: Dict[str, date] = {}
    exam_stats: Dict[str, Dict[str, float]] = {}
    for row in recent_sessions:
        if not row.user_id:
            continue
        if row.date and row.date > last_session_date.get(row.user_id, date.min):
            last_session_date[row.user_id] = row.date
        if not _is_exam_type(row.session_type):
            continue
        stats = exam_stats.setdefault(
            row.user_id, {"exams": 0.0, "questions": 0.0, "correct": 0.0, "xp": 0.0}
        )
        stats["exams"] += 1
        stats["questions"] += float(row.total_questions or 0)
        stats["correct"] += float(max(0, min(row.score or 0, row.total_questions or 0)))
        stats["xp"] += float(row.xp_earned or 0)

    ranked: List[Dict[str, Any]] = []
    for user_id, stats in exam_stats.items():
        progress = progress_rows.get(user_id)
        last_active = last_session_date.get(user_id)
        if progress and progress.last_active_date:
            last_active = max(filter(None, [last_active, progress.last_active_date]))
        if not last_active or last_active < active_cutoff:
            continue  # inactive students never become someone's ghost rival

        accuracy = (stats["correct"] / stats["questions"] * 100.0) if stats["questions"] else 0.0
        volume = min(stats["exams"], EXAM_VOLUME_CAP) / EXAM_VOLUME_CAP * 100.0
        score = round(accuracy * 0.7 + volume * 0.3, 3)
        ranked.append(
            {
                "user_id": user_id,
                "score": score,
                "monthly_accuracy": round(accuracy, 1),
                "monthly_exams": int(stats["exams"]),
                "lifetime_xp": int(progress.xp or 0) if progress else 0,
            }
        )

    ranked.sort(key=lambda row: (-row["score"], -row["lifetime_xp"], row["user_id"]))
    for index, row in enumerate(ranked, start=1):
        row["rank"] = index
    return ranked


def _newcomer_pool(db: Session, exclude: set, today: date) -> List[Dict[str, Any]]:
    """Active students with no monthly exam record, ordered by lifetime XP."""
    active_cutoff = today - timedelta(days=INACTIVE_AFTER_DAYS)
    pool = []
    for progress in db.query(UserProgress).all():
        user_id = progress.user_id
        if not user_id or user_id in exclude:
            continue
        if not progress.last_active_date or progress.last_active_date < active_cutoff:
            continue
        pool.append(
            {
                "user_id": user_id,
                "score": float(progress.xp or 0),
                "monthly_accuracy": 0.0,
                "monthly_exams": 0,
                "lifetime_xp": int(progress.xp or 0),
                "rank": None,
            }
        )
    pool.sort(key=lambda row: (-row["score"], row["user_id"]))
    return pool


# ---------------------------------------------------------------------------
# Weekly pairing lifecycle
# ---------------------------------------------------------------------------

def _adjacent_pairs(pool: List[Dict[str, Any]]) -> Tuple[List[Tuple[Dict, Dict]], Optional[Dict]]:
    pairs = [(pool[i], pool[i + 1]) for i in range(0, len(pool) - 1, 2)]
    leftover = pool[-1] if len(pool) % 2 else None
    return pairs, leftover


def _store_pair(
    db: Session,
    week_start: date,
    left: Dict[str, Any],
    right: Dict[str, Any],
    basis: str,
) -> None:
    for me, other in ((left, right), (right, left)):
        db.add(
            RivalPairing(
                week_start=week_start,
                user_id=me["user_id"],
                rival_user_id=other["user_id"],
                status="active",
                match_basis=basis,
                user_seed_score=float(me["score"]),
                rival_seed_score=float(other["score"]),
                user_seed_rank=me.get("rank"),
                rival_seed_rank=other.get("rank"),
            )
        )


def _store_unmatched(db: Session, week_start: date, entry: Dict[str, Any], basis: str) -> None:
    db.add(
        RivalPairing(
            week_start=week_start,
            user_id=entry["user_id"],
            rival_user_id=None,
            status="unmatched",
            match_basis=basis,
            user_seed_score=float(entry["score"]),
            user_seed_rank=entry.get("rank"),
        )
    )


def generate_week_pairings(db: Session, week_start: Optional[date] = None) -> int:
    """Create this week's pairings for every eligible student without one.

    Deterministic and idempotent: ranked students pair adjacently (1-2, 3-4…),
    newcomers pair among themselves by XP, the odd one out is stored as
    unmatched. Existing rows for the week are never modified.
    """
    week_start = week_start or week_start_for()
    today = datetime.now(timezone.utc).date()

    existing = {
        row.user_id
        for row in db.query(RivalPairing.user_id).filter(RivalPairing.week_start == week_start)
    }

    ranked = [row for row in monthly_exam_rankings(db, today) if row["user_id"] not in existing]
    created = 0

    pairs, leftover = _adjacent_pairs(ranked)
    for left, right in pairs:
        _store_pair(db, week_start, left, right, "monthly_exam")
        created += 2

    newcomers = _newcomer_pool(db, existing | {row["user_id"] for row in ranked}, today)
    if leftover is not None:
        # The odd ranked student duels the strongest newcomer rather than nobody.
        if newcomers:
            partner = newcomers.pop(0)
            _store_pair(db, week_start, leftover, partner, "fallback")
            created += 2
        else:
            _store_unmatched(db, week_start, leftover, "monthly_exam")
            created += 1

    newcomer_pairs, newcomer_leftover = _adjacent_pairs(newcomers)
    for left, right in newcomer_pairs:
        _store_pair(db, week_start, left, right, "newcomer")
        created += 2
    if newcomer_leftover is not None:
        _store_unmatched(db, week_start, newcomer_leftover, "newcomer")
        created += 1

    try:
        db.commit()
    except IntegrityError:
        # A concurrent request generated this week first; theirs wins.
        db.rollback()
        created = 0
    return created


def _week_xp(db: Session, user_id: str, week_start: date) -> int:
    value = (
        db.query(func.coalesce(func.sum(TestHistory.xp_earned), 0))
        .filter(
            TestHistory.user_id == user_id,
            TestHistory.date >= week_start,
            TestHistory.date < week_start + timedelta(days=7),
        )
        .scalar()
    )
    return int(value or 0)


def resolve_finished_weeks(db: Session, user_id: str) -> Optional[RivalPairing]:
    """Resolve all of this user's finished, unresolved battles (newest last).

    Both sides of a pairing are resolved together so rewards are applied
    exactly once per student regardless of who logs in first. Returns the
    most recent resolved pairing, or None when nothing needed resolving.
    """
    latest: Optional[RivalPairing] = None
    for _ in range(8):  # bounded: one row per skipped week
        pairing = _resolve_next_finished_week(db, user_id)
        if pairing is None:
            break
        latest = pairing
    return latest


def _resolve_next_finished_week(db: Session, user_id: str) -> Optional[RivalPairing]:
    current_week = week_start_for()
    pairing = (
        db.query(RivalPairing)
        .filter(
            RivalPairing.user_id == user_id,
            RivalPairing.week_start < current_week,
            RivalPairing.resolved.is_(False),
        )
        .order_by(RivalPairing.week_start.asc())
        .first()
    )
    if pairing is None:
        return None

    week_start = pairing.week_start
    my_xp = _week_xp(db, user_id, week_start)

    if pairing.status == "unmatched" or not pairing.rival_user_id:
        pairing.outcome = "unmatched"
        pairing.my_week_xp = my_xp
        pairing.resolved = True
        pairing.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        return pairing

    rival_xp = _week_xp(db, pairing.rival_user_id, week_start)
    mirror = (
        db.query(RivalPairing)
        .filter(
            RivalPairing.week_start == week_start,
            RivalPairing.user_id == pairing.rival_user_id,
            RivalPairing.resolved.is_(False),
        )
        .first()
    )

    def _apply(row: RivalPairing, own_xp: int, other_xp: int) -> None:
        row.my_week_xp = own_xp
        row.rival_week_xp = other_xp
        if own_xp > other_xp:
            row.outcome = "won"
            row.reward_xp = WEEKLY_WIN_XP
        elif own_xp == other_xp:
            row.outcome = "tied"
            row.reward_xp = WEEKLY_TIE_XP
        else:
            row.outcome = "lost"
            row.reward_xp = 0
        row.resolved = True
        row.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
        if row.reward_xp:
            progress = db.query(UserProgress).filter(UserProgress.user_id == row.user_id).first()
            if progress:
                progress.xp = int(progress.xp or 0) + row.reward_xp

    _apply(pairing, my_xp, rival_xp)
    if mirror is not None:
        _apply(mirror, rival_xp, my_xp)
    db.commit()
    return pairing


# ---------------------------------------------------------------------------
# Weekly stats, missions, and activity
# ---------------------------------------------------------------------------

def weekly_stats(db: Session, user_id: str, week_start: date) -> Dict[str, Any]:
    week_days = [week_start + timedelta(days=offset) for offset in range(7)]
    sessions = (
        db.query(TestHistory)
        .filter(
            TestHistory.user_id == user_id,
            TestHistory.date >= week_start,
            TestHistory.date < week_start + timedelta(days=7),
        )
        .all()
    )
    xp_by_day = {day: 0 for day in week_days}
    questions = correct = minutes = 0
    active_days = set()
    for row in sessions:
        if row.date in xp_by_day:
            xp_by_day[row.date] += int(row.xp_earned or 0)
            active_days.add(row.date)
        questions += int(row.total_questions or 0)
        correct += int(max(0, min(row.score or 0, row.total_questions or 0)))
        minutes += int(row.time_spent_seconds or 0) // 60

    return {
        "week_xp": sum(xp_by_day.values()),
        "sessions": len(sessions),
        "accuracy": round(correct / questions * 100, 1) if questions else 0.0,
        "study_minutes": minutes,
        "active_days": len(active_days),
        "daily_xp": [xp_by_day[day] for day in week_days],
    }


def _today_stats(db: Session, user_id: str) -> Dict[str, Any]:
    today = datetime.now(timezone.utc).date()
    rows = (
        db.query(TestHistory)
        .filter(TestHistory.user_id == user_id, TestHistory.date == today)
        .all()
    )
    return {
        "sessions": len(rows),
        "xp": sum(int(row.xp_earned or 0) for row in rows),
        "topics": {str(row.topic or "").lower() for row in rows},
        "best_accuracy": max((float(row.accuracy_rate or 0) for row in rows), default=0.0),
    }


def _avg_daily_xp(db: Session, user_id: str, days: int = 14) -> float:
    since = datetime.now(timezone.utc).date() - timedelta(days=days)
    total = (
        db.query(func.coalesce(func.sum(TestHistory.xp_earned), 0))
        .filter(TestHistory.user_id == user_id, TestHistory.date >= since)
        .scalar()
    )
    return float(total or 0) / days


def _weakest_topic(db: Session, user_id: str) -> Optional[TopicPerformance]:
    rows = (
        db.query(TopicPerformance)
        .filter(TopicPerformance.user_id == user_id, TopicPerformance.attempts > 0)
        .all()
    )
    if not rows:
        return None
    return min(rows, key=lambda row: (row.accuracy, -row.attempts))


def build_daily_missions(
    db: Session,
    user_id: str,
    rival_name: str = "",
    rival_today_xp: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Missions derive from real learning data and complete themselves.

    Targets adapt to the student's own recent pace, so a beginner and a
    grinder both get a reachable-but-stretching day. No manual claiming:
    completion is recomputed from today's sessions on every read.
    """
    today = _today_stats(db, user_id)
    missions: List[Dict[str, Any]] = []

    missions.append(
        {
            "id": "daily_session",
            "title": "Complete one learning session",
            "detail": "Any Exam Mode, Mission, or practice run counts.",
            "target": 1,
            "progress": min(today["sessions"], 1),
            "completed": today["sessions"] >= 1,
        }
    )

    pace = _avg_daily_xp(db, user_id)
    xp_target = int(min(150, max(30, round(pace * 1.2 / 10) * 10 or 30)))
    missions.append(
        {
            "id": "daily_xp",
            "title": f"Earn {xp_target} XP today",
            "detail": "Tuned to your recent pace — a stretch, not a wall.",
            "target": xp_target,
            "progress": min(today["xp"], xp_target),
            "completed": today["xp"] >= xp_target,
        }
    )

    weak = _weakest_topic(db, user_id)
    if weak is not None and weak.accuracy < 75:
        topic_label = str(weak.topic or "").replace("_", " ").title()
        missions.append(
            {
                "id": "weak_topic",
                "title": f"Strengthen {topic_label}",
                "detail": f"Currently at {round(weak.accuracy)}% — one session moves the needle.",
                "target": 1,
                "progress": 1 if str(weak.topic or "").lower() in today["topics"] else 0,
                "completed": str(weak.topic or "").lower() in today["topics"],
            }
        )
    else:
        accuracy_target = 80.0
        missions.append(
            {
                "id": "accuracy",
                "title": f"Score {int(accuracy_target)}%+ in a session",
                "detail": "Precision beats volume for exam rank.",
                "target": int(accuracy_target),
                "progress": int(min(today["best_accuracy"], accuracy_target)),
                "completed": today["best_accuracy"] >= accuracy_target,
            }
        )

    if rival_name and rival_today_xp is not None:
        missions.append(
            {
                "id": "outpace_rival",
                "title": f"Out-earn {rival_name} today",
                "detail": f"{rival_name} has {rival_today_xp} XP today. Stay in front.",
                "target": rival_today_xp + 1,
                "progress": today["xp"],
                "completed": today["xp"] > rival_today_xp,
            }
        )

    return missions


def rival_activity(db: Session, rival_user_id: str, week_start: date, limit: int = 5) -> List[Dict[str, Any]]:
    """Privacy-safe rival feed: session type, topic, XP — never scores or answers."""
    rows = (
        db.query(TestHistory)
        .filter(
            TestHistory.user_id == rival_user_id,
            TestHistory.date >= week_start,
            TestHistory.date < week_start + timedelta(days=7),
        )
        .order_by(TestHistory.id.desc())
        .limit(limit)
        .all()
    )
    feed = []
    for row in rows:
        session_type = (row.session_type or "study").lower()
        label = (
            "Exam Mode" if "exam" in session_type
            else "Autonomous Mission" if "mission" in session_type
            else "Study practice"
        )
        feed.append(
            {
                "type": label,
                "topic": str(row.topic or "").replace("_", " ").title(),
                "xp_earned": int(row.xp_earned or 0),
                "completed_at": (
                    row.completed_at.isoformat() + "Z"
                    if row.completed_at
                    else (row.date.isoformat() if row.date else "")
                ),
            }
        )
    return feed


# ---------------------------------------------------------------------------
# Challenge payload
# ---------------------------------------------------------------------------

def _display_name(db: Session, user_id: str) -> str:
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    name = privacy_safe_display_name(profile.display_name if profile else "")
    if name:
        return name
    from hashlib import sha256

    return f"Student {sha256(user_id.encode('utf-8')).hexdigest()[:6].upper()}"


def _class_level(db: Session, user_id: str) -> str:
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    return (profile.class_level or "") if profile else ""


def build_weekly_challenge(db: Session, user_id: str) -> Dict[str, Any]:
    """Assemble the full living Weekly Rival Challenge payload for one student."""
    week_start = week_start_for()
    now = datetime.now(timezone.utc)
    end = week_end_utc(week_start)

    resolve_finished_weeks(db, user_id)
    # Last week's outcome stays visible all week, not only on the request
    # that happened to resolve it.
    last_week = (
        db.query(RivalPairing)
        .filter(
            RivalPairing.user_id == user_id,
            RivalPairing.week_start == week_start - timedelta(days=7),
            RivalPairing.resolved.is_(True),
        )
        .first()
    )

    pairing = (
        db.query(RivalPairing)
        .filter(RivalPairing.week_start == week_start, RivalPairing.user_id == user_id)
        .first()
    )
    if pairing is None:
        generate_week_pairings(db, week_start)
        pairing = (
            db.query(RivalPairing)
            .filter(RivalPairing.week_start == week_start, RivalPairing.user_id == user_id)
            .first()
        )

    my_stats = weekly_stats(db, user_id, week_start)
    seconds_remaining = max(0, int((end - now).total_seconds()))

    payload: Dict[str, Any] = {
        "user_id": user_id,
        "week": {
            "start": week_start.isoformat(),
            "end_utc": end.isoformat(),
            "seconds_remaining": seconds_remaining,
            "days_remaining": max(1, min(7, (seconds_remaining + 86399) // 86400)),
        },
        "me": {
            "name": _display_name(db, user_id),
            "class_level": _class_level(db, user_id),
            **my_stats,
        },
        "reward": {"win_xp": WEEKLY_WIN_XP, "tie_xp": WEEKLY_TIE_XP, "badge": BADGE_NAME},
        "last_week": None,
        "rival": None,
        "battle": {"status": "unmatched", "my_week_xp": my_stats["week_xp"], "rival_week_xp": 0, "xp_gap": 0},
    }

    if last_week is not None and last_week.outcome and last_week.outcome != "unmatched":
        payload["last_week"] = {
            "outcome": last_week.outcome,
            "reward_xp": int(last_week.reward_xp or 0),
            "my_week_xp": int(last_week.my_week_xp or 0),
            "rival_week_xp": int(last_week.rival_week_xp or 0),
            "rival_name": _display_name(db, last_week.rival_user_id) if last_week.rival_user_id else "",
        }

    rival_name = ""
    rival_today_xp: Optional[int] = None
    if pairing is not None and pairing.status == "active" and pairing.rival_user_id:
        rival_id = pairing.rival_user_id
        rival_name = _display_name(db, rival_id)
        rival_stats = weekly_stats(db, rival_id, week_start)
        rival_today_xp = _today_stats(db, rival_id)["xp"]
        payload["rival"] = {
            "name": rival_name,
            "class_level": _class_level(db, rival_id),
            "match_basis": pairing.match_basis,
            "activity": rival_activity(db, rival_id, week_start),
            **rival_stats,
        }
        gap = my_stats["week_xp"] - rival_stats["week_xp"]
        payload["battle"] = {
            "status": "leading" if gap > 0 else "trailing" if gap < 0 else "tied",
            "my_week_xp": my_stats["week_xp"],
            "rival_week_xp": rival_stats["week_xp"],
            "xp_gap": abs(gap),
        }

    payload["missions"] = build_daily_missions(db, user_id, rival_name, rival_today_xp)
    return payload
