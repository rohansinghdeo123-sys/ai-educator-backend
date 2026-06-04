"""Student-safe citation metadata for grounded Coach answers."""

from __future__ import annotations

from typing import Any, Dict


def _excerpt(value: Any, limit: int = 220) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def build_source_bundle(
    retrieved_material: Dict[str, Any] | None = None,
    attachment_bundle: Any = None,
    retrieval_policy: str = "none",
    material_supported: bool = True,
) -> Dict[str, Any]:
    citations = []
    material = retrieved_material or {}
    context = str(material.get("context") or "").strip()
    has_notes = False
    if context:
        has_notes = True
        scope = material.get("scope") if isinstance(material.get("scope"), dict) else {}
        section_id = str(material.get("section_id") or scope.get("section_id") or "")
        label = str(scope.get("topic") or section_id or scope.get("chapter") or "Selected study material")
        citations.append({
            "id": "notes-1",
            "label": label.replace("_", " ").title(),
            "source": str(material.get("source") or "Study notes"),
            "section_id": section_id,
            "excerpt": _excerpt(context),
            "kind": "notes",
        })

    attachment_citations = list(getattr(attachment_bundle, "citations", []) or [])
    citations.extend({**citation, "kind": citation.get("kind") or "upload"} for citation in attachment_citations)
    multimodal = getattr(attachment_bundle, "multimodal", {}) or {}
    if multimodal and any(
        multimodal.get(key)
        for key in ("ocr_text", "handwritten_text", "math_lines", "formulas", "diagram_specs")
    ):
        extraction_excerpt = _excerpt(
            multimodal.get("ocr_text")
            or multimodal.get("handwritten_text")
            or "Structured OCR, formula, and diagram extraction from uploaded material."
        )
        citations.append({
            "id": "upload-multimodal-extraction",
            "label": "Multimodal extraction",
            "source": "Uploaded material analysis",
            "section_id": "",
            "excerpt": extraction_excerpt,
            "kind": "upload_analysis",
        })
    has_image = any(str(item.get("source") or "") == "Uploaded image" for item in attachment_citations)
    has_upload = bool(attachment_citations or (getattr(attachment_bundle, "has_material", False)))
    grounded = bool(citations)
    if has_notes and has_upload:
        indicator = "Based on your notes + upload"
        answer_basis = "notes_and_upload"
    elif has_image:
        indicator = "Based on your uploaded image"
        answer_basis = "upload"
    elif has_upload:
        indicator = "Based on your uploaded material"
        answer_basis = "upload"
    elif has_notes:
        indicator = "Based on your notes"
        answer_basis = "notes"
    elif retrieval_policy == "required" and not material_supported:
        indicator = "Study material not found"
        answer_basis = "missing_required_source"
    else:
        indicator = "General tutor reasoning"
        answer_basis = "general_reasoning"

    return {
        "grounded": grounded,
        "indicator": indicator,
        "answer_basis": answer_basis,
        "retrieval_policy": retrieval_policy,
        "material_supported": bool(material_supported),
        "source_count": len(citations),
        "citations": citations[:8],
    }
