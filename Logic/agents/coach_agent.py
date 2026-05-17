# Logic/agents/coach_agent.py

"""
PERSONAL AI COACH AGENT – Three‑pass expert system

Pass 1 – Draft (intent‑aware)
Pass 2 – Review (verify & enrich with Knowledge Graph)
Pass 3 – Format (beautiful, student‑friendly final output)

- intent = "study_advice" → detailed, friendly study explanation, no analytics
- intent = "planning"      → analytics, weak topics, next best action
"""

import logging
import os
import time
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Generator

from groq import Groq

from Logic.agent_event_bus import event_bus
from Logic.analytics_engine import get_user_analytics
from Logic.knowledge_graph import knowledge_graph
from models import (
    AICoachDailySignal,
    AICoachInteraction,
    AICoachMemory,
    AICoachProfile,
    TestHistory,
    TopicPerformance,
    UserProgress,
)

logger = logging.getLogger("ai_educator.agents.coach")

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL_NAME = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")


COACH_NAMES = [
    "Astra",
    "Nova",
    "Kiran",
    "Orion",
    "Mira",
    "Veda",
    "Aria",
    "Nexus",
]


def _safe_json(value: Any, fallback: Any):
    return value if value is not None else fallback


def _coach_name_for_user(user_id: str) -> str:
    index = sum(ord(char) for char in user_id) % len(COACH_NAMES)
    return COACH_NAMES[index]


def get_or_create_coach(
    db,
    user_id: str,
    student_display_name: Optional[str] = None,
    preferred_subjects: Optional[List[str]] = None,
    target_exam: Optional[str] = None,
    target_exam_date: Optional[date] = None,
) -> AICoachProfile:
    coach = db.query(AICoachProfile).filter(AICoachProfile.user_id == user_id).first()

    if coach:
        changed = False

        if student_display_name and coach.student_display_name != student_display_name:
            coach.student_display_name = student_display_name
            changed = True

        if preferred_subjects:
            coach.preferred_subjects = preferred_subjects
            changed = True

        if target_exam:
            coach.target_exam = target_exam
            changed = True

        if target_exam_date:
            coach.target_exam_date = target_exam_date
            changed = True

        if changed:
            coach.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(coach)

        return coach

    coach = AICoachProfile(
        coach_id=f"coach_{uuid.uuid4().hex[:16]}",
        user_id=user_id,
        coach_name=_coach_name_for_user(user_id),
        coach_tone="focused_supportive",
        coach_style="exam_oriented",
        coach_status="active",
        student_display_name=student_display_name,
        target_exam=target_exam,
        target_exam_date=target_exam_date,
        preferred_subjects=preferred_subjects or ["Chemistry"],
        motivation_profile={
            "style": "calm_direct",
            "prefers": ["short guidance", "clear priorities", "exam-focused checkpoints"],
        },
        study_preferences={
            "session_length_minutes": 25,
            "revision_style": "practice_first",
            "feedback_depth": "medium",
        },
        long_term_summary="New learner profile. Coach should observe performance and adapt advice over time.",
        daily_strategy="Start with one focused practice block, then review weak answers.",
        next_best_action="Attempt a short MCQ set and review the incorrect questions.",
    )

    db.add(coach)
    db.commit()
    db.refresh(coach)

    db.add(
        AICoachMemory(
            coach_id=coach.coach_id,
            user_id=user_id,
            memory_type="profile",
            title="Coach initialized",
            summary="A personal AI coach profile was created for this learner.",
            importance=0.7,
            confidence=1.0,
            source="system",
            metadata_json={"event": "coach_created"},
        )
    )
    db.commit()

    return coach


def _get_recent_memories(db, coach_id: str, limit: int = 6) -> List[AICoachMemory]:
    return (
        db.query(AICoachMemory)
        .filter(AICoachMemory.coach_id == coach_id)
        .order_by(AICoachMemory.importance.desc(), AICoachMemory.updated_at.desc())
        .limit(limit)
        .all()
    )


def _get_recent_sessions(db, user_id: str, limit: int = 5) -> List[TestHistory]:
    return (
        db.query(TestHistory)
        .filter(TestHistory.user_id == user_id)
        .order_by(TestHistory.id.desc())
        .limit(limit)
        .all()
    )


