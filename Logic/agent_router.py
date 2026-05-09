# Logic/agent_router.py

"""
AGENT ORCHESTRATOR - Routes student requests to the correct specialist agent.

The personal AI Coach is a first-class agent. It is used when mode="coach"
or when a student asks for advice, motivation, strategy, weak-area guidance,
or what to study next.
"""

import logging
import os
import time

from groq import Groq

from Logic.agent_event_bus import event_bus
from Logic.agents.exam_agent import exam_agent
from Logic.agents.planner_agent import planner_agent
from Logic.agents.revision_agent import revision_agent
from Logic.agents.tutor_agent import tutor_agent

try:
    from Logic.agents.coach_agent import coach_agent
except Exception:
    coach_agent = None

logger = logging.getLogger("ai_educator.orchestrator")

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL_NAME = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")


# =====================================================
# FAST KEYWORD CLASSIFIER
# =====================================================
INTENT_KEYWORDS = {
    "coach": [
        "coach",
        "motivate",
        "motivation",
        "advice",
        "guide me",
        "what should i study",
        "where am i weak",
        "weak area",
        "focus on",
        "next best",
        "strategy",
        "exam strategy",
        "study advice",
        "daily plan",
        "improve",
        "how can i score",
    ],
    "exam": [
        "mcq",
        "quiz",
        "test",
        "exam",
        "generate questions",
        "practice questions",
        "probable",
    ],
    "revision": [
        "summary",
        "summarize",
        "revise",
        "revision",
        "key points",
        "explain",
        "keypoints",
        "deep explain",
    ],
    "plan": [
        "plan",
        "study plan",
        "roadmap",
        "schedule",
    ],
    "greeting": [
        "hello",
        "hi",
        "hey",
        "good morning",
        "good evening",
        "thanks",
        "thank you",
    ],
}


def _fast_classify(message: str) -> str:
    msg_lower = message.lower().strip()

    for intent, keywords in INTENT_KEYWORDS.items():
        if any(keyword in msg_lower for keyword in keywords):
            return intent

    return "doubt"


def _detect_revision_type(message: str) -> str:
    msg_lower = message.lower()

    if any(keyword in msg_lower for keyword in ["summary", "summarize", "smart summary"]):
        return "summary"

    if any(keyword in msg_lower for keyword in ["explain", "deep explain", "explanation"]):
        return "explain"

    if any(keyword in msg_lower for keyword in ["key point", "keypoint", "key_point", "bullet"]):
        return "key"

    return "summary"


def _detect_exam_type(message: str) -> str:
    msg_lower = message.lower()

    if any(keyword in msg_lower for keyword in ["probable", "3 marks", "5 marks", "subjective"]):
        return "probable"

    return "mcq"


def classify_intent(message: str) -> str:
    return _fast_classify(message)


def _coach_unavailable_response() -> dict:
    return {
        "type": "coach",
        "answer": "Coach agent is not available yet. Please check Logic/agents/coach_agent.py.",
        "metadata": {"agent": "coach", "status": "unavailable"},
    }


# =====================================================
# MAIN ROUTER
# =====================================================
def route_to_agent(request, db=None) -> dict:
    start_time = time.time()

    question = request.question
    mode = getattr(request, "mode", None)
    target_agent = "orchestrator"

    event_bus.emit(
        "orchestrator",
        "task_start",
        {
            "task": f"Route: {question[:60]}...",
            "message": f"Received request. Mode: {mode or 'auto-detect'}",
            "mode": mode or "auto-detect",
        },
    )

    if mode:
        normalized_mode = str(mode).lower().strip()

        logger.info("[ORCHESTRATOR] Explicit mode: %s", normalized_mode)

        event_bus.emit(
            "orchestrator",
            "step",
            {
                "step": "classify",
                "message": f"Explicit mode from frontend: {normalized_mode}",
                "intent": normalized_mode,
                "method": "explicit",
            },
        )

        if normalized_mode == "coach":
            target_agent = "coach"
            event_bus.emit(
                "orchestrator",
                "step",
                {
                    "step": "route",
                    "message": "Routing to Personal AI Coach",
                    "target_agent": target_agent,
                },
            )
            result = coach_agent(request, db=db) if coach_agent else _coach_unavailable_response()

        elif normalized_mode in ("summary", "explain", "key", "keypoints"):
            target_agent = "revision"
            event_bus.emit(
                "orchestrator",
                "step",
                {
                    "step": "route",
                    "message": f"Routing to Revision Agent ({normalized_mode})",
                    "target_agent": target_agent,
                },
            )
            result = revision_agent(request, revision_type=normalized_mode)

        elif normalized_mode == "exam":
            target_agent = "exam"
            event_bus.emit(
                "orchestrator",
                "step",
                {
                    "step": "route",
                    "message": "Routing to Exam Agent (MCQ)",
                    "target_agent": target_agent,
                },
            )
            result = exam_agent(request, exam_type="mcq")

        elif normalized_mode == "probable":
            target_agent = "exam"
            event_bus.emit(
                "orchestrator",
                "step",
                {
                    "step": "route",
                    "message": "Routing to Exam Agent (Probable)",
                    "target_agent": target_agent,
                },
            )
            result = exam_agent(request, exam_type="probable")

        elif normalized_mode == "plan":
            target_agent = "planner"
            event_bus.emit(
                "orchestrator",
                "step",
                {
                    "step": "route",
                    "message": "Routing to Planner Agent",
                    "target_agent": target_agent,
                },
            )
            result = planner_agent(request, db)

        else:
            target_agent = "tutor"
            event_bus.emit(
                "orchestrator",
                "step",
                {
                    "step": "route",
                    "message": "Routing to Tutor Agent (default)",
                    "target_agent": target_agent,
                },
            )
            result = tutor_agent(request)

    else:
        intent = classify_intent(question)
        logger.info("[ORCHESTRATOR] Classified intent: %s for: '%s...'", intent, question[:50])

        event_bus.emit(
            "orchestrator",
            "step",
            {
                "step": "classify",
                "message": f"Auto-classified intent: {intent}",
                "intent": intent,
                "method": "keyword",
            },
        )

        if intent == "coach":
            target_agent = "coach"
            event_bus.emit(
                "orchestrator",
                "step",
                {
                    "step": "route",
                    "message": "Routing to Personal AI Coach",
                    "target_agent": target_agent,
                },
            )
            result = coach_agent(request, db=db) if coach_agent else _coach_unavailable_response()

        elif intent == "exam":
            exam_type = _detect_exam_type(question)
            target_agent = "exam"
            event_bus.emit(
                "orchestrator",
                "step",
                {
                    "step": "route",
                    "message": f"Routing to Exam Agent ({exam_type})",
                    "target_agent": target_agent,
                },
            )
            result = exam_agent(request, exam_type=exam_type)

        elif intent == "revision":
            revision_type = _detect_revision_type(question)
            target_agent = "revision"
            event_bus.emit(
                "orchestrator",
                "step",
                {
                    "step": "route",
                    "message": f"Routing to Revision Agent ({revision_type})",
                    "target_agent": target_agent,
                },
            )
            result = revision_agent(request, revision_type=revision_type)

        elif intent == "plan":
            target_agent = "planner"
            event_bus.emit(
                "orchestrator",
                "step",
                {
                    "step": "route",
                    "message": "Routing to Planner Agent",
                    "target_agent": target_agent,
                },
            )
            result = planner_agent(request, db)

        elif intent == "greeting":
            target_agent = "orchestrator"
            event_bus.emit(
                "orchestrator",
                "step",
                {
                    "step": "route",
                    "message": "Handling greeting directly",
                    "target_agent": "orchestrator",
                },
            )
            result = {
                "type": "greeting",
                "answer": "Ready. Ask any chemistry question, request a study strategy, or select a mode from the left panel.",
                "metadata": {"agent": "orchestrator"},
            }

        else:
            target_agent = "tutor"
            event_bus.emit(
                "orchestrator",
                "step",
                {
                    "step": "route",
                    "message": "Routing to Tutor Agent (doubt)",
                    "target_agent": target_agent,
                },
            )
            result = tutor_agent(request)

    latency_ms = round((time.time() - start_time) * 1000)

    event_bus.emit(
        "orchestrator",
        "task_complete",
        {
            "status": "success",
            "message": f"Routed to {target_agent}. Total latency: {latency_ms}ms",
            "target_agent": target_agent,
            "latency_ms": latency_ms,
        },
    )

    return result
