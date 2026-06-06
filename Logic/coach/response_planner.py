"""Response planning for dynamic, student-friendly Study Lab answers."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Literal, Optional

from pydantic import BaseModel, Field


AnswerLength = Literal["one_line", "short", "medium", "detailed", "long"]
FormatStyle = Literal[
    "plain",
    "bullets",
    "numbered_steps",
    "table",
    "notes",
    "exam_answer",
    "derivation",
    "code",
    "quiz",
    "flowchart",
]
Tone = Literal["simple", "friendly", "exam_focused", "professional", "deep_teaching"]
StudentLevel = Literal["beginner", "intermediate", "advanced"]
PlannerMode = Literal[
    "doubt",
    "revision",
    "exam",
    "practice",
    "upload_explanation",
    "coding_help",
    "concept_teaching",
]


class ResponsePlannerOutput(BaseModel):
    answer_length: AnswerLength = "medium"
    format_style: FormatStyle = "plain"
    tone: Tone = "friendly"
    student_level: StudentLevel = "intermediate"
    mode: PlannerMode = "concept_teaching"
    use_rag: bool = False
    grounding_required: bool = False
    include_examples: bool = True
    include_formula: bool = False
    include_code: bool = False
    include_summary: bool = False
    ask_follow_up: bool = True
    source_scope: str = "general"
    special_instruction: str = Field(default="", max_length=900)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


def _contains_any(value: str, terms: Iterable[str]) -> bool:
    return any(term in value for term in terms)


def _selected_source_scope(scope: Dict[str, Any], attachments: Iterable[Dict[str, Any]] = ()) -> str:
    attachments = list(attachments or [])
    if attachments:
        return "uploaded_material"
    for key, label in (("section_id", "selected_section"), ("topic", "selected_topic"), ("chapter", "selected_chapter")):
        value = str((scope or {}).get(key) or "").strip()
        if value and value.lower() not in {"general", "open", "any", "all", "open_tutor_topic"}:
            return label
    return "general"


def _student_level(adaptive_context: Optional[Dict[str, Any]], question: str) -> StudentLevel:
    adaptive_context = adaptive_context or {}
    student_state = adaptive_context.get("student_state") if isinstance(adaptive_context.get("student_state"), dict) else {}
    raw = str(
        student_state.get("knowledge_level")
        or student_state.get("level")
        or student_state.get("student_level")
        or ""
    ).lower()
    q = question.lower()
    if "beginner" in raw or "basic" in raw or _contains_any(q, ("beginner", "from zero", "simple words", "simple english")):
        return "beginner"
    if "advanced" in raw or _contains_any(q, ("advanced", "deeply", "technical terms", "interview answer")):
        return "advanced"
    return "intermediate"


def _language_instruction(normalized: str) -> str:
    if _contains_any(normalized, ("in hinglish", "hinglish", "simple words me", "samjhao")):
        return "Respond in natural Hinglish if the app language policy allows it; keep technical terms clear."
    if _contains_any(normalized, ("in hindi", "hindi me", "hindi mein")):
        return "Respond in Hindi if the app language policy allows it; keep subject terms understandable."
    return ""


def _source_requested(normalized: str) -> bool:
    return _contains_any(
        normalized,
        (
            "use my notes",
            "from my notes",
            "notes only",
            "from uploaded",
            "from this pdf",
            "from pdf",
            "chapter only",
            "from chapter",
            "from this chapter",
            "this chapter",
            "selected chapter",
            "selected source",
            "selected material",
            "study material only",
            "according to my notes",
            "is this in my notes",
        ),
    )


def build_response_plan(
    *,
    question: str,
    query: Any,
    answer_format: Dict[str, Any],
    mode: str = "coach",
    retrieval_policy: str = "none",
    selected_scope: Optional[Dict[str, Any]] = None,
    attachments: Iterable[Dict[str, Any]] = (),
    adaptive_context: Optional[Dict[str, Any]] = None,
    conversation_context: Optional[Dict[str, Any]] = None,
) -> ResponsePlannerOutput:
    """Choose the answer shape before the tutor writes the response."""
    selected_scope = selected_scope or {}
    adaptive_context = adaptive_context or {}
    conversation_context = conversation_context or {}
    attachments = list(attachments or [])
    normalized = re.sub(r"\s+", " ", (question or "").strip().lower())
    intent = str(getattr(query, "intent", "") or "").lower()
    format_id = str((answer_format or {}).get("id") or getattr(query, "answer_format", "") or "concept")
    source_scope = _selected_source_scope(selected_scope, attachments)
    has_image = any(
        str(item.get("mime_type") or item.get("type") or "").lower().startswith("image/")
        for item in attachments
        if isinstance(item, dict)
    )
    has_upload = bool(attachments)
    is_follow_up = bool(getattr(query, "is_follow_up", False) or conversation_context.get("is_follow_up"))
    level = _student_level(adaptive_context, normalized)

    answer_length: AnswerLength = "medium"
    format_style: FormatStyle = "plain"
    tone: Tone = "friendly"
    planner_mode: PlannerMode = "concept_teaching"
    include_examples = True
    include_formula = False
    include_code = False
    include_summary = False
    ask_follow_up = not bool(getattr(query, "is_conversational", False))
    special: list[str] = []

    if _contains_any(
        normalized,
        ("answer only", "only answer", "final answer only", "only final value", "syntax only", "one line", "one-line"),
    ):
        answer_length = "one_line"
        format_style = "code" if "syntax" in normalized else "plain"
        include_examples = False
        include_summary = False
        ask_follow_up = False
        special.append("Return only what the student asked for. Do not add explanation, examples, summary, or follow-up.")
    elif _contains_any(normalized, ("means?", "meaning?", "only definition", "definition only", "make it short", "in short", "hint only")):
        answer_length = "short"
        format_style = "plain"
        include_examples = False
        ask_follow_up = False
        special.append("Keep it short and crisp. Avoid heavy formatting.")
        if "hint only" in normalized:
            special.append("Give a hint only. Do not reveal the full solution.")

    if _contains_any(normalized, ("deeply", "detailed", "detail", "from zero", "teach me this topic", "make it detailed")):
        answer_length = "long" if _contains_any(normalized, ("from zero", "teach me this topic")) else "detailed"
        tone = "deep_teaching"
        include_examples = True
        include_summary = True
        special.append("Build depth gradually and include the important reasoning, not just facts.")

    if intent == "exam" or _contains_any(normalized, ("marks answer", "5 marks", "board exam", "final exam answer", "competitive exam")):
        planner_mode = "exam"
        format_style = "exam_answer"
        tone = "exam_focused"
        answer_length = "medium" if answer_length in {"one_line", "short"} else answer_length
        include_formula = include_formula or _contains_any(normalized, ("formula", "equation", "physics", "chemistry"))
        include_summary = True
        special.append("Use marks-oriented structure: definition, key points/process, formula/equation if needed, and conclusion.")

    if intent == "revision" or _contains_any(normalized, ("revision notes", "key points", "summary", "summarize", "quick notes", "formula list")):
        planner_mode = "revision"
        format_style = "notes" if not _contains_any(normalized, ("key points", "points")) else "bullets"
        answer_length = "short" if _contains_any(normalized, ("quick", "short")) else "medium"
        include_formula = include_formula or "formula list" in normalized
        include_summary = True
        ask_follow_up = False

    if intent == "practice" or _contains_any(normalized, ("mcq", "quiz", "test me", "ask me questions", "practice questions", "make me practice")):
        planner_mode = "practice"
        format_style = "quiz"
        answer_length = "medium"
        include_examples = False
        ask_follow_up = True
        special.append("For interactive practice, ask one question at a time unless the student explicitly asks for a full set.")

    if _contains_any(normalized, ("compare", "difference between", "differentiate", " vs ", "versus")):
        format_style = "bullets" if _contains_any(normalized, ("no table", "without table")) else "table"
        special.append("Use direct comparison. If table is disallowed, use clean bullets.")

    if _contains_any(normalized, ("step by step", "solve", "numerical", "calculate", "show calculation", "now solve it")):
        planner_mode = "doubt"
        format_style = "numbered_steps"
        include_formula = True
        answer_length = "medium" if answer_length in {"one_line", "short"} and not _contains_any(normalized, ("answer only", "only final")) else answer_length
        special.append("For numericals, use formula -> substitution -> calculation -> final answer.")

    if _contains_any(normalized, ("formula", "equation", "theorem statement", "reaction list", "give reactions")):
        include_formula = True

    if _contains_any(normalized, ("derive", "derivation", "prove", "proof")):
        format_style = "derivation"
        include_formula = True
        answer_length = "detailed"
        tone = "deep_teaching"
    if _contains_any(normalized, ("no derivation", "just formula")):
        format_style = "plain"
        answer_length = "short"
        include_formula = True
        include_examples = False
        special.append("Do not derive. Give the formula and direct application only.")

    if _contains_any(normalized, ("flowchart", "diagram", "with diagram")):
        format_style = "flowchart"
        include_summary = False
        special.append("Use a text flowchart or labelled diagram description if no visual generation is available.")

    if _contains_any(normalized, ("python", "code", "syntax", "fix this code", "output", "full code", "no comments in code")):
        planner_mode = "coding_help"
        include_code = True
        format_style = "code" if _contains_any(normalized, ("syntax only", "full code", "fix this code")) else "numbered_steps"
        tone = "professional" if "interview" in normalized else tone
        if "no comments in code" in normalized:
            special.append("Return clean code without comments.")
        if "full code" in normalized:
            answer_length = "detailed"
            special.append("Provide complete runnable code.")
        if "explain this" in normalized or "line by line" in normalized:
            special.append("Explain the code line by line.")

    if has_image:
        planner_mode = "upload_explanation"
        format_style = "numbered_steps"
        special.append("Use vision extraction first. If the image is unclear, ask for a clearer upload instead of guessing.")
    elif has_upload:
        planner_mode = "upload_explanation"

    if intent == "clarification" or _contains_any(normalized, ("i did not understand", "don't understand", "confused", "explain again", "simpler")):
        tone = "simple"
        level = "beginner"
        answer_length = "medium" if answer_length == "long" else answer_length
        include_examples = True
        special.append("Re-explain in easier words with a simple analogy. Do not switch topics.")

    if _contains_any(normalized, ("technical terms", "advanced", "interview answer")):
        tone = "professional" if "interview" in normalized else "deep_teaching"
        level = "advanced"
    if _contains_any(normalized, ("simple english", "don't use difficult words", "easy words")):
        tone = "simple"
        level = "beginner"

    language_instruction = _language_instruction(normalized)
    if language_instruction:
        special.append(language_instruction)

    grounding_required = retrieval_policy == "required" or _source_requested(normalized)
    source_selected = source_scope != "general"
    if source_selected and planner_mode in {"exam", "practice", "revision", "upload_explanation"}:
        grounding_required = True
    use_rag = retrieval_policy != "none" or grounding_required or (
        source_selected and planner_mode in {"exam", "practice", "revision", "upload_explanation"}
    )
    if grounding_required:
        special.append("Use only retrieved or uploaded study material. If support is weak, say there is not enough information in the selected material.")

    if planner_mode == "concept_teaching" and intent in {"doubt", "concept", "definition", "comparison", "numerical", "clarification"}:
        planner_mode = "doubt" if intent in {"numerical", "clarification"} else "concept_teaching"
    if getattr(query, "is_conversational", False):
        answer_length = "short"
        format_style = "plain"
        tone = "friendly"
        planner_mode = "doubt"
        include_examples = False
        include_summary = False
        ask_follow_up = False

    if format_id == "definition" and answer_length == "medium" and _contains_any(normalized, ("means", "meaning", "define")):
        answer_length = "short" if normalized.endswith("?") and len(normalized.split()) <= 4 else answer_length
    if format_id == "comparison" and format_style == "plain":
        format_style = "table"
    if format_id == "quiz":
        format_style = "quiz"
        planner_mode = "practice"

    return ResponsePlannerOutput(
        answer_length=answer_length,
        format_style=format_style,
        tone=tone,
        student_level=level,
        mode=planner_mode,
        use_rag=use_rag,
        grounding_required=grounding_required,
        include_examples=include_examples,
        include_formula=include_formula,
        include_code=include_code,
        include_summary=include_summary,
        ask_follow_up=ask_follow_up,
        source_scope=source_scope,
        special_instruction=" ".join(dict.fromkeys(item for item in special if item)).strip(),
    )


def build_response_plan_instruction(plan: ResponsePlannerOutput | Dict[str, Any] | None) -> str:
    if plan is None:
        return "RESPONSE PLANNER: No explicit planner output supplied. Choose the shortest useful student-friendly answer."
    payload = plan.to_dict() if hasattr(plan, "to_dict") else dict(plan or {})
    return f"""
