# Logic/agents/coach_agent.py

"""
PERSONAL AI COACH AGENT – Hybrid autonomous architecture

- Definition/explanation questions → built directly from Knowledge Graph (no LLM)
- Planning questions → LLM draft + KG enricher safety net
"""

import logging
import time
import uuid
import base64
import json
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Generator

from Logic.agent_event_bus import event_bus
from Logic.analytics_engine import get_user_analytics
from Logic.coach import (
    build_coach_plan,
    build_adaptive_answer_blocks,
    build_compact_context,
    coach_observability,
    coach_settings,
    coach_tool_registry,
    llm_router,
    parse_semantic_event,
    resolve_hybrid_query,
    score_coach_answer,
    semantic_event,
    understand_query,
)
from Logic.coach.memory_store import (
    build_layered_lesson_memory,
    build_memory_summary,
    format_layered_lesson_memory,
    interaction_messages,
)
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

FAST_MODEL = coach_settings.fast_model
TUTOR_MODEL = coach_settings.tutor_model
REVIEW_MODEL = coach_settings.review_model
MODEL_NAME = TUTOR_MODEL

MATERIAL_NOT_FOUND_MESSAGE = coach_settings.not_found_message


COACH_NAMES = [
    "Astra", "Nova", "Kiran", "Orion", "Mira", "Veda", "Aria", "Nexus",
]


def _safe_json(value: Any, fallback: Any):
    return value if value is not None else fallback


def _material_not_found(adaptive_context: Optional[Dict[str, Any]] = None) -> str:
    learning_context = _as_dict((adaptive_context or {}).get("learning_context"))
    policy_text = str(learning_context.get("required_not_found_response") or "").strip()
    return policy_text or MATERIAL_NOT_FOUND_MESSAGE


def _strict_grounding_enabled(request, adaptive_context: Optional[Dict[str, Any]] = None) -> bool:
    if bool(getattr(request, "strict_grounding", False) or getattr(request, "retrieval_required", False)):
        return True
    if getattr(request, "fallback_to_general_knowledge", True) is False:
        return True
    learning_context = _as_dict((adaptive_context or {}).get("learning_context"))
    if learning_context.get("scope") == "selected_study_material_only":
        return True
    answer_policy = str(learning_context.get("answer_policy") or "").lower()
    strict_markers = (
        "answer from study material only",
        "selected study material only",
        "retrieved study material only",
        "use only retrieved study material",
    )
    return any(marker in answer_policy for marker in strict_markers)


def _previous_retrieval_policy(interactions: List[AICoachInteraction]) -> str:
    for interaction in reversed(interactions or []):
        metadata = interaction.metadata_json if isinstance(interaction.metadata_json, dict) else {}
        policy = str(metadata.get("retrieval_policy") or "").strip().lower()
        if policy in {"none", "optional", "required"}:
            return policy
    return "none"


def _retrieval_policy(
    request,
    query_understanding,
    adaptive_context: Optional[Dict[str, Any]] = None,
    previous_policy: str = "none",
) -> str:
    """Choose whether RAG is unnecessary, useful, or mandatory for this turn."""
    if coach_settings.strict_grounding_default or _strict_grounding_enabled(request, adaptive_context):
        return "required"
    if bool(getattr(query_understanding, "requires_grounding", False)):
        return "required"
    if bool(getattr(query_understanding, "is_follow_up", False)) and previous_policy == "required":
        return "required"
    if str(getattr(query_understanding, "retrieval_policy", "none")) == "optional":
        return "optional"
    return "none"


def _apply_effective_retrieval_policy(query_understanding, retrieval_policy: str):
    query_understanding.retrieval_policy = retrieval_policy
    query_understanding.needs_retrieval = retrieval_policy != "none"
    query_understanding.requires_grounding = retrieval_policy == "required"
    if retrieval_policy == "required":
        query_understanding.reasoning_mode = "source_grounded"
    elif bool(getattr(query_understanding, "is_follow_up", False)):
        query_understanding.reasoning_mode = "contextual_reasoning"

    tools = list(getattr(query_understanding, "requested_tools", []) or [])
    if retrieval_policy != "none" and "knowledge_search" not in tools:
        tools.insert(0, "knowledge_search")
    if retrieval_policy == "none" and "knowledge_search" in tools:
        tools.remove("knowledge_search")
    query_understanding.requested_tools = tools
    return query_understanding


def _selected_material_scope(request, adaptive_context: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    learning_context = _as_dict((adaptive_context or {}).get("learning_context"))
    return {
        "subject": str(
            getattr(request, "subject", "")
            or learning_context.get("selected_subject")
            or learning_context.get("subject")
            or ""
        ).strip(),
        "chapter": str(
            getattr(request, "chapter", "")
            or learning_context.get("selected_chapter")
            or learning_context.get("chapter")
            or ""
        ).strip(),
        "topic": str(
            getattr(request, "topic", "")
            or learning_context.get("selected_topic")
            or learning_context.get("topic")
            or ""
        ).strip(),
        "section_id": str(
            getattr(request, "section_id", "")
            or learning_context.get("section_id")
            or learning_context.get("topic")
            or ""
        ).strip(),
    }


def _grounding_terms(value: str) -> List[str]:
    stopwords = {
        "define", "explain", "simple", "words", "please", "can", "you", "this", "that",
        "what", "why", "how", "again", "more", "example", "examples", "give", "tell",
        "about", "concept", "topic", "previous", "answer", "student", "question", "selected",
        "chapter", "from", "with", "only", "study", "material", "the", "and", "for", "are",
        "into", "like", "first", "time",
    }
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]+", (value or "").lower())
    terms = []
    for word in words:
        if len(word) < 4 or word in stopwords:
            continue
        if word not in terms:
            terms.append(word)
    return terms[:10]


