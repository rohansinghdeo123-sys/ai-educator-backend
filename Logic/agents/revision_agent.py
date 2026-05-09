# Logic/agents/revision_agent.py

"""
REVISION AGENT — Smart Summary, Deep Explain, Key Points (with Admin Telemetry)

This agent handles all revision-related tasks with the agentic cycle:
1. RETRIEVE: Search knowledge base for the topic
2. GENERATE: Create mode-specific content using specialized prompts
3. FORMAT: Apply chemistry formatting
4. VALIDATE: Check output quality

Every step emits events to the Agent Event Bus for real-time admin monitoring.
"""

import os
import time
import logging
from groq import Groq
from prompts.agent_prompts import (
    SUMMARY_AGENT_PROMPT,
    EXPLAIN_AGENT_PROMPT,
    KEYPOINTS_AGENT_PROMPT,
)
from Logic.tools.knowledge_search import search_knowledge_base
from Logic.tools.chemistry_formatter import format_chemistry_output
from Logic.tools.answer_evaluator import evaluate_answer_quality
from Logic.agent_event_bus import event_bus

logger = logging.getLogger("ai_educator.agents.revision")

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL_NAME = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

# Mode-to-prompt mapping
REVISION_PROMPTS = {
    "summary": {"prompt": SUMMARY_AGENT_PROMPT, "temp": 0.25, "max_tokens": 400},
    "explain": {"prompt": EXPLAIN_AGENT_PROMPT, "temp": 0.3, "max_tokens": 600},
    "key":     {"prompt": KEYPOINTS_AGENT_PROMPT, "temp": 0.2, "max_tokens": 450},
    "keypoints": {"prompt": KEYPOINTS_AGENT_PROMPT, "temp": 0.2, "max_tokens": 450},
}


def revision_agent(request, revision_type: str = "summary") -> dict:
    """
    Agentic Revision: Retrieve → Generate → Format → Validate
    """
    start_time = time.time()

    section_id = request.section_id
    question = request.question

    if revision_type not in REVISION_PROMPTS:
        revision_type = "summary"

    config = REVISION_PROMPTS[revision_type]

    logger.info(f"[REVISION] Type: {revision_type} | Section: {section_id}")

    # ===== EMIT: Task Start =====
    event_bus.emit("revision", "task_start", {
        "task": f"{revision_type.upper()} for {section_id}",
        "section": section_id,
        "revision_type": revision_type,
        "message": f"Starting {revision_type} generation for {section_id}",
    })

    # ===== STEP 1: RETRIEVE =====
    event_bus.emit("revision", "tool_call", {
        "step": "retrieve",
        "step_num": 1,
        "total_steps": 4,
        "tool": "knowledge_search",
        "message": f"Searching knowledge base for {section_id}...",
    })

    search_result = search_knowledge_base(
        section_id=section_id,
        question=question,
        max_paragraphs=8,
        max_chars=4000,
    )

    if search_result.get("error"):
        event_bus.emit("revision", "error", {
            "step": "retrieve",
            "message": f"Knowledge base error: {search_result['error']}",
        }, severity="error")
        return {
            "type": "revision",
            "answer": f"Knowledge base error: {search_result['error']}",
            "metadata": {"agent": "revision", "revision_type": revision_type},
        }

    context = search_result["context"]

    event_bus.emit("revision", "step", {
        "step": "retrieve_complete",
        "step_num": 1,
        "total_steps": 4,
        "message": f"Retrieved {search_result['paragraphs_found']} paragraphs",
        "paragraphs": search_result["paragraphs_found"],
    })

    # ===== STEP 2: GENERATE =====
    event_bus.emit("revision", "tool_call", {
        "step": "generate",
        "step_num": 2,
        "total_steps": 4,
        "tool": "groq_llm",
        "message": f"Generating {revision_type} via {MODEL_NAME}...",
        "model": MODEL_NAME,
        "temperature": config["temp"],
    })

    system_prompt = config["prompt"].format(context=context)
    messages = [{"role": "user", "content": system_prompt}]

    try:
        response = groq_client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            temperature=config["temp"],
            max_tokens=config["max_tokens"],
        )
        raw_answer = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"[REVISION] Groq API error: {e}")
        event_bus.emit("revision", "error", {
            "step": "generate",
            "message": f"LLM API error: {str(e)}",
        }, severity="error")

        event_bus.emit("revision", "task_complete", {
            "status": "failed",
            "message": "Generation failed",
            "latency_ms": round((time.time() - start_time) * 1000),
        })

        return {
            "type": "revision",
            "answer": "AI service encountered an error. Please try again.",
            "metadata": {"agent": "revision", "error": str(e)},
        }

    # ===== STEP 3: FORMAT =====
    event_bus.emit("revision", "tool_call", {
        "step": "format",
        "step_num": 3,
        "total_steps": 4,
        "tool": "chemistry_formatter",
        "message": "Applying chemistry formatting...",
    })

    formatted_answer = format_chemistry_output(raw_answer)

    # ===== STEP 4: VALIDATE =====
    event_bus.emit("revision", "tool_call", {
        "step": "validate",
        "step_num": 4,
        "total_steps": 4,
        "tool": "answer_evaluator",
        "message": "Validating answer quality...",
    })

    quality = evaluate_answer_quality(
        question=question,
        answer=formatted_answer,
        mode=revision_type,
        context=context,
    )

    if not quality["passed"]:
        event_bus.emit("revision", "step", {
            "step": "retry",
            "message": f"Quality failed (score={quality['score']}). Retrying with more context...",
        }, severity="warning")

        retry_search = search_knowledge_base(
            section_id=section_id,
            question=f"complete overview of {section_id}",
            max_paragraphs=10,
            max_chars=5000,
        )
        if retry_search["context"]:
            retry_prompt = config["prompt"].format(context=retry_search["context"])
            try:
                retry_response = groq_client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=[{"role": "user", "content": retry_prompt}],
                    temperature=config["temp"],
                    max_tokens=config["max_tokens"] + 100,
                )
                formatted_answer = format_chemistry_output(
                    retry_response.choices[0].message.content.strip()
                )
                quality = evaluate_answer_quality(
                    question=question,
                    answer=formatted_answer,
                    mode=revision_type,
                )
            except Exception:
                pass

    latency_ms = round((time.time() - start_time) * 1000)

    # ===== EMIT: Task Complete =====
    event_bus.emit("revision", "task_complete", {
        "status": "success",
        "message": f"{revision_type.upper()} delivered. Quality: {quality['score']}",
        "latency_ms": latency_ms,
        "quality_score": quality["score"],
        "quality_passed": quality["passed"],
    })

    logger.info(f"[REVISION] Complete. Quality: {quality['score']} | Latency: {latency_ms}ms")

    return {
        "type": "revision",
        "revision_type": revision_type,
        "answer": formatted_answer,
        "metadata": {
            "agent": "revision",
            "revision_type": revision_type,
            "quality_score": quality["score"],
            "quality_passed": quality["passed"],
            "paragraphs_retrieved": search_result["paragraphs_found"],
            "latency_ms": latency_ms,
        },
    }