"""Deterministic quality scoring for every final Study Lab coach answer."""

from __future__ import annotations

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
_FORMAT_HINTS = {
    "definition": ("definition", "means", "refers to", "simple"),
    "comparison": ("difference", "whereas", "while", "compared", "versus"),
    "numerical": ("formula", "step", "calculate", "answer", "="),
    "exam_answer": ("key", "exam", "marks", "point", "tip"),
    "revision": ("summary", "recap", "remember", "key", "formula"),
    "quiz": ("question", "option", "answer", "try"),
    "stuck": ("simple", "example", "step", "understand", "check"),
    "planning": ("step", "minute", "plan", "priority", "next"),
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


def _intent_score(answer: str, intent: str, answer_format: str) -> float:
    normalized = answer.lower()
    if intent == "conversation" or answer_format == "conversation":
        return 1.0 if 1 <= len(answer.split()) <= 80 else 0.72
    hints = _FORMAT_HINTS.get(answer_format) or _FORMAT_HINTS.get(intent) or ()
    if not hints:
        return 0.78
    matches = sum(1 for hint in hints if hint in normalized)
    return min(1.0, 0.58 + matches * 0.12)


def _readability_score(answer: str) -> float:
    sentences = [item.strip() for item in re.split(r"[.!?]+", answer) if item.strip()]
    words = re.findall(r"[A-Za-z0-9]+", answer)
    if not words:
        return 0.0
    average_sentence_words = len(words) / max(1, len(sentences))
    if average_sentence_words <= 22:
        score = 0.94
    elif average_sentence_words <= 32:
        score = 0.82
    elif average_sentence_words <= 42:
        score = 0.68
    else:
        score = 0.5
    if len(max(words, key=len, default="")) > 28:
        score -= 0.08
    return max(0.0, min(1.0, score))


def score_coach_answer(
    question: str,
    answer: str,
    retrieved_context: str = "",
    strict_grounding: bool = True,
    intent: str = "concept",
    answer_format: str = "concept",
) -> QualityReport:
    answer = (answer or "").strip()
    retrieved_context = (retrieved_context or "").strip()
    issues: List[str] = []

    if not answer:
        return QualityReport(
            score=0.0,
            passed=False,
            relevance=0.0,
            grounding=0.0,
            completeness=0.0,
            clarity=0.0,
            student_friendliness=0.0,
            formatting=0.0,
            intent_satisfaction=0.0,
            readability=0.0,
            hallucination_risk=1.0,
            issues=["empty_answer"],
        )

    is_not_found = _NOT_FOUND in answer.lower()
    is_conversational = intent == "conversation" or answer_format == "conversation"
    relevance = 1.0 if is_not_found else _overlap_score(answer, _terms(question), default=0.9 if is_conversational else 0.72)
    grounding = 1.0 if is_not_found else _overlap_score(answer, _terms(retrieved_context), default=1.0 if not strict_grounding else 0.55)
    completeness = 1.0 if is_not_found or is_conversational else min(1.0, len(answer) / 420)
    clarity = 1.0 if is_not_found else (0.94 if is_conversational or len(answer.splitlines()) >= 3 else 0.76)
    student_friendliness = 1.0 if is_not_found else (0.94 if len(answer) <= 4500 else 0.68)
    formatting = 1.0 if is_not_found or is_conversational else (0.92 if "\n" in answer else 0.74)
    intent_satisfaction = 1.0 if is_not_found else _intent_score(answer, intent, answer_format)
    readability = 1.0 if is_not_found else _readability_score(answer)
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
    if len(answer) < 20 and not is_conversational:
        issues.append("answer_too_short")
    if readability < 0.6:
        issues.append("hard_to_read")
    if intent_satisfaction < 0.62:
        issues.append("intent_may_be_unsatisfied")

    hallucination_risk = min(1.0, hallucination_risk)
    score = (
        relevance * 0.16
        + grounding * 0.20
        + completeness * 0.11
        + clarity * 0.12
        + student_friendliness * 0.10
        + formatting * 0.09
        + intent_satisfaction * 0.14
        + readability * 0.08
        - hallucination_risk * 0.30
    )
    score = round(max(0.0, min(1.0, score)), 2)
    return QualityReport(
        score=score,
        passed=score >= 0.62 and hallucination_risk < 0.65 and "answer_too_short" not in issues,
        relevance=round(relevance, 2),
        grounding=round(grounding, 2),
        completeness=round(completeness, 2),
        clarity=round(clarity, 2),
        student_friendliness=round(student_friendliness, 2),
        formatting=round(formatting, 2),
        intent_satisfaction=round(intent_satisfaction, 2),
        readability=round(readability, 2),
        hallucination_risk=round(hallucination_risk, 2),
        issues=issues,
    )