def _get_topic_snapshot(db, user_id: str) -> Dict[str, List[Dict[str, Any]]]:
    topics = (
        db.query(TopicPerformance)
        .filter(TopicPerformance.user_id == user_id)
        .order_by(TopicPerformance.attempts.desc())
        .all()
    )

    topic_rows = [
        {
            "topic": topic.topic,
            "attempts": int(topic.attempts or 0),
            "correct": int(topic.correct or 0),
            "accuracy": round(float(topic.accuracy), 1),
            "weak": bool(topic.weak),
            "trend_score": round(float(topic.trend_score or 0), 1),
            "avg_time_per_question": round(float(topic.avg_time_per_question or 0), 1),
        }
        for topic in topics
    ]

    weak_topics = sorted(
        [item for item in topic_rows if item["attempts"] > 0],
        key=lambda item: (item["accuracy"], -item["attempts"]),
    )[:5]

    strong_topics = sorted(
        [item for item in topic_rows if item["attempts"] > 0],
        key=lambda item: (-item["accuracy"], -item["attempts"]),
    )[:5]

    return {
        "all_topics": topic_rows,
        "weak_topics": weak_topics,
        "strong_topics": strong_topics,
    }


def _build_progress_snapshot(db, user_id: str) -> Dict[str, Any]:
    progress = db.query(UserProgress).filter(UserProgress.user_id == user_id).first()

    if not progress:
        return {
            "total_tests": 0,
            "total_questions": 0,
            "total_correct": 0,
            "accuracy": 0.0,
            "xp": 0,
            "level": 1,
            "streak": 0,
            "focus_score": 0.0,
            "consistency_index": 0.0,
            "learning_efficiency": 0.0,
        }

    return {
        "total_tests": int(progress.total_tests or 0),
        "total_questions": int(progress.total_questions or 0),
        "total_correct": int(progress.total_correct or 0),
        "accuracy": round(float(progress.accuracy), 1),
        "xp": int(progress.xp or 0),
        "level": int(progress.level),
        "streak": int(progress.streak or 0),
        "focus_score": round(float(progress.focus_score or 0), 1),
        "consistency_index": round(float(progress.consistency_index or 0), 1),
        "learning_efficiency": round(float(progress.learning_efficiency or 0), 1),
    }


def _build_recent_session_snapshot(sessions: List[TestHistory]) -> List[Dict[str, Any]]:
    rows = []

    for session in sessions:
        total = int(session.total_questions or 0)
        score = int(session.score or 0)

        rows.append(
            {
                "date": session.date.isoformat() if session.date else None,
                "topic": session.topic,
                "score": score,
                "total_questions": total,
                "accuracy": round((score / total) * 100, 1) if total else 0.0,
                "xp_earned": int(session.xp_earned or 0),
                "focus_score": round(float(session.focus_score or 0), 1),
                "session_type": session.session_type,
            }
        )

    return rows


def _make_rule_based_recommendation(
    progress: Dict[str, Any],
    weak_topics: List[Dict[str, Any]],
    recent_sessions: List[Dict[str, Any]],
) -> str:
    base = ""
    if progress["total_questions"] == 0:
        base = "Start with a 10-question diagnostic MCQ set to establish your baseline."
    elif weak_topics:
        topic = weak_topics[0]["topic"]
        accuracy = weak_topics[0]["accuracy"]
        base = f"Focus next on {topic}. Current accuracy is {accuracy}%, so do one short revision pass and then 15 MCQs."
    elif progress["accuracy"] < 60:
        base = "Prioritize accuracy over speed today. Review incorrect answers before starting a new test."
    elif recent_sessions and recent_sessions[0]["accuracy"] >= 80:
        base = "Good momentum. Move to mixed practice and protect your streak with one timed set."
    else:
        base = "Do one focused practice block, then review mistakes immediately while memory is fresh."

    # ── Enrich with Knowledge Graph insights ──────────────────────────────
    if knowledge_graph.concepts and weak_topics:
        w = weak_topics[0]["topic"]
        concepts = knowledge_graph.search_by_keyword(w, limit=2)
        if concepts:
            c = concepts[0]
            if c.get("typical_exam_weightage") == "high":
                base += f" (Note: '{c['title']}' has high exam weightage – prioritise this.)"
            if c.get("prerequisites"):
                prereqs = ", ".join(c["prerequisites"])
                base += f" Also consider revising prerequisites: {prereqs}."
            if c.get("common_mistakes"):
                mistakes = [m['mistake'] for m in c['common_mistakes'][:2]]
                base += f" Watch out for common errors like: {', '.join(mistakes)}."

    return base


