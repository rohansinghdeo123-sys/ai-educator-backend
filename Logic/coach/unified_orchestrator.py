"""Intent-based tool routing for the single student-facing Coach."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List


_CHEMISTRY_TERMS = (
    "reaction", "formula", "molecule", "compound", "alkane", "alkene", "alkyne",
    "acid", "base", "mole", "carbon", "chemical", "oxidation", "reduction",
)
_DIAGRAM_TERMS = ("draw", "diagram", "graph", "visual", "label", "structure", "geometry", "mechanism")
_HOMEWORK_TERMS = ("solve", "homework", "assignment", "how do i", "help me answer", "show steps")
_SAFETY_TERMS = ("self harm", "suicide", "hurt myself", "make a bomb", "cheat in exam")


def _append(values: List[str], name: str) -> None:
    if name not in values:
        values.append(name)


def build_orchestration_plan(
    query: Any,
    question: str,
    attachments: Iterable[Dict[str, Any]] = (),
    mastery_profile: Dict[str, Any] | None = None,
    direct_answer: bool = False,
    socratic_mode: bool = True,
) -> Dict[str, Any]:
    normalized = (question or "").lower()
    attachments = list(attachments or [])
    attachment_types = {str(item.get("mime_type") or item.get("type") or "").lower() for item in attachments if isinstance(item, dict)}
    tools: List[str] = []
    statuses: List[str] = []

    if getattr(query, "retrieval_policy", "none") != "none":
        _append(tools, "knowledge_search")
        statuses.append("Checking your notes")
    if any(mime.startswith("image/") for mime in attachment_types):
        _append(tools, "attachment_reader")
        statuses.append("Reading your image")
    elif attachment_types:
        _append(tools, "attachment_reader")
        statuses.append("Reading your material")
    if getattr(query, "intent", "") == "numerical" or (
        re.search(r"\d", normalized) and any(term in normalized for term in ("calculate", "solve", "find", "evaluate"))
    ):
        _append(tools, "calculator")
        statuses.append("Checking the calculation")
    if any(term in normalized for term in _CHEMISTRY_TERMS) or re.search(r"\b(?:[A-Z][a-z]?\d*){2,}\b", question or ""):
        _append(tools, "formula_checker")
        statuses.append("Checking the formula")
    if getattr(query, "intent", "") in {"practice", "exam"}:
        _append(tools, "practice_generator")
        statuses.append("Preparing practice")
    if any(term in normalized for term in _DIAGRAM_TERMS) or any(mime.startswith("image/") for mime in attachment_types):
        _append(tools, "diagram_helper")
    if (mastery_profile or {}).get("route") not in {None, "", "baseline"}:
        _append(tools, "mastery_engine")
    if any(term in normalized for term in _SAFETY_TERMS):
        _append(tools, "safety_review")

    socratic = bool(
        socratic_mode
        and not direct_answer
        and getattr(query, "intent", "") not in {"conversation", "definition", "revision", "exam", "practice"}
        and any(term in normalized for term in _HOMEWORK_TERMS)
    )
    if socratic:
        _append(tools, "socratic_tutor")
        statuses.append("Preparing a helpful hint")
    if not getattr(query, "is_conversational", False):
        _append(tools, "answer_verifier")
        statuses.append("Verifying the answer")

    return {
        "tools": tools,
        "statuses": list(dict.fromkeys(statuses)),
        "socratic": socratic,
        "direct_answer": bool(direct_answer),
        "student_status": statuses[0] if statuses else "Preparing your answer",
    }


def format_orchestration_prompt(
    plan: Dict[str, Any],
    tool_outputs: Dict[str, Any],
    mastery_profile: Dict[str, Any],
) -> str:
    lines = ["UNIFIED COACH ROUTE:"]
    if mastery_profile:
        lines.append(f"- Mastery adjustment: {mastery_profile.get('directive', '')}")
    if plan.get("socratic"):
        lines.append("- Socratic guide active: give one useful hint and ask exactly one guiding question. Do not reveal the final answer yet. Mention that the student can choose Direct answer.")
    elif plan.get("direct_answer"):
        lines.append("- The student requested the direct answer. Give the complete explanation now.")
    for name, output in tool_outputs.items():
        if output:
            lines.append(f"- {name}: {output}")
    lines.append("- Keep internal tools private. Return one calm Coach response, never an agent transcript.")
    return "\n".join(lines)
