"""Small deterministic specialist tools selected by the unified Coach."""

from __future__ import annotations

import ast
import operator
import re
from typing import Any, Dict

from .multimodal_learning import infer_diagram_specs, parse_formula_signals
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


def formula_checker(question: str, answer: str = "", multimodal: Dict[str, Any] | None = None) -> Dict[str, Any]:
    multimodal = multimodal if isinstance(multimodal, dict) else {}
    extracted = []
    for item in multimodal.get("formulas") or []:
        if isinstance(item, dict):
            extracted.append(str(item.get("raw") or item.get("display") or ""))
        else:
            extracted.append(str(item))
    formulas = parse_formula_signals(f"{question}\n{answer}\n" + "\n".join(extracted))
    formatted = [formula.to_dict() for formula in formulas]
    return {
        "used": bool(formulas),
        "formulas": [formula.raw for formula in formulas[:12]],
        "formatted": formatted[:12],
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


def diagram_helper(question: str, topic: str = "", multimodal: Dict[str, Any] | None = None) -> Dict[str, Any]:
    normalized = (question or "").lower()
    multimodal = multimodal if isinstance(multimodal, dict) else {}
    context = "\n".join(
        value
        for value in (
            str(multimodal.get("ocr_text") or ""),
            str(multimodal.get("handwritten_text") or ""),
            "\n".join(str(line) for line in multimodal.get("math_lines") or []),
        )
        if value
    )
    formula_signals = parse_formula_signals(
        "\n".join(
            str(item.get("raw") or item.get("display") or item)
            for item in multimodal.get("formulas") or []
        )
    )
    specs = [spec.to_dict() for spec in infer_diagram_specs(question, context, formula_signals)]
    diagram_type = (
        specs[0]["diagram_type"]
        if specs
        else "reaction flow"
        if any(term in normalized for term in ("reaction", "mechanism"))
        else "concept sketch"
    )
    return {
        "used": True,
        "diagram_type": diagram_type,
        "topic": topic or "current concept",
        "diagram_specs": specs,
        "directive": (
            "When a diagram helps, describe the labelled visual using the diagram_specs. "
            "Keep it educational and tied to the student's exact doubt; do not add decoration."
        ),
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
