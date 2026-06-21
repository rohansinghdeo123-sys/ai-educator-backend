"""Structured, deterministic prompts for the exam-intelligence agents.

Design rules baked into every prompt:
- JSON-only output, no markdown, so parsing is robust.
- Observed facts (question text, printed marks) are kept separate from inferred
  fields (topic, intent, difficulty), which carry a confidence.
- Probable questions are always framed as *most probable*, never guaranteed.
"""

from __future__ import annotations

from typing import Any, Dict, List

# Mandatory, reused everywhere probable questions are returned.
PROBABLE_DISCLAIMER = (
    "These are the most probable questions based on your uploaded papers, syllabus "
    "patterns, and repeated concepts. They are study guidance only and are not a "
    "prediction or guarantee of the actual questions in any exam."
)

# Canonical vocabularies the model is steered toward (free text still tolerated).
EXAM_TYPES = [
    "class_test", "unit_test", "school_exam", "pre_board",
    "board_exam", "chapter_wise", "subject_wise", "other",
]
QUESTION_INTENTS = [
    "definition", "explanation", "reasoning", "numerical", "derivation",
    "diagram", "difference", "short_note", "case_based", "assertion_reason",
    "application", "fill_in_blank", "true_false", "mcq", "other",
]
QUESTION_TYPES = [
    "very_short_answer", "short_answer", "long_answer", "numerical",
    "mcq", "case_based", "assertion_reason", "diagram", "other",
]


def _scope_line(class_level: str, subject: str, chapter_name: str) -> str:
    parts = []
    if class_level:
        parts.append(f"Class: {class_level}")
    if subject:
        parts.append(f"Subject: {subject}")
    if chapter_name:
        parts.append(f"Chapter/Topic: {chapter_name}")
    return " | ".join(parts) if parts else "Scope: not specified"


