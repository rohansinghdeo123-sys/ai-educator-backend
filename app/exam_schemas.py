"""Pydantic request/response schemas for the exam-intelligence APIs.

Dynamic, model-generated structures (analysis blocks, probable questions, rubric
scores) are typed as ``Dict[str, Any]`` / ``List[Dict[str, Any]]`` so the response
envelope is documented in Swagger without FastAPI dropping nested keys.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# =========================================================
# PAPER UPLOAD / ANALYSIS
# =========================================================
class PaperOut(BaseModel):
    id: int
    class_level: str = ""
    subject: str = ""
    chapter_id: Optional[int] = None
    chapter_name: str = ""
    exam_type: str = ""
    paper_title: str = ""
    file_name: str = ""
    file_type: str = ""
    file_size: int = 0
    upload_status: str = ""
    parse_status: str = ""
    uploaded_at: Optional[datetime] = None
    parsed_at: Optional[datetime] = None
    extraction_confidence: float = 0.0
    extracted_question_count: int = 0
    warnings: List[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ExtractedQuestionOut(BaseModel):
    id: int
    paper_id: int
    question_number: str = ""
    section_name: str = ""
    question_text: str = ""
    marks: Optional[float] = None
    question_type: str = ""
    intent: str = ""
    difficulty: str = ""
    topic: str = ""
    concept_tags: List[str] = Field(default_factory=list)
    expected_answer_style: str = ""
    confidence_score: float = 0.0


class PaperUploadResponse(BaseModel):
    paper: PaperOut
    analysis: Dict[str, Any] = Field(default_factory=dict)
    questions_extracted: int = 0
    warnings: List[str] = Field(default_factory=list)
    message: str = ""


class PaperListResponse(BaseModel):
    total: int
    papers: List[PaperOut]


class PaperQuestionsResponse(BaseModel):
    paper_id: int
    count: int
    questions: List[ExtractedQuestionOut]


class PaperAnalysisResponse(BaseModel):
    paper_id: int
    parse_status: str = ""
    extraction_confidence: float = 0.0
    analysis: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class PaperReanalyzeRequest(BaseModel):
    """Optional overrides applied before re-running analysis on stored text."""
    class_level: Optional[str] = Field(default=None, max_length=40)
    subject: Optional[str] = Field(default=None, max_length=120)
    chapter_name: Optional[str] = Field(default=None, max_length=200)
    exam_type: Optional[str] = Field(default=None, max_length=40)


# =========================================================
# PATTERN INTELLIGENCE
# =========================================================
class PatternAnalysisOut(BaseModel):
    id: int
    class_level: str = ""
    subject: str = ""
    chapter_id: Optional[int] = None
    chapter_name: str = ""
    source_paper_ids: List[int] = Field(default_factory=list)
    total_questions: int = 0
    total_marks: Optional[float] = None
    marks_distribution: Dict[str, Any] = Field(default_factory=dict)
    question_type_distribution: Dict[str, Any] = Field(default_factory=dict)
    chapter_weightage: Dict[str, Any] = Field(default_factory=dict)
    topic_frequency: Dict[str, Any] = Field(default_factory=dict)
    repeated_concepts: List[Any] = Field(default_factory=list)
    difficulty_distribution: Dict[str, Any] = Field(default_factory=dict)
    pattern_summary: str = ""
    confidence_score: float = 0.0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PatternAnalyzeRequest(BaseModel):
    """Aggregate pattern intelligence across the caller's analyzed papers.

    Leave ``paper_ids`` null to use all analyzed papers (optionally narrowed by
    subject / chapter)."""
    paper_ids: Optional[List[int]] = None
    class_level: Optional[str] = Field(default=None, max_length=40)
    subject: Optional[str] = Field(default=None, max_length=120)
    chapter_name: Optional[str] = Field(default=None, max_length=200)


class PatternSummaryResponse(BaseModel):
    papers_total: int = 0
    papers_analyzed: int = 0
    subjects: List[str] = Field(default_factory=list)
    latest_analysis: Optional[PatternAnalysisOut] = None
    analyses: List[PatternAnalysisOut] = Field(default_factory=list)


class PatternGroupOut(BaseModel):
    key: str
    label: str = ""
    paper_count: int = 0
    total_questions: int = 0
    top_concepts: List[str] = Field(default_factory=list)
    marks_distribution: Dict[str, Any] = Field(default_factory=dict)
    question_type_distribution: Dict[str, Any] = Field(default_factory=dict)


class PatternGroupedResponse(BaseModel):
    grouped_by: str
    groups: List[PatternGroupOut]


# =========================================================
# PROBABLE QUESTIONS
# =========================================================
class ProbableQuestionSetOut(BaseModel):
    id: int
    class_level: str = ""
    subject: str = ""
    chapter_id: Optional[int] = None
    chapter_name: str = ""
    source_analysis_ids: List[int] = Field(default_factory=list)
    generation_mode: str = ""
    probable_questions: List[Dict[str, Any]] = Field(default_factory=list)
    priority_topics: List[Dict[str, Any]] = Field(default_factory=list)
    strategy_summary: str = ""
    disclaimer: str = ""
    confidence_score: float = 0.0
    created_at: Optional[datetime] = None


class ProbableListResponse(BaseModel):
    total: int
    sets: List[ProbableQuestionSetOut]


class ProbableGenerateRequest(BaseModel):
    """Generate probable questions from a stored pattern analysis, or freshly
    aggregate across the caller's analyzed papers."""
    analysis_id: Optional[int] = Field(default=None, ge=1)
    paper_ids: Optional[List[int]] = None
    class_level: Optional[str] = Field(default=None, max_length=40)
    subject: Optional[str] = Field(default=None, max_length=120)
    chapter_name: Optional[str] = Field(default=None, max_length=200)
    generation_mode: str = Field(default="mixed", max_length=40)
    count: int = Field(default=8, ge=1, le=20)
    use_syllabus_grounding: bool = True


