"""RAG gate for deciding whether a coach turn may answer from available sources."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class RetrievalGateDecision:
    policy: str
    strict_grounding: bool
    can_answer: bool
    material_supported: bool
    grounding_status: str
    required_action: str = ""
    reason: str = ""
    source_mix: List[str] = field(default_factory=list)
    paragraphs_found: int = 0
    section_id: str = ""
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def evaluate_retrieval_gate(
    *,
    policy: str,
    retrieved_material: Optional[Dict[str, Any]],
    strict_grounding: bool,
    material_supported: bool,
    attachment_summary: Optional[Dict[str, Any]] = None,
) -> RetrievalGateDecision:
    material = retrieved_material or {}
    attachment_summary = attachment_summary or {}
    context = str(material.get("context") or "").strip()
    source = str(material.get("source") or "")
    section_id = str(material.get("section_id") or "")
    paragraphs_found = int(material.get("paragraphs_found") or 0)
    has_context = bool(context)
    has_upload = bool(attachment_summary.get("has_material"))
    has_image = int(attachment_summary.get("images") or 0) > 0
    has_document = int(attachment_summary.get("documents") or 0) > 0

    source_mix: List[str] = []
    if has_context and source != "student_upload":
        source_mix.append("platform_notes")
    if has_document:
        source_mix.append("uploaded_document")
    if has_image:
        source_mix.append("uploaded_image")
    if has_upload and not (has_document or has_image):
        source_mix.append("student_upload")
    if len(source_mix) > 1:
        source_mix.append("hybrid")

    supported = bool(material_supported and has_context) or bool(has_upload and has_context)
    normalized_policy = policy if policy in {"none", "optional", "required"} else "none"

    if normalized_policy == "none" and not strict_grounding:
        return RetrievalGateDecision(
            policy=normalized_policy,
            strict_grounding=False,
            can_answer=True,
            material_supported=supported,
            grounding_status="not_required",
            reason="The route does not require retrieved study material.",
            source_mix=source_mix,
            paragraphs_found=paragraphs_found,
            section_id=section_id,
            source=source,
        )

    if strict_grounding and not supported:
        return RetrievalGateDecision(
            policy=normalized_policy,
            strict_grounding=True,
            can_answer=False,
            material_supported=False,
            grounding_status="missing_required_source",
            required_action="request_or_select_study_material",
            reason="Strict grounding is active but no supporting study material was found.",
            source_mix=source_mix,
            paragraphs_found=paragraphs_found,
            section_id=section_id,
            source=source,
        )

    if supported:
        return RetrievalGateDecision(
            policy=normalized_policy,
            strict_grounding=strict_grounding,
            can_answer=True,
            material_supported=True,
            grounding_status="grounded",
            reason="The answer can use available retrieved or uploaded study material.",
            source_mix=source_mix,
            paragraphs_found=paragraphs_found,
            section_id=section_id,
            source=source,
        )

    return RetrievalGateDecision(
        policy=normalized_policy,
        strict_grounding=strict_grounding,
        can_answer=True,
        material_supported=False,
        grounding_status="optional_no_source",
        reason="No strong retrieved source was found, but the route allows general tutoring.",
        source_mix=source_mix,
        paragraphs_found=paragraphs_found,
        section_id=section_id,
        source=source,
    )