# ─── Intent‑based prompt builders ───────────────────────────────────────────

def _build_study_prompt(
    coach: AICoachProfile,
    question: str,
    topic_snapshot: Dict[str, Any],
) -> str:
    """Prompt for study_advice – detailed, friendly, no analytics."""
    graph_context = ""
    # Search knowledge graph for concepts matching the question
    keywords = [w for w in question.lower().split() if len(w) > 2]
    for kw in keywords[:3]:
        concepts = knowledge_graph.search_by_keyword(kw, limit=2)
        if concepts:
            for c in concepts:
                graph_context += f"--- CONCEPT: {c['title']} (ID: {c['concept_id']}) ---\n"
                graph_context += f"Definition: {c.get('definition', '')}\n"
                graph_context += f"Explanation: {c.get('core_explanation', '')}\n"
                if c.get("key_points"):
                    graph_context += "Key Points:\n  " + "\n  ".join(c["key_points"]) + "\n"
                if c.get("examples"):
                    graph_context += "Examples:\n  " + "\n  ".join(c["examples"]) + "\n"
                if c.get("common_mistakes"):
                    graph_context += "Common Mistakes:\n  " + "\n  ".join(
                        [f"{m['mistake']} -> {m['correction']}" for m in c["common_mistakes"]]
                    ) + "\n"
                break  # only use the first matching concept

    return f"""
You are {coach.coach_name}, a personal AI study coach.

STUDY MODE – Provide a clear, detailed, and friendly explanation of the concept the student asks about. Use the provided knowledge base. Do NOT mention any analytics like Xp, streaks, focus scores, or study plans unless the student explicitly asks for them.

Use simple language, everyday analogies, and break down complex ideas step-by-step. Highlight common mistakes to help the student avoid them.

FORMATTING RULES (STRICT):
- Use plain text only. Do NOT use markdown symbols like asterisks (**), underscores, or backticks.
- Use blank lines between paragraphs.
- Use simple bullet points with a dash (-) or a simple colon if listing items.
- Keep sentences short and friendly.
- Present the answer as a well-structured mini-article.

KNOWLEDGE BASE:
{graph_context if graph_context else "No specific curriculum data found – explain from your general chemistry knowledge."}

QUESTION FROM STUDENT:
{question}
""".strip()


def _build_planning_prompt(
    coach: AICoachProfile,
    progress: Dict[str, Any],
    topic_snapshot: Dict[str, Any],
    recent_sessions: List[Dict[str, Any]],
    memories: List[AICoachMemory],
    analytics_snapshot: Dict[str, Any],
    recommendation: str,
) -> str:
    """Prompt for planning – includes analytics and recommendations."""
    memory_text = "\n".join(
        f"- {memory.title}: {memory.summary}"
        for memory in memories
    ) or "- No durable coach memories yet."

    graph_hints = ""
    if knowledge_graph.concepts and topic_snapshot["weak_topics"]:
        for wt in topic_snapshot["weak_topics"][:2]:
            concepts = knowledge_graph.search_by_keyword(wt["topic"], limit=2)
            for c in concepts:
                hint = f"- {c['title']}: importance={c.get('importance_level','medium')}, weightage={c.get('typical_exam_weightage','medium')}"
                if c.get('prerequisites'):
                    hint += f", prerequisites={c['prerequisites']}"
                if c.get('common_mistakes'):
                    hint += f", common mistakes: {[m['mistake'] for m in c['common_mistakes'][:2]]}"
                graph_hints += hint + "\n"
    if graph_hints:
        graph_hints = "\nCURRICULUM INSIGHTS (use these to prioritise):\n" + graph_hints

    return f"""
You are {coach.coach_name}, a personal AI study coach.

PLANNING MODE – The student wants a study plan or performance review. Use the analytics below to give concise, actionable advice. Focus on weak topics, recent performance, and clear next steps. End with exactly one recommended action.

FORMATTING RULES (STRICT):
- Use plain text only. Do NOT use markdown symbols like asterisks (**), underscores, or backticks.
- Use numbered phases (1., 2., 3.) and sub-points with dashes (-) or simple indentation.
- Keep the plan structured, easy to scan, and friendly.
- Do not write a long essay; use short paragraphs and clear sections.

STUDENT PROFILE:
Name: {coach.student_display_name or "Student"}
Target exam: {coach.target_exam or "Not set"}
Target exam date: {coach.target_exam_date or "Not set"}
Preferred subjects: {coach.preferred_subjects}

PROGRESS:
{progress}

TOPIC SNAPSHOT:
Weak topics: {topic_snapshot["weak_topics"]}
Strong topics: {topic_snapshot["strong_topics"]}

RECENT SESSIONS:
{recent_sessions}

ANALYTICS:
{analytics_snapshot}

COACH MEMORY:
{memory_text}
{graph_hints}

RECOMMENDATION (rule‑based):
{recommendation}
""".strip()


