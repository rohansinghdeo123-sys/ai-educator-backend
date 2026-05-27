import re
from typing import Any, Dict, List, Optional

from Logic.knowledge_graph import knowledge_graph
from Logic.tools.knowledge_search import search_knowledge_base

SECTION_ALIASES = {
    "basic_concepts_of_chemistry": "matter_definition",
    "basic_concept_of_chemistry": "matter_definition",
    "matter": "matter_definition",
    "hydrocarbon": "alkanes",
    "hydrocarbons": "alkanes",
    "aromatic_hydrocarbons": "aromatics",
}


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value:
        return [value]
    return []


def _short_label(text: str, fallback: str) -> str:
    cleaned = re.sub(r"\s+", " ", _as_text(text)).strip(". ")
    if not cleaned:
        return fallback

    if " is " in cleaned.lower():
        return cleaned.split(" is ", 1)[0][:34].strip() or fallback
    if " are " in cleaned.lower():
        return cleaned.split(" are ", 1)[0][:34].strip() or fallback

    words = cleaned.split()
    return " ".join(words[:4])[:34].strip() or fallback


def _find_concept(section_id: str, topic: Optional[str]) -> Optional[Dict[str, Any]]:
    concept = knowledge_graph.get_concept(section_id)
    if concept:
        return concept

    search_terms = [
        topic or "",
        section_id.replace("_", " "),
    ]
    for term in search_terms:
        if not term.strip():
            continue
        matches = knowledge_graph.search_by_keyword(term, limit=1)
        if matches:
            return matches[0]

    return None


def _strip_markdown(value: str) -> str:
    cleaned = re.sub(r"`([^`]+)`", r"\1", value)
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"^[#>\-\d\.\s]+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _first_sentence(value: str, fallback: str) -> str:
    cleaned = _strip_markdown(value)
    if not cleaned:
        return fallback
    match = re.search(r"(.+?[.!?])\s", cleaned)
    return (match.group(1) if match else cleaned[:220]).strip()


def _markdown_to_concept(section_id: str, topic: Optional[str]) -> Optional[Dict[str, Any]]:
    result = search_knowledge_base(
        section_id=section_id,
        question=topic or section_id.replace("_", " "),
        max_paragraphs=10,
        max_chars=9000,
    )
    context = _as_text(result.get("context"))
    if not context or result.get("error"):
        return None

    title = topic or section_id.replace("_", " ").title()
    heading = next(
        (
            _strip_markdown(line)
            for line in context.splitlines()
            if line.strip().startswith("#") and _strip_markdown(line)
        ),
        "",
    )
    if heading:
        title = heading

    bullet_points = []
    for line in context.splitlines():
        stripped = line.strip()
        if re.match(r"^[-*]\s+", stripped) or re.match(r"^\d+\.\s+", stripped):
            point = _strip_markdown(re.sub(r"^([-*]|\d+\.)\s+", "", stripped))
            if point and len(point) > 8:
                bullet_points.append(point)

    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", _strip_markdown(context))
        if len(sentence.strip()) > 24
    ]
    key_points = []
    for item in bullet_points + sentences:
        if item not in key_points:
            key_points.append(item)
        if len(key_points) >= 8:
            break

    formulas = []
    formula_pattern = re.compile(r"(C[₀-₉nNH+\-\d\s]+|C\d*H\d*|[A-Za-z]+\s*=\s*[^.,;\n]+)")
    for match in formula_pattern.findall(context):
        formula = _strip_markdown(match)
        if formula and formula not in formulas:
            formulas.append(formula)
        if len(formulas) >= 4:
            break

    examples = [
        sentence
        for sentence in sentences
        if any(marker in sentence.lower() for marker in ["example", "e.g", "for example", "such as"])
    ][:4]

    related_lookup = {
        "alkanes": ["alkenes", "alkynes", "aromatics"],
        "alkenes": ["alkanes", "alkynes", "aromatics"],
        "alkynes": ["alkanes", "alkenes", "aromatics"],
        "aromatics": ["alkanes", "alkenes", "alkynes"],
    }
    related_concepts = [
        {"concept_id": item, "relationship": "related_hydrocarbon"}
        for item in related_lookup.get(section_id, [])
    ]

    common_mistakes = [
        {
            "mistake": f"Reading {title} as a memorization topic only.",
            "correction": "Connect each formula, property, and reaction to the structural feature first.",
            "frequency": "medium",
        },
        {
            "mistake": "Confusing similar hydrocarbon families.",
            "correction": "Check the bond type first, then apply formula, naming, and reaction rules.",
            "frequency": "high",
        },
    ] if section_id in related_lookup else []

    return {
        "concept_id": section_id,
        "title": title,
        "definition": _first_sentence(context, f"{title} is a selected study topic."),
        "core_explanation": _strip_markdown(context)[:900],
        "key_points": key_points,
        "examples": examples,
        "formulas": formulas,
        "properties": [_short_label(point, f"Point {index + 1}") for index, point in enumerate(key_points[:6])],
        "common_mistakes": common_mistakes,
        "related_concepts": related_concepts,
        "prerequisites": [],
        "_artifact_source": result.get("source", "markdown"),
    }


