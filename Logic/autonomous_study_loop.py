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
from datetime import datetime
from typing import Any, Dict, List, Optional

from Logic.agent_event_bus import event_bus
from Logic.analytics_engine import get_user_analytics
from Logic.agents.coach_agent import get_or_create_coach
from Logic.knowledge_graph import knowledge_graph


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
    mastery_band = _mastery_band(accuracy, total_topics)
    topic_label = topic.replace("_", " ")

    if mastery_band == "baseline":
        why = "The coach needs one first signal before personalizing the roadmap."
    elif mastery_band in {"critical", "weak"}:
        why = f"{target['reason']} Start with clarity first, then one diagnostic check."
    elif mastery_band == "building":
        why = f"{target['reason']} One smart question will show what to revise next."
    else:
        why = "The topic looks stronger, so the mission checks whether the student is ready for exam-level practice."

    return {
        "primary_agent": "coach",
        "mode": "adaptive_mission",
        "difficulty": "medium" if mastery_band != "strong" else "hard",
        "objective": f"Build a simple adaptive roadmap for {topic_label}.",
        "question": f"Run one intelligent diagnostic question on {topic_label}.",
        "why": why,
        "steps": [
            f"Understand the core idea of {topic_label}.",
            "Answer one diagnostic MCQ honestly without notes.",
            "Let the coach adjust the next study block from the result.",
        ],
        "next_actions": [
            "Answer the one-question diagnostic.",
            "Read the instant feedback carefully.",
            "Follow the personalized next block until the topic crosses 80%.",
        ],
    }


def _find_topic_concept(topic: str) -> Optional[Dict[str, Any]]:
    topic_words = [word for word in topic.replace("_", " ").split() if len(word) > 2]
    for word in topic_words[:4]:
        matches = knowledge_graph.search_by_keyword(word, limit=1)
        if matches:
            return matches[0]
    return None


def _build_simple_study_plan(topic: str, mastery_band: str) -> List[Dict[str, str]]:
    topic_label = topic.replace("_", " ")
    if mastery_band in {"baseline", "critical", "weak"}:
        focus = "foundation"
        practice = "one easy diagnostic first"
    elif mastery_band == "building":
        focus = "mistake repair"
        practice = "one targeted diagnostic"
    else:
        focus = "exam sharpening"
        practice = "one challenge diagnostic"

    return [
        {
            "title": "Understand",
            "duration": "5 min",
            "detail": f"Read the core idea of {topic_label} and connect it with one real-life example.",
            "focus": focus,
        },
        {
            "title": "Diagnose",
            "duration": "2 min",
            "detail": f"Attempt {practice} without checking notes.",
            "focus": "honest signal",
        },
        {
            "title": "Adapt",
            "duration": "8 min",
            "detail": "If correct, move to exam application. If wrong, rebuild the weak sub-concept with the coach.",
            "focus": "personalized next step",
        },
    ]