# ─── Review prompt (new) ───────────────────────────────────────────────────
def _build_review_prompt(
    coach: AICoachProfile,
    question: str,
    draft: str,
    topic_snapshot: Dict[str, Any],
) -> str:
    """Prompt that reviews a draft answer, corrects errors, adds missing data."""
    graph_context = ""
    keywords = [w for w in question.lower().split() if len(w) > 2]
    for kw in keywords[:3]:
        concepts = knowledge_graph.search_by_keyword(kw, limit=2)
        if concepts:
            for c in concepts:
                graph_context += f"--- CONCEPT: {c['title']} (ID: {c['concept_id']}) ---\n"
                graph_context += f"Definition: {c.get('definition', '')}\n"
                graph_context += f"Explanation: {c.get('core_explanation', '')}\n"
                if c.get("key_points"):
                    graph_context += "Key Points:\n  " + "\n  ".join(c["key_points"]) + "\n"
                if c.get("examples"):
                    graph_context += "Examples:\n  " + "\n  ".join(c["examples"]) + "\n"
                if c.get("common_mistakes"):
                    graph_context += "Common Mistakes:\n  " + "\n  ".join(
                        [f"{m['mistake']} -> {m['correction']}" for m in c["common_mistakes"]]
                    ) + "\n"
                break

    return f"""
You are {coach.coach_name}, a personal AI study coach. Review the draft answer below and produce an enriched version.

TASKS:
- Correct any factual errors using the provided curriculum data.
- Add any missing key points, examples, or common mistakes from the curriculum.
- Keep the tone friendly and encouraging.
- Do NOT use markdown symbols. Use plain text with dashes (-) for bullet points.
- Do NOT mention analytics, XP, streaks, or study plans unless asked.

CURRICULUM DATA:
{graph_context if graph_context else "No specific curriculum data found – keep the draft's content."}

DRAFT ANSWER:
{draft}

Now provide the enriched answer.
""".strip()


# ─── Format prompt (new) ───────────────────────────────────────────────────
def _build_format_prompt(coach: AICoachProfile, enriched: str) -> str:
    """Prompt that takes an enriched answer and formats it beautifully."""
    return f"""
You are {coach.coach_name}, a personal AI study coach. Take the enriched answer below and reformat it into a beautiful, student‑friendly mini‑article.

FORMATTING RULES:
- Use clear, bold‑looking headings (e.g., "What is Matter?") by using a blank line before and after the heading.
- Use short paragraphs (2‑3 sentences each).
- Use simple bullet points with a dash (-) for lists.
- Highlight important words by placing them on their own line or repeating them naturally.
- Keep the language warm and encouraging.
- Do NOT use any markdown symbols like asterisks or underscores.

ENRICHED ANSWER:
{enriched}

Now provide the final, beautifully formatted answer.
""".strip()


def _persist_interaction(
    db,
    coach: AICoachProfile,
    role: str,
    message: str,
    intent: str = "general",
    mode: str = "coach",
    metadata: Optional[Dict[str, Any]] = None,
    quality_score: float = 0.0,
):
    interaction = AICoachInteraction(
        coach_id=coach.coach_id,
        user_id=coach.user_id,
        role=role,
        message=message,
        intent=intent,
        mode=mode,
        quality_score=quality_score,
        metadata_json=metadata or {},
    )

    db.add(interaction)
    coach.last_interaction_at = datetime.utcnow()
    coach.updated_at = datetime.utcnow()
    db.commit()