def _relationship_label(value: Any) -> str:
    if isinstance(value, dict):
        return _as_text(value.get("relationship") or "connects")
    return "connects"


def _related_id(value: Any) -> str:
    if isinstance(value, dict):
        return _as_text(value.get("concept_id") or value.get("title") or "related")
    return _as_text(value)


def _title_from_concept_id(concept_id: str) -> str:
    concept = knowledge_graph.get_concept(concept_id)
    if concept:
        return _as_text(concept.get("title")) or concept_id.replace("_", " ").title()
    return concept_id.replace("_", " ").title()


def _build_concept_map(concept: Dict[str, Any]) -> Dict[str, Any]:
    concept_id = _as_text(concept.get("concept_id")) or "concept"
    title = _as_text(concept.get("title")) or _title_from_concept_id(concept_id)
    properties = [_as_text(item) for item in _as_list(concept.get("properties")) if _as_text(item)]
    key_points = [_as_text(item) for item in _as_list(concept.get("key_points")) if _as_text(item)]
    related = _as_list(concept.get("related_concepts"))
    prerequisites = [_as_text(item) for item in _as_list(concept.get("prerequisites")) if _as_text(item)]

    nodes = [
        {
            "id": concept_id,
            "label": title,
            "description": _as_text(concept.get("definition")) or _as_text(concept.get("core_explanation")),
            "kind": "core",
        }
    ]
    edges = []

    source_items = properties[:7] or [_short_label(point, f"Point {index + 1}") for index, point in enumerate(key_points[:5])]
    for index, item in enumerate(source_items):
        node_id = f"{concept_id}-property-{index + 1}"
        nodes.append(
            {
                "id": node_id,
                "label": item,
                "description": key_points[index] if index < len(key_points) else f"Important part of {title}.",
                "kind": "property",
            }
        )
        edges.append({"from": concept_id, "to": node_id, "label": "includes"})

    for index, item in enumerate(related[:3]):
        related_id = _related_id(item)
        node_id = f"{concept_id}-related-{index + 1}"
        nodes.append(
            {
                "id": node_id,
                "label": _title_from_concept_id(related_id),
                "description": f"Connected idea: {_relationship_label(item).replace('_', ' ')}.",
                "kind": "related",
            }
        )
        edges.append({"from": concept_id, "to": node_id, "label": _relationship_label(item).replace("_", " ")})

    for index, item in enumerate(prerequisites[:2]):
        node_id = f"{concept_id}-prerequisite-{index + 1}"
        nodes.append(
            {
                "id": node_id,
                "label": _title_from_concept_id(item),
                "description": "Review this first if the main idea feels confusing.",
                "kind": "prerequisite",
            }
        )
        edges.append({"from": node_id, "to": concept_id, "label": "supports"})

    return {
        "type": "concept_map",
        "title": f"{title} concept map",
        "subtitle": "A quick visual route through the idea.",
        "nodes": nodes,
        "edges": edges,
    }


def _build_flip_cards(concept: Dict[str, Any]) -> Dict[str, Any]:
    title = _as_text(concept.get("title")) or "Topic"
    cards = []
    definition = _as_text(concept.get("definition"))
    if definition:
        cards.append(
            {
                "front": f"What is {title}?",
                "back": definition,
                "tag": "definition",
            }
        )

    for index, point in enumerate(_as_list(concept.get("key_points"))[:6]):
        text = _as_text(point)
        if text:
            cards.append(
                {
                    "front": _short_label(text, f"Key idea {index + 1}"),
                    "back": text,
                    "tag": "key point",
                }
            )

    for example in _as_list(concept.get("examples"))[:2]:
        text = _as_text(example)
        if text:
            cards.append(
                {
                    "front": "Can you connect this to real life?",
                    "back": text,
                    "tag": "example",
                }
            )

    if not cards:
        cards.append(
            {
                "front": f"Core idea of {title}",
                "back": _as_text(concept.get("core_explanation")) or "Review the teacher notes for this topic.",
                "tag": "core",
            }
        )

    return {
        "type": "flip_cards",
        "title": "Tap-to-reveal cards",
        "subtitle": "Fast recall without rereading long notes.",
        "cards": cards[:8],
    }


