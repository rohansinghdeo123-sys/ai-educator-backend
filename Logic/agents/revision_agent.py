# Logic/agents/revision_agent.py

"""
REVISION AGENT — Smart Summary, Deep Explain, Key Points (with Admin Telemetry)

This agent handles all revision-related tasks with the agentic cycle:
1. RETRIEVE: Search knowledge base AND knowledge graph for the topic
2. ENRICH: Combine markdown and structured concept data
3. GENERATE: Create mode-specific content using specialised prompts
4. FORMAT: Apply chemistry formatting
5. VALIDATE: Check output quality

Every step emits events to the Agent Event Bus for real-time admin monitoring.
"""

import time
import logging
from prompts.agent_prompts import (
    SUMMARY_AGENT_PROMPT,
    EXPLAIN_AGENT_PROMPT,
    KEYPOINTS_AGENT_PROMPT,
)
from Logic.coach.model_gateway import model_gateway
from Logic.tools.knowledge_search import search_knowledge_base
from Logic.tools.chemistry_formatter import format_chemistry_output
from Logic.tools.answer_evaluator import evaluate_answer_quality
from Logic.agent_event_bus import event_bus
from Logic.knowledge_graph import knowledge_graph   # <-- NEW

logger = logging.getLogger("ai_educator.agents.revision")

# Revision content is template-driven, so the fast tier is enough. The shared
# gateway owns timeouts, retries, provider fallback, and cost records;
# GROQ_FAST_MODEL still selects the primary model.
MODEL_NAME = model_gateway.model_for("tutor", complexity="fast")

# Mode-to-prompt mapping
REVISION_PROMPTS = {
    "summary": {"prompt": SUMMARY_AGENT_PROMPT, "temp": 0.25, "max_tokens": 400},
    "explain": {"prompt": EXPLAIN_AGENT_PROMPT, "temp": 0.3, "max_tokens": 600},
    "key":     {"prompt": KEYPOINTS_AGENT_PROMPT, "temp": 0.2, "max_tokens": 450},
    "keypoints": {"prompt": KEYPOINTS_AGENT_PROMPT, "temp": 0.2, "max_tokens": 450},
}