def run_daily_learning_cycle(db, user_id: str) -> AICoachDailySignal:
    coach = get_or_create_coach(db, user_id)
    today = date.today()

    progress = _build_progress_snapshot(db, user_id)
    topic_snapshot = _get_topic_snapshot(db, user_id)

    sessions_today = (
        db.query(TestHistory)
        .filter(TestHistory.user_id == user_id, TestHistory.date == today)
        .all()
    )

    questions_attempted = sum(int(session.total_questions or 0) for session in sessions_today)
    correct = sum(int(session.score or 0) for session in sessions_today)
    xp_earned = sum(int(session.xp_earned or 0) for session in sessions_today)
    focus_scores = [float(session.focus_score or 0) for session in sessions_today]

    accuracy = round((correct / questions_attempted) * 100, 1) if questions_attempted else progress["accuracy"]
    focus_score = round(sum(focus_scores) / len(focus_scores), 1) if focus_scores else progress["focus_score"]

    recommendation = _make_rule_based_recommendation(
        progress=progress,
        weak_topics=topic_snapshot["weak_topics"],
        recent_sessions=_build_recent_session_snapshot(_get_recent_sessions(db, user_id, limit=3)),
    )

    risk_level = "normal"
    if accuracy < 45 and questions_attempted >= 10:
        risk_level = "high"
    elif accuracy < 60:
        risk_level = "watch"

    signal = AICoachDailySignal(
        user_id=user_id,
        coach_id=coach.coach_id,
        signal_date=today,
        sessions_count=len(sessions_today),
        questions_attempted=questions_attempted,
        accuracy=accuracy,
        focus_score=focus_score,
        xp_earned=xp_earned,
        weakest_topics=topic_snapshot["weak_topics"],
        strongest_topics=topic_snapshot["strong_topics"],
        recommendation=recommendation,
        risk_level=risk_level,
    )

    db.add(signal)

    coach.weak_topics_snapshot = topic_snapshot["weak_topics"]
    coach.strengths_snapshot = topic_snapshot["strong_topics"]
    coach.daily_strategy = recommendation
    coach.next_best_action = recommendation
    coach.last_recommendation = {
        "date": today.isoformat(),
        "recommendation": recommendation,
        "risk_level": risk_level,
    }
    coach.last_learning_cycle_at = datetime.utcnow()
    coach.updated_at = datetime.utcnow()

    db.add(
        AICoachMemory(
            coach_id=coach.coach_id,
            user_id=user_id,
            memory_type="daily_learning",
            title=f"Daily signal {today.isoformat()}",
            summary=f"Accuracy {accuracy}%, focus {focus_score}, risk {risk_level}. Recommendation: {recommendation}",
            importance=0.8,
            confidence=0.85,
            source="daily_learning_cycle",
            metadata_json={
                "accuracy": accuracy,
                "focus_score": focus_score,
                "weakest_topics": topic_snapshot["weak_topics"][:3],
                "risk_level": risk_level,
            },
        )
    )

    db.commit()
    db.refresh(signal)

    return signal