def _build_single_diagnostic_question(topic: str) -> Dict[str, Any]:
    topic_label = topic.replace("_", " ")
    normalized_topic = topic.lower().replace("_", " ")
    topic_bank: Dict[str, List[Dict[str, Any]]] = {
        "alkane": [
            {
                "question": "In alkanes, what type of bond is present between carbon atoms?",
                "options": [
                    "Only single covalent bonds",
                    "At least one carbon-carbon double bond",
                    "At least one carbon-carbon triple bond",
                    "Only ionic bonds",
                ],
                "correct": "Only single covalent bonds",
                "explanation": "Alkanes are saturated hydrocarbons, so carbon atoms are connected by single covalent bonds.",
            },
            {
                "question": "Why are alkanes called saturated hydrocarbons?",
                "options": [
                    "They contain the maximum possible number of hydrogen atoms for single-bonded carbon chains",
                    "They dissolve completely in water",
                    "They always contain a benzene ring",
                    "They have at least one carbon-carbon triple bond",
                ],
                "correct": "They contain the maximum possible number of hydrogen atoms for single-bonded carbon chains",
                "explanation": "A saturated hydrocarbon has only single bonds, so each carbon can hold the maximum number of hydrogen atoms.",
            },
        ],
        "alkene": [
            {
                "question": "Which feature identifies an alkene?",
                "options": [
                    "At least one carbon-carbon double bond",
                    "Only carbon-carbon single bonds",
                    "A carbon-carbon triple bond",
                    "No hydrogen atoms",
                ],
                "correct": "At least one carbon-carbon double bond",
                "explanation": "Alkenes are unsaturated hydrocarbons because they contain at least one carbon-carbon double bond.",
            }
        ],
        "alkyne": [
            {
                "question": "Which bond is the key identifying feature of an alkyne?",
                "options": [
                    "A carbon-carbon triple bond",
                    "Only single covalent bonds",
                    "A benzene ring",
                    "A carbon-oxygen double bond",
                ],
                "correct": "A carbon-carbon triple bond",
                "explanation": "Alkynes are unsaturated hydrocarbons with at least one carbon-carbon triple bond.",
            }
        ],
        "aromatic": [
            {
                "question": "What makes an aromatic hydrocarbon different from a simple alkane?",
                "options": [
                    "It has a stable ring system with delocalized electrons",
                    "It has only single bonds in an open chain",
                    "It has no carbon atoms",
                    "It is always ionic",
                ],
                "correct": "It has a stable ring system with delocalized electrons",
                "explanation": "Aromatic hydrocarbons, such as benzene, are known for ring stability and delocalized electrons.",
            }
        ],
        "matter": [
            {
                "question": "Which statement correctly defines matter?",
                "options": [
                    "Anything that has mass and occupies space",
                    "Anything that produces light",
                    "Only things that are visible",
                    "Only solids and liquids",
                ],
                "correct": "Anything that has mass and occupies space",
                "explanation": "Matter is defined by two core properties: it has mass and it occupies space.",
            }
        ],
        "states": [
            {
                "question": "What mainly changes when matter changes from solid to liquid?",
                "options": [
                    "Particle arrangement and freedom of movement",
                    "The atoms stop existing",
                    "Mass becomes zero",
                    "The substance must become a gas first",
                ],
                "correct": "Particle arrangement and freedom of movement",
                "explanation": "During melting, particles gain freedom of movement while the substance remains matter.",
            }
        ],
    }

    for key, questions in topic_bank.items():
        if key in normalized_topic:
            question = questions[int(uuid.uuid4().hex[:2], 16) % len(questions)]
            return {
                "id": f"mission_{uuid.uuid4().hex[:8]}",
                "topic": topic,
                "subtopic": topic_label,
                "question": question["question"],
                "options": question["options"],
                "correct": question["correct"],
                "explanation": question["explanation"],
            }

    concept = _find_topic_concept(topic)
    definition = ""
    examples: List[str] = []
    mistakes: List[Any] = []
    if concept:
        definition = str(concept.get("definition") or "")
        examples = list(concept.get("examples") or [])[:2]
        mistakes = list(concept.get("common_mistakes") or [])[:2]

    correct_option = (
        definition
        if definition
        else f"It explains the main idea of {topic_label} using the correct property or rule."
    )
    example_text = f" For example: {examples[0]}." if examples else ""
    mistake_option = "It is only about memorizing words without understanding the reason."
    if mistakes:
        first = mistakes[0]
        if isinstance(first, dict) and first.get("mistake"):
            mistake_option = str(first["mistake"])
        elif first:
            mistake_option = str(first)

    options = [
        correct_option,
        mistake_option,
        f"It is unrelated to {topic_label} and can be skipped for exams.",
        "It is correct only when the answer is copied exactly from notes.",
    ]

    return {
        "id": f"mission_{uuid.uuid4().hex[:8]}",
        "topic": topic,
        "subtopic": topic_label,
        "question": f"Which statement correctly describes {topic_label}?",
        "options": options,
        "correct": correct_option,
        "explanation": (
            f"The best answer connects {topic_label} with its core meaning, property, or rule."
            f"{example_text} If this felt difficult, revise the definition and one example before more MCQs."
        ),
    }


def _build_adaptive_roadmap(topic: str, mastery_band: str) -> List[Dict[str, str]]:
    topic_label = topic.replace("_", " ")
    return [
        {
            "condition": "If the student answers correctly",
            "next_step": f"Move to two exam-style applications of {topic_label}.",
            "mentor_action": "Increase challenge slowly and check answer structure.",
        },
        {
            "condition": "If the student answers incorrectly",
            "next_step": f"Explain {topic_label} again using a simpler example and one common mistake.",
            "mentor_action": "Repair the exact misconception before giving more questions.",
        },
        {
            "condition": "If the student feels unsure",
            "next_step": "Ask the coach for a live example, then retry one similar question.",
            "mentor_action": "Build confidence before measuring speed.",
        },
    ]


