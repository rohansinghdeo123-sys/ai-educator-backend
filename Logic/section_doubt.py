# Logic/section_doubt.py

"""
SECTION DOUBT - Backward-Compatible Entry Point

Keeps the legacy section_doubt() function alive while adding structured
exam-generation helpers for the dashboard/study frontend.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from Logic.agent_router import route_to_agent
from Logic.agents.tutor_agent import reset_tutor_session

logger = logging.getLogger("ai_educator.section_doubt")


class _LegacyRequest:
    """Adapter to convert function args into a request-like object."""

    def __init__(self, question, section_id, session_id, mode, difficulty):
        self.question = question
        self.section_id = section_id
        self.session_id = session_id
        self.mode = mode
        self.difficulty = difficulty


def normalize_section_id(section_id: str) -> str:
    return (section_id or "").strip().lower()


def section_doubt(
    question: str,
    section_id: str,
    session_id: str,
    mode: str = "revision",
    difficulty: str = "medium",
) -> str:
    """
    Main entry point for the section-based AI tutor.

    Backward-compatible with old /section-ai calls.
    """
    if not section_id:
        return "Invalid section selected."

    section_id = normalize_section_id(section_id)

    request = _LegacyRequest(
        question=question,
        section_id=section_id,
        session_id=session_id,
        mode=mode,
        difficulty=difficulty,
    )

    logger.info(
        "[SECTION_DOUBT] Routing to agent system: mode=%s, section=%s",
        mode,
        section_id,
    )

    result = route_to_agent(request)

    if isinstance(result, dict):
        answer = result.get("answer", "") or result.get("data", "")
    else:
        answer = str(result or "")

    return answer or "No response generated."


def reset_conversation(session_id: str):
    """Clear conversation memory for a session."""
    reset_tutor_session(session_id)
    logger.info("Session %s memory cleared.", session_id)


# =====================================================
# STRUCTURED GENERATION HELPERS
# =====================================================
def extract_json_payload(value: Any) -> Optional[Dict[str, Any]]:
    """
    Extract a JSON object from agent output.

    Agents may return:
    - a dict
    - a raw JSON string
    - a string with surrounding text
    """
    if isinstance(value, dict):
        return value

    if not isinstance(value, str):
        return None

    text = value.strip()

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None

    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def unwrap_agent_data(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Agent routes commonly return:
    { "answer": "...", "data": { "questions": [...] } }

    This helper gives structured endpoints the nested data object when present.
    """
    if not isinstance(payload, dict):
        return payload

    data = payload.get("data")
    if isinstance(data, dict):
        return data

    return payload


def run_structured_agent(
    question: str,
    section_id: str,
    session_id: str,
    mode: str,
    difficulty: str,
) -> Tuple[Optional[Dict[str, Any]], Any]:
    request = _LegacyRequest(
        question=question,
        section_id=normalize_section_id(section_id),
        session_id=session_id,
        mode=mode,
        difficulty=difficulty,
    )

    result = route_to_agent(request)

    if isinstance(result, dict):
        raw = result.get("data") or result.get("answer") or result
    else:
        raw = result

    payload = unwrap_agent_data(extract_json_payload(raw))
    return payload, raw


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
    answer = str(
        question.get("answer")
        or question.get("correct")
        or question.get("correct_answer")
        or ""
    ).strip().upper()

    if answer[:1] in {"A", "B", "C", "D"}:
        return answer[:1]

    return "A"


def normalize_mcq_questions(payload: Optional[Dict[str, Any]], count: int) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    questions = payload.get("questions")
    if not isinstance(questions, list):
        return []

    normalized: List[Dict[str, Any]] = []

    for index, item in enumerate(questions[:count]):
        if not isinstance(item, dict):
            continue

        question_text = str(item.get("question") or "").strip()
        if not question_text:
            continue

        normalized.append(
            {
                "id": str(item.get("id") or f"Q{index + 1}"),
                "question": question_text,
                "options": normalize_mcq_options(item.get("options")),
                "correct": normalize_mcq_answer(item),
                "explanation": str(item.get("explanation") or "").strip(),
            }
        )

    return normalized


def parse_text_mcqs(text: str, count: int = 5) -> List[Dict[str, Any]]:
    """
    Fallback parser for compact legacy MCQ text.

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

        correct = normalize_option_key(answer_match.group(1) if answer_match else "A", "A")
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

        if question and len(options) == 4:
            parsed.append(
                {
                    "id": qid,
                    "question": question,
                    "options": options,
                    "correct": correct,
                    "explanation": explanation,
                }
            )

        if len(parsed) >= count:
            break

    return parsed


def build_mcq_instruction(topic: str, difficulty: str, count: int) -> str:
    return f"""
Generate exactly {count} high-quality Class 11 Chemistry MCQs for topic: {topic}.

Return valid JSON only.
Do not include markdown.
Do not include any text before or after JSON.

JSON shape:
{{
  "questions": [
    {{
      "id": "Q1",
      "question": "Question text",
      "options": [
        {{ "key": "A", "text": "Option text" }},
        {{ "key": "B", "text": "Option text" }},
        {{ "key": "C", "text": "Option text" }},
        {{ "key": "D", "text": "Option text" }}
      ],
      "answer": "A",
      "explanation": "Short feedback explaining the correct answer"
    }}
  ]
}}

Rules:
- Exactly {count} questions.
- Exactly 4 options per question.
- One and only one correct answer per question.
- Options must be plausible.
- Explanations must be useful feedback after user selection.
- Use Unicode chemical subscripts and superscripts where needed.
- Use only the current section content available to the agent.
""".strip()


def generate_structured_mcqs(
    topic: str,
    section_id: str,
    session_id: str = "exam-session",
    difficulty: str = "medium",
    count: int = 5,
) -> Dict[str, Any]:
    safe_count = max(1, min(int(count or 5), 10))
    safe_topic = (topic or section_id or "unknown").strip()
    safe_section_id = normalize_section_id(section_id or safe_topic)
    safe_difficulty = (difficulty or "medium").strip().lower()

    payload, raw = run_structured_agent(
        question=build_mcq_instruction(safe_topic, safe_difficulty, safe_count),
        section_id=safe_section_id,
        session_id=session_id,
        mode="exam",
        difficulty=safe_difficulty,
    )

    questions = normalize_mcq_questions(payload, safe_count)

    if len(questions) < safe_count:
        if isinstance(raw, dict):
            fallback_text = raw.get("answer") or json.dumps(raw)
        else:
            fallback_text = str(raw or "")
        fallback_questions = parse_text_mcqs(fallback_text, safe_count)
        if len(fallback_questions) > len(questions):
            questions = fallback_questions

    return {
        "topic": safe_topic,
        "section_id": safe_section_id,
        "difficulty": safe_difficulty,
        "questions": questions[:safe_count],
        "raw_answer": "" if questions else str(raw or ""),
    }


def normalize_probable_questions(payload: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    questions = payload.get("questions")
    if not isinstance(questions, list):
        return []

    normalized: List[Dict[str, Any]] = []

    for index, item in enumerate(questions[:5]):
        if not isinstance(item, dict):
            continue

        question_text = str(item.get("question") or "").strip()
        if not question_text:
            continue

        fallback_marks = 3 if index < 3 else 5

        try:
            marks = int(item.get("marks") or fallback_marks)
        except Exception:
            marks = fallback_marks

        normalized.append(
            {
                "id": str(item.get("id") or f"Q{index + 1}"),
                "marks": marks,
                "question": question_text,
            }
        )

    return normalized


def build_probable_instruction(topic: str, difficulty: str) -> str:
    return f"""
Generate probable Class 11 Chemistry exam theory questions for topic: {topic}.

Return valid JSON only.
Do not include markdown.
Do not include any text before or after JSON.

JSON shape:
{{
  "questions": [
    {{ "id": "Q1", "marks": 3, "question": "Question text" }},
    {{ "id": "Q2", "marks": 3, "question": "Question text" }},
    {{ "id": "Q3", "marks": 3, "question": "Question text" }},
    {{ "id": "Q4", "marks": 5, "question": "Question text" }},
    {{ "id": "Q5", "marks": 5, "question": "Question text" }}
  ]
}}

Rules:
- Generate exactly 5 questions.
- First 3 questions are 3 marks.
- Last 2 questions are 5 marks.
- Do not provide answers.
- Do not provide hints.
- Do not provide explanations.
- Use only the current section content available to the agent.
""".strip()


def format_probable_text(questions: List[Dict[str, Any]]) -> str:
    return "\n".join(
        f"{item['id']} ({item['marks']} Marks): {item['question']}"
        for item in questions
    )


def generate_structured_probable_questions(
    topic: str,
    section_id: str,
    session_id: str = "probable-session",
    difficulty: str = "medium",
) -> Dict[str, Any]:
    safe_topic = (topic or section_id or "unknown").strip()
    safe_section_id = normalize_section_id(section_id or safe_topic)
    safe_difficulty = (difficulty or "medium").strip().lower()

    payload, raw = run_structured_agent(
        question=build_probable_instruction(safe_topic, safe_difficulty),
        section_id=safe_section_id,
        session_id=session_id,
        mode="probable",
        difficulty=safe_difficulty,
    )

    questions = normalize_probable_questions(payload)

    return {
        "topic": safe_topic,
        "section_id": safe_section_id,
        "difficulty": safe_difficulty,
        "questions": questions,
        "text": format_probable_text(questions),
        "raw_answer": "" if questions else str(raw or ""),
    }
