"""Sampled LLM-as-judge scoring for delivered Study Lab answers.

The deterministic quality scorer measures term overlap and readability; it
cannot catch a confidently wrong mechanism explanation. This module scores a
configurable sample of delivered turns with a model so answer quality becomes
a tracked number instead of a guess.

Disabled by default: set COACH_JUDGE_SAMPLE_RATE (0.0-1.0, e.g. 0.05 for 5%)
to enable. Judging runs after the final SSE frame, so sampled turns add no
student-visible latency, and failures never reach the turn.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
from typing import Any, Dict, Optional

logger = logging.getLogger("ai_educator.coach.llm_judge")

_RUBRIC = """
You are a strict evaluator of AI tutor answers for school students.
Score the answer on three axes from 0 to 10:
- factual_accuracy: are the subject-matter claims correct?
- pedagogy: is it clear, well-sequenced, and at the right level for a school student?
- grounding_fidelity: when study material is supplied, does the answer stay consistent
  with it and avoid inventing source attributions? (Score 10 if no material was required.)

Return ONLY a JSON object:
{"factual_accuracy": 0-10, "pedagogy": 0-10, "grounding_fidelity": 0-10,
 "verdict": "pass" or "fail", "main_issue": "one short sentence, or empty string"}
""".strip()


def judge_sample_rate() -> float:
    try:
        rate = float(os.getenv("COACH_JUDGE_SAMPLE_RATE", "0"))
    except ValueError:
        return 0.0
    return max(0.0, min(1.0, rate))


def should_judge_turn() -> bool:
    rate = judge_sample_rate()
    return rate > 0 and random.random() < rate


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    cleaned = str(text or "").strip()
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _score(value: Any) -> float:
    try:
        return max(0.0, min(10.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def judge_coach_answer(
    model_gateway: Any,
    *,
    question: str,
    answer: str,
    retrieved_context: str = "",
    intent: str = "concept",
) -> Optional[Dict[str, Any]]:
    """Score one delivered answer. Returns None on any failure."""
    material = (retrieved_context or "").strip()
    user_payload = "\n\n".join(
        part
        for part in (
            f"Student intent: {intent}",
            f"Student question:\n{question[:600]}",
            f"Supplied study material (may be empty):\n{material[:2500] or 'None'}",
            f"Tutor answer to evaluate:\n{answer[:3500]}",
        )
        if part
    )
    try:
        raw = model_gateway.complete(
            role="profiler",
            messages=[
                {"role": "system", "content": _RUBRIC},
                {"role": "user", "content": user_payload},
            ],
            agent_name="quality_verifier",
            task="LLM-judge sample of a delivered coach answer.",
            student_visible=False,
            safety_tier="evaluation",
            temperature=0.0,
            max_tokens=220,
        )
    except Exception as exc:
        logger.warning("LLM judge call failed: %s", exc)
        return None

    payload = _extract_json(raw)
    if payload is None:
        logger.warning("LLM judge returned unparseable output")
        return None

    factual = _score(payload.get("factual_accuracy"))
    pedagogy = _score(payload.get("pedagogy"))
    grounding = _score(payload.get("grounding_fidelity"))
    verdict = str(payload.get("verdict") or "").strip().lower()
    return {
        "factual_accuracy": factual,
        "pedagogy": pedagogy,
        "grounding_fidelity": grounding,
        "overall": round((factual + pedagogy + grounding) / 3, 2),
        "verdict": verdict if verdict in {"pass", "fail"} else ("pass" if factual >= 6 else "fail"),
        "main_issue": str(payload.get("main_issue") or "")[:240],
        "sample_rate": judge_sample_rate(),
    }