def _extract_variables(formula: str) -> List[str]:
    if "=" not in formula:
        return []

    left, right = formula.split("=", 1)
    left_terms = {item.lower() for item in re.findall(r"[A-Za-z]+", left)}
    variables = []
    for token in re.findall(r"[A-Za-z]+", right):
        normalized = token.lower()
        if normalized not in left_terms and normalized not in {"and", "or", "by", "per"} and normalized not in variables:
            variables.append(normalized)
    return variables[:4]


def _build_formula_lab(concept: Dict[str, Any]) -> Dict[str, Any]:
    formulas = []
    for item in _as_list(concept.get("formulas"))[:5]:
        expression = _as_text(item)
        if not expression:
            continue
        label = _short_label(expression.split("=", 1)[0] if "=" in expression else expression, "Formula")
        formulas.append(
            {
                "label": label,
                "formula": expression,
                "variables": _extract_variables(expression),
                "hint": "Write units first, substitute values second, calculate last.",
            }
        )

    return {
        "type": "formula_lab",
        "title": "Formula lab",
        "subtitle": "Use formulas actively, not just as text.",
        "formulas": formulas,
        "empty_note": "This topic is concept-heavy, so focus on definitions, examples, and mistakes first.",
    }


def _build_mistake_cards(concept: Dict[str, Any]) -> Dict[str, Any]:
    mistakes = []
    for item in _as_list(concept.get("common_mistakes"))[:5]:
        if isinstance(item, dict):
            mistake = _as_text(item.get("mistake"))
            correction = _as_text(item.get("correction"))
            frequency = _as_text(item.get("frequency")) or "medium"
        else:
            mistake = _as_text(item)
            correction = "Compare it with the key points before answering."
            frequency = "medium"

        if mistake:
            mistakes.append(
                {
                    "mistake": mistake,
                    "correction": correction,
                    "frequency": frequency,
                }
            )

    return {
        "type": "mistake_cards",
        "title": "Mistake shield",
        "subtitle": "Common traps students should avoid.",
        "mistakes": mistakes,
        "empty_note": "No common mistakes are listed for this topic yet.",
    }


def generate_study_artifacts(section_id: str, topic: Optional[str] = None) -> Dict[str, Any]:
    normalized_id = re.sub(r"[^a-z0-9]+", "_", (section_id or "").strip().lower()).strip("_")
    normalized_id = SECTION_ALIASES.get(normalized_id, normalized_id)
    if not normalized_id:
        raise ValueError("section_id is required.")

    concept = _find_concept(normalized_id, topic)
    if not concept:
        concept = _markdown_to_concept(normalized_id, topic)
    if not concept:
        raise LookupError(f"Section '{normalized_id}' was not found in the knowledge graph.")

    title = _as_text(concept.get("title")) or _title_from_concept_id(normalized_id)
    key_points = [_as_text(item) for item in _as_list(concept.get("key_points")) if _as_text(item)]
    formulas = [_as_text(item) for item in _as_list(concept.get("formulas")) if _as_text(item)]
    mistakes = _as_list(concept.get("common_mistakes"))

    return {
        "source": _as_text(concept.get("_artifact_source")) or "knowledge_graph",
        "section_id": _as_text(concept.get("concept_id")) or normalized_id,
        "title": title,
        "subtitle": _as_text(concept.get("definition")) or _as_text(concept.get("core_explanation")),
        "student_goal": f"Understand {title}, recall the key points, and avoid common exam mistakes.",
        "quality": {
            "key_points": len(key_points),
            "formulas": len(formulas),
            "mistakes": len(mistakes),
        },
        "artifacts": [
            _build_concept_map(concept),
            _build_flip_cards(concept),
            _build_formula_lab(concept),
            _build_mistake_cards(concept),
        ],
    }