def coach_agent(request, db=None) -> dict:
    start_time = time.time()

    if db is None:
        return {
            "type": "coach",
            "answer": "Coach needs database access to personalize advice.",
            "metadata": {"agent": "coach", "status": "db_required"},
        }

    user_id = getattr(request, "user_id", None) or getattr(request, "session_id", "anonymous")
    question = getattr(request, "question", "")
    session_id = getattr(request, "session_id", f"coach-{user_id}")
    intent = getattr(request, "intent", "general")
    mode = getattr(request, "mode", "coach")

    event_bus.emit(
        "coach",
        "task_start",
        {
            "task": f"Coach advice: {question[:60]}...",
            "message": f"Loading personal coach for user {user_id}",
            "user_id": user_id,
        },
        session_id=session_id,
    )

    coach = get_or_create_coach(db, user_id)
    progress = _build_progress_snapshot(db, user_id)
    topic_snapshot = _get_topic_snapshot(db, user_id)
    recent_sessions = _build_recent_session_snapshot(_get_recent_sessions(db, user_id))
    memories = _get_recent_memories(db, coach.coach_id)

    try:
        analytics_snapshot = get_user_analytics(db, user_id)
    except Exception:
        analytics_snapshot = {}

    recommendation = _make_rule_based_recommendation(
        progress=progress,
        weak_topics=topic_snapshot["weak_topics"],
        recent_sessions=recent_sessions,
    )

    event_bus.emit(
        "coach",
        "step",
        {
            "step": "context",
            "step_num": 1,
            "total_steps": 4,
            "message": "Built analytics, memory, and session context (including Knowledge Graph insights)",
            "weak_topics": topic_snapshot["weak_topics"][:3],
        },
        session_id=session_id,
    )

    # Use intent‑based prompt selection
    if intent == "planning":
        system_prompt = _build_planning_prompt(
            coach=coach,
            progress=progress,
            topic_snapshot=topic_snapshot,
            recent_sessions=recent_sessions,
            memories=memories,
            analytics_snapshot=analytics_snapshot,
            recommendation=recommendation,
        )
    else:
        system_prompt = _build_study_prompt(
            coach=coach,
            question=question,
            topic_snapshot=topic_snapshot,
        )

    event_bus.emit(
        "coach",
        "tool_call",
        {
            "step": "generate",
            "step_num": 2,
            "total_steps": 4,
            "tool": "groq_llm",
            "message": f"Generating personal coach response via {MODEL_NAME}",
            "model": MODEL_NAME,
            "temperature": 0.35,
        },
        session_id=session_id,
    )

    try:
        response = groq_client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
            temperature=0.35,
            max_tokens=600,
        )
        answer = response.choices[0].message.content.strip()
    except Exception as exc:
        logger.error("[COACH] Groq API error: %s", exc)
        answer = recommendation

        event_bus.emit(
            "coach",
            "error",
            {
                "step": "generate",
                "message": f"LLM failed, using rule-based recommendation: {str(exc)}",
            },
            session_id=session_id,
            severity="warning",
        )

    event_bus.emit(
        "coach",
        "step",
        {
            "step": "memory",
            "step_num": 3,
            "total_steps": 4,
            "message": "Saving coach interaction and updating next best action",
        },
        session_id=session_id,
    )

    coach.daily_strategy = recommendation
    coach.next_best_action = recommendation
    coach.last_interaction_at = datetime.utcnow()
    coach.updated_at = datetime.utcnow()

    _persist_interaction(
        db=db,
        coach=coach,
        role="user",
        message=question,
        intent=intent,
        mode=mode,
        metadata={"session_id": session_id},
    )

    _persist_interaction(
        db=db,
        coach=coach,
        role="assistant",
        message=answer,
        intent=intent,
        mode=mode,
        metadata={
            "session_id": session_id,
            "progress": progress,
            "weak_topics": topic_snapshot["weak_topics"][:3],
        },
        quality_score=0.8,
    )

    latency_ms = round((time.time() - start_time) * 1000)

    event_bus.emit(
        "coach",
        "task_complete",
        {
            "status": "success",
            "message": f"Coach response delivered by {coach.coach_name}",
            "latency_ms": latency_ms,
            "quality_score": 0.8,
            "quality_passed": True,
        },
        session_id=session_id,
    )

    return {
        "type": "coach",
        "answer": answer,
        "coach_id": coach.coach_id,
        "coach_name": coach.coach_name,
        "next_best_action": coach.next_best_action,
        "daily_strategy": coach.daily_strategy,
        "memory_used": [
            {
                "title": memory.title,
                "summary": memory.summary,
                "importance": memory.importance,
            }
            for memory in memories
        ],
        "analytics_snapshot": {
            "progress": progress,
            "weak_topics": topic_snapshot["weak_topics"],
            "strong_topics": topic_snapshot["strong_topics"],
        },
        "metadata": {
            "agent": "coach",
            "latency_ms": latency_ms,
            "model": MODEL_NAME,
        },
    }


