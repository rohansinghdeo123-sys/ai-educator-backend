"""Small deterministic specialist tools selected by the unified Coach."""

from __future__ import annotations

import ast
import operator
import re
from typing import Any, Dict

from .quality_scorer import score_coach_answer


_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}
_FORMULA_PATTERN = re.compile(r"\b(?:[A-Z][a-z]?\d*){2,}(?:[+-]\d*|[+-])?\b")


def _evaluate(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPERATORS:
        return _OPERATORS[type(node.op)](_evaluate(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        return _OPERATORS[type(node.op)](_evaluate(node.left), _evaluate(node.right))
    raise ValueError("Unsupported calculation")


def calculator(question: str) -> Dict[str, Any]:
    """Evaluate only an explicit bounded arithmetic expression."""
    candidates = re.findall(r"(?<![A-Za-z])[-+*/().\d\s]{3,}(?![A-Za-z])", question or "")
    for raw in sorted(candidates, key=len, reverse=True):
        expression = raw.strip().replace("^", "**")
        if not expression or not re.search(r"\d", expression) or not re.search(r"[-+*/]", expression):
            continue
        try:
            parsed = ast.parse(expression, mode="eval")
            result = _evaluate(parsed)
        except Exception:
            continue
        return {"used": True, "expression": raw.strip(), "result": round(result, 8)}
    return {"used": False, "reason": "No explicit arithmetic expression found."}


def formula_checker(question: str, answer: str = "") -> Dict[str, Any]:
    formulas = []
    for value in _FORMULA_PATTERN.findall(f"{question} {answer}"):
        if value not in formulas:
            formulas.append(value)
    return {
        "used": bool(formulas),
        "formulas": formulas[:12],
        "directive": "Keep chemical symbols, subscripts, charges, and equations exact." if formulas else "",
    }


def practice_generator(question: str, topic: str = "", difficulty: str = "adaptive") -> Dict[str, Any]:
    return {
        "used": True,
        "topic": topic or "current concept",
        "difficulty": difficulty,
        "directive": (
            "Create a short practice step only when it helps. Ask one question at a time, "
            "wait for the student's answer, then evaluate it."
        ),
    }


def diagram_helper(question: str, topic: str = "") -> Dict[str, Any]:
    normalized = (question or "").lower()
    diagram_type = "reaction flow" if any(term in normalized for term in ("reaction", "mechanism")) else "concept sketch"
    return {
        "used": True,
        "diagram_type": diagram_type,
        "topic": topic or "current concept",
        "directive": "Describe a simple labelled visual only if it makes the explanation easier to understand.",
    }


def answer_verifier(
    question: str,
    answer: str,
    retrieved_context: str = "",
    strict_grounding: bool = False,
    intent: str = "concept",
    answer_format: str = "concept",
    calculator_result: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    quality = score_coach_answer(
        question=question,
        answer=answer,
        retrieved_context=retrieved_context,
        strict_grounding=strict_grounding,
        intent=intent,
        answer_format=answer_format,
    ).to_dict()
    issues = list(quality.get("issues") or [])
    calculation = calculator_result or {}
    if calculation.get("used") and str(calculation.get("result")) not in answer:
        issues.append("Check the numerical result against the calculator output.")
    return {
        "used": True,
        "passed": bool(quality.get("passed")) and not issues,
        "quality": quality,
        "issues": issues,
    }


def safety_review(question: str) -> Dict[str, Any]:
    normalized = (question or "").lower()
    high_risk = any(term in normalized for term in ("self harm", "suicide", "hurt myself", "make a bomb", "cheat in exam"))
    return {
        "used": high_risk,
        "high_risk": high_risk,
        "directive": "Respond safely, avoid enabling harm or cheating, and guide the student toward appropriate help." if high_risk else "",
    }