# ---------------------------------------------------------------------------
# ExamPatternAnalyzerAgent
# ---------------------------------------------------------------------------
def analyzer_messages(
    paper_text: str,
    *,
    class_level: str = "",
    subject: str = "",
    chapter_name: str = "",
    exam_type: str = "",
) -> List[Dict[str, str]]:
    system = (
        "You are an exam-paper analysis engine for school students. You read the raw "
        "text of one uploaded question paper and return STRICT JSON describing its "
        "structure and pattern. Use ONLY the supplied paper text. Never invent "
        "questions, marks, or sections that are not present. If a question's marks are "
        "not printed, set marks to null — do not guess a number. Separate observed "
        "facts (question_text, printed marks, section labels) from your inferences "
        "(topic, intent, difficulty), and give every inference a confidence in [0,1]. "
        "Return ONLY a JSON object, no markdown, no commentary."
    )
    shape = (
        '{\n'
        '  "paper_title": "string (best guess or empty)",\n'
        '  "exam_type": "one of ' + ", ".join(EXAM_TYPES) + '",\n'
        '  "questions": [\n'
        '    {\n'
        '      "question_number": "string e.g. 1 or 1(a)",\n'
        '      "section_name": "string e.g. A (empty if none)",\n'
        '      "question_text": "verbatim question text",\n'
        '      "marks": number or null,\n'
        '      "question_type": "one of ' + ", ".join(QUESTION_TYPES) + '",\n'
        '      "intent": "one of ' + ", ".join(QUESTION_INTENTS) + '",\n'
        '      "difficulty": "easy | medium | hard",\n'
        '      "topic": "short topic/concept label",\n'
        '      "concept_tags": ["concept", "..."],\n'
        '      "expected_answer_style": "short phrase e.g. 2-3 sentences, stepwise numerical",\n'
        '      "confidence": 0.0\n'
        '    }\n'
        '  ],\n'
        '  "analysis": {\n'
        '    "total_questions": 0,\n'
        '    "total_marks": number or null,\n'
        '    "section_breakdown": {"A": {"questions": 0, "marks": 0}},\n'
        '    "marks_distribution": {"1": 0, "2": 0, "3": 0, "5": 0},\n'
        '    "question_type_distribution": {"short_answer": 0},\n'
        '    "difficulty_distribution": {"easy": 0, "medium": 0, "hard": 0},\n'
        '    "topic_frequency": {"topic": 0},\n'
        '    "repeated_concepts": ["concept appearing more than once"],\n'
        '    "high_frequency_concepts": ["most asked concepts"],\n'
        '    "chapter_weightage": {"chapter or topic": "share or marks"},\n'
        '    "short_vs_long": {"short_answer": 0, "long_answer": 0},\n'
        '    "pattern_style": "board_style | school_style | mixed",\n'
        '    "pattern_summary": "2-4 sentence plain-English summary of the paper pattern",\n'
        '    "warnings": ["anything unclear or partially extracted"]\n'
        '  },\n'
        '  "confidence": 0.0\n'
        '}'
    )
    user = (
        f"{_scope_line(class_level, subject, chapter_name)}\n"
        f"User-declared exam type (may be wrong, verify against text): {exam_type or 'unknown'}\n\n"
        "Analyze the following question paper text and return the JSON object exactly "
        "in this shape:\n\n"
        f"{shape}\n\n"
        "=== QUESTION PAPER TEXT START ===\n"
        f"{paper_text}\n"
        "=== QUESTION PAPER TEXT END ==="
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ---------------------------------------------------------------------------
# ProbableQuestionAgent
# ---------------------------------------------------------------------------
def probable_messages(
    *,
    analysis_payload: Dict[str, Any],
    class_level: str = "",
    subject: str = "",
    chapter_name: str = "",
    generation_mode: str = "mixed",
    count: int = 8,
    augment_context: str = "",
) -> List[Dict[str, str]]:
    import json

    system = (
        "You are a study-strategy engine that proposes the MOST PROBABLE practice "
        "questions for a school student, derived from the observed pattern of their "
        "uploaded papers plus syllabus/chapter content. You must NEVER claim a question "
        "is guaranteed or 'will appear'. Always frame output as most-probable practice "
        "based on repeated concepts, marks patterns, and syllabus weightage. Do not "
        "fabricate facts about the syllabus. Return ONLY a JSON object, no markdown."
    )
    mode_hint = {
        "chapter_wise": "Group/skew questions by chapter and high-weightage topics.",
        "marks_wise": "Spread questions across the observed marks buckets (1/2/3/5 etc.).",
        "section_wise": "Mirror the observed section structure of the papers.",
        "mixed": "Balance across chapters, marks, and sections.",
    }.get(generation_mode, "Balance across chapters, marks, and sections.")
    shape = (
        '{\n'
        '  "probable_questions": [\n'
        '    {\n'
        '      "id": "P1",\n'
        '      "question": "question text",\n'
        '      "marks": number or null,\n'
        '      "question_type": "short_answer | long_answer | numerical | ...",\n'
        '      "intent": "definition | explanation | numerical | ...",\n'
        '      "topic": "topic label",\n'
        '      "priority": "high | medium | low",\n'
        '      "based_on": "why it is probable (repeated concept / marks pattern / weightage)"\n'
        '    }\n'
        '  ],\n'
        '  "priority_topics": [\n'
        '    {"topic": "topic", "reason": "why high priority", "weight": "high | medium | low"}\n'
        '  ],\n'
        '  "strategy_summary": "3-5 sentence exam-prep + revision priority strategy",\n'
        '  "confidence": 0.0\n'
        '}'
    )
    reference = f"\n\n=== REFERENCE SYLLABUS CONTENT ===\n{augment_context}\n=== END REFERENCE ===" if augment_context else ""
    user = (
        f"{_scope_line(class_level, subject, chapter_name)}\n"
        f"Generation mode: {generation_mode} — {mode_hint}\n"
        f"Generate up to {count} probable questions.\n\n"
        "Observed pattern analysis (JSON) to base your reasoning on:\n"
        f"{json.dumps(analysis_payload, ensure_ascii=False)[:9000]}"
        f"{reference}\n\n"
        f"Return ONLY this JSON shape:\n{shape}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ---------------------------------------------------------------------------
# Written practice: question generation
# ---------------------------------------------------------------------------
def written_question_messages(
    *,
    class_level: str = "",
    subject: str = "",
    chapter_name: str = "",
    topic: str = "",
    marks_focus: str = "",
    question_type: str = "",
    augment_context: str = "",
) -> List[Dict[str, str]]:
    system = (
        "You are a school teacher setting ONE descriptive (non-MCQ) practice question "
        "for a student to write a full answer to. Choose marks appropriate to the "
        "requested focus. Provide the model's expected key points (the marking scheme) "
        "so the answer can later be graded. Return ONLY a JSON object, no markdown."
    )
    shape = (
        '{\n'
        '  "question_text": "the question to answer",\n'
        '  "question_type": "definition | short_answer | long_answer | numerical | derivation | difference | short_note | case_based | ...",\n'
        '  "marks_total": number,\n'
        '  "topic": "topic label",\n'
        '  "command_word": "define | explain | derive | compare | ...",\n'
        '  "expected_points": ["key point worth marks", "..."]\n'
        '}'
    )
    reference = f"\n\n=== REFERENCE CONTENT ===\n{augment_context}\n=== END REFERENCE ===" if augment_context else ""
    user = (
        f"{_scope_line(class_level, subject, chapter_name)}\n"
        f"Topic focus: {topic or chapter_name or subject or 'any from the chapter'}\n"
        f"Marks focus: {marks_focus or 'teacher choice'}\n"
        f"Preferred question type: {question_type or 'teacher choice'}\n"
        f"{reference}\n\n"
        f"Return ONLY this JSON shape:\n{shape}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ---------------------------------------------------------------------------
# WrittenAnswerEvaluatorAgent + TeacherFeedbackAgent (single structured call)
# ---------------------------------------------------------------------------
def written_eval_messages(
    *,
    question_text: str,
    question_type: str,
    marks_total: float,
    student_answer: str,
    expected_points: List[str],
    class_level: str = "",
    subject: str = "",
    chapter_name: str = "",
    augment_context: str = "",
) -> List[Dict[str, str]]:
    import json

    system = (
        "You are a strict but supportive school teacher grading a student's written "
        "answer like a real exam. Grade ONLY against the question and the expected "
        "marking points; do not penalize correct extra detail. Award fair partial "
        "marks. Be specific about what was missing and how to reach full marks. Never "
        "exceed marks_total and never award negative marks. Return ONLY a JSON object, "
        "no markdown."
    )
    shape = (
        '{\n'
        '  "marks_awarded": number,\n'
        '  "marks_total": number,\n'
        '  "covered_points": ["expected points the student covered"],\n'
        '  "missing_points": ["expected points the student missed"],\n'
        '  "incorrect_points": ["statements that were wrong or misleading"],\n'
        '  "weak_explanation_areas": ["areas explained unclearly or incompletely"],\n'
        '  "presentation_feedback": "feedback on structure, steps, units, diagram, neatness",\n'
        '  "teacher_feedback": "2-5 sentence overall teacher comment, strict but encouraging",\n'
        '  "model_answer": "a concise full-marks model answer",\n'
        '  "improve_to_full_marks": "specific, actionable steps to reach full marks",\n'
        '  "rubric_scores": {\n'
        '    "concept_accuracy": 0.0, "key_points_covered": 0.0, "completeness": 0.0,\n'
        '    "formula_keyword_usage": 0.0, "step_logic": 0.0, "explanation_clarity": 0.0,\n'
        '    "exam_presentation": 0.0\n'
        '  },\n'
        '  "next_question_suggestion": "a follow-up practice question to fix the gaps",\n'
        '  "weakness_tags": [\n'
        '    {"topic": "topic", "weakness_type": "concept_gap | missing_key_points | incomplete | presentation | formula | step_logic | clarity", "note": "short evidence"}\n'
        '  ]\n'
        '}'
    )
    reference = f"\n\n=== REFERENCE CONTENT (for fact-checking) ===\n{augment_context}\n=== END REFERENCE ===" if augment_context else ""
    user = (
        f"{_scope_line(class_level, subject, chapter_name)}\n"
        f"Question type: {question_type or 'descriptive'}\n"
        f"Total marks: {marks_total}\n\n"
        f"QUESTION:\n{question_text}\n\n"
        "EXPECTED MARKING POINTS (marking scheme):\n"
        f"{json.dumps(list(expected_points or []), ensure_ascii=False)[:4000]}\n\n"
        "STUDENT ANSWER:\n"
        f"{student_answer}\n"
        f"{reference}\n\n"
        f"Grade it and return ONLY this JSON shape:\n{shape}\n"
        f"rubric_scores values are fractions in [0,1]. marks_awarded must be between 0 and {marks_total}."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
