"""Shared runtime helpers for one reasoning-first Study Lab coach turn."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from typing import Any, Callable, Dict, Iterable, List, Optional

from .models import QueryUnderstanding
from .query_understanding import understand_query


@dataclass(frozen=True)
class AdaptiveAnswerBlock:
    kind: str
    title: str
    content: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


_ALLOWED_INTENTS = {
    "conversation",
    "planning",
    "exam",
    "revision",
    "practice",
    "clarification",
    "comparison",
    "numerical",
    "definition",
    "concept",
}
_ALLOWED_FORMATS = {
    "conversation",
    "planning",
    "exam_answer",
    "revision",
    "quiz",
    "stuck",
    "comparison",
    "numerical",
    "definition",
    "concept",
}
_ALLOWED_RETRIEVAL_POLICIES = {"none", "optional", "required"}


def _extract_json_object(value: str) -> Dict[str, Any]:
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


def resolve_hybrid_query(
    question: str,
    declared_intent: str = "general",
    has_history: bool = False,
    classifier: Optional[Callable[[Iterable[Dict[str, str]]], str]] = None,
) -> QueryUnderstanding:
    """Combine deterministic safety rules with a cheap structured reasoning pass."""
    query = understand_query(question, declared_intent=declared_intent, has_history=has_history)
    if query.is_conversational or classifier is None:
        return query

    prompt = f"""
Classify one student tutor message. Return JSON only.

Student message:
{question}

Deterministic baseline:
{json.dumps(query.to_dict(), ensure_ascii=False)}

Choose the best interpretation after considering natural language, implied follow-up meaning,
topic changes, whether fresh study-material retrieval is useful, and the clearest teaching strategy.

JSON shape:
{{
  "intent": "concept",
  "answer_format": "concept",
  "is_follow_up": false,
  "topic_shift": false,
  "retrieval_policy": "none",
  "confidence": 0.85,
  "teaching_strategy": "brief concept explanation with one concrete example"
}}

Rules:
- Keep greetings and thanks conversational.
- Use required retrieval only when the student explicitly requests notes, textbook, syllabus, or source verification.
- Use optional retrieval for chapter recap or curriculum alignment when it improves the answer.
- Otherwise reason from the conversation and reliable subject knowledge.
""".strip()

    try:
        parsed = _extract_json_object(
            classifier(
                [
                    {
                        "role": "system",
                        "content": "You are a compact tutor-routing classifier. Return one JSON object and no prose.",
                    },
                    {"role": "user", "content": prompt},
                ]
            )
        )
    except Exception:
        return query

    intent = str(parsed.get("intent") or "").strip().lower()
    answer_format = str(parsed.get("answer_format") or "").strip().lower()
    retrieval_policy = str(parsed.get("retrieval_policy") or "").strip().lower()
    if intent in _ALLOWED_INTENTS:
        query.intent = intent
    if answer_format in _ALLOWED_FORMATS:
        query.answer_format = answer_format
    if retrieval_policy in _ALLOWED_RETRIEVAL_POLICIES:
        query.retrieval_policy = retrieval_policy
        query.needs_retrieval = retrieval_policy != "none"
        query.requires_grounding = retrieval_policy == "required"
    if isinstance(parsed.get("is_follow_up"), bool):
        query.is_follow_up = bool(parsed["is_follow_up"]) and has_history
    if isinstance(parsed.get("topic_shift"), bool):
        query.topic_shift = bool(parsed["topic_shift"])
    try:
        query.confidence = max(0.0, min(1.0, float(parsed.get("confidence", query.confidence))))
    except Exception:
        pass
    teaching_strategy = str(parsed.get("teaching_strategy") or "").strip()
    if teaching_strategy:
        query.teaching_strategy = teaching_strategy[:240]

    query.reasoning_mode = (
        "source_grounded"
        if query.requires_grounding
        else "contextual_reasoning"
        if query.is_follow_up
        else "general_reasoning"
    )
    query.needs_memory = has_history or query.is_follow_up
    return query


def _block_kind(title: str, content: str) -> str:
    value = f"{title} {content}".lower()
    if any(term in value for term in ("formula", "equation", "calculate")):
        return "formula"
    if any(term in value for term in ("example", "worked")):
        return "example"
    if any(term in value for term in ("mistake", "trap", "avoid")):
        return "mistake"
    if any(term in value for term in ("check", "try this", "your turn", "question")):
        return "checkpoint"
    if any(term in value for term in ("remember", "summary", "recap", "key point")):
        return "recall"
    return "explanation"


def build_adaptive_answer_blocks(answer: str) -> List[Dict[str, str]]:
    """Turn a polished tutor answer into lightweight UI blocks without changing its text."""
    text = str(answer or "").strip()
    if not text:
        return []

    blocks: List[AdaptiveAnswerBlock] = []
    for raw_block in re.split(r"\n{2,}", text):
        lines = [line.strip() for line in raw_block.splitlines() if line.strip()]
        if not lines:
            continue
        heading = ""
        body = lines
        markdown_heading = re.match(r"^#{1,6}\s+(.+)$", lines[0])
        if markdown_heading:
            heading = markdown_heading.group(1).strip()
            body = lines[1:]
        elif lines[0].endswith(":") and len(lines[0]) <= 84:
            heading = lines[0][:-1].strip()
            body = lines[1:]
        content = "\n".join(body).strip()
        if not content and heading:
            content = heading
            heading = ""
        if not content:
            continue
        blocks.append(
            AdaptiveAnswerBlock(
                kind=_block_kind(heading, content),
                title=heading,
                content=content,
            )
        )

    if not blocks:
        blocks.append(AdaptiveAnswerBlock(kind="explanation", title="", content=text))
    return [block.to_dict() for block in blocks]


def semantic_event(event: str, **data: Any) -> str:
    return f"data: {json.dumps({'type': 'turn_event', 'event': event, **data}, ensure_ascii=False)}\n\n"


def parse_semantic_event(frame: str) -> Dict[str, Any]:
    payload = str(frame or "").strip()
    if payload.lower().startswith("data:"):
        payload = payload[5:].strip()
    if not payload.startswith("{"):
        return {}
    try:
        parsed = json.loads(payload)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}