# =========================================================
# WRITTEN ANSWER PRACTICE
# =========================================================
class WrittenStartRequest(BaseModel):
    class_level: Optional[str] = Field(default=None, max_length=40)
    subject: Optional[str] = Field(default=None, max_length=120)
    chapter_name: Optional[str] = Field(default=None, max_length=200)
    chapter_id: Optional[int] = Field(default=None, ge=1)
    topic: Optional[str] = Field(default=None, max_length=200)
    marks_focus: Optional[str] = Field(default=None, max_length=40)


class WrittenSessionOut(BaseModel):
    id: int
    class_level: str = ""
    subject: str = ""
    chapter_id: Optional[int] = None
    chapter_name: str = ""
    topic: str = ""
    marks_focus: str = ""
    session_status: str = ""
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    attempt_count: int = 0


class WrittenQuestionRequest(BaseModel):
    session_id: int = Field(ge=1)
    topic: Optional[str] = Field(default=None, max_length=200)
    marks_focus: Optional[str] = Field(default=None, max_length=40)
    question_type: Optional[str] = Field(default=None, max_length=40)
    use_syllabus_grounding: bool = True


class WrittenQuestionOut(BaseModel):
    """A generated question — expected marking points are intentionally hidden
    from the student until after they submit an answer."""
    attempt_id: int
    session_id: int
    question_text: str = ""
    question_type: str = ""
    marks_total: float = 0.0
    topic: str = ""
    command_word: str = ""
    evaluation_status: str = ""


class WrittenSubmitRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=20000)
    attempt_id: Optional[int] = Field(default=None, ge=1)
    # For ad-hoc submissions of a self-chosen question (no prior generate call):
    session_id: Optional[int] = Field(default=None, ge=1)
    question_text: Optional[str] = Field(default=None, max_length=4000)
    question_type: Optional[str] = Field(default=None, max_length=40)
    marks_total: Optional[float] = Field(default=None, ge=0, le=100)
    topic: Optional[str] = Field(default=None, max_length=200)
    expected_points: Optional[List[str]] = None
    use_syllabus_grounding: bool = True


class WrittenFeedbackOut(BaseModel):
    attempt_id: int
    question_text: str = ""
    question_type: str = ""
    student_answer: str = ""
    marks_awarded: float = 0.0
    marks_total: float = 0.0
    score_percentage: float = 0.0
    covered_points: List[str] = Field(default_factory=list)
    missing_points: List[str] = Field(default_factory=list)
    incorrect_points: List[str] = Field(default_factory=list)
    weak_explanation: List[str] = Field(default_factory=list)
    presentation_feedback: str = ""
    teacher_feedback: str = ""
    model_answer: str = ""
    improve_to_full_marks: str = ""
    rubric_scores: Dict[str, Any] = Field(default_factory=dict)
    next_question_suggestion: str = ""
    created_at: Optional[datetime] = None


class WrittenSubmitResponse(BaseModel):
    attempt_id: int
    feedback: WrittenFeedbackOut
    weaknesses_updated: int = 0


class WrittenAttemptSummary(BaseModel):
    id: int
    session_id: Optional[int] = None
    question_text: str = ""
    question_type: str = ""
    marks_total: float = 0.0
    marks_awarded: Optional[float] = None
    score_percentage: Optional[float] = None
    evaluation_status: str = ""
    topic: str = ""
    subject: str = ""
    submitted_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class WrittenHistoryResponse(BaseModel):
    total: int
    attempts: List[WrittenAttemptSummary]


class WrittenSessionDetailResponse(BaseModel):
    session: WrittenSessionOut
    attempts: List[WrittenAttemptSummary]


class WrittenSessionListResponse(BaseModel):
    total: int
    sessions: List[WrittenSessionOut]


# =========================================================
# STUDENT WEAKNESS REPORT
# =========================================================
class WeaknessOut(BaseModel):
    id: int
    class_level: str = ""
    subject: str = ""
    chapter_id: Optional[int] = None
    chapter_name: str = ""
    topic: str = ""
    weakness_type: str = ""
    weakness_summary: str = ""
    evidence: List[str] = Field(default_factory=list)
    frequency_count: int = 0
    last_seen_at: Optional[datetime] = None
    improvement_suggestion: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class WeaknessReportResponse(BaseModel):
    total: int
    weaknesses: List[WeaknessOut]


class WeaknessTopicGroup(BaseModel):
    topic: str
    subject: str = ""
    total_frequency: int = 0
    weakness_types: List[str] = Field(default_factory=list)
    latest_suggestion: str = ""


class WeaknessByTopicResponse(BaseModel):
    total_topics: int
    topics: List[WeaknessTopicGroup]