def revision_agent(request, revision_type: str = "summary") -> dict:
    """
    Agentic Revision: Retrieve → Enrich → Generate → Format → Validate
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

    # ===== STEP 1: RETRIEVE from markdown =====
    event_bus.emit("revision", "tool_call", {
        "step": "retrieve_markdown",
        "step_num": 1,
        "total_steps": 5,
        "tool": "knowledge_search",
        "message": f"Searching markdown knowledge base for {section_id}...",
    })

    search_result = search_knowledge_base(
        section_id=section_id,
        question=question,
        max_paragraphs=8,
        max_chars=4000,
    )

    context = str(search_result.get("context") or "").strip()
    if search_result.get("error") or not context:
        not_found = (
            getattr(request, "required_not_found_response", "")
            or "I could not find this in your study material. Please upload or select the correct chapter/data."
        )
        event_bus.emit("revision", "error", {
            "step": "retrieve_markdown",
            "message": f"No revision content for section '{section_id}': {search_result.get('error') or 'empty context'}",
        }, severity="error")
        return {
            "type": "revision",
            "answer": not_found,
            "metadata": {"agent": "revision", "revision_type": revision_type, "status": "material_not_found"},
        }

    event_bus.emit("revision", "step", {
        "step": "retrieve_markdown_complete",
        "step_num": 1,
        "total_steps": 5,
        "message": f"Retrieved {search_result['paragraphs_found']} paragraphs from markdown",
        "paragraphs": search_result["paragraphs_found"],
    })

    # ===== STEP 2: ENRICH with knowledge graph =====
    event_bus.emit("revision", "tool_call", {
        "step": "retrieve_graph",
        "step_num": 2,
        "total_steps": 5,
        "tool": "knowledge_graph",
        "message": f"Searching knowledge graph for {section_id}...",
    })

    concept_data = ""
    concepts_found = []
    if knowledge_graph.concepts:
        exact = knowledge_graph.get_concept(section_id)
        if exact:
            concepts_found = [exact]

        if concepts_found:
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

    event_bus.emit("revision", "step", {
        "step": "retrieve_graph_complete",
        "step_num": 2,
        "total_steps": 5,
        "message": f"Found {len(concepts_found)} concept(s) in knowledge graph",
        "concepts_found": len(concepts_found),
    })

    # Combine markdown context with structured data
    enriched_context = context
    if concept_data:
        enriched_context += "\n\n--- STRUCTURED KNOWLEDGE (from official curriculum) ---\n" + concept_data

    # Student-friendly instruction prefix
    student_instructions = (
        "You are a friendly, patient AI revision assistant for school students.\n"
        "Use simple language and everyday analogies to explain concepts.\n"
        "When you see 'Common Mistakes' in the context, mention them so the student can avoid them.\n"
        "Highlight the most important points clearly.\n"
        "Encourage the student and make them feel confident.\n"
        "If the context contains both markdown text and structured knowledge, combine them to create the best revision material."
    )

    # ===== STEP 3: GENERATE =====
    event_bus.emit("revision", "tool_call", {
        "step": "generate",
        "step_num": 3,
        "total_steps": 5,
        "tool": "groq_llm",
        "message": f"Generating {revision_type} via {MODEL_NAME}...",
        "model": MODEL_NAME,
        "temperature": config["temp"],
    })

    # Insert the enriched context into the prompt template
    system_prompt = student_instructions + "\n\n" + config["prompt"].format(context=enriched_context)
    messages = [{"role": "user", "content": system_prompt}]

    try:
        raw_answer = model_gateway.complete(
            role="tutor",
            messages=messages,
            complexity="fast",
            agent_name="tutor_model",
            task=f"Generate {revision_type} revision content from section context.",
            student_visible=True,
            safety_tier="final_answer",
            temperature=config["temp"],
            max_tokens=config["max_tokens"],
        ).strip()
    except Exception as e:
        logger.error(f"[REVISION] LLM error: {e}")
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

    # ===== STEP 4: FORMAT =====
    event_bus.emit("revision", "tool_call", {
        "step": "format",
        "step_num": 4,
        "total_steps": 5,
        "tool": "chemistry_formatter",
        "message": "Applying chemistry formatting...",
    })

    formatted_answer = format_chemistry_output(raw_answer)

    # ===== STEP 5: VALIDATE =====
    event_bus.emit("revision", "tool_call", {
        "step": "validate",
        "step_num": 5,
        "total_steps": 5,
        "tool": "answer_evaluator",
        "message": "Validating answer quality...",
    })

    quality = evaluate_answer_quality(
        question=question,
        answer=formatted_answer,
        mode=revision_type,
        context=context,
    )

    if not quality["passed"] and not concepts_found:
        event_bus.emit("revision", "step", {
            "step": "retry",
            "message": f"Quality failed (score={quality['score']}). Retrying with more markdown context...",
        }, severity="warning")

        retry_search = search_knowledge_base(
            section_id=section_id,
            question=f"complete overview of {section_id}",
            max_paragraphs=10,
            max_chars=5000,
        )
        if retry_search["context"]:
            retry_context = retry_search["context"]
            if concept_data:
                retry_context += "\n\n--- STRUCTURED KNOWLEDGE ---\n" + concept_data
            retry_prompt = student_instructions + "\n\n" + config["prompt"].format(context=retry_context)
            try:
                retry_answer = model_gateway.complete(
                    role="tutor",
                    messages=[{"role": "user", "content": retry_prompt}],
                    complexity="fast",
                    agent_name="tutor_model",
                    task=f"Retry low-quality {revision_type} revision content with more context.",
                    student_visible=True,
                    safety_tier="final_answer",
                    temperature=config["temp"],
                    max_tokens=config["max_tokens"] + 100,
                )
                formatted_answer = format_chemistry_output(retry_answer.strip())
                quality = evaluate_answer_quality(
                    question=question,
                    answer=formatted_answer,
                    mode=revision_type,
                )
            except Exception as exc:
                # Keep the original answer, but record that the quality retry failed.
                logger.warning("Revision quality retry failed; keeping first answer | section_id=%s error=%s", section_id, exc)

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
            "concepts_from_graph": len(concepts_found),
            "latency_ms": latency_ms,
        },
    }
