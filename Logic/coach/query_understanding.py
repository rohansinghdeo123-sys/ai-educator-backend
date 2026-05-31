"""Fast reasoning-route analysis before any model call.

The open Study Coach is intentionally not a retrieval-first chatbot. This
module decides when the tutor can reason from the conversation and when the
student is explicitly asking for fresh, source-grounded study material.
"""

import re
from typing import Iterable, List

from .models import QueryUnderstanding


_CONVERSATIONAL = {
    "hi", "hello", "hey", "thanks", "thank you", "thankyou", "ok", "okay",
    "got it", "clear", "great", "cool", "perfect", "yes", "no", "not now",
}
_FOLLOW_UP_MARKERS = (
    "this", "that", "it", "same", "again", "simpler", "more", "example",
    "what about", "explain again", "show me", "test me", "quiz me",
)
_FOLLOW_UP_PHRASES = (
    "why", "how", "why is that", "how does that work", "can you explain that",
    "one more example", "practice this", "test me on this", "quiz me on this",
)
_FOLLOW_UP_CONTEXT_PHRASES = (
    "explain it", "explain this", "explain that", "simplify it", "simplify this",
    "simpler words", "simple words", "another example", "more examples",
    "show another", "tell me more", "go deeper", "in short",
)
_STOPWORDS = {
    "define", "explain", "please", "what", "why", "how", "this", "that", "with",
    "from", "about", "again", "more", "example", "give", "tell", "the", "and",
}
_GROUNDING_REQUESTS = (
    "from my notes", "from the notes", "from my material", "study material",
    "uploaded material", "uploaded notes", "selected chapter", "selected topic",
    "chapter data", "knowledge base", "according to my notes", "according to the notes",
    "according to ncert", "from ncert", "in ncert", "textbook says", "from the textbook",
    "use my notes", "check my notes", "check the chapter", "check the textbook",
    "use the chapter", "look in the notes", "verify from the notes",
)
_FRESHNESS_REQUESTS = (
    "latest syllabus", "current syllabus", "updated syllabus", "new syllabus",
    "latest notes", "updated notes", "latest study material",
)
_OPTIONAL_RETRIEVAL_HINTS = (
    "chapter overview", "lesson overview", "curriculum overview", "chapter recap",
)


def _contains_any(value: str, terms: Iterable[str]) -> bool:
    return any(term in value for term in terms)


def _anchor_terms(value: str) -> List[str]:
    terms: List[str] = []
    for term in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]+", value.lower()):
        if len(term) < 4 or term in _STOPWORDS or term in terms:
            continue
        terms.append(term)
    return terms[:10]


def understand_query(question: str, declared_intent: str = "general", has_history: bool = False) -> QueryUnderstanding:
    normalized = re.sub(r"\s+", " ", (question or "").strip().lower())
    is_conversational = normalized in _CONVERSATIONAL or len(normalized) <= 2
    compact = normalized.rstrip("?.!")
    is_follow_up = bool(
        has_history
        and (
            compact in _FOLLOW_UP_PHRASES
            or any(normalized == marker or normalized.startswith(f"{marker} ") for marker in _FOLLOW_UP_MARKERS)
            or _contains_any(normalized, _FOLLOW_UP_CONTEXT_PHRASES)
            or _contains_any(normalized, ("previous answer", "last answer", "same topic"))
        )
    )

    declared = (declared_intent or "").strip().lower()
    if is_conversational:
        intent = "conversation"
        answer_format = "conversation"
    elif declared in {"planning", "plan"} or _contains_any(normalized, ("study plan", "roadmap", "schedule", "what should i study")):
        intent = "planning"
        answer_format = "planning"
    elif declared in {"exam", "exam_prep"} or _contains_any(normalized, ("exam answer", "board answer", "marks", "important question")):
        intent = "exam"
        answer_format = "exam_answer"
    elif declared in {"revision", "summary"} or _contains_any(normalized, ("revise", "revision", "summary", "key points", "quick notes")):
        intent = "revision"
        answer_format = "revision"
    elif declared in {"practice", "quiz", "mcq"} or _contains_any(normalized, ("quiz me", "test me", "mcq", "practice question")):
        intent = "practice"
        answer_format = "quiz"
    elif _contains_any(normalized, ("confused", "stuck", "do not understand", "don't understand", "not getting")):
        intent = "clarification"
        answer_format = "stuck"
    elif _contains_any(normalized, ("difference between", "compare", "differentiate", " versus ", " vs ")):
        intent = "comparison"
        answer_format = "comparison"
    elif re.search(r"\d", normalized) and _contains_any(normalized, ("calculate", "find", "solve", "formula", "mass", "volume", "density")):
        intent = "numerical"
        answer_format = "numerical"
    elif _contains_any(normalized, ("define", "what is", "what are", "meaning", "definition", "describe")):
        intent = "definition"
        answer_format = "definition"
    else:
        intent = declared if declared not in {"", "general", "concept", "curiosity"} else "concept"
        answer_format = "concept"

    requires_grounding = bool(
        not is_conversational
        and _contains_any(normalized, _GROUNDING_REQUESTS + _FRESHNESS_REQUESTS)
    )
    prefers_retrieval = bool(
        not is_conversational
        and not requires_grounding
        and _contains_any(normalized, _OPTIONAL_RETRIEVAL_HINTS)
    )
    retrieval_policy = "required" if requires_grounding else "optional" if prefers_retrieval else "none"
    needs_retrieval = retrieval_policy != "none"
    tools = ["knowledge_search"] if needs_retrieval else []
    if intent in {"practice", "exam"}:
        tools.append("answer_quality")

    reasoning_mode = (
        "conversation"
        if is_conversational
        else "source_grounded"
        if requires_grounding
        else "contextual_reasoning"
        if is_follow_up
        else "general_reasoning"
    )

    return QueryUnderstanding(
        intent=intent,
        answer_format=answer_format,
        is_conversational=is_conversational,
        is_follow_up=is_follow_up,
        needs_retrieval=needs_retrieval,
        retrieval_policy=retrieval_policy,
        requires_grounding=requires_grounding,
        reasoning_mode=reasoning_mode,
        needs_memory=has_history or is_follow_up,
        needs_quality_review=not is_conversational,
        requested_tools=tools,
        anchor_terms=_anchor_terms(question),
    )
