# Logic/agents/tutor_agent.py

"""
TUTOR AGENT — The ASK AI Brain (with Admin Telemetry)

This is a TRUE AGENT that follows the Think → Act → Observe → Respond cycle:
1. THINK: Analyze the student's question
2. ACT: Search the knowledge base (markdown) AND the knowledge graph (JSON concepts)
3. OBSERVE: Evaluate the retrieved context quality, enrich with structured concept data
4. RESPOND: Generate a structured answer using the enriched context
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
from Logic.knowledge_graph import knowledge_graph   # <-- NEW import

logger = logging.getLogger("ai_educator.agents.tutor")

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL_NAME = os.getenv("GROQ_TUTOR_MODEL", "openai/gpt-oss-120b")

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
        "total_steps": 7,
        "message": "Analyzing question intent and complexity...",
    }, session_id=session_id)

    # ===== STEP 2: ACT — Search knowledge base (markdown) =====
    event_bus.emit("tutor", "tool_call", {
        "step": "retrieve_markdown",
        "step_num": 2,
        "total_steps": 7,
        "tool": "knowledge_search",
        "message": f"Searching markdown knowledge base for: {question[:40]}...",
    }, session_id=session_id)

    search_result = search_knowledge_base(
        section_id=section_id,
        question=question,
        max_paragraphs=5,
        max_chars=3000,
    )

    if search_result.get("error"):
        event_bus.emit("tutor", "error", {
            "step": "retrieve_markdown",
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
        "step": "retrieve_markdown_complete",
        "step_num": 2,
        "total_steps": 7,
        "message": f"Retrieved {search_result['paragraphs_found']} paragraphs from markdown",
        "paragraphs": search_result["paragraphs_found"],
        "keywords": search_result["keywords_used"][:5],
    }, session_id=session_id)

    # ===== STEP 2b: ACT — Search knowledge graph (JSON concepts) =====
    event_bus.emit("tutor", "tool_call", {
        "step": "retrieve_graph",
        "step_num": 3,
        "total_steps": 7,
        "tool": "knowledge_graph",
        "message": f"Searching knowledge graph for concepts related to: {question[:40]}...",
    }, session_id=session_id)

    # Attempt to find relevant concepts by keyword or section_id
    concept_data = ""
    concepts_found = []
    if knowledge_graph.concepts:
        # First try exact match via section_id (which may be a concept_id)
        exact = knowledge_graph.get_concept(section_id)
        if exact:
            concepts_found = [exact]
        else:
            # Otherwise search by keywords extracted from the question
            # Simple keyword extraction (we could use a more sophisticated method later)
            keywords = [w.strip().lower() for w in question.replace("?", "").replace(".", "").split() if len(w) > 3]
            # Use the first meaningful keyword to search
            search_kw = keywords[0] if keywords else section_id
            concepts_found = knowledge_graph.search_by_keyword(search_kw, limit=3)

        if concepts_found:
            # Build a structured text block from the concepts
            blocks = []
            for c in concepts_found:
                block = f"--- CONCEPT: {c['title']} (ID: {c['concept_id']}) ---\n"
                block += f"Definition: {c.get('definition', '')}\n"
                block += f"Explanation: {c.get('core_explanation', '')}\n"
                if c.get("key_points"):
                    block += "Key Points:\n  " + "\n  ".join(c["key_points"]) + "\n"
                if c.get("formulas"):
                    block += "Formulas:\n  " + "\n  ".join(c["formulas"]) + "\n"
                if c.get("examples"):
                    block += "Examples:\n  " + "\n  ".join(c["examples"]) + "\n"
                if c.get("common_mistakes"):
                    mistakes = "\n  ".join([f"Mistake: {m['mistake']} -> Correction: {m['correction']}" for m in c["common_mistakes"]])
                    block += "Common Mistakes:\n  " + mistakes + "\n"
                blocks.append(block)
            concept_data = "\n\n".join(blocks)
            logger.info(f"[TUTOR] Found {len(concepts_found)} concept(s) in knowledge graph: {[c['concept_id'] for c in concepts_found]}")
        else:
            logger.info("[TUTOR] No matching concepts found in knowledge graph")

    event_bus.emit("tutor", "step", {
        "step": "retrieve_graph_complete",
        "step_num": 3,
        "total_steps": 7,
        "message": f"Knowledge graph search yielded {len(concepts_found)} concept(s)",
        "concepts_found": len(concepts_found),
        "concept_ids": [c["concept_id"] for c in concepts_found] if concepts_found else [],
    }, session_id=session_id)

    # ===== STEP 3: OBSERVE — Build context-aware prompt with enriched content =====
    event_bus.emit("tutor", "step", {
        "step": "build_prompt",
        "step_num": 4,
        "total_steps": 7,
        "message": "Building context-aware prompt with markdown, graph concepts, and memory...",
    }, session_id=session_id)

    # Combine markdown context with structured concept data
    enriched_context = context
    if concept_data:
        enriched_context += "\n\n--- STRUCTURED KNOWLEDGE (from official curriculum) ---\n" + concept_data

    # Student-friendly instructions
    student_instructions = (
        "You are a friendly, patient AI tutor for school students.\n"
        "Use simple language and everyday analogies to explain concepts.\n"
        "When you see 'Common Mistakes' in the context, mention them to the student so they can avoid those errors.\n"
        "Break down complex ideas step-by-step.\n"
        "Encourage the student and make them feel confident.\n"
        "If the context contains both markdown text and structured knowledge, combine them to give the best answer."
    )

    # Build the system prompt
    system_prompt = f"{student_instructions}\n\n{enriched_context}\n\n{basics}"

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
        "step_num": 5,
        "total_steps": 7,
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
        "step_num": 6,
        "total_steps": 7,
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

    # If quality is too low, retry with more context (only if we haven't already used graph data)
    if not quality["passed"] and search_result["paragraphs_found"] < 3 and not concepts_found:
        event_bus.emit("tutor", "step", {
            "step": "retry",
            "step_num": 6,
            "total_steps": 7,
            "message": f"Quality check failed (score={quality['score']}). Retrying with more markdown context...",
        }, session_id=session_id, severity="warning")

        retry_result = search_knowledge_base(
            section_id=section_id,
            question=question,
            max_paragraphs=8,
            max_chars=4000,
        )
        if retry_result["context"]:
            retry_context = retry_result["context"]
            if concept_data:
                retry_context += "\n\n--- STRUCTURED KNOWLEDGE ---\n" + concept_data
            retry_prompt = f"{student_instructions}\n\n{retry_context}\n\n{basics}"
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

    # ===== STEP 7: UPDATE MEMORY =====
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
        "concepts_from_graph": len(concepts_found),
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
            "concepts_from_graph": len(concepts_found),
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