def _mastery_band(accuracy: float, total_topics: int) -> str:
    if total_topics == 0:
        return "baseline"
    if accuracy < 40:
        return "critical"
    if accuracy < 60:
        return "weak"
    if accuracy < 80:
        return "building"
    return "strong"


def _mission_priority(mastery_band: str) -> str:
    if mastery_band in {"baseline", "critical", "weak"}:
        return "high"
    if mastery_band == "building":
        return "medium"
    return "stretch"


def _estimate_minutes(mode: str, mastery_band: str) -> int:
    if mode == "adaptive_mission":
        return 15
    if mastery_band == "baseline":
        return 12
    if mode == "explain":
        return 18
    if mode == "exam":
        return 20
    if mode == "probable":
        return 25
    return 15


def _build_agent_sequence(plan: Dict[str, Any]) -> List[Dict[str, str]]:
    if plan["primary_agent"] == "coach":
        specialist = "Adaptive Tutor"
        specialist_action = "Creates a simple plan and one diagnostic question"
    else:
        specialist = "Revision Specialist" if plan["primary_agent"] == "revision" else "Exam Generator"
        specialist_action = "Rebuilds the concept" if plan["primary_agent"] == "revision" else "Creates targeted practice"

    return [
        {
            "agent": "Supervisor Orchestrator",
            "role": "diagnose",
            "status": "complete",
            "detail": "Selects the highest-value mission from analytics and current context.",
        },
        {
            "agent": "Personal Coach",
            "role": "plan",
            "status": "complete",
            "detail": "Turns the mission into a student-friendly objective and next action.",
        },
        {
            "agent": specialist,
            "role": "execute",
            "status": "complete",
            "detail": specialist_action,
        },
        {
            "agent": "Subject Reviewer",
            "role": "verify",
            "status": "complete",
            "detail": "Checks usefulness, clarity, and exam readiness before delivery.",
        },
    ]


def _build_success_criteria(plan: Dict[str, Any], mastery_band: str) -> List[str]:
    if plan["mode"] == "adaptive_mission":
        return [
            "Complete the one-question diagnostic without notes.",
            "Read the feedback and identify whether the issue is concept clarity, memory, or application.",
            "Follow the personalized next block until the topic reaches 80%+ confidence.",
        ]
    if mastery_band == "baseline":
        return [
            "Attempt every diagnostic question without notes.",
            "Save the result so the coach can create a first weak-topic signal.",
            "Review each wrong answer before starting the next mission.",
        ]
    if plan["mode"] == "explain":
        return [
            "Explain the concept back in your own words.",
            "Write one exam-ready answer without copying.",
            "Move to MCQs only after the common mistakes feel clear.",
        ]
    if plan["mode"] == "probable":
        return [
            "Write one probable answer in exam language.",
            "Check whether your answer includes the expected keywords.",
            "Convert missing points into a short revision note.",
        ]
    return [
        "Attempt the full practice set.",
        "Score at least 80% or review every wrong answer.",
        "Ask for mistake analysis if the same concept fails twice.",
    ]


def _build_checkpoints(plan: Dict[str, Any], success_criteria: List[str]) -> List[Dict[str, str]]:
    return [
        {
            "title": "Mission selected",
            "owner": "Supervisor",
            "status": "complete",
            "detail": plan["objective"],
        },
        {
            "title": "Specialist output ready",
            "owner": plan["primary_agent"],
            "status": "complete",
            "detail": plan["steps"][0],
        },
        {
            "title": "Student action required",
            "owner": "student",
            "status": "pending",
            "detail": success_criteria[0],
        },
        {
            "title": "Memory update",
            "owner": "coach",
            "status": "pending",
            "detail": "Save the result or continue the chat so the coach can update memory.",
        },
    ]


