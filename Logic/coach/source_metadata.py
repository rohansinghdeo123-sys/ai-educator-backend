"""Student-safe citation metadata for grounded Coach answers."""

from __future__ import annotations

from typing import Any, Dict


def _excerpt(value: Any, limit: int = 220) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def build_source_bundle(
    retrieved_material: Dict[str, Any] | None = None,
    attachment_bundle: Any = None,
) -> Dict[str, Any]:
    citations = []
    material = retrieved_material or {}
    context = str(material.get("context") or "").strip()
    if context:
        scope = material.get("scope") if isinstance(material.get("scope"), dict) else {}
        section_id = str(material.get("section_id") or scope.get("section_id") or "")
        label = str(scope.get("topic") or section_id or scope.get("chapter") or "Selected study material")
        citations.append({
            "id": "notes-1",
            "label": label.replace("_", " ").title(),
            "source": str(material.get("source") or "Study notes"),
            "section_id": section_id,
            "excerpt": _excerpt(context),
        })

    attachment_citations = list(getattr(attachment_bundle, "citations", []) or [])
    citations.extend(attachment_citations)
    has_image = any(str(item.get("source") or "") == "Uploaded image" for item in attachment_citations)
    grounded = bool(citations)
    return {
        "grounded": grounded,
        "indicator": "Based on your uploaded image" if has_image else "Based on your notes" if grounded else "",
        "citations": citations[:8],
    }
