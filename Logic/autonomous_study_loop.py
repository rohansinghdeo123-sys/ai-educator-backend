# Logic/autonomous_study_loop.py

"""
Autonomous study execution loop.

This is the first production-safe slice of autonomy: pick the student's next
best study mission, dispatch the right specialist agent, and return a structured
mission object the frontend can render.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from Logic.agent_event_bus import event_bus
from Logic.agent_router import route_to_agent
from Logic.analytics_engine import get_user_analytics
from Logic.agents.coach_agent import get_or_create_coach


@dataclass
class AutonomousAgentRequest:
    user_id: str
    question: str
    section_id: str
    session_id: str
    mode: str
    difficulty: str = "medium"
    intent: str = "autonomous_study"


def _safe_topic(value: Optional[str]) -> str:
    topic = str(value or "").strip().lower()
    return topic or "matter_definition"


def _select_target_topic(
    analytics: Dict[str, Any],
    current_topic: Optional[str],
) -> Dict[str, Any]:
    weak_areas = analytics.get("weak_areas") or []
    if weak_areas:
        weakest = weak_areas[0]
        return {
            "topic": _safe_topic(weakest.get("topic")),
            "accuracy": float(weakest.get("accuracy") or 0),
            "source": "weak_area",
            "reason": f"Lowest active mastery signal: {weakest.get('accuracy', 0)}% accuracy.",
        }

    heatmap = analytics.get("topic_heatmap") or []
    if heatmap:
        sorted_topics = sorted(heatmap, key=lambda item: float(item.get("value") or 0))
        topic = sorted_topics[0]
        return {
            "topic": _safe_topic(topic.get("topic")),
            "accuracy": float(topic.get("value") or 0),
            "source": "topic_heatmap",
            "reason": f"Lowest available topic score: {topic.get('value', 0)}%.",
        }

    return {
        "topic": _safe_topic(current_topic),
        "accuracy": 0.0,
        "source": "current_context",
        "reason": "No weak-topic history yet, so the current study context is used.",
    }


def _build_mission_plan(target: Dict[str, Any], analytics: Dict[str, Any]) -> Dict[str, Any]:
    topic = target["topic"]
    accuracy = float(target.get("accuracy") or 0)
    summary = analytics.get("summary") or {}
    total_topics = int(summary.get("total_topics") or 0)

    if total_topics == 0:
        return {
            "primary_agent": "exam",
            "mode": "exam",
            "difficulty": "medium",
            "objective": f"Run a quick diagnostic on {topic.replace('_', ' ')}.",
            "question": f"Generate 5 diagnostic MCQs on {topic} with answers and explanations.",
            "why": "The system needs a first performance signal before it can personalize deeply.",
            "steps": [
                "Create a short diagnostic set.",
                "Attempt the questions without notes.",
                "Save the session so the coach can update weak-area memory.",
            ],
            "next_actions": [
                "Complete the diagnostic MCQs.",
                "Review every wrong answer explanation.",
                "Ask the coach for a revision plan after saving the score.",
            ],
        }

    if accuracy < 60:
        return {
            "primary_agent": "revision",
            "mode": "explain",
            "difficulty": "medium",
            "objective": f"Repair weak understanding in {topic.replace('_', ' ')}.",
            "question": f"Explain {topic} clearly with direct answer, simple explanation, examples, common mistakes, and exam-ready answer.",
            "why": target["reason"],
            "steps": [
                "Rebuild the concept from first principles.",
                "Study the examples and common mistakes.",
                "Move to practice only after the explanation feels clear.",
            ],
            "next_actions": [
                "Read the explanation once slowly.",
                "Write the exam-ready answer in your own words.",
                "Generate MCQs on the same topic next.",
            ],
        }

    if accuracy < 80:
        return {
            "primary_agent": "exam",
            "mode": "exam",
            "difficulty": "medium",
            "objective": f"Convert partial mastery of {topic.replace('_', ' ')} into exam confidence.",
            "question": f"Generate 8 medium-level MCQs on {topic} with answers and concise explanations.",
            "why": target["reason"],
            "steps": [
                "Practice targeted MCQs.",
                "Track wrong answers.",
                "Revise only the sub-points that caused mistakes.",
            ],
            "next_actions": [
                "Attempt the practice set.",
                "Save the result.",
                "Ask for a mistake analysis if accuracy is below 80%.",
            ],
        }

    return {
        "primary_agent": "exam",
        "mode": "probable",
        "difficulty": "hard",
        "objective": f"Push strong topic {topic.replace('_', ' ')} toward exam excellence.",
        "question": f"Generate probable exam questions from {topic} with model answer points.",
        "why": "Current mastery is strong enough for higher-value exam practice.",
        "steps": [
            "Attempt subjective/probable questions.",
            "Compare your answer with model points.",
            "Polish answer structure for marks.",
        ],
        "next_actions": [
            "Write one model answer without looking.",
            "Check if your answer includes keywords.",
            "Move to the next weakest topic after this challenge.",
        ],
    }


def run_autonomous_study_loop(
    db,
    user_id: str,
    current_topic: Optional[str] = None,
    current_chapter: Optional[str] = None,
    subject: str = "Chemistry",
) -> Dict[str, Any]:
    started_at = time.time()
    mission_id = f"mission_{uuid.uuid4().hex[:12]}"
    session_id = f"autonomous-{user_id}-{mission_id}"

    event_bus.emit(
        "orchestrator",
        "task_start",
        {
            "task": f"Autonomous study mission {mission_id}",
            "message": "Selecting the next best study mission from analytics.",
            "mission_id": mission_id,
            "user_id": user_id,
        },
        session_id=session_id,
    )

    analytics = get_user_analytics(db, user_id)
    target = _select_target_topic(analytics, current_topic)
    plan = _build_mission_plan(target, analytics)

    event_bus.emit(
        "orchestrator",
        "step",
        {
            "step": "mission_plan",
            "message": plan["objective"],
            "mission_id": mission_id,
            "target_topic": target["topic"],
            "primary_agent": plan["primary_agent"],
        },
        session_id=session_id,
    )

    request = AutonomousAgentRequest(
        user_id=user_id,
        question=plan["question"],
        section_id=target["topic"],
        session_id=session_id,
        mode=plan["mode"],
        difficulty=plan["difficulty"],
    )

    result = route_to_agent(request, db=db)
    latency_ms = round((time.time() - started_at) * 1000)

    coach = get_or_create_coach(db, user_id)
    coach.next_best_action = plan["next_actions"][0]
    coach.daily_strategy = plan["objective"]
    coach.last_recommendation = {
        "mission_id": mission_id,
        "objective": plan["objective"],
        "target_topic": target["topic"],
        "primary_agent": plan["primary_agent"],
        "generated_at": datetime.utcnow().isoformat(),
    }
    coach.updated_at = datetime.utcnow()
    db.commit()

    event_bus.emit(
        "orchestrator",
        "task_complete",
        {
            "status": "success",
            "message": f"Autonomous mission delivered by {plan['primary_agent']}.",
            "mission_id": mission_id,
            "latency_ms": latency_ms,
        },
        session_id=session_id,
    )

    return {
        "mission_id": mission_id,
        "status": "ready",
        "subject": subject,
        "chapter": current_chapter or "",
        "target_topic": target["topic"],
        "target_source": target["source"],
        "primary_agent": plan["primary_agent"],
        "mode": plan["mode"],
        "difficulty": plan["difficulty"],
        "objective": plan["objective"],
        "why": plan["why"],
        "steps": plan["steps"],
        "next_actions": plan["next_actions"],
        "result": result,
        "analytics_summary": analytics.get("summary", {}),
        "latency_ms": latency_ms,
    }