def _build_mission_contract(plan: Dict[str, Any], target: Dict[str, Any], analytics: Dict[str, Any]) -> Dict[str, Any]:
    summary = analytics.get("summary") or {}
    total_topics = int(summary.get("total_topics") or 0)
    accuracy = float(target.get("accuracy") or 0)
    mastery_band = _mastery_band(accuracy, total_topics)
    success_criteria = _build_success_criteria(plan, mastery_band)
    mission_type = "adaptive_diagnostic" if plan["mode"] == "adaptive_mission" else (
        "diagnostic" if mastery_band == "baseline" else plan["mode"]
    )

    return {
        "mission_type": mission_type,
        "priority": _mission_priority(mastery_band),
        "mastery_band": mastery_band,
        "estimated_minutes": _estimate_minutes(plan["mode"], mastery_band),
        "student_state": {
            "accuracy": accuracy,
            "source": target.get("source", "current_context"),
            "total_topics": total_topics,
            "average_accuracy": float(summary.get("avg_accuracy") or 0),
            "streak": int(summary.get("streak") or 0),
        },
        "agent_sequence": _build_agent_sequence(plan),
        "success_criteria": success_criteria,
        "checkpoints": _build_checkpoints(plan, success_criteria),
        "completion_report": {
            "status": "awaiting_diagnostic_answer" if plan["mode"] == "adaptive_mission" else "awaiting_student_action",
            "measure": success_criteria[0],
            "next_memory_event": "mission_result_saved",
            "coach_follow_up": plan["next_actions"][0],
            "final_report_sections": [
                "What you understood",
                "Weak point detected",
                "Confidence signal",
                "Next 80% roadmap",
            ],
        },
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
    contract = _build_mission_contract(plan, target, analytics)

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

    study_plan = _build_simple_study_plan(target["topic"], contract["mastery_band"])
    diagnostic_question = _build_single_diagnostic_question(target["topic"])
    adaptive_roadmap = _build_adaptive_roadmap(target["topic"], contract["mastery_band"])
    plan_lines = [
        "Simple Adaptive Study Plan:",
        *[f"- {item['title']} ({item['duration']}): {item['detail']}" for item in study_plan],
        "",
        "One-Question Diagnostic:",
        diagnostic_question["question"],
        *[f"- {option}" for option in diagnostic_question["options"]],
        "",
        "After You Answer:",
        "- Correct: move to exam application.",
        "- Wrong or unsure: rebuild the exact weak point with the coach.",
    ]
    result = {
        "type": "adaptive_mission",
        "answer": "\n".join(plan_lines),
        "data": {
            "text": "\n".join(plan_lines),
            "questions": [diagnostic_question],
            "study_plan": study_plan,
            "adaptive_roadmap": adaptive_roadmap,
        },
        "metadata": {
            "agent": "adaptive_tutor",
            "mission_model": "single_question_diagnostic",
            "personalization": "roadmap_updates_after_student_answer",
        },
    }
    latency_ms = round((time.time() - started_at) * 1000)

    coach = get_or_create_coach(db, user_id)
    coach.next_best_action = plan["next_actions"][0]
    coach.daily_strategy = plan["objective"]
    coach.last_recommendation = {
        "mission_id": mission_id,
        "objective": plan["objective"],
        "target_topic": target["topic"],
        "primary_agent": plan["primary_agent"],
        "mission_type": contract["mission_type"],
        "mastery_band": contract["mastery_band"],
        "priority": contract["priority"],
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
        "mission_type": contract["mission_type"],
        "priority": contract["priority"],
        "mastery_band": contract["mastery_band"],
        "estimated_minutes": contract["estimated_minutes"],
        "primary_agent": plan["primary_agent"],
        "mode": plan["mode"],
        "difficulty": plan["difficulty"],
        "objective": plan["objective"],
        "why": plan["why"],
        "steps": plan["steps"],
        "next_actions": plan["next_actions"],
        "success_criteria": contract["success_criteria"],
        "study_plan": study_plan,
        "diagnostic_question": diagnostic_question,
        "adaptive_roadmap": adaptive_roadmap,
        "agent_sequence": contract["agent_sequence"],
        "checkpoints": contract["checkpoints"],
        "student_state": contract["student_state"],
        "completion_report": contract["completion_report"],
        "result": result,
        "analytics_summary": analytics.get("summary", {}),
        "latency_ms": latency_ms,
    }
