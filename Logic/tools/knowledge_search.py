# Logic/tools/knowledge_search.py

import os
import re
import logging
from typing import List, Tuple

logger = logging.getLogger("ai_educator.tools.knowledge_search")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SECTION_FILE_MAP = {
    "alkanes": os.path.join(BASE_DIR, "data", "chemistry", "hydrocarbon", "part1_alkanes.md"),
    "alkenes": os.path.join(BASE_DIR, "data", "chemistry", "hydrocarbon", "part2_alkenes.md"),
    "alkynes": os.path.join(BASE_DIR, "data", "chemistry", "hydrocarbon", "part3_alkynes.md"),
    "aromatics": os.path.join(BASE_DIR, "data", "chemistry", "hydrocarbon", "part4_aromatics.md"),
}

BASICS_PATH = os.path.join(BASE_DIR, "data", "datachemistry_basics.txt")

STOPWORDS = {
    "what", "is", "the", "of", "define", "explain", "write", "give",
    "state", "why", "how", "are", "in", "for", "with", "and", "from",
    "a", "an", "this", "that", "which", "do", "does", "can", "about",
    "tell", "me", "describe", "discuss", "mention", "list", "name",
}

CHEMISTRY_SYNONYMS = {
    "formula": ["general formula", "molecular formula", "chemical formula", "CₙH"],
    "alkane": ["alkanes", "paraffin", "paraffins", "saturated hydrocarbon", "CₙH₂ₙ₊₂"],
    "alkene": ["alkenes", "olefin", "olefins", "unsaturated hydrocarbon", "CₙH₂ₙ"],
    "alkyne": ["alkynes", "acetylene", "acetylenes", "CₙH₂ₙ₋₂"],
    "aromatic": ["aromatics", "arene", "arenes", "benzene"],
    "isomer": ["isomerism", "isomers", "structural isomer", "chain isomer"],
    "reaction": ["reactions", "reacts", "reactivity", "chemical reaction"],
    "property": ["properties", "physical properties", "chemical properties"],
    "preparation": ["prepare", "prepared", "synthesis", "method of preparation"],
    "nomenclature": ["naming", "IUPAC", "IUPAC nomenclature", "IUPAC name"],
    "boiling point": ["boiling points", "b.p.", "bp"],
    "melting point": ["melting points", "m.p.", "mp"],
}


def _normalize(text: str) -> str:
    """Normalize Unicode subscripts/superscripts for matching."""
    replacements = {
        "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
        "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
        "⁺": "+", "⁻": "-", "⁰": "0", "¹": "1", "²": "2", "³": "3",
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text.lower()


def _expand_query(question: str) -> List[str]:
    """Expand query keywords with chemistry-specific synonyms."""
    norm_q = _normalize(question)
    raw_keywords = [
        w for w in re.findall(r"\b\w+\b", norm_q)
        if w not in STOPWORDS and len(w) > 2
    ]

    expanded = set(raw_keywords)
    for kw in raw_keywords:
        for base, synonyms in CHEMISTRY_SYNONYMS.items():
            if kw in _normalize(base) or any(kw in _normalize(s) for s in synonyms):
                expanded.add(_normalize(base))
                for s in synonyms:
                    expanded.add(_normalize(s))

    return list(expanded)


def _score_paragraph(paragraph: str, keywords: List[str]) -> float:
    """Score a paragraph based on keyword density and position."""
    norm_para = _normalize(paragraph)
    para_len = max(len(norm_para.split()), 1)

    # Keyword frequency score
    freq_score = sum(norm_para.count(kw) for kw in keywords)

    # Keyword density (normalize by paragraph length)
    density_score = freq_score / para_len

    # Bonus for paragraphs that contain headings or definitions
    heading_bonus = 0.5 if any(marker in paragraph for marker in ["##", "**", "Definition", "General Formula"]) else 0

    # Bonus for paragraphs with chemical formulas
    formula_bonus = 0.3 if any(c in paragraph for c in ["₂", "₃", "₄", "ₙ", "⁺", "⁻"]) else 0

    return freq_score + (density_score * 10) + heading_bonus + formula_bonus


def search_knowledge_base(
    section_id: str,
    question: str,
    max_paragraphs: int = 5,
    max_chars: int = 3000,
) -> dict:
    """
    TOOL: Search the knowledge base for relevant content.

    This is the primary retrieval tool for all agents.
    It returns the most relevant paragraphs from the section file
    along with metadata about the search.

    Returns:
        dict with keys:
        - "context": str — The retrieved text
        - "section_id": str — Which section was searched
        - "paragraphs_found": int — How many relevant paragraphs
        - "keywords_used": list — What keywords were searched
        - "basics_context": str — Supplementary basics text
    """
    section_id = section_id.strip().lower()

    if section_id not in SECTION_FILE_MAP:
        return {
            "context": "",
            "section_id": section_id,
            "paragraphs_found": 0,
            "keywords_used": [],
            "basics_context": "",
            "error": f"Section '{section_id}' not found.",
        }

    try:
        with open(SECTION_FILE_MAP[section_id], "r", encoding="utf-8") as f:
            section_text = f.read()
    except FileNotFoundError:
        return {
            "context": "",
            "section_id": section_id,
            "paragraphs_found": 0,
            "keywords_used": [],
            "basics_context": "",
            "error": "Section file not found.",
        }

    # Load basics
    basics_text = ""
    try:
        with open(BASICS_PATH, "r", encoding="utf-8") as f:
            basics_text = f.read()[:800]
    except FileNotFoundError:
        pass

    # If section is small enough, return all of it
    if len(section_text) <= max_chars:
        return {
            "context": section_text,
            "section_id": section_id,
            "paragraphs_found": len(section_text.split("\n\n")),
            "keywords_used": [],
            "basics_context": basics_text,
        }

    # Expand query with synonyms
    keywords = _expand_query(question)

    # Split into paragraphs and score
    paragraphs = [p.strip() for p in section_text.split("\n\n") if p.strip()]
    scored: List[Tuple[float, str]] = []

    for para in paragraphs:
        score = _score_paragraph(para, keywords)
        scored.append((score, para))

    # Sort by score (highest first)
    scored.sort(key=lambda x: x[0], reverse=True)

    # Select top paragraphs within char limit
    selected = []
    total_len = 0

    for score, para in scored[:max_paragraphs * 2]:  # Consider more, pick best
        if score <= 0:
            continue
        if total_len + len(para) > max_chars:
            continue
        selected.append(para)
        total_len += len(para)

    # If nothing matched, return the first few paragraphs as fallback
    if not selected:
        fallback = paragraphs[:3]
        return {
            "context": "\n\n".join(fallback),
            "section_id": section_id,
            "paragraphs_found": len(fallback),
            "keywords_used": keywords,
            "basics_context": basics_text,
        }

    return {
        "context": "\n\n".join(selected),
        "section_id": section_id,
        "paragraphs_found": len(selected),
        "keywords_used": keywords,
        "basics_context": basics_text,
    }