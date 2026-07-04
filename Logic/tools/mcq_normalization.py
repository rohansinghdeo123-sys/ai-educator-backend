"""Canonical MCQ normalization, validation, and shuffling.

Single source of truth used by both exam generation paths (the exam agent
and the structured generators in section_doubt). Guarantees:
- an answer key is never guessed — keyless questions are dropped;
- option sets are complete, non-empty, and duplicate-free;
- correct-answer positions are deterministically shuffled so packs do not
  inherit the model's A/B placement bias (skipped when the explanation
  cites option letters, keeping explanations truthful).
"""

from __future__ import annotations

import re
from hashlib import sha256
from typing import Any, Dict, List

__all__ = [
    "normalize_option_key",
    "normalize_mcq_options",
    "normalize_mcq_answer",
    "mcq_options_are_valid",
    "shuffle_mcq_options",
    "parse_text_mcqs",
]


def normalize_option_key(value: Any, fallback: str) -> str:
    key = str(value or fallback).strip().upper()[:1]
    return key if key in {"A", "B", "C", "D"} else fallback


def normalize_mcq_options(options: Any) -> List[str]:
    normalized: List[str] = []

    if not isinstance(options, list):
        options = []

    for index, option in enumerate(options[:4]):
        fallback_key = chr(65 + index)

        if isinstance(option, dict):
            key = normalize_option_key(option.get("key"), fallback_key)
            text = str(option.get("text") or "").strip()
        else:
            raw_text = str(option or "").strip()
            match = re.match(r"^([A-D])[\.\)]\s*(.+)$", raw_text, flags=re.IGNORECASE)

            if match:
                key = normalize_option_key(match.group(1), fallback_key)
                text = match.group(2).strip()
            else:
                key = fallback_key
                text = raw_text

        normalized.append(f"{key}. {text or 'Option unavailable'}")

    while len(normalized) < 4:
        key = chr(65 + len(normalized))
        normalized.append(f"{key}. Option unavailable")

    return normalized


def normalize_mcq_answer(question: Dict[str, Any]) -> str:
    """Return the validated answer key, or "" when the model's key is unusable.

    An empty return drops the question upstream — a guessed default would
    silently mark a wrong option as correct, which is worse than one fewer
    question (the generator's retry loop tops packs back up).
    """
    answer = str(
        question.get("answer")
        or question.get("correct")
        or question.get("correct_answer")
        or ""
    ).strip().upper()

    if answer[:1] in {"A", "B", "C", "D"}:
        return answer[:1]

    return ""


def _option_texts(options: List[str]) -> List[str]:
    return [re.sub(r"^[A-D][\.\)]\s*", "", option, flags=re.IGNORECASE).strip() for option in options]


def mcq_options_are_valid(options: List[str]) -> bool:
    """Reject degenerate packs: missing, empty, or duplicate option texts."""
    texts = _option_texts(options)
    if len(texts) != 4:
        return False
    if any(not text or text.lower() == "option unavailable" for text in texts):
        return False
    lowered = [re.sub(r"\s+", " ", text.lower()) for text in texts]
    return len(set(lowered)) == 4


_LETTER_REFERENCE = re.compile(r"\boption\s+[A-D]\b|\(\s*[A-D]\s*\)|\b[A-D][\.\)]", re.IGNORECASE)


def shuffle_mcq_options(question: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministically reshuffle option positions and re-letter the answer.

    Models place the correct option at A/B far more often than chance, which
    lets students pattern-guess. Seeding by question text keeps the same pack
    stable across refetches. Questions whose explanation cites option letters
    are left untouched so the explanation stays truthful.
    """
    options = list(question.get("options") or [])
    correct = str(question.get("correct") or "").strip().upper()[:1]
    if len(options) != 4 or correct not in {"A", "B", "C", "D"}:
        return question
    if _LETTER_REFERENCE.search(str(question.get("explanation") or "")):
        return question

    texts = _option_texts(options)
    correct_text = texts[ord(correct) - 65]

    seed = int.from_bytes(
        sha256(str(question.get("question") or "").encode("utf-8")).digest()[:4],
        "big",
    )
    order = list(range(4))
    # Fisher-Yates with a tiny deterministic LCG keyed by the question text.
    state = seed or 1
    for index in range(3, 0, -1):
        state = (state * 1103515245 + 12345) % (2 ** 31)
        swap = state % (index + 1)
        order[index], order[swap] = order[swap], order[index]

    shuffled = [texts[position] for position in order]
    new_correct_index = shuffled.index(correct_text)
    return {
        **question,
        "options": [f"{chr(65 + index)}. {text}" for index, text in enumerate(shuffled)],
        "correct": chr(65 + new_correct_index),
    }


def parse_text_mcqs(text: str, count: int = 5) -> List[Dict[str, Any]]:
    """Fallback parser for compact legacy MCQ text.

    Example supported:
    Q1. Question? A. Opt B. Opt C. Opt D. Opt Answer: C Explanation: ...
    """
    if not text:
        return []

    normalized_text = re.sub(r"\s+", " ", str(text).strip())
    blocks = re.split(r"(?=Q\s*\d+\s*[\.\)])", normalized_text, flags=re.IGNORECASE)
    parsed: List[Dict[str, Any]] = []

    for block in blocks:
        block = block.strip()
        if not re.match(r"Q\s*\d+\s*[\.\)]", block, flags=re.IGNORECASE):
            continue

        qid_match = re.match(r"Q\s*(\d+)\s*[\.\)]", block, flags=re.IGNORECASE)
        qid = f"Q{qid_match.group(1)}" if qid_match else f"Q{len(parsed) + 1}"

        answer_match = re.search(r"Answer\s*:\s*([A-D])", block, flags=re.IGNORECASE)
        explanation_match = re.search(
            r"Explanation\s*:\s*(.*?)(?=Q\s*\d+\s*[\.\)]|$)",
            block,
            flags=re.IGNORECASE,
        )

        # No explicit answer marker means the key would be a guess; skip the
        # block rather than crown option A arbitrarily.
        if not answer_match:
            continue
        correct = normalize_option_key(answer_match.group(1), "A")
        explanation = explanation_match.group(1).strip() if explanation_match else ""

        before_answer = re.split(r"Answer\s*:", block, flags=re.IGNORECASE)[0]
        option_matches = list(
            re.finditer(
                r"\b([A-D])[\.\)]\s*(.*?)(?=\s+\b[A-D][\.\)]\s+|$)",
                before_answer,
                flags=re.IGNORECASE,
            )
        )

        if len(option_matches) < 4:
            continue

        first_option_start = option_matches[0].start()
        question = re.sub(
            r"^Q\s*\d+\s*[\.\)]\s*",
            "",
            before_answer[:first_option_start].strip(),
            flags=re.IGNORECASE,
        )

        options = []
        for match in option_matches[:4]:
            key = normalize_option_key(match.group(1), chr(65 + len(options)))
            option_text = match.group(2).strip()
            options.append(f"{key}. {option_text}")

        if question and len(options) == 4 and mcq_options_are_valid(options):
            parsed.append(
                shuffle_mcq_options(
                    {
                        "id": qid,
                        "question": question,
                        "options": options,
                        "correct": correct,
                        "explanation": explanation,
                        "source": "",
                    }
                )
            )

        if len(parsed) >= count:
            break

    return parsed
