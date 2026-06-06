"""Growth-loop evaluation for agentic coach turns."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List


@dataclass(frozen=True)
class GrowthEvaluation:
    readiness: str
    score: float
    recommendations: List[str] = field(default_factory=list)
    signals: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _count_status(rows: Iterable[Dict[str, Any]], status: str) -> int:
    return sum(1 for row in rows or [] if str(row.get("status") or "") == status)


def evaluate_turn_growth(observability: Dict[str, Any]) -> Dict[str, Any]:
    """Score one coach turn for backend improvement signals."""
    observability = observability or {}
    quality = _as_dict(observability.get("quality"))
    retrieval = _as_dict(observability.get("retrieval"))
    retrieval_gate = _as_dict(retrieval.get("gate"))
    repair = _as_dict(observability.get("repair"))
    repair_final = _as_dict(repair.get("final"))
    memory_update = _as_dict(observability.get("student_memory_update"))
    model_calls = list(observability.get("model_calls") or [])
    tool_calls = list(observability.get("tool_gateway") or [])

    quality_score = float(quality.get("score") or 0.0)
    growth_score = quality_score
    recommendations: List[str] = []

    if quality_score < 0.72:
        recommendations.append("Improve final-answer clarity and completeness for this route.")
        growth_score -= 0.08
    if retrieval_gate.get("grounding_status") == "missing_required_source":
        recommendations.append("Improve source-selection/upload flow before strict grounded answers.")
        growth_score -= 0.12
    if repair_final.get("repair_applied"):
        recommendations.append("Review why the answer needed repair and strengthen the earlier gate.")
        growth_score -= 0.08
    if _count_status(model_calls, "error"):
        recommendations.append("Review model provider fallback health for this turn type.")
        growth_score -= 0.04
    if _count_status(tool_calls, "error"):
        recommendations.append("Review deterministic tool failures and fail-open coverage.")
        growth_score -= 0.04
    if memory_update.get("support_style") == "simplify_and_check":
        recommendations.append("Keep the next explanation simpler with one quick understanding check.")
    if not recommendations:
        recommendations.append("Keep this route as a stable baseline for future evaluations.")

    growth_score = round(max(0.0, min(1.0, growth_score)), 3)
    if growth_score >= 0.85:
        readiness = "excellent"
    elif growth_score >= 0.72:
        readiness = "stable"
    elif growth_score >= 0.55:
        readiness = "watch"
    else:
        readiness = "repair"

    return GrowthEvaluation(
        readiness=readiness,
        score=growth_score,
        recommendations=recommendations[:5],
        signals={
            "quality_score": quality_score,
            "grounding_status": retrieval_gate.get("grounding_status", ""),
            "repair_action": repair_final.get("action", ""),
            "repair_applied": bool(repair_final.get("repair_applied")),
            "model_errors": _count_status(model_calls, "error"),
            "tool_errors": _count_status(tool_calls, "error"),
            "support_style": memory_update.get("support_style", ""),
        },
    ).to_dict()