# ─── STREAMING GENERATOR – Three‑pass expert system ────────────────────────
def coach_agent_stream(request, db=None) -> Generator[str, None, None]:
    """
    Stream the coach's reply after three passes:
    1. Draft – intent‑aware generation
    2. Review – verify & enrich with Knowledge Graph
    3. Format – beautiful final structure
    """
    if db is None:
        yield "Coach needs database access to personalize advice."
        return

    user_id = getattr(request, "user_id", None) or getattr(request, "session_id", "anonymous")
    question = getattr(request, "question", "")
    session_id = getattr(request, "session_id", f"coach-{user_id}")
    intent = getattr(request, "intent", "study_advice")
    mode = getattr(request, "mode", "coach")

    coach = get_or_create_coach(db, user_id)
    progress = _build_progress_snapshot(db, user_id)
    topic_snapshot = _get_topic_snapshot(db, user_id)
    recent_sessions = _build_recent_session_snapshot(_get_recent_sessions(db, user_id))
    memories = _get_recent_memories(db, coach.coach_id)

    try:
        analytics_snapshot = get_user_analytics(db, user_id)
    except Exception:
        analytics_snapshot = {}

    recommendation = _make_rule_based_recommendation(
        progress=progress,
        weak_topics=topic_snapshot["weak_topics"],
        recent_sessions=recent_sessions,
    )

    # ── Pass 1: Draft ──────────────────────────────────────────────────────
    if intent == "planning":
        draft_prompt = _build_planning_prompt(
            coach=coach,
            progress=progress,
            topic_snapshot=topic_snapshot,
            recent_sessions=recent_sessions,
            memories=memories,
            analytics_snapshot=analytics_snapshot,
            recommendation=recommendation,
        )
    else:
        draft_prompt = _build_study_prompt(
            coach=coach,
            question=question,
            topic_snapshot=topic_snapshot,
        )

    draft = ""
    try:
        draft_resp = groq_client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": draft_prompt},
                {"role": "user", "content": question},
            ],
            temperature=0.35,
            max_tokens=450,
            stream=False,
        )
        draft = draft_resp.choices[0].message.content.strip()
    except Exception as exc:
        logger.error("[COACH DRAFT] Groq error: %s", exc)
        fallback = recommendation if intent == "planning" else "I'm having trouble explaining that right now."
        draft = fallback

    # ── Pass 2: Review & Enrich ────────────────────────────────────────────
    review_prompt = _build_review_prompt(
        coach=coach,
        question=question,
        draft=draft,
        topic_snapshot=topic_snapshot,
    )
    enriched = ""
    try:
        review_resp = groq_client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": review_prompt},
                {"role": "user", "content": f"Please review and improve this answer:\n\n{draft}"},
            ],
            temperature=0.3,
            max_tokens=550,
            stream=False,
        )
        enriched = review_resp.choices[0].message.content.strip()
    except Exception as exc:
        logger.error("[COACH REVIEW] Groq error: %s", exc)
        enriched = draft   # fallback to draft

    # ── Pass 3: Format & Stream ────────────────────────────────────────────
    format_prompt = _build_format_prompt(coach=coach, enriched=enriched)
    full_answer = ""
    try:
        format_stream = groq_client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": format_prompt},
                {"role": "user", "content": "Please format this answer."},
            ],
            temperature=0.25,
            max_tokens=600,
            stream=True,
        )
        for chunk in format_stream:
            token = chunk.choices[0].delta.content
            if token:
                full_answer += token
                yield token
    except Exception as exc:
        logger.error("[COACH FORMAT] Groq error: %s", exc)
        # stream enriched as fallback
        for char in enriched:
            yield char
            full_answer += char

    # Persist after stream
    coach.daily_strategy = recommendation
    coach.next_best_action = recommendation
    coach.last_interaction_at = datetime.utcnow()
    coach.updated_at = datetime.utcnow()

    _persist_interaction(
        db=db,
        coach=coach,
        role="user",
        message=question,
        intent=intent,
        mode=mode,
        metadata={"session_id": session_id},
    )
    _persist_interaction(
        db=db,
        coach=coach,
        role="assistant",
        message=full_answer,
        intent=intent,
        mode=mode,
        metadata={
            "session_id": session_id,
            "progress": progress,
            "weak_topics": topic_snapshot["weak_topics"][:3],
        },
        quality_score=0.9,
    )

    event_bus.emit(
        "coach",
        "task_complete",
        {
            "status": "success",
            "message": f"Coach reviewed and delivered by {coach.coach_name}",
            "latency_ms": 0,
            "quality_score": 0.9,
            "quality_passed": True,
        },
        session_id=session_id,
    )