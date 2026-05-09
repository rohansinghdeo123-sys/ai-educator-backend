# Logic/agents/tutor_agent.py

"""
TUTOR AGENT — The ASK AI Brain (with Admin Telemetry)

This is a TRUE AGENT that follows the Think → Act → Observe → Respond cycle:
1. THINK: Analyze the student's question
2. ACT: Search the knowledge base for relevant content
3. OBSERVE: Evaluate the retrieved context quality
4. RESPOND: Generate a structured answer using the context
5. VALIDATE: Check answer quality before returning

Every step emits events to the Agent Event Bus for real-time admin monitoring.
"""

import os
import time
import logging
from groq import Groq
from prompts.agent_prompts import TUTOR_AGENT_PROMPT
from Logic.tools.knowledge_search import search_knowledge_base
from Logic.tools.chemistry_formatter import format_chemistry_output
from Logic.tools.answer_evaluator import evaluate_answer_quality
from Logic.agent_event_bus import event_bus

logger = logging.getLogger("ai_educator.agents.tutor")

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL_NAME = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

# In-memory session store
_sessions: dict[str, list] = {}


def tutor_agent(request) -> dict:
    """
    Agentic Tutor: Think → Retrieve → Generate → Validate → Respond
    """
    start_time = time.time()

    question = request.question
    section_id = request.section_id
    session_id = request.session_id
    difficulty = getattr(request, "difficulty", "medium")

    logger.info(f"[TUTOR] Processing: '{question}' | Section: {section_id}")

    # ===== EMIT: Task Start =====
    event_bus.emit("tutor", "task_start", {
        "task": f"Answer: {question[:60]}...",
        "section": section_id,
        "session": session_id,
        "message": f"Processing question about {section_id}",
    }, session_id=session_id)

    # ===== STEP 1: THINK — Analyze the question =====
    event_bus.emit("tutor", "step", {
        "step": "think",
        "step_num": 1,
        "total_steps": 6,
        "message": "Analyzing question intent and complexity...",
    }, session_id=session_id)

    # ===== STEP 2: ACT — Search knowledge base =====
    event_bus.emit("tutor", "tool_call", {
        "step": "retrieve",
        "step_num": 2,
        "total_steps": 6,
        "tool": "knowledge_search",
        "message": f"Searching knowledge base for: {question[:40]}...",
    }, session_id=session_id)

    search_result = search_knowledge_base(
        section_id=section_id,
        question=question,
        max_paragraphs=5,
        max_chars=3000,
    )

    if search_result.get("error"):
        event_bus.emit("tutor", "error", {
            "step": "retrieve",
            "message": f"Knowledge base error: {search_result['error']}",
        }, session_id=session_id, severity="error")
        return {
            "type": "tutor",
            "answer": f"Knowledge base error: {search_result['error']}",
            "metadata": {"agent": "tutor", "step": "retrieval_failed"},
        }

    context = search_result["context"]
    basics = search_result.get("basics_context", "")

    event_bus.emit("tutor", "step", {
        "step": "retrieve_complete",
        "step_num": 2,
        "total_steps": 6,
        "message": f"Retrieved {search_result['paragraphs_found']} paragraphs, keywords: {search_result['keywords_used'][:5]}",
        "paragraphs": search_result["paragraphs_found"],
        "keywords": search_result["keywords_used"][:5],
    }, session_id=session_id)

    # ===== STEP 3: OBSERVE — Build context-aware prompt =====
    event_bus.emit("tutor", "step", {
        "step": "build_prompt",
        "step_num": 3,
        "total_steps": 6,
        "message": "Building context-aware prompt with memory...",
    }, session_id=session_id)

    system_prompt = TUTOR_AGENT_PROMPT.format(context=context, basics=basics)

    if session_id not in _sessions:
        _sessions[session_id] = []

    memory = _sessions[session_id]

    messages = [
        {"role": "system", "content": system_prompt},
    ] + memory + [
        {"role": "user", "content": question},
    ]

    # ===== STEP 4: RESPOND — Generate answer =====
    event_bus.emit("tutor", "tool_call", {
        "step": "generate",
        "step_num": 4,
        "total_steps": 6,
        "tool": "groq_llm",
        "message": f"Generating answer via {MODEL_NAME}...",
        "model": MODEL_NAME,
        "temperature": 0.25,
    }, session_id=session_id)

    try:
        response = groq_client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            temperature=0.25,
            max_tokens=500,
        )
        raw_answer = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"[TUTOR] Groq API error: {e}")
        event_bus.emit("tutor", "error", {
            "step": "generate",
            "message": f"LLM API error: {str(e)}",
            "error": str(e),
        }, session_id=session_id, severity="error")

        event_bus.emit("tutor", "task_complete", {
            "status": "failed",
            "message": "Generation failed",
            "latency_ms": round((time.time() - start_time) * 1000),
        }, session_id=session_id)

        return {
            "type": "tutor",
            "answer": "AI service encountered an error. Please try again.",
            "metadata": {"agent": "tutor", "step": "generation_failed", "error": str(e)},
        }

    # ===== STEP 5: VALIDATE — Format and evaluate =====
    event_bus.emit("tutor", "tool_call", {
        "step": "validate",
        "step_num": 5,
        "total_steps": 6,
        "tool": "chemistry_formatter",
        "message": "Applying chemistry formatting and quality validation...",
    }, session_id=session_id)

    formatted_answer = format_chemistry_output(raw_answer)

    quality = evaluate_answer_quality(
        question=question,
        answer=formatted_answer,
        mode="ask",
        context=context,
    )

    # If quality is too low, retry with more context
    if not quality["passed"] and search_result["paragraphs_found"] < 3:
        event_bus.emit("tutor", "step", {
            "step": "retry",
            "step_num": 5,
            "total_steps": 6,
            "message": f"Quality check failed (score={quality['score']}). Retrying with more context...",
        }, session_id=session_id, severity="warning")

        retry_result = search_knowledge_base(
            section_id=section_id,
            question=question,
            max_paragraphs=8,
            max_chars=4000,
        )
        if retry_result["context"]:
            retry_prompt = TUTOR_AGENT_PROMPT.format(
                context=retry_result["context"],
                basics=basics,
            )
            retry_messages = [
                {"role": "system", "content": retry_prompt},
                {"role": "user", "content": question},
            ]
            try:
                retry_response = groq_client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=retry_messages,
                    temperature=0.2,
                    max_tokens=600,
                )
                formatted_answer = format_chemistry_output(
                    retry_response.choices[0].message.content.strip()
                )
                quality = evaluate_answer_quality(
                    question=question,
                    answer=formatted_answer,
                    mode="ask",
                )
                event_bus.emit("tutor", "step", {
                    "step": "retry_complete",
                    "message": f"Retry quality: score={quality['score']}",
                }, session_id=session_id)
            except Exception:
                pass

    # ===== STEP 6: UPDATE MEMORY =====
    memory.append({"role": "user", "content": question})
    memory.append({"role": "assistant", "content": formatted_answer})
    if len(memory) > 10:
        _sessions[session_id] = memory[-10:]

    latency_ms = round((time.time() - start_time) * 1000)

    # ===== EMIT: Task Complete =====
    event_bus.emit("tutor", "task_complete", {
        "status": "success",
        "message": f"Answer delivered. Quality: {quality['score']}",
        "latency_ms": latency_ms,
        "quality_score": quality["score"],
        "quality_passed": quality["passed"],
        "paragraphs_retrieved": search_result["paragraphs_found"],
    }, session_id=session_id)

    logger.info(f"[TUTOR] Complete. Quality: {quality['score']} | Latency: {latency_ms}ms")

    return {
        "type": "tutor",
        "answer": formatted_answer,
        "metadata": {
            "agent": "tutor",
            "quality_score": quality["score"],
            "quality_passed": quality["passed"],
            "paragraphs_retrieved": search_result["paragraphs_found"],
            "keywords_used": search_result["keywords_used"][:5],
            "latency_ms": latency_ms,
        },
    }


def reset_tutor_session(session_id: str):
    """Clear tutor memory for a session."""
    _sessions.pop(session_id, None)
    event_bus.emit("tutor", "state_change", {
        "state": "idle",
        "message": f"Session {session_id} memory cleared",
    }, session_id=session_id)
    logger.info(f"[TUTOR] Session {session_id} cleared.")