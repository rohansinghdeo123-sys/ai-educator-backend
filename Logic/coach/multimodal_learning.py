"""Structured multimodal learning extraction for uploaded study material."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from typing import Any, Dict, Iterable, List


_CHEMICAL_FORMULA_RE = re.compile(r"\b(?:[A-Z][a-z]?\d*){2,}(?:[+-]\d*|[+-])?\b")
_EQUATION_LINE_RE = re.compile(
    r"(?=.*\d)(?=.*(?:=|/|\^|\+|-|\*|×|÷|√|π|theta|sin|cos|tan|log|lim|dx|dy|∫|Σ)).{3,}",
    re.IGNORECASE,
)
_VARIABLE_RE = re.compile(r"\b([a-zA-Z])\s*=\s*(-?\d+(?:\.\d+)?(?:\s*[a-zA-Z/%²³^]+)?)")
_MATH_EXPRESSION_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9_^\u00b2\u00b3]*"
    r"(?:\s*[+\-*/×÷]\s*[A-Za-z0-9_^\u00b2\u00b3]+)*"
    r"\s*=\s*[^.;,\n]+)"
)


@dataclass
class FormulaSignal:
    raw: str
    kind: str
    display: str
    variables: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DiagramSpec:
    diagram_type: str
    title: str
    purpose: str
    nodes: List[str] = field(default_factory=list)
    labels: List[str] = field(default_factory=list)
    steps: List[str] = field(default_factory=list)
    render_hint: str = "Use a clean labelled educational diagram."

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MultimodalExtraction:
    ocr_text: str = ""
    handwritten_text: str = ""
    math_lines: List[str] = field(default_factory=list)
    formulas: List[FormulaSignal] = field(default_factory=list)
    diagram_specs: List[DiagramSpec] = field(default_factory=list)
    confidence: float = 0.0
    warnings: List[str] = field(default_factory=list)

    @property
    def has_learning_signal(self) -> bool:
        return bool(
            self.ocr_text.strip()
            or self.handwritten_text.strip()
            or self.math_lines
            or self.formulas
            or self.diagram_specs
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ocr_text": self.ocr_text,
            "handwritten_text": self.handwritten_text,
            "math_lines": list(self.math_lines),
            "formulas": [formula.to_dict() for formula in self.formulas],
            "diagram_specs": [diagram.to_dict() for diagram in self.diagram_specs],
            "confidence": self.confidence,
            "warnings": list(self.warnings),
        }

    def as_context(self) -> str:
        if not self.has_learning_signal:
            return ""
        lines = ["MULTIMODAL EXTRACTION:"]
        if self.ocr_text.strip():
            lines.append(f"Visible OCR text:\n{self.ocr_text.strip()}")
        if self.handwritten_text.strip():
            lines.append(f"Handwritten work interpreted:\n{self.handwritten_text.strip()}")
        if self.math_lines:
            lines.append("Math/equation lines:\n" + "\n".join(f"- {line}" for line in self.math_lines[:12]))
        if self.formulas:
            lines.append(
                "Parsed formulas:\n"
                + "\n".join(
                    f"- {formula.display} ({formula.kind})"
                    for formula in self.formulas[:12]
                )
            )
        if self.diagram_specs:
            lines.append(
                "Diagram opportunities:\n"
                + "\n".join(
                    f"- {diagram.title}: {diagram.purpose}"
                    for diagram in self.diagram_specs[:4]
                )
            )
        if self.warnings:
            lines.append("Extraction warnings:\n" + "\n".join(f"- {warning}" for warning in self.warnings[:4]))
        return "\n\n".join(lines)


def _compact_lines(value: Any, limit: int = 4000) -> str:
    text = str(value or "").replace("\r", "\n")
    lines = [" ".join(line.strip().split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)[:limit]


def _extract_json(value: str) -> Dict[str, Any]:
    text = str(value or "").strip()
    if not text:
        return {}
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    candidate = fenced.group(1) if fenced else text
    if not candidate.startswith("{"):
        match = re.search(r"\{.*\}", candidate, flags=re.DOTALL)
        candidate = match.group(0) if match else ""
    try:
        parsed = json.loads(candidate)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _dedupe(values: Iterable[str], limit: int = 12) -> List[str]:
    results: List[str] = []
    for value in values:
        clean = " ".join(str(value or "").strip().split())
        if clean and clean not in results:
            results.append(clean)
        if len(results) >= limit:
            break
    return results


def _display_chemical_formula(raw: str) -> str:
    return re.sub(r"(\d+)", lambda match: "".join(chr(0x2080 + int(ch)) for ch in match.group(1)), raw)


def parse_formula_signals(text: str) -> List[FormulaSignal]:
    formulas: List[FormulaSignal] = []
    seen: set[str] = set()
    source = str(text or "")

    for formula in _CHEMICAL_FORMULA_RE.findall(source):
        if formula in seen:
            continue
        seen.add(formula)
        formulas.append(
            FormulaSignal(
                raw=formula,
                kind="chemical",
                display=_display_chemical_formula(formula),
            )
        )

    for line in source.splitlines():
        clean = " ".join(line.strip().split())
        if not clean or clean in seen:
            continue
        if _EQUATION_LINE_RE.search(clean) and not _CHEMICAL_FORMULA_RE.fullmatch(clean):
            match = _MATH_EXPRESSION_RE.search(clean)
            clean = " ".join((match.group(1) if match else clean).strip().split())
            if clean in seen:
                continue
            variables = {key: value for key, value in _VARIABLE_RE.findall(clean)}
            seen.add(clean)
            formulas.append(
                FormulaSignal(
                    raw=clean,
                    kind="math",
                    display=clean.replace("^2", "²").replace("^3", "³"),
                    variables=variables,
                )
            )

    return formulas[:16]


def extract_math_lines(text: str) -> List[str]:
    return _dedupe(
        (" ".join(_MATH_EXPRESSION_RE.search(line).group(1).strip().split()) if _MATH_EXPRESSION_RE.search(line) else line)
        for line in str(text or "").splitlines()
        if _EQUATION_LINE_RE.search(line)
    )


def infer_diagram_specs(question: str, text: str = "", formulas: Iterable[FormulaSignal] = ()) -> List[DiagramSpec]:
    normalized = f"{question} {text}".lower()
    diagrams: List[DiagramSpec] = []

    def add(spec: DiagramSpec) -> None:
        if not any(existing.title == spec.title for existing in diagrams):
            diagrams.append(spec)

    if any(term in normalized for term in ("photosynthesis", "respiration", "cycle", "food chain")):
        add(DiagramSpec(
            diagram_type="process_flow",
            title="Process flow diagram",
            purpose="Show inputs, steps, and outputs in the biological process.",
            nodes=["Inputs", "Main process", "Products", "Where it happens"],
            labels=["light", "CO2", "H2O", "glucose", "O2"],
            steps=["Place inputs on the left", "Put the process in the center", "Show products on the right"],
            render_hint="Render as a left-to-right labelled flow with arrows.",
        ))

    if any(term in normalized for term in ("force", "motion", "friction", "inclined plane", "velocity", "acceleration")):
        add(DiagramSpec(
            diagram_type="physics_free_body",
            title="Free-body diagram",
            purpose="Separate forces visually before solving the numerical.",
            nodes=["Object", "Normal reaction", "Weight", "Applied force", "Friction"],
            labels=["N", "mg", "F", "f"],
            steps=["Draw the object", "Add weight downward", "Add normal force", "Add applied/friction forces"],
            render_hint="Render as a simple object with arrows and force labels.",
        ))

    if any(term in normalized for term in ("triangle", "circle", "geometry", "angle", "area", "perimeter")):
        add(DiagramSpec(
            diagram_type="geometry_sketch",
            title="Geometry sketch",
            purpose="Make given lengths, angles, and unknowns visible.",
            nodes=["Shape", "Known values", "Unknown value", "Formula"],
            labels=["angle", "side", "height", "radius"],
            steps=["Draw the shape", "Mark givens", "Mark the unknown", "Link to the formula"],
            render_hint="Render as a clean labelled geometry sketch.",
        ))

    if any(term in normalized for term in ("circuit", "resistor", "current", "voltage", "battery")):
        add(DiagramSpec(
            diagram_type="circuit_diagram",
            title="Circuit diagram",
            purpose="Show current path and component relationships.",
            nodes=["Battery", "Switch", "Resistor", "Current direction"],
            labels=["V", "I", "R"],
            steps=["Draw the loop", "Place battery and resistor", "Mark current direction", "Label voltage/current"],
            render_hint="Render as a standard school circuit with symbols.",
        ))

    if any(term in normalized for term in ("reaction", "alkane", "alkene", "organic", "mechanism", "molecule", "bond")):
        add(DiagramSpec(
            diagram_type="chemistry_structure",
            title="Chemical structure or reaction map",
            purpose="Show bonds, reactants, products, and important conditions clearly.",
            nodes=["Reactant", "Condition", "Product", "Key bond/change"],
            labels=["single bond", "double bond", "catalyst", "heat"],
            steps=["Write reactants", "Mark the bond or functional group", "Add condition", "Write product"],
            render_hint="Render as a simple reaction/structure map with labels.",
        ))

    if not diagrams and (list(formulas) or any(term in normalized for term in ("diagram", "draw", "graph", "visual"))):
        add(DiagramSpec(
            diagram_type="concept_map",
            title="Concept map",
            purpose="Organize the visible information into a student-friendly visual.",
            nodes=["Given", "Concept", "Formula", "Answer"],
            labels=["known", "unknown", "relationship"],
            steps=["Start with givens", "Connect to concept", "Apply formula", "Conclude"],
            render_hint="Render as a compact concept map, not decoration.",
        ))

    return diagrams[:4]


def build_multimodal_extraction(
    *,
    question: str,
    vision_summary: str = "",
    document_text: str = "",
) -> MultimodalExtraction:
    combined = "\n".join(value for value in (_compact_lines(document_text), _compact_lines(vision_summary)) if value)
    formulas = parse_formula_signals(combined)
    math_lines = extract_math_lines(combined)
    diagrams = infer_diagram_specs(question, combined, formulas)
    confidence = 0.0
    if document_text.strip():
        confidence = max(confidence, 0.82)
    if vision_summary.strip():
        confidence = max(confidence, 0.68)
    if math_lines or formulas:
        confidence = max(confidence, 0.74)

    warnings: List[str] = []
    if vision_summary.strip():
        warnings.append("Image extraction is model-assisted; verify unclear handwriting before final calculation.")

    return MultimodalExtraction(
        ocr_text=_compact_lines(document_text or vision_summary, limit=5000),
        handwritten_text=_compact_lines(vision_summary, limit=3000) if vision_summary else "",
        math_lines=math_lines,
        formulas=formulas,
        diagram_specs=diagrams,
        confidence=round(confidence, 2),
        warnings=warnings,
    )


def build_multimodal_extraction_from_json(
    *,
    question: str,
    raw_response: str,
    fallback_text: str = "",
) -> MultimodalExtraction:
    parsed = _extract_json(raw_response)
    if not parsed:
        return build_multimodal_extraction(question=question, vision_summary=raw_response, document_text=fallback_text)

    visible_text = _compact_lines(parsed.get("visible_text") or parsed.get("ocr_text") or "")
    handwriting = _compact_lines(parsed.get("handwritten_work") or parsed.get("handwritten_text") or "")
    math_lines = _dedupe(list(parsed.get("math_lines") or []) + extract_math_lines(f"{visible_text}\n{handwriting}"))
    formula_text = "\n".join(str(item) for item in parsed.get("formulas") or [])
    formulas = parse_formula_signals(f"{visible_text}\n{handwriting}\n{formula_text}")
    diagram_text = "\n".join(str(item) for item in parsed.get("diagram_labels") or [])
    diagrams = infer_diagram_specs(question, f"{visible_text}\n{handwriting}\n{diagram_text}", formulas)
    try:
        confidence = float(parsed.get("confidence", 0.72))
    except Exception:
        confidence = 0.72

    warnings = [
        "Image extraction is model-assisted; verify unclear handwriting before final calculation."
    ]
    return MultimodalExtraction(
        ocr_text=visible_text or _compact_lines(fallback_text),
        handwritten_text=handwriting,
        math_lines=math_lines,
        formulas=formulas,
        diagram_specs=diagrams,
        confidence=round(max(0.0, min(1.0, confidence)), 2),
        warnings=warnings,
    )
