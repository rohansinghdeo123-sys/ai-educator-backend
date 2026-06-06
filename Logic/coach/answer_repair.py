"""Verification and repair decisions for final coach answers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class AnswerRepairDecision:
    action: str
    reason: str
    passed: bool
    repair_applied: bool = False
    issues: List[str] = field(default_factory=list)
    hallucination_risk: float = 0.0
    required_action: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _quality_dict(value: Any) -> Dict[str, Any]:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value if isinstance(value, dict) else {}


def decide_answer_repair(
    *,
    quality: Any,
    verification: Dict[str, Any] | None = None,
    retrieval_gate: Any = None,
    strict_grounding: bool = False,
) -> AnswerRepairDecision:
    quality_payload = _quality_dict(quality)
    verification = verification if isinstance(verification, dict) else {}
    issues = list(quality_payload.get("issues") or [])
    issues.extend(str(item) for item in list(verification.get("issues") or []) if str(item) not in issues)
    hallucination_risk = float(quality_payload.get("hallucination_risk") or 0.0)
    quality_passed = bool(quality_payload.get("passed"))
    verifier_passed = verification.get("passed")
    gate_can_answer = True if retrieval_gate is None else bool(getattr(retrieval_gate, "can_answer", True))
    gate_required_action = "" if retrieval_gate is None else str(getattr(retrieval_gate, "required_action", "") or "")

    if not gate_can_answer:
        return AnswerRepairDecision(
            action="replace_with_material_not_found",
            reason="Required study material is missing, so the answer must not use unsupported facts.",
            passed=False,
            issues=issues,
            hallucination_risk=hallucination_risk,
            required_action=gate_required_action,
        )

    if strict_grounding and hallucination_risk >= 0.65:
        return AnswerRepairDecision(
            action="replace_with_material_not_found",
            reason="Strict grounding is active and hallucination risk crossed the repair threshold.",
            passed=False,
            issues=issues,
            hallucination_risk=hallucination_risk,
            required_action="use_material_not_found_response",
        )

    if verifier_passed is False or not quality_passed:
        return AnswerRepairDecision(
            action="needs_review",
            reason="The answer can be delivered only with a needs-review runtime status.",
            passed=False,
            issues=issues,
            hallucination_risk=hallucination_risk,
            required_action="review_or_repair_answer",
        )

    return AnswerRepairDecision(
        action="deliver",
        reason="Quality and verification checks passed.",
        passed=True,
        issues=issues,
        hallucination_risk=hallucination_risk,
    )


def mark_repair_applied(
    *,
    original: AnswerRepairDecision,
    quality: Any,
    action: str = "deliver",
    reason: str = "Repair was applied and the replacement answer was rescored.",
) -> AnswerRepairDecision:
    quality_payload = _quality_dict(quality)
    return AnswerRepairDecision(
        action=action,
        reason=reason,
        passed=bool(quality_payload.get("passed")),
        repair_applied=True,
        issues=list(quality_payload.get("issues") or original.issues),
        hallucination_risk=float(quality_payload.get("hallucination_risk") or 0.0),
        required_action=original.required_action,
    )