def _merge_frontend_context(
    conversation_context: Dict[str, Any],
    adaptive_context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    merged = dict(conversation_context or {})
    learning_context = _as_dict((adaptive_context or {}).get("learning_context"))

    previous_question = str(
        learning_context.get("previous_user_question")
        or learning_context.get("last_student_question")
        or ""
    ).strip()
    previous_answer = str(
        learning_context.get("previous_ai_answer")
        or learning_context.get("previous_assistant_answer")
        or ""
    ).strip()

    if learning_context.get("is_follow_up"):
        merged["is_follow_up"] = True
    if previous_question:
        merged["last_student_question"] = previous_question

    extra_lines = []
    if previous_question:
        extra_lines.append(f"Student: {previous_question[:300]}")
    if previous_answer:
        extra_lines.append(f"Tutor: {previous_answer[:420]}")
    existing_thread = str(merged.get("recent_thread") or "").strip()
    if extra_lines:
        merged["recent_thread"] = "\n".join(extra_lines + ([existing_thread] if existing_thread else []))

    return merged


def _resolve_retrieval_question(question: str, adaptive_context: Dict[str, Any], conversation_context: Dict[str, Any]) -> str:
    learning_context = _as_dict(adaptive_context.get("learning_context"))
    is_follow_up = bool(learning_context.get("is_follow_up") or conversation_context.get("is_follow_up"))
    previous_question = str(learning_context.get("previous_user_question") or conversation_context.get("last_student_question") or "").strip()
    previous_answer = str(learning_context.get("previous_ai_answer") or "").strip()
    grounding_prompt = str(learning_context.get("grounding_context_prompt") or "").strip()

    if is_follow_up and (previous_question or previous_answer):
        return "\n".join(
            part
            for part in [
                previous_question,
                previous_answer[:1200],
                question,
                grounding_prompt,
            ]
            if part
        )
    return "\n".join(part for part in [question, grounding_prompt] if part)


def _retrieve_selected_material(request, adaptive_context: Dict[str, Any], conversation_context: Dict[str, Any]) -> Dict[str, Any]:
    scope = _selected_material_scope(request, adaptive_context)
    retrieval_question = _resolve_retrieval_question(
        question=getattr(request, "question", "") or "",
        adaptive_context=adaptive_context,
        conversation_context=conversation_context,
    )

    result = coach_tool_registry.run(
        "knowledge_search",
        question=retrieval_question or scope.get("section_id") or "general",
        scope=scope,
    ).to_dict()
    result["retrieval_question"] = retrieval_question
    return result


def _material_supports_question(search_result: Dict[str, Any], adaptive_context: Dict[str, Any], conversation_context: Dict[str, Any]) -> bool:
    context = str(search_result.get("context") or "")
    if search_result.get("error") or not context.strip():
        return False

    learning_context = _as_dict(adaptive_context.get("learning_context"))
    is_follow_up = bool(learning_context.get("is_follow_up") or conversation_context.get("is_follow_up"))
    anchor_source = (
        str(learning_context.get("previous_user_question") or "")
        if is_follow_up
        else str(search_result.get("retrieval_question") or "")
    )
    terms = _grounding_terms(anchor_source)
    if not terms:
        return True

    searchable = " ".join(
        [
            context,
            str(search_result.get("section_id") or ""),
            str(search_result.get("scope", {}).get("topic") or ""),
            str(search_result.get("scope", {}).get("chapter") or ""),
        ]
    ).lower()
    return any(term in searchable for term in terms)


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


def _get_recent_interactions(
    db,
    coach_id: str,
    session_id: Optional[str] = None,
    limit: int = 8,
) -> List[AICoachInteraction]:
    rows = (
        db.query(AICoachInteraction)
        .filter(AICoachInteraction.coach_id == coach_id)
        .order_by(AICoachInteraction.id.desc())
        .limit(limit * 3)
        .all()
    )
    if session_id:
        session_rows = [
            row for row in rows
            if isinstance(row.metadata_json, dict)
            and row.metadata_json.get("session_id") == session_id
        ]
        if session_rows:
            rows = session_rows

    return list(reversed(rows[:limit]))


def _looks_like_follow_up(question: str) -> bool:
    q = (question or "").strip().lower()
    if not q:
        return False

    compact = q.rstrip("?.!")
    exact_followups = {
        "why", "how", "why is that", "how does that work", "one more example",
        "practice this", "test me", "test me on this", "quiz me", "quiz me on this",
    }
    if compact in exact_followups:
        return True

    followup_starts = (
        "then", "and", "but", "what about", "example", "give example", "more",
        "simpler", "explain again", "this", "it", "that", "same", "next",
        "practice this", "show me",
    )
    return any(q == prefix or q.startswith(f"{prefix} ") for prefix in followup_starts)


def _build_conversation_context(
    question: str,
    coach: AICoachProfile,
    interactions: List[AICoachInteraction],
    memories: List[AICoachMemory],
) -> Dict[str, Any]:
    recent_lines = []
    last_student_question = ""
    for item in interactions[-8:]:
        role = "Student" if item.role == "user" else "Tutor"
        message = (item.message or "").strip().replace("\n", " ")
        if not message:
            continue
        if item.role == "user":
            last_student_question = message
        recent_lines.append(f"{role}: {message[:260]}")

    memory_lines = [
        f"- {memory.title}: {memory.summary}"
        for memory in memories[:6]
        if memory.summary
    ]

    return {
        "is_follow_up": bool(recent_lines and _looks_like_follow_up(question)),
        "last_student_question": last_student_question,
        "recent_thread": "\n".join(recent_lines) or "No previous lesson thread in this session.",
        "durable_memory": "\n".join(memory_lines) or "No durable learning memories yet.",
        "long_term_summary": coach.long_term_summary or "No long-term learning summary yet.",
    }


def _build_assistance_blocks(question: str, answer_format: Dict[str, Any]) -> List[Dict[str, str]]:
    topic_hint = (question or "this concept").strip()
    if len(topic_hint) > 90:
        topic_hint = topic_hint[:87].rstrip() + "..."

    return [
        {
            "label": "Need simpler explanation?",
            "prompt": f"Explain {topic_hint} in a simpler way with a very easy example.",
        },
        {
            "label": "Show live example",
            "prompt": f"Show a real-life example of {topic_hint} and connect it to the concept.",
        },
        {
            "label": "Practice this concept",
            "prompt": f"Give me one practice question on {topic_hint}, then check my answer.",
        },
        {
            "label": "Common mistake students make",
            "prompt": f"What common mistake do students make in {topic_hint}, and how can I avoid it?",
        },
    ]


def _update_learning_journey_summary(
    coach: AICoachProfile,
    question: str,
    answer_format: Dict[str, Any],
    topic_snapshot: Dict[str, Any],
    is_follow_up: bool,
) -> None:
    weak_topics = [
        item.get("topic", "")
        for item in topic_snapshot.get("weak_topics", [])[:3]
        if item.get("topic")
    ]
    followup_note = "connected follow-up" if is_follow_up else "new learning query"
    coach.long_term_summary = (
        f"Recent focus: {question[:160]}. "
        f"Response style used: {answer_format.get('label', 'Concept Builder')}. "
        f"Conversation type: {followup_note}. "
        f"Watched weak areas: {', '.join(weak_topics) if weak_topics else 'not enough data yet'}."
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


def _is_definition_question(question: str) -> bool:
    definition_keywords = ["define", "what is", "explain", "meaning", "definition", "describe", "tell me about", "what are"]
    q = question.lower()
    return any(kw in q for kw in definition_keywords)


def _normalized_short_message(question: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", (question or "").lower())).strip()


def _build_lightweight_conversation_reply(
    question: str,
    conversation_context: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    q = _normalized_short_message(question)
    if not q or len(q) > 80:
        return None

    thanks = {
        "thanks",
        "thank you",
        "thankyou",
        "thx",
        "ty",
        "thanks a lot",
        "thank you so much",
        "thanks sir",
        "thanks mam",
        "thanks ma'am",
    }
    acknowledgements = {
        "ok",
        "okay",
        "okk",
        "got it",
        "understood",
        "clear",
        "done",
        "nice",
        "great",
        "cool",
        "perfect",
    }
    greetings = {
        "hi",
        "hello",
        "hey",
        "good morning",
        "good afternoon",
        "good evening",
    }
    tokens = set(q.split())

    if q in thanks or (
        len(tokens) <= 5 and ("thanks" in tokens or q.startswith("thank you"))
    ):
        return "You're welcome. Ask me the next doubt whenever you're ready."

    if q in acknowledgements or (
        tokens
        and len(tokens) <= 4
        and tokens.issubset(
            {
                "ok",
                "okay",
                "okk",
                "got",
                "it",
                "understood",
                "clear",
                "done",
                "nice",
                "great",
                "cool",
                "perfect",
            }
        )
    ):
        return "Good. Send the next question when you're ready, or ask me to test you on the last concept."

    if q in greetings:
        return "Hi, I am ready. Ask me any subject, topic, doubt, or pasted question."

    if q in {"yes", "yeah", "yep"}:
        recent_thread = str((conversation_context or {}).get("recent_thread", "")).lower()
        if any(keyword in recent_thread for keyword in ("practice question", "quick check", "test you", "try one")):
            return "Yes, let's do it. Send your answer to the last check question, or say 'ask me one' and I will give you a fresh practice question."
        return "Okay. Tell me what you want to do next."

    if q in {"no", "nope", "not now"}:
        return "No problem. Ask the next doubt whenever you want to continue."

    return None


ANSWER_FORMATS: Dict[str, Dict[str, Any]] = {
    "definition": {
        "label": "Definition Tutor",
        "description": "Best for short concept definitions and basic explanations.",
        "sections": [
            "Direct Answer",
            "Simple Explanation",
            "Important Points",
            "Examples",
            "Common Mistakes",
            "Exam-Ready Answer",
            "Quick Revision",
        ],
        "rules": [
            "Start with the exact definition in one or two lines.",
            "Then explain it in simple student-friendly language.",
            "Include examples and common mistakes only when they improve understanding.",
        ],
    },
    "numerical": {
        "label": "Numerical Solver",
        "description": "Best for calculations, formula-based questions, and step-by-step problem solving.",
        "sections": [
            "Given",
            "Formula",
            "Substitution",
            "Calculation",
            "Final Answer",
            "Check",
        ],
        "rules": [
            "Extract the given values first.",
            "Write the formula before substituting values.",
            "Show the calculation step by step and keep units visible.",
            "End with the final answer and a quick reasonableness check.",
        ],
    },
    "comparison": {
        "label": "Comparison Explainer",
        "description": "Best for difference-between, compare, vs, and distinguish questions.",
        "sections": [
            "Core Difference",
            "Point-by-Point Comparison",
            "Memory Trick",
            "Exam Line",
        ],
        "rules": [
            "Start with the most important difference.",
            "Use aligned bullet points instead of a dense paragraph.",
            "Add a memory trick if it helps the student remember.",
        ],
    },
    "quiz": {
        "label": "Quiz Coach",
        "description": "Best when the student wants practice questions or a quick self-test.",
        "sections": [
            "Practice Set",
            "Answer Key",
            "Explanation",
            "Next Drill",
        ],
        "rules": [
            "Create exam-style questions at the requested level.",
            "Keep options clear and avoid ambiguous distractors.",
            "Explain why the correct answer is correct.",
        ],
    },
    "revision": {
        "label": "Revision Sheet",
        "description": "Best for summary, key points, last-minute recall, and chapter revision.",
        "sections": [
            "High-Yield Summary",
            "Must-Remember Points",
            "Common Mistakes",
            "Quick Recall",
            "Next Practice",
        ],
        "rules": [
            "Compress the topic into high-yield revision notes.",
            "Prefer bullets and recall prompts over long teaching paragraphs.",
            "End with what to practice next.",
        ],
    },
    "exam_answer": {
        "label": "Exam Answer Writer",
        "description": "Best for marks-oriented written answers.",
        "sections": [
            "Exam-Ready Answer",
            "Keywords",
            "How To Score Full Marks",
        ],
        "rules": [
            "Write the answer in polished exam language.",
            "Include keywords that a teacher or examiner expects.",
            "Avoid unnecessary extra explanation unless it improves marks.",
        ],
    },
    "stuck": {
        "label": "Confusion Resolver",
        "description": "Best when the student says they are confused, stuck, or not understanding.",
        "sections": [
            "Start Here",
            "Why It Feels Confusing",
            "Step-by-Step Explanation",
            "Tiny Check",
            "Next Step",
        ],
        "rules": [
            "Reduce cognitive load and explain from the simplest point.",
            "Use one analogy or simple example if useful.",
            "End with a tiny check question or next action.",
        ],
    },
    "planning": {
        "label": "Study Planner",
        "description": "Best for schedules, roadmaps, daily plans, and what-to-study-next requests.",
        "sections": [
            "Today's Priority",
            "Study Blocks",
            "Practice Plan",
            "Revision Method",
            "Next Checkpoint",
        ],
        "rules": [
            "Make the plan specific, timed, and realistic.",
            "Use the student's weak areas and recent progress when available.",
            "End with the next measurable checkpoint.",
        ],
    },
    "concept": {
        "label": "Concept Builder",
        "description": "Best for open doubts and normal concept explanations.",
        "sections": [
            "Core Idea",
            "How It Works",
            "Example",
            "Common Trap",
            "Try This Next",
        ],
        "rules": [
            "Teach the concept in a natural order instead of forcing every possible section.",
            "Use examples only where they make the idea clearer.",
            "End with one useful follow-up action.",
        ],
    },
}


def _detect_answer_format(question: str, intent: str = "general", mode: str = "coach") -> Dict[str, Any]:
    q = (question or "").lower().strip()
    intent_lower = (intent or "").lower()
    mode_lower = (mode or "").lower()

    if intent_lower == "planning" or mode_lower in {"plan", "planner", "study_plan"}:
        format_id = "planning"
    elif intent_lower == "exam":
        format_id = "exam_answer"
    elif intent_lower == "revision":
        format_id = "revision"
    elif intent_lower == "practice":
        format_id = "quiz"
    elif any(keyword in q for keyword in ("don't understand", "do not understand", "confused", "stuck", "not getting", "explain simply")):
        format_id = "stuck"
    elif any(keyword in q for keyword in ("numerical", "calculate", "find the", "solve", "formula", "mole", "moles", "mass", "volume", "density")) and re.search(r"\d", q):
        format_id = "numerical"
    elif any(keyword in q for keyword in ("difference between", "differentiate", "compare", " vs ", "versus", "distinguish")):
        format_id = "comparison"
    elif any(keyword in q for keyword in ("quiz me", "mcq", "test me", "ask me questions", "practice questions")):
        format_id = "quiz"
    elif any(keyword in q for keyword in ("exam answer", "write answer", "marks", "board answer", "answer in exam")):
        format_id = "exam_answer"
    elif any(keyword in q for keyword in ("revise", "revision", "summary", "summarize", "key points", "quick notes")):
        format_id = "revision"
    elif _is_definition_question(question):
        format_id = "definition"
    else:
        format_id = "concept"

    selected = ANSWER_FORMATS[format_id]
    return {
        "id": format_id,
        "label": selected["label"],
        "description": selected["description"],
        "sections": list(selected["sections"]),
        "rules": list(selected["rules"]),
    }


def _build_answer_format_instruction(answer_format: Dict[str, Any]) -> str:
    sections = "\n".join(f"- {section}" for section in answer_format.get("sections", []))
    rules = "\n".join(f"- {rule}" for rule in answer_format.get("rules", []))

    return f"""
ADAPTIVE ANSWER FORMAT:
Selected format: {answer_format.get("label", "Concept Builder")}
When to use: {answer_format.get("description", "")}

Recommended sections:
{sections}

Format-specific rules:
{rules}

Do not force every section if the question is simple. Use only the sections that genuinely help the student.
For Study Page responses, prefer clean learning-block headings when they fit:
Direct Answer, Concept, Simple Explanation, Example, Common Mistake, Formula, Exam Tip, Quick Check, Next Step.
Use headings ending with a colon so the UI can render the answer as readable tutor blocks.
""".strip()


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _build_adaptive_context_from_request(request) -> Dict[str, Any]:
    student_state = _as_dict(getattr(request, "student_state", {}))
    adaptive_strategy = _as_dict(getattr(request, "adaptive_strategy", {}))
    learning_context = _as_dict(getattr(request, "learning_context", {}))
    mentor_directive = (getattr(request, "mentor_directive", "") or "").strip()
    grounding_context_prompt = (getattr(request, "grounding_context_prompt", "") or "").strip()

    if grounding_context_prompt:
        learning_context["grounding_context_prompt"] = grounding_context_prompt
    if getattr(request, "required_not_found_response", None):
        learning_context["required_not_found_response"] = getattr(request, "required_not_found_response")
    if getattr(request, "strict_grounding", False):
        learning_context["strict_grounding"] = True
    if getattr(request, "retrieval_required", False):
        learning_context["retrieval_required"] = True

    return {
        "mentor_directive": mentor_directive,
        "system_guardrail": (getattr(request, "system_guardrail", "") or "").strip(),
        "student_state": student_state,
        "adaptive_strategy": adaptive_strategy,
        "learning_context": learning_context,
        "has_signals": bool(mentor_directive or student_state or adaptive_strategy or learning_context),
    }


def _build_adaptive_teaching_instruction(adaptive_context: Optional[Dict[str, Any]]) -> str:
    context = adaptive_context or {}
    student_state = _as_dict(context.get("student_state"))
    adaptive_strategy = _as_dict(context.get("adaptive_strategy"))
    learning_context = _as_dict(context.get("learning_context"))
    mentor_directive = context.get("mentor_directive") or ""
    system_guardrail = context.get("system_guardrail") or ""

    if not context.get("has_signals"):
        return """
ADAPTIVE TEACHING ENGINE:
- Decide the best response shape from the student's question, not from a fixed template.
- Vary headings and examples naturally so repeated questions do not feel copy-pasted.
- Teach like a real tutor: diagnose, explain, check, and guide the next step.
""".strip()

    weak_signals = adaptive_strategy.get("weak_signals") or []
    recent_messages = learning_context.get("recent_messages") or []
    recent_text = ""
    if isinstance(recent_messages, list) and recent_messages:
        recent_text = "\n".join(
            f"- {item.get('role', 'message')}: {str(item.get('content', ''))[:220]}"
            for item in recent_messages[-4:]
            if isinstance(item, dict)
        )

    return f"""
ADAPTIVE TEACHING ENGINE:
You are not a static chatbot. You are a private teacher who adapts every response to the student.

Frontend mentor directive:
{mentor_directive or "No explicit directive supplied; infer from the question and memory."}

System grounding guardrail:
{system_guardrail or "Use the selected study material when it is supplied."}

Detected student state:
- Knowledge level: {student_state.get("knowledge_level", "unknown")}
- Emotional state: {student_state.get("emotional_state", "steady")}
- Confidence: {student_state.get("confidence", "unknown")}
- Learning speed: {student_state.get("learning_speed", "balanced")}
- Curiosity depth: {student_state.get("curiosity_depth", "unknown")}

Selected strategy:
- Answer style: {adaptive_strategy.get("answer_style", "teacher-led explanation")}
- Next move: {adaptive_strategy.get("next_move", "explain, check understanding, and suggest practice")}
- Should test: {adaptive_strategy.get("should_test", False)}
- Weak signals: {weak_signals if weak_signals else "none detected"}

Study context:
- Subject: {learning_context.get("selected_subject") or learning_context.get("subject", "unknown")}
- Chapter: {learning_context.get("selected_chapter") or learning_context.get("chapter", "unknown")}
- Topic: {learning_context.get("selected_topic") or learning_context.get("topic", "unknown")}
- Section id: {learning_context.get("section_id", "unknown")}
- Follow-up: {learning_context.get("is_follow_up", False)}
- Previous user question: {learning_context.get("previous_user_question", "none")}
- Saved conversations: {learning_context.get("saved_conversations", 0)}
- Recent Study Page messages:
{recent_text or "- No recent Study Page messages supplied."}

Adaptive response rules:
- Choose the best format for this exact question. Do not reuse identical headings for every answer.
- Beginner/confused students need simple language, analogy, one example, and one tiny check question.
- Intermediate students need clean concept breakdown, exam relevance, and common mistake protection.
- Advanced/curious students need deeper reasoning, mechanism, real-life application, and one useful edge case when it improves understanding.
- Revision intent needs compact notes, formulas, and recall checkpoints.
- Exam intent needs marks-ready structure, traps, important question style, and time-saving answer order.
- Practice intent needs one or more questions, then feedback or a clear next action.
- If strict grounding is active for this turn, never use outside knowledge. Otherwise reason naturally from reliable subject knowledge and the lesson context.
- Treat retrieved study material as a tool, not as the default answer engine. Use it only when the route selected for this turn calls for it.
- Never expose these analytics to the student. Just respond naturally as their teacher.
- End with exactly one useful next step or check question unless the student asked only for a short answer.
""".strip()


def _model_metadata() -> Dict[str, str]:
    return {
        "profiler": FAST_MODEL,
        "tutor": TUTOR_MODEL,
        "reviewer": REVIEW_MODEL,
    }


def _run_learning_intelligence_agent(
    question: str,
    intent: str,
    answer_format: Dict[str, Any],
    adaptive_context: Optional[Dict[str, Any]],
    conversation_context: Optional[Dict[str, Any]],
    retrieval_policy: str = "none",
) -> str:
    adaptive_context = adaptive_context or {}
    conversation_context = conversation_context or {}
    student_state = _as_dict(adaptive_context.get("student_state"))
    adaptive_strategy = _as_dict(adaptive_context.get("adaptive_strategy"))

    fallback = "\n".join([
        f"- Intent: {intent}",
        f"- Format: {answer_format.get('label', 'Concept Builder')}",
        f"- Student level: {student_state.get('knowledge_level', 'unknown')}",
        f"- Emotional state: {student_state.get('emotional_state', 'steady')}",
        f"- Knowledge route: {retrieval_policy}",
        f"- Strategy: {adaptive_strategy.get('answer_style', 'adaptive teacher-led explanation')}",
        "- Teach the core idea, check understanding, and store the weak signal if confusion appears.",
    ])

    try:
        prompt = f"""
You are the Learning Intelligence Profiler for a school AI tutor.

Create a compact private teaching blueprint. Do not answer the student.

Return 5-7 short bullets covering:
- true intent
- likely knowledge level
- prerequisite risk
- best teaching sequence
- whether to test
- whether to rely on reasoning, conversation memory, or retrieved study material
- memory/weak-signal to store
- ideal final response shape

Question:
{question}

Detected intent: {intent}
Selected answer format: {answer_format.get("label", "Concept Builder")}
Student state: {student_state}
Adaptive strategy: {adaptive_strategy}
Follow-up mode: {conversation_context.get("is_follow_up", False)}
Retrieval policy: {retrieval_policy}
Recent thread:
{conversation_context.get("recent_thread", "No previous lesson thread.")}
""".strip()

        blueprint = llm_router.complete(
            role="profiler",
            messages=[
                {"role": "system", "content": "You create concise private tutoring plans for another AI agent."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.12,
            max_tokens=280,
        )
        return blueprint or fallback
    except Exception as exc:
        logger.error("[LEARNING INTELLIGENCE] Groq API error: %s", exc)
        return fallback


# ─── KNOWLEDGE-GRAPH ANSWER BUILDER (no LLM) ────────────────────────────────

_QUESTION_STOPWORDS = {
    "define", "definition", "what", "what's", "explain", "meaning", "describe",
    "tell", "about", "the", "is", "are", "of", "for", "with", "give", "me",
    "please", "concept", "short", "brief", "detailed", "thank", "thanks", "you",
    "ok", "okay", "got", "understood", "clear",
}


def _extract_search_terms(question: str) -> List[str]:
    words = re.findall(r"[a-zA-Z0-9]+", question.lower())
    terms = [word for word in words if len(word) > 2 and word not in _QUESTION_STOPWORDS]
    return terms[:8]


def _find_relevant_concept(question: str) -> Optional[Dict[str, Any]]:
    terms = _extract_search_terms(question)
    if not terms or not knowledge_graph.concepts:
        return None

    scored: Dict[str, Dict[str, Any]] = {}
    for term in terms:
        for concept in knowledge_graph.search_by_keyword(term, limit=4):
            title = str(concept.get("title", "")).lower()
            definition = str(concept.get("definition", "")).lower()
            concept_id = str(concept.get("id") or concept.get("title") or id(concept))
            score = 2 if term in title else 1
            if term in definition:
                score += 1
            if concept_id not in scored:
                scored[concept_id] = {"concept": concept, "score": 0}
            scored[concept_id]["score"] += score

    if not scored:
        return None

    best = max(scored.values(), key=lambda item: item["score"])
    return best["concept"] if best["score"] >= 1 else None


def _can_answer_definition_locally(question: str) -> bool:
    return _find_relevant_concept(question) is not None


def _build_complete_answer_from_kg(question: str) -> str:
    concept = _find_relevant_concept(question)

    if not concept:
        return ""

    title = concept.get("title", "Selected concept")
    definition = concept.get("definition", "")
    key_points = concept.get("key_points", [])
    examples = concept.get("examples", [])
    common_mistakes = concept.get("common_mistakes", [])

    mistake_lines = []
    for item in common_mistakes[:3]:
        if isinstance(item, dict):
            mistake = item.get("mistake", "")
            correction = item.get("correction", "")
            if mistake and correction:
                mistake_lines.append(f"- {mistake} Correction: {correction}")
            elif mistake:
                mistake_lines.append(f"- {mistake}")
        elif item:
            mistake_lines.append(f"- {item}")

    simple_explanation = concept.get("core_explanation") or definition

    sections = [
        ("Direct Answer", definition),
        ("Simple Explanation", simple_explanation),
        ("Important Points", "\n".join(f"- {point}" for point in key_points[:5])),
        ("Examples", "\n".join(f"- {example}" for example in examples[:5])),
        (
            "Common Mistakes",
            "\n".join(mistake_lines) if mistake_lines else "",
        ),
        ("Exam-Ready Answer", f"{title} can be defined as: {definition}" if definition else ""),
        ("Quick Revision", f"Remember: {definition}" if definition else ""),
    ]

    return "\n\n".join(
        f"{heading}:\n{content.strip()}"
        for heading, content in sections
        if content and content.strip()
    )


# ─── LLM DRAFT PROMPTS ──────────────────────────────────────────────────────

def _build_study_prompt(
    coach: AICoachProfile,
    question: str,
    topic_snapshot: Dict[str, Any],
    answer_format: Dict[str, Any],
    conversation_context: Optional[Dict[str, Any]] = None,
    adaptive_context: Optional[Dict[str, Any]] = None,
    learning_blueprint: str = "",
    retrieved_material: Optional[Dict[str, Any]] = None,
    strict_grounding: bool = False,
    retrieval_policy: str = "none",
) -> str:
    graph_context = ""
    if retrieved_material and str(retrieved_material.get("context") or "").strip():
        graph_context = str(retrieved_material.get("context") or "").strip()

    adaptive_format = _build_answer_format_instruction(answer_format)
    adaptive_teaching = _build_adaptive_teaching_instruction(adaptive_context)
    conversation_context = conversation_context or {}
    follow_up_mode = "YES" if conversation_context.get("is_follow_up") else "NO"
    material_scope = (retrieved_material or {}).get("scope", {}) if isinstance(retrieved_material, dict) else {}
    not_found_message = _material_not_found(adaptive_context)
    if strict_grounding:
        material_policy = (
            "SOURCE-GROUNDED ANSWER POLICY:\n"
            "- The student explicitly requested an answer grounded in their study material.\n"
            "- Answer only from OFFICIAL RETRIEVED STUDY MATERIAL below.\n"
            "- Do not use general knowledge, model memory, or outside facts for factual claims.\n"
            f"- If the material does not contain the answer, reply exactly: {not_found_message}\n"
            "- If the student asks a follow-up, keep the previous topic from the recent lesson thread unless they clearly ask for a new topic."
        )
    elif graph_context:
        material_policy = (
            "REASONING-FIRST WITH MATERIAL ENRICHMENT:\n"
            "- First understand the student's actual question and reason through the clearest teaching route.\n"
            "- Use the retrieved study material below when it genuinely improves accuracy or curriculum alignment.\n"
            "- You may use reliable subject knowledge, logical reasoning, and conversation context beyond the retrieved excerpt.\n"
            "- Never imply that a claim came from the student's notes unless it is present in the retrieved material."
        )
    else:
        material_policy = (
            "REASONING-FIRST OPEN TUTOR POLICY:\n"
            "- Answer intelligently from reliable subject knowledge, logical reasoning, session memory, and conversation context.\n"
            "- Do not behave like a keyword-search bot and do not force a study-material refusal.\n"
            "- If a question depends on fresh or unavailable source material, say what is missing and ask for the relevant notes or source.\n"
            "- If the student asks a follow-up, resolve short references from the recent lesson thread before answering."
        )

    return f"""
You are {coach.coach_name}, a specialist subject tutor and personal study coach.

Write the answer like a patient expert teacher. The student should be able to revise directly from your response.

Base formatting rules:
- Use clear section headings ending with a colon, but choose headings naturally for the question.
- Put a blank line between sections.
- Use short paragraphs and dash bullets.
- Start with the most useful answer for this exact question.
- Avoid raw markdown tables, decorative symbols, and long unbroken paragraphs.
- If the question is too broad, answer the core concept first and then add what to study next.
- Avoid sounding like a fixed template. The format should feel intentionally chosen for the student's need.

Private tuition behavior:
- Treat the student as someone you are mentoring over time, not a one-off question.
- If Follow-up mode is YES, infer what short words like "this", "it", "why", "example", or "again" refer to from the recent lesson thread.
- Connect the new answer to the previous concept in one natural sentence when useful.
- Use one real-life example, then a step-by-step breakdown when the concept needs depth.
- End with one gentle checkpoint question or next action.
- The UI shows clickable help blocks, so do not print button labels as plain text unless they are part of the teaching answer.

{adaptive_format}

{adaptive_teaching}

{material_policy}

LEARNING INTELLIGENCE BLUEPRINT:
{learning_blueprint or "No separate blueprint was generated. Infer the best teaching route from the question and memory."}

CONVERSATION CONTEXT:
Follow-up mode: {follow_up_mode}
Last student question: {conversation_context.get("last_student_question", "None")}
Recent lesson thread:
{conversation_context.get("recent_thread", "No previous lesson thread.")}

LONG-TERM STUDENT GUIDANCE:
{conversation_context.get("long_term_summary", "No long-term summary yet.")}

COACH MEMORY:
{conversation_context.get("durable_memory", "No durable memory yet.")}

LAYERED LESSON MEMORY:
{conversation_context.get("lesson_memory_prompt", "No layered lesson memory yet.")}

SELECTED STUDY SCOPE:
Retrieval policy: {retrieval_policy}
Subject: {material_scope.get("subject") or "unknown"}
Chapter: {material_scope.get("chapter") or "unknown"}
Topic: {material_scope.get("topic") or "unknown"}
Section id: {material_scope.get("section_id") or "unknown"}

OFFICIAL RETRIEVED STUDY MATERIAL:
{graph_context if graph_context else "No study-material retrieval was needed for this turn."}

KNOWLEDGE BASE (use this data if it helps):
{graph_context if graph_context else "No retrieved excerpt. Use reasoning-first tutor behavior."}

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
    adaptive_context: Optional[Dict[str, Any]] = None,
    learning_blueprint: str = "",
) -> str:
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

    adaptive_teaching = _build_adaptive_teaching_instruction(adaptive_context)

    return f"""
You are {coach.coach_name}, a personal AI study coach.

PLANNING MODE – Create a clear study plan based on the analytics below.

Formatting rules:
- Use headings ending with a colon.
- Use dash bullets under each heading.
- Include Today's Priority, Study Blocks, Practice Plan, Revision Method, and Next Checkpoint.
- Keep each bullet specific and actionable.
- Adapt the plan length and detail to the student's confidence, speed, and current need.

{adaptive_teaching}

LEARNING INTELLIGENCE BLUEPRINT:
{learning_blueprint or "No separate blueprint was generated. Build the most efficient plan from analytics and student state."}

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


# ─── FORMATTER ───────────────────────────────────────────────────────────────

def _build_review_prompt(
    coach: AICoachProfile,
    question: str,
    draft: str,
    intent: str,
    answer_format: Dict[str, Any],
    adaptive_context: Optional[Dict[str, Any]] = None,
    learning_blueprint: str = "",
    strict_grounding: bool = False,
) -> str:
    adaptive_format = _build_answer_format_instruction(answer_format)
    adaptive_teaching = _build_adaptive_teaching_instruction(adaptive_context)

    evidence_policy = (
        "- Preserve strict platform-data grounding. Never add facts, examples, formulas, or claims that are not present in the draft or supplied study context."
        if strict_grounding
        else "- Verify the explanation using sound subject reasoning. Improve factual accuracy when needed, but do not invent source attributions or pretend an answer came from uploaded notes."
    )

    return f"""
You are the Subject Reviewer and Final Tutor for {coach.coach_name}.

Your job is to transform the draft into the final answer a specialist teacher would confidently give a student.

Review rules:
- Fix factual errors, vague wording, and missing reasoning.
{evidence_policy}
- Keep the answer easy to revise from directly.
- Preserve helpful references to the previous lesson if the student asked a follow-up.
- Use clear headings ending with a colon.
- Put a blank line between sections.
- Prefer short paragraphs and dash bullets.
- Preserve the selected answer structure unless the question clearly needs something simpler.
- Make the final response feel like a human teacher chose the best format for this specific student.
- Remove any repetitive, generic, or over-templated wording.
- Do not mention that you reviewed the answer.
- Do not include JSON, metadata, markdown tables, or decorative symbols.
- If the draft is already strong, polish it without changing the meaning.

Intent: {intent}

{adaptive_format}

{adaptive_teaching}

LEARNING INTELLIGENCE BLUEPRINT:
{learning_blueprint or "No separate blueprint was generated. Review against the student's likely need and selected format."}

Student question:
{question}

Draft answer:
{draft}

Return only the final polished answer.
""".strip()


def _review_and_polish_answer(
    coach: AICoachProfile,
    question: str,
    draft: str,
    intent: str,
    answer_format: Optional[Dict[str, Any]] = None,
    adaptive_context: Optional[Dict[str, Any]] = None,
    learning_blueprint: str = "",
    strict_grounding: bool = False,
) -> str:
    if not draft or len(draft.strip()) < 20:
        return draft

    try:
        selected_format = answer_format or _detect_answer_format(question, intent=intent)
        reviewed = llm_router.complete(
            role="reviewer",
            messages=[
                {
                    "role": "system",
                    "content": _build_review_prompt(
                        coach=coach,
                        question=question,
                        draft=draft,
                        intent=intent,
                        answer_format=selected_format,
                        adaptive_context=adaptive_context,
                        learning_blueprint=learning_blueprint,
                        strict_grounding=strict_grounding,
                    ),
                },
                {"role": "user", "content": "Polish the draft into the final student answer."},
            ],
            temperature=0.18,
            max_tokens=850,
        )
        return reviewed or draft
    except Exception as exc:
        logger.error("[COACH REVIEW] Groq API error: %s", exc)
        return draft


def _iter_text_chunks(text: str, chunk_size: int = 90) -> Generator[str, None, None]:
    buffer = ""
    for token in re.findall(r"\S+\s*", text or ""):
        buffer += token
        if len(buffer) >= chunk_size:
            yield buffer
            buffer = ""
    if buffer:
        yield buffer


def _apply_deterministic_format(text: str) -> str:
    text = text.replace("*", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    formatted = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            formatted.append("")
            continue

        is_heading = stripped.endswith(":") and len(stripped) < 60
        if is_heading:
            if formatted and formatted[-1] != "":
                formatted.append("")
            formatted.append(stripped)
        else:
            formatted.append(stripped)

    final_lines = []
    prev_blank = False
    for line in formatted:
        if line == "":
            if not prev_blank:
                final_lines.append(line)
                prev_blank = True
        else:
            final_lines.append(line)
            prev_blank = False

    while final_lines and final_lines[-1] == "":
        final_lines.pop()

    final = "\n".join(final_lines)
    return final if len(final) >= 20 else text


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
    """Run the same canonical turn engine used by SSE clients and return its final snapshot."""
    completed: Dict[str, Any] = {}
    fallback_answer = ""
    for frame in coach_agent_stream(request, db=db):
        event = parse_semantic_event(frame)
        if event.get("event") == "answer.completed":
            completed = event
            fallback_answer = str(event.get("answer") or "")
        elif not completed and str(frame or "").startswith("data: ") and not event:
            fallback_answer = str(frame or "")[6:].strip()

    if completed:
        snapshot = completed.get("snapshot") or {}
        return {
            "type": "coach",
            "answer": fallback_answer,
            "answer_blocks": completed.get("blocks") or [],
            **snapshot,
            "metadata": {
                "agent": "coach",
                "turn_id": completed.get("turn_id"),
                **(completed.get("metadata") or {}),
            },
        }
    return {
        "type": "coach",
        "answer": fallback_answer or "The tutor could not complete that response right now. Please try again.",
        "metadata": {"agent": "coach", "status": "incomplete_turn"},
    }

# ─── STREAMING GENERATOR (Base64‑Encoded Answer) ──────────────────────────

def _stage_event(stage: str, status: str, agent: str, title: str, detail: str, turn_id: str = "") -> str:
    payload = {
        "type": "agent_stage",
        "event": "turn.stage",
        "turn_id": turn_id,
        "stage": stage,
        "status": status,
        "agent": agent,
        "title": title,
        "detail": detail,
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _answer_delta_event(delta: str, turn_id: str = "") -> str:
    payload = {
        "type": "answer_delta",
        "event": "answer.delta",
        "turn_id": turn_id,
        "delta": delta,
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def coach_agent_stream(request, db=None) -> Generator[str, None, None]:
    if db is None:
        yield "data: Coach needs database access to personalize advice.\n\n"
        return

    user_id = getattr(request, "user_id", None) or getattr(request, "session_id", "anonymous")
    question = getattr(request, "question", "")
    session_id = getattr(request, "session_id", f"coach-{user_id}")
    turn_id = f"turn_{uuid.uuid4().hex[:12]}"
    intent = getattr(request, "intent", "study_advice")
    mode = getattr(request, "mode", "coach")
    adaptive_context = _build_adaptive_context_from_request(request)
    query_understanding = understand_query(question, declared_intent=intent)
    intent = query_understanding.intent
    answer_format = _detect_answer_format(question, intent=intent, mode=mode)

    yield semantic_event("turn.started", turn_id=turn_id, session_id=session_id)
    yield _stage_event(
        stage="received",
        status="active",
        agent="Study Desk",
        title="Question received",
        detail="Your doubt is in the tutor workspace. I am preparing the right learning route.",
        turn_id=turn_id,
    )
    time.sleep(0.12)
    yield _stage_event(
        stage="received",
        status="done",
        agent="Study Desk",
        title="Question received",
        detail="Question accepted and passed to the learning profiler.",
        turn_id=turn_id,
    )
    yield _stage_event(
        stage="understanding",
        status="active",
        agent="Learning Profiler",
        title=f"Understanding need: {answer_format['label']}",
        detail="Mapping intent, confidence, follow-up context, and likely weak points.",
        turn_id=turn_id,
    )

    coach = get_or_create_coach(db, user_id)
    progress = _build_progress_snapshot(db, user_id)
    topic_snapshot = _get_topic_snapshot(db, user_id)
    recent_sessions = _build_recent_session_snapshot(_get_recent_sessions(db, user_id))
    memories = _get_recent_memories(db, coach.coach_id)
    recent_interactions = _get_recent_interactions(db, coach.coach_id, session_id=session_id)
    query_understanding = resolve_hybrid_query(
        question,
        declared_intent=intent,
        has_history=bool(recent_interactions),
        classifier=lambda messages: llm_router.complete(
            role="profiler",
            messages=messages,
            temperature=0.05,
            max_tokens=240,
        ),
    )
    intent = query_understanding.intent
    answer_format = _detect_answer_format(question, intent=intent, mode=mode)
    coach_plan = build_coach_plan(query_understanding)
    conversation_context = _build_conversation_context(
        question=question,
        coach=coach,
        interactions=recent_interactions,
        memories=memories,
    )
    conversation_context = _merge_frontend_context(conversation_context, adaptive_context)
    lesson_memory = build_layered_lesson_memory(
        coach=coach,
        memories=memories,
        interactions=recent_interactions,
        current_question=question,
    )
    conversation_context["lesson_memory"] = lesson_memory
    conversation_context["lesson_memory_prompt"] = format_layered_lesson_memory(lesson_memory)
    retrieval_policy = _retrieval_policy(
        request,
        query_understanding,
        adaptive_context,
        previous_policy=_previous_retrieval_policy(recent_interactions),
    )
    query_understanding = _apply_effective_retrieval_policy(query_understanding, retrieval_policy)
    coach_plan = build_coach_plan(query_understanding)
    strict_grounding = retrieval_policy == "required"
    retrieved_material = (
        _retrieve_selected_material(request, adaptive_context, conversation_context)
        if retrieval_policy != "none"
        else None
    )
    material_is_supported = (
        _material_supports_question(retrieved_material, adaptive_context, conversation_context)
        if retrieved_material is not None
        else True
    )
    assistance_blocks = _build_assistance_blocks(question, answer_format)
    lightweight_reply = _build_lightweight_conversation_reply(question, conversation_context)
    compact_context = build_compact_context(
        query=query_understanding,
        retrieval=retrieved_material or {},
        recent_messages=interaction_messages(recent_interactions),
        memory_summary=build_memory_summary(memories, recent_interactions),
        student_state=adaptive_context["student_state"],
        lesson_memory=lesson_memory,
    )
    if conversation_context.get("is_follow_up") and not lightweight_reply:
        yield _stage_event(
            stage="understanding",
            status="active",
            agent="Memory Tutor",
            title="Connecting follow-up",
            detail="Using the recent lesson thread so the answer continues naturally.",
            turn_id=turn_id,
        )

    learning_blueprint = (
        "- Conversational input detected. Reply naturally and do not continue the previous lesson unless the student asks."
        if lightweight_reply
        else "- No matching ingested study source was found. Return the required material-not-found response without adding outside facts."
        if strict_grounding and not material_is_supported
        else _run_learning_intelligence_agent(
            question=question,
            intent=intent,
            answer_format=answer_format,
            adaptive_context=adaptive_context,
            conversation_context=conversation_context,
            retrieval_policy=retrieval_policy,
        )
    )

    try:
        analytics_snapshot = get_user_analytics(db, user_id)
    except Exception:
        analytics_snapshot = {}

    recommendation = _make_rule_based_recommendation(
        progress=progress,
        weak_topics=topic_snapshot["weak_topics"],
        recent_sessions=recent_sessions,
    )

    yield _stage_event(
        stage="understanding",
        status="done",
        agent="Learning Profiler",
        title="Intent understood",
        detail=(
            "Conversational reply selected."
            if lightweight_reply
            else f"Student need, answer format, and {retrieval_policy} retrieval route selected."
        ),
        turn_id=turn_id,
    )
    yield _stage_event(
        stage="drafting",
        status="active",
        agent="Conversation Router" if lightweight_reply else "Adaptive Mentor",
        title="Preparing quick reply" if lightweight_reply else "Drafting answer",
        detail=(
            "This is a short conversation turn, so I am not reopening the previous lesson."
            if lightweight_reply
            else (
                "Building the tutor response from verified study material."
                if strict_grounding
                else "Reasoning through the clearest tutor response using lesson context and the selected strategy."
            )
        ),
        turn_id=turn_id,
    )

    # ── Answer source ──────────────────────────────────────────────────
    should_review_answer = True
    if lightweight_reply:
        final_answer = lightweight_reply
        should_review_answer = False
    elif strict_grounding and not material_is_supported:
        final_answer = _material_not_found(adaptive_context)
        should_review_answer = False
    else:
        if intent == "planning":
            draft_prompt = _build_planning_prompt(
                coach=coach,
                progress=progress,
                topic_snapshot=topic_snapshot,
                recent_sessions=recent_sessions,
                memories=memories,
                analytics_snapshot=analytics_snapshot,
                recommendation=recommendation,
                adaptive_context=adaptive_context,
                learning_blueprint=learning_blueprint,
            )
        else:
            draft_prompt = _build_study_prompt(
                coach=coach,
                question=question,
                topic_snapshot=topic_snapshot,
                answer_format=answer_format,
                conversation_context=conversation_context,
                adaptive_context=adaptive_context,
                learning_blueprint=learning_blueprint,
                retrieved_material=retrieved_material,
                strict_grounding=strict_grounding,
                retrieval_policy=retrieval_policy,
            )

        draft = ""
        try:
            draft = llm_router.complete(
                role="tutor",
                messages=[
                    {"role": "system", "content": draft_prompt},
                    {"role": "user", "content": question},
                ],
                temperature=0.35,
                max_tokens=700,
            )
        except Exception as exc:
            logger.error("[COACH DRAFT] Groq error: %s", exc)
            fallback = _material_not_found(adaptive_context) if strict_grounding else (
                recommendation if intent == "planning" else "I'm having trouble explaining that right now."
            )
            draft = fallback

        enriched = (
            _build_complete_answer_from_kg(question)
            if not strict_grounding and len(draft.strip()) < 50 and _can_answer_definition_locally(question)
            else draft
        )
        final_answer = _apply_deterministic_format(enriched)
        if len(final_answer) < 20:
            final_answer = draft

    yield _stage_event(
        stage="drafting",
        status="done",
        agent="Conversation Router" if lightweight_reply else "Adaptive Mentor",
        title="Reply ready" if lightweight_reply else "Draft complete",
        detail=(
            "Conversational response is ready."
            if lightweight_reply
            else "Core explanation is ready for strategy review."
        ),
        turn_id=turn_id,
    )
    if not lightweight_reply:
        yield _stage_event(
            stage="reviewing",
            status="active",
            agent="Strategy Tutor",
            title="Refining explanation",
            detail=f"Checking clarity, accuracy, and adaptive {answer_format['label']} structure.",
            turn_id=turn_id,
        )
        time.sleep(0.12)
        yield _stage_event(
            stage="reviewing",
            status="done",
            agent="Strategy Tutor",
            title="Refinement complete",
            detail="Review strategy selected for accuracy, depth, and student understanding.",
            turn_id=turn_id,
        )
    yield _stage_event(
        stage="formatting",
        status="active",
        agent="Response Designer",
        title="Formatting response",
        detail="Streaming the final tutor answer into clean learning blocks.",
        turn_id=turn_id,
    )

    streamed_answer = ""
    if should_review_answer:
        try:
            review_stream = llm_router.stream(
                role="reviewer",
                messages=[
                    {
                        "role": "system",
                        "content": _build_review_prompt(
                            coach=coach,
                            question=question,
                            draft=final_answer,
                            intent=intent,
                            answer_format=answer_format,
                            adaptive_context=adaptive_context,
                            learning_blueprint=learning_blueprint,
                            strict_grounding=strict_grounding,
                        ),
                    },
                    {"role": "user", "content": "Polish the draft into the final student answer."},
                ],
                temperature=0.18,
                max_tokens=850,
            )
            for chunk in review_stream:
                delta = getattr(chunk.choices[0].delta, "content", None) or ""
                if not delta:
                    continue
                streamed_answer += delta
                yield _answer_delta_event(delta, turn_id=turn_id)
        except Exception as exc:
            logger.error("[COACH STREAM REVIEW] Groq API error: %s", exc)

    if streamed_answer.strip():
        final_answer = _apply_deterministic_format(streamed_answer)
    else:
        final_answer = _apply_deterministic_format(final_answer)
        for delta in _iter_text_chunks(final_answer):
            streamed_answer += delta
            yield _answer_delta_event(delta, turn_id=turn_id)

    quality_report = score_coach_answer(
        question=question,
        answer=final_answer,
        retrieved_context=str((retrieved_material or {}).get("context") or ""),
        strict_grounding=strict_grounding,
    )
    if (
        strict_grounding
        and quality_report.hallucination_risk >= 0.65
    ):
        final_answer = _material_not_found(adaptive_context)
        quality_report = score_coach_answer(
            question=question,
            answer=final_answer,
            retrieved_context=str((retrieved_material or {}).get("context") or ""),
            strict_grounding=True,
        )
    observability = coach_observability.snapshot(
        query=query_understanding.to_dict(),
        retrieval={
            "policy": retrieval_policy,
            "section_id": str((retrieved_material or {}).get("section_id") or ""),
            "source": str((retrieved_material or {}).get("source") or ""),
            "paragraphs_found": int((retrieved_material or {}).get("paragraphs_found") or 0),
            "supported": material_is_supported,
        },
        plan=coach_plan.to_dict(),
        quality=quality_report.to_dict(),
    )
    answer_blocks = build_adaptive_answer_blocks(final_answer)

    yield _stage_event(
        stage="formatting",
        status="done",
        agent="Response Designer",
        title="Format ready",
        detail="The response is structured for easy reading and quick revision.",
        turn_id=turn_id,
    )
    yield _stage_event(
        stage="delivering",
        status="active",
        agent="Tutor Voice",
        title="Delivering answer",
        detail="Finalizing the response in your study chat.",
        turn_id=turn_id,
    )
    time.sleep(0.25)
    yield _stage_event(
        stage="delivering",
        status="done",
        agent="Tutor Voice",
        title="Delivered",
        detail="Final answer delivered.",
        turn_id=turn_id,
    )
    time.sleep(0.2)

    # ── Base64‑encode the entire answer to protect newlines ─────────────
    yield semantic_event(
        "answer.completed",
        turn_id=turn_id,
        answer=final_answer,
        blocks=answer_blocks,
        snapshot={
            "coach_id": coach.coach_id,
            "coach_name": coach.coach_name,
            "next_best_action": recommendation,
            "daily_strategy": recommendation,
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
        },
        metadata={
            "intent": intent,
            "answer_format": answer_format,
            "query": query_understanding.to_dict(),
            "retrieval_policy": retrieval_policy,
            "quality": quality_report.to_dict(),
            "assistance_blocks": assistance_blocks,
            "adaptive_teacher": adaptive_context.get("has_signals", False),
            "learning_blueprint": learning_blueprint,
            "coach_plan": coach_plan.to_dict(),
            "observability": observability,
        },
    )

    # Keep the encoded answer for older clients while the semantic contract rolls out.
    encoded = base64.b64encode(final_answer.encode("utf-8")).decode("ascii")
    yield f"data: {encoded}\n\n"
    yield "data: [DONE]\n\n"

    # Persist
    coach.daily_strategy = recommendation
    coach.next_best_action = recommendation
    coach.last_interaction_at = datetime.utcnow()
    coach.updated_at = datetime.utcnow()
    _update_learning_journey_summary(
        coach=coach,
        question=question,
        answer_format=answer_format,
        topic_snapshot=topic_snapshot,
        is_follow_up=bool(conversation_context.get("is_follow_up")),
    )

    _persist_interaction(
        db=db,
        coach=coach,
        role="user",
        message=question,
        intent=intent,
        mode=mode,
        metadata={
            "session_id": session_id,
            "is_follow_up": bool(conversation_context.get("is_follow_up")),
            "last_student_question": conversation_context.get("last_student_question"),
            "student_state": adaptive_context["student_state"],
            "adaptive_strategy": adaptive_context["adaptive_strategy"],
            "learning_context": adaptive_context["learning_context"],
            "learning_blueprint": learning_blueprint,
            "coach_plan": coach_plan.to_dict(),
            "retrieval_policy": retrieval_policy,
        },
    )
    _persist_interaction(
        db=db,
        coach=coach,
        role="assistant",
        message=final_answer,
        intent=intent,
        mode=mode,
        metadata={
            "session_id": session_id,
            "progress": progress,
            "weak_topics": topic_snapshot["weak_topics"][:3],
            "answer_format": answer_format,
            "is_follow_up": bool(conversation_context.get("is_follow_up")),
            "assistance_blocks": assistance_blocks,
            "student_state": adaptive_context["student_state"],
            "adaptive_strategy": adaptive_context["adaptive_strategy"],
            "learning_context": adaptive_context["learning_context"],
            "mentor_directive_used": bool(adaptive_context.get("mentor_directive")),
            "learning_blueprint": learning_blueprint,
            "coach_plan": coach_plan.to_dict(),
            "retrieval_policy": retrieval_policy,
            "compact_context": compact_context,
            "lesson_memory": lesson_memory,
            "answer_blocks": answer_blocks,
            "quality": quality_report.to_dict(),
            "observability": observability,
        },
        quality_score=quality_report.score,
    )

    coach_observability.emit(
        session_id,
        "metric",
        message="Streaming coach answer scored and persisted.",
        query_intent=intent,
        tools=coach_plan.tools,
        retrieved_chunks=int((retrieved_material or {}).get("paragraphs_found") or 0),
        quality_score=quality_report.score,
        quality_passed=quality_report.passed,
    )

    event_bus.emit(
        "coach",
        "task_complete",
        {
            "status": "success",
            "message": f"Coach reviewed and delivered by {coach.coach_name}",
            "latency_ms": 0,
            "quality_score": quality_report.score,
            "quality_passed": quality_report.passed,
        },
        session_id=session_id,
    )