RESPONSE PLANNER OUTPUT - FOLLOW STRICTLY:
- answer_length: {payload.get("answer_length")}
- format_style: {payload.get("format_style")}
- tone: {payload.get("tone")}
- student_level: {payload.get("student_level")}
- mode: {payload.get("mode")}
- use_rag: {payload.get("use_rag")}
- grounding_required: {payload.get("grounding_required")}
- include_examples: {payload.get("include_examples")}
- include_formula: {payload.get("include_formula")}
- include_code: {payload.get("include_code")}
- include_summary: {payload.get("include_summary")}
- ask_follow_up: {payload.get("ask_follow_up")}
- source_scope: {payload.get("source_scope")}
- special_instruction: {payload.get("special_instruction") or "None"}

Planner compliance rules:
- If answer_length is one_line or short, be concise and avoid unnecessary headings.
- If format_style is table, use a compact markdown table. If format_style is not table, do not use a table.
- If format_style is numbered_steps, show the reasoning in ordered steps.
- If format_style is exam_answer, write marks-ready answer structure.
- If format_style is code, provide code only when include_code is true and respect code-specific instructions.
- If format_style is quiz, make the response interactive when ask_follow_up is true.
- If grounding_required is true, use only retrieved/uploaded/selected material and say when support is missing.
- Do not add examples, formulas, code, summaries, or follow-up questions when the planner says false.
""".strip()
