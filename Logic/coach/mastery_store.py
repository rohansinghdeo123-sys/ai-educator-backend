"""Compact, deduplicated mastery signals learned from Study Lab coach turns."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Dict, Iterable, List

from models import AICoachMemory, AICoachProfile


_GENERIC_SCOPE = {"", "general", "open", "any", "all", "open_tutor_topic"}
_SUPPORT_EMOTIONS = {"confused", "anxious", "stuck", "unsure"}
_PREFERENCE_KEYS = {"answer_style", "learning_speed", "preferred_style", "feedback_depth"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalized(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _scope_topic(scope: Dict[str, Any], anchors: Iterable[str]) -> str:
    for value in (scope.get("topic"), scope.get("section_id"), scope.get("chapter")):
        normalized = _normalized(value)
        if normalized not in _GENERIC_SCOPE:
            return normalized
    useful = [_normalized(term) for term in anchors if len(_normalized(term)) >= 4]
    return "_".join(useful[:3]) or "open_concept"


def build_mastery_signal(
    query: Any,
    adaptive_context: Dict[str, Any],
    scope: Dict[str, Any],
    quality: Dict[str, Any],
    answer_blocks: Iterable[Dict[str, Any]] = (),
) -> Dict[str, Any]:
    """Extract a cautious learning signal. Coach conversation is evidence, not a test score."""
    if bool(getattr(query, "is_conversational", False)):
        return {"stored": False, "reason": "conversational_turn"}

    student_state = dict((adaptive_context or {}).get("student_state") or {})
    adaptive_strategy = dict((adaptive_context or {}).get("adaptive_strategy") or {})
    emotion = str(student_state.get("emotional_state") or "").strip().lower()
    weak_signals = [
        str(item).strip().lower()
        for item in (adaptive_strategy.get("weak_signals") or [])
        if str(item).strip()
    ]
    confidence_raw = student_state.get("confidence", 66)
    try:
        confidence = float(confidence_raw)
    except Exception:
        confidence = 66.0
    if confidence <= 1:
        confidence *= 100
    confidence = max(0.0, min(100.0, confidence))

    intent = str(getattr(query, "intent", "concept") or "concept")
    needs_support = (
        intent == "clarification"
        or emotion in _SUPPORT_EMOTIONS
        or any(term in " ".join(weak_signals) for term in ("confusion", "mistake", "reinforcement", "weak"))
    )
    topic = _scope_topic(scope, getattr(query, "anchor_terms", []) or [])
    block_kinds = sorted({
        str(block.get("kind") or "explanation")
        for block in answer_blocks
        if isinstance(block, dict)
    })
    preference_updates = {
        key: adaptive_strategy.get(key) or student_state.get(key)
        for key in _PREFERENCE_KEYS
        if adaptive_strategy.get(key) or student_state.get(key)
    }

    return {
        "stored": True,
        "topic": topic,
        "intent": intent,
        "emotion": emotion or "steady",
        "confidence": round(confidence, 1),
        "needs_support": needs_support,
        "is_follow_up": bool(getattr(query, "is_follow_up", False)),
        "topic_shift": bool(getattr(query, "topic_shift", False)),
        "teaching_strategy": str(getattr(query, "teaching_strategy", "") or adaptive_strategy.get("answer_style") or "")[:240],
        "weak_signals": weak_signals[:4],
        "quality_score": float(quality.get("score") or 0.0),
        "block_kinds": block_kinds,
        "preference_updates": preference_updates,
    }


def build_student_memory_update(
    *,
    mastery_signal: Dict[str, Any],
    mastery_profile: Dict[str, Any],
    repair_report: Dict[str, Any],
    retrieval_gate: Any,
    recommendation: str = "",
) -> Dict[str, Any]:
    """Create a student-friendly memory update for the next coach turn."""
    if not mastery_signal.get("stored"):
        return {"stored": False, "reason": mastery_signal.get("reason", "not_stored")}

    route = str((mastery_profile or {}).get("route") or "baseline")
    final_repair = dict((repair_report or {}).get("final") or {})
    initial_repair = dict((repair_report or {}).get("initial") or {})
    grounding_status = str(getattr(retrieval_gate, "grounding_status", "") or "")
    guardrails: List[str] = []
    if initial_repair.get("action") != "deliver" or final_repair.get("repair_applied"):
        guardrails.append("answer_needed_repair")
    if grounding_status == "missing_required_source":
        guardrails.append("needs_source_selection")
    if mastery_signal.get("needs_support"):
        support_style = "simplify_and_check"
    elif route == "increase_difficulty":
        support_style = "challenge_gently"
    else:
        support_style = "steady_guided"

    next_review_action = recommendation or (mastery_profile or {}).get("directive") or "Continue with one short check."
    if (mastery_profile or {}).get("revision_due"):
        next_review_action = f"Review {mastery_signal.get('topic', 'this concept')} before moving ahead."

    return {
        "stored": True,
        "topic": mastery_signal.get("topic", "open_concept"),
        "support_style": support_style,
        "next_review_action": str(next_review_action)[:320],
        "guardrails": guardrails,
        "confidence": mastery_signal.get("confidence", 0),
        "quality_score": mastery_signal.get("quality_score", 0),
        "mastery_route": route,
    }


def persist_mastery_signal(db, coach: AICoachProfile, signal: Dict[str, Any]) -> Dict[str, Any]:
    """Upsert one compact concept memory so repeated chats strengthen one learner record."""
    if not signal.get("stored"):
        return signal

    topic = str(signal.get("topic") or "open_concept")
    memory_type = "concept_watch" if signal.get("needs_support") else "concept_learning"
    existing = (
        db.query(AICoachMemory)
        .filter(
            AICoachMemory.coach_id == coach.coach_id,
            AICoachMemory.memory_type.in_(["concept_watch", "concept_learning"]),
        )
        .all()
    )
    memory = next(
        (
            row for row in existing
            if isinstance(row.metadata_json, dict)
            and row.metadata_json.get("topic") == topic
        ),
        None,
    )
    previous = dict(memory.metadata_json or {}) if memory else {}
    observations = int(previous.get("observations") or 0) + 1
    support_count = int(previous.get("support_count") or 0) + int(bool(signal.get("needs_support")))
    prior_confidence = float(previous.get("average_confidence") or signal.get("confidence") or 0.0)
    average_confidence = round(
        ((prior_confidence * max(0, observations - 1)) + float(signal.get("confidence") or 0.0)) / observations,
        1,
    )
    metadata = {
        **previous,
        **signal,
        "topic": topic,
        "observations": observations,
        "support_count": support_count,
        "average_confidence": average_confidence,
        "last_observed_at": datetime.now(timezone.utc).isoformat(),
    }
    label = topic.replace("_", " ").title()
    summary = (
        f"{label}: {observations} coach observation(s), average confidence {average_confidence}%. "
        f"Support requested {support_count} time(s). Last intent: {signal.get('intent', 'concept')}."
    )

    if memory:
        memory.memory_type = memory_type
        memory.title = f"Concept watch: {label}" if signal.get("needs_support") else f"Concept learning: {label}"
        memory.summary = summary
        memory.importance = min(1.0, 0.55 + support_count * 0.08 + observations * 0.02)
        memory.confidence = min(1.0, max(0.35, average_confidence / 100))
        memory.metadata_json = metadata
        memory.updated_at = _utc_now()
    else:
        memory = AICoachMemory(
            coach_id=coach.coach_id,
            user_id=coach.user_id,
            memory_type=memory_type,
            title=f"Concept watch: {label}" if signal.get("needs_support") else f"Concept learning: {label}",
            summary=summary,
            importance=0.68 if signal.get("needs_support") else 0.56,
            confidence=min(1.0, max(0.35, average_confidence / 100)),
            source="coach_turn",
            metadata_json=metadata,
        )
        db.add(memory)

    preferences = dict(coach.study_preferences or {})
    preferences.update(signal.get("preference_updates") or {})
    preferences["last_tutor_strategy"] = signal.get("teaching_strategy") or preferences.get("last_tutor_strategy", "")
    preferences["last_learning_emotion"] = signal.get("emotion") or "steady"
    coach.study_preferences = preferences
    coach.updated_at = _utc_now()
    db.commit()

    return {**signal, "memory_type": memory_type, "observations": observations, "support_count": support_count}
