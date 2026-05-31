"""Deterministic quality scoring for every final Study Lab coach answer."""

import re
from typing import Iterable, List

from .models import QualityReport


_NOT_FOUND = "i could not find this in your study material"
_HALLUCINATION_MARKERS = (
    "based on my training",
    "as an ai",
    "as a language model",
    "i do not have access",
    "i don't have access",
)
_STOPWORDS = {
    "define", "explain", "please", "what", "why", "how", "this", "that", "with",
    "from", "about", "again", "more", "example", "give", "tell", "the", "and",
}


def _terms(value: str) -> List[str]:
    result: List[str] = []
    for term in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]+", (value or "").lower()):
        if len(term) < 4 or term in _STOPWORDS or term in result:
            continue
        result.append(term)
    return result[:18]


def _overlap_score(answer: str, candidates: Iterable[str], default: float = 0.72) -> float:
    terms = list(candidates)
    if not terms:
        return default
    answer_lower = answer.lower()
    matched = sum(1 for term in terms if term in answer_lower)
    return min(1.0, max(0.0, matched / max(1, min(len(terms), 8))))


def score_coach_answer(
    question: str,
    answer: str,
    retrieved_context: str = "",
    strict_grounding: bool = True,
) -> QualityReport:
    answer = (answer or "").strip()
    retrieved_context = (retrieved_context or "").strip()
    issues: List[str] = []

    if not answer:
        return QualityReport(0.0, False, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, ["empty_answer"])

    is_not_found = _NOT_FOUND in answer.lower()
    relevance = 1.0 if is_not_found else _overlap_score(answer, _terms(question))
    grounding = 1.0 if is_not_found else _overlap_score(answer, _terms(retrieved_context), default=0.55)
    completeness = 1.0 if is_not_found else min(1.0, len(answer) / 420)
    clarity = 1.0 if is_not_found else (0.92 if len(answer.splitlines()) >= 3 else 0.72)
    student_friendliness = 1.0 if is_not_found else (0.9 if len(answer) <= 4500 else 0.68)
    formatting = 1.0 if is_not_found else (0.9 if "\n" in answer else 0.72)
    hallucination_risk = 0.0

    if any(marker in answer.lower() for marker in _HALLUCINATION_MARKERS):
        hallucination_risk += 0.45
        issues.append("hallucination_marker")
    if strict_grounding and not retrieved_context and not is_not_found:
        hallucination_risk += 0.7
        issues.append("unsupported_without_retrieval")
    if strict_grounding and grounding < 0.2 and not is_not_found:
        hallucination_risk += 0.25
        issues.append("low_grounding_overlap")
    if len(answer) < 20:
        issues.append("answer_too_short")

    hallucination_risk = min(1.0, hallucination_risk)
    score = (
        relevance * 0.20
        + grounding * 0.28
        + completeness * 0.14
        + clarity * 0.14
        + student_friendliness * 0.12
        + formatting * 0.12
        - hallucination_risk * 0.30
    )
    score = round(max(0.0, min(1.0, score)), 2)
    return QualityReport(
        score=score,
        passed=score >= 0.58 and hallucination_risk < 0.65 and "answer_too_short" not in issues,
        relevance=round(relevance, 2),
        grounding=round(grounding, 2),
        completeness=round(completeness, 2),
        clarity=round(clarity, 2),
        student_friendliness=round(student_friendliness, 2),
        formatting=round(formatting, 2),
        hallucination_risk=round(hallucination_risk, 2),
        issues=issues,
    )
