"""Service layer for exam pattern intelligence and probable-question generation.

Aggregation is deterministic (the per-paper intelligence already came from the
analyzer); the only LLM call here is probable-question generation, which always
carries the non-guarantee disclaimer.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from Logic.exam import agents
from models import ExamPatternAnalysis, ProbableQuestionSet, UploadedExamPaper

logger = logging.getLogger("ai_educator.services.exam_pattern")

ANALYZED_STATUSES = {"analyzed", "analyzed_empty"}


class PatternError(ValueError):
    """Raised when there is nothing analyzable to aggregate."""


# ---------------------------------------------------------------------------
# Paper selection
# ---------------------------------------------------------------------------
def _analyzed_papers(
    db: Session,
    user_id: str,
    *,
    paper_ids: Optional[List[int]] = None,
    subject: Optional[str] = None,
    chapter_name: Optional[str] = None,
) -> List[UploadedExamPaper]:
    query = db.query(UploadedExamPaper).filter(
        UploadedExamPaper.user_id == user_id,
        UploadedExamPaper.parse_status.in_(ANALYZED_STATUSES),
    )
    if paper_ids:
        query = query.filter(UploadedExamPaper.id.in_(paper_ids))
    if subject:
        query = query.filter(UploadedExamPaper.subject == subject)
    if chapter_name:
        query = query.filter(UploadedExamPaper.chapter_name == chapter_name)
    papers = query.order_by(UploadedExamPaper.id.desc()).all()
    # Only keep papers that actually produced structured questions.
    return [p for p in papers if (p.analysis_json or {}).get("total_questions")]


def _paper_to_analysis_payload(paper: UploadedExamPaper) -> Dict[str, Any]:
    # aggregate_analyses reads {"analysis": {...}, "questions": [...], "confidence": x}
    questions = [{} for _ in range(int((paper.analysis_json or {}).get("total_questions") or 0))]
    return {
        "analysis": paper.analysis_json or {},
        "questions": questions,
        "confidence": paper.extraction_confidence or 0.0,
    }


# ---------------------------------------------------------------------------
# Pattern analysis (aggregate + persist)
# ---------------------------------------------------------------------------
def run_pattern_analysis(
    db: Session,
    user_id: str,
    *,
    paper_ids: Optional[List[int]] = None,
    class_level: Optional[str] = None,
    subject: Optional[str] = None,
    chapter_name: Optional[str] = None,
) -> ExamPatternAnalysis:
    papers = _analyzed_papers(db, user_id, paper_ids=paper_ids, subject=subject, chapter_name=chapter_name)
    if not papers:
        raise PatternError(
            "No analyzed papers found to build a pattern from. Upload and analyze at "
            "least one paper first."
        )

    analyses = [_paper_to_analysis_payload(p) for p in papers]
    meta = [{"chapter_name": p.chapter_name, "subject": p.subject} for p in papers]
    agg = agents.aggregate_analyses(analyses, papers_meta=meta)

    resolved_subject = subject or _most_common([p.subject for p in papers])
    resolved_class = class_level or _most_common([p.class_level for p in papers])
    resolved_chapter = chapter_name or _most_common([p.chapter_name for p in papers])

    row = ExamPatternAnalysis(
        user_id=user_id,
        class_level=resolved_class,
        subject=resolved_subject,
        chapter_name=resolved_chapter,
        source_paper_ids_json=[p.id for p in papers],
        total_questions=agg["total_questions"],
        total_marks=agg["total_marks"],
        marks_distribution_json=agg["marks_distribution"],
        question_type_distribution_json=agg["question_type_distribution"],
        chapter_weightage_json=agg["chapter_weightage"],
        topic_frequency_json=agg["topic_frequency"],
        repeated_concepts_json=agg["repeated_concepts"],
        difficulty_distribution_json=agg["difficulty_distribution"],
        pattern_summary=agg["pattern_summary"],
        confidence_score=agg["confidence_score"],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _most_common(values: List[str]) -> str:
    counter = Counter(v for v in values if v)
    return counter.most_common(1)[0][0] if counter else ""


def get_owned_pattern(db: Session, user_id: str, analysis_id: int) -> Optional[ExamPatternAnalysis]:
    return (
        db.query(ExamPatternAnalysis)
        .filter(ExamPatternAnalysis.id == analysis_id, ExamPatternAnalysis.user_id == user_id)
        .first()
    )


# ---------------------------------------------------------------------------
# Summary / grouped views
# ---------------------------------------------------------------------------
def pattern_summary(db: Session, user_id: str) -> Dict[str, Any]:
    papers = db.query(UploadedExamPaper).filter(UploadedExamPaper.user_id == user_id).all()
    analyzed = [p for p in papers if p.parse_status in ANALYZED_STATUSES]
    subjects = sorted({p.subject for p in papers if p.subject})
    analyses = (
        db.query(ExamPatternAnalysis)
        .filter(ExamPatternAnalysis.user_id == user_id)
        .order_by(ExamPatternAnalysis.id.desc())
        .limit(10)
        .all()
    )
    serialized = [serialize_pattern(row) for row in analyses]
    return {
        "papers_total": len(papers),
        "papers_analyzed": len(analyzed),
        "subjects": subjects,
        "latest_analysis": serialized[0] if serialized else None,
        "analyses": serialized,
    }


def pattern_grouped(
    db: Session,
    user_id: str,
    *,
    group_by: str,
    subject: Optional[str] = None,
) -> Dict[str, Any]:
    papers = _analyzed_papers(db, user_id, subject=subject)
    groups: Dict[str, Dict[str, Any]] = {}
    for paper in papers:
        analysis = paper.analysis_json or {}
        if group_by == "chapter":
            key = (paper.chapter_name or "Unspecified chapter").strip() or "Unspecified chapter"
        else:
            key = (paper.subject or "Unspecified subject").strip() or "Unspecified subject"
        bucket = groups.setdefault(
            key,
            {
                "key": key,
                "label": key,
                "paper_count": 0,
                "total_questions": 0,
                "_topics": Counter(),
                "marks_distribution": Counter(),
                "question_type_distribution": Counter(),
            },
        )
        bucket["paper_count"] += 1
        bucket["total_questions"] += int(analysis.get("total_questions") or 0)
        for topic, freq in (analysis.get("topic_frequency") or {}).items():
            bucket["_topics"][str(topic)] += int(freq or 0)
        for marks, freq in (analysis.get("marks_distribution") or {}).items():
            bucket["marks_distribution"][str(marks)] += int(freq or 0)
        for qtype, freq in (analysis.get("question_type_distribution") or {}).items():
            bucket["question_type_distribution"][str(qtype)] += int(freq or 0)

    out_groups = []
    for bucket in groups.values():
        out_groups.append(
            {
                "key": bucket["key"],
                "label": bucket["label"],
                "paper_count": bucket["paper_count"],
                "total_questions": bucket["total_questions"],
                "top_concepts": [t for t, _ in bucket["_topics"].most_common(6)],
                "marks_distribution": dict(bucket["marks_distribution"]),
                "question_type_distribution": dict(bucket["question_type_distribution"]),
            }
        )
    out_groups.sort(key=lambda g: g["total_questions"], reverse=True)
    return {"grouped_by": group_by, "groups": out_groups}


# ---------------------------------------------------------------------------
# Probable question generation
# ---------------------------------------------------------------------------
def _payload_from_pattern(row: ExamPatternAnalysis) -> Dict[str, Any]:
    topic_freq = row.topic_frequency_json or {}
    high_freq = [t for t, _ in Counter(topic_freq).most_common(8)]
    return {
        "total_questions": row.total_questions,
        "total_marks": row.total_marks,
        "marks_distribution": row.marks_distribution_json or {},
        "question_type_distribution": row.question_type_distribution_json or {},
        "difficulty_distribution": row.difficulty_distribution_json or {},
        "topic_frequency": topic_freq,
        "repeated_concepts": row.repeated_concepts_json or [],
        "high_frequency_concepts": high_freq,
        "chapter_weightage": row.chapter_weightage_json or {},
        "pattern_summary": row.pattern_summary or "",
    }


def generate_probable(
    db: Session,
    user_id: str,
    *,
    analysis_id: Optional[int] = None,
    paper_ids: Optional[List[int]] = None,
    class_level: Optional[str] = None,
    subject: Optional[str] = None,
    chapter_name: Optional[str] = None,
    generation_mode: str = "mixed",
    count: int = 8,
    use_syllabus_grounding: bool = True,
) -> ProbableQuestionSet:
    source_analysis_ids: List[int] = []

    if analysis_id is not None:
        pattern = get_owned_pattern(db, user_id, analysis_id)
        if pattern is None:
            raise PatternError("Pattern analysis not found.")
        payload = _payload_from_pattern(pattern)
        source_analysis_ids = [pattern.id]
        resolved_class = class_level or pattern.class_level
        resolved_subject = subject or pattern.subject
        resolved_chapter = chapter_name or pattern.chapter_name
    else:
        papers = _analyzed_papers(db, user_id, paper_ids=paper_ids, subject=subject, chapter_name=chapter_name)
        if not papers:
            raise PatternError(
                "No analyzed papers found. Upload and analyze at least one paper, or "
                "pass an analysis_id."
            )
        analyses = [_paper_to_analysis_payload(p) for p in papers]
        meta = [{"chapter_name": p.chapter_name, "subject": p.subject} for p in papers]
        payload = agents.aggregate_analyses(analyses, papers_meta=meta)
        resolved_class = class_level or _most_common([p.class_level for p in papers])
        resolved_subject = subject or _most_common([p.subject for p in papers])
        resolved_chapter = chapter_name or _most_common([p.chapter_name for p in papers])

    augment_context = ""
    if use_syllabus_grounding:
        top_concepts = " ".join((payload.get("high_frequency_concepts") or [])[:5])
        augment_context = agents.build_reference_context(
            section_id=resolved_chapter or resolved_subject or "general",
            query=top_concepts or resolved_chapter or resolved_subject,
            subject=resolved_subject,
            chapter=resolved_chapter,
        )

    result = agents.generate_probable_questions(
        analysis_payload=payload,
        class_level=resolved_class,
        subject=resolved_subject,
        chapter_name=resolved_chapter,
        generation_mode=generation_mode,
        count=count,
        augment_context=augment_context,
    )

    row = ProbableQuestionSet(
        user_id=user_id,
        class_level=resolved_class,
        subject=resolved_subject,
        chapter_name=resolved_chapter,
        source_analysis_ids_json=source_analysis_ids,
        generation_mode=generation_mode,
        probable_questions_json=result["probable_questions"],
        priority_topics_json=result["priority_topics"],
        strategy_summary=result["strategy_summary"],
        disclaimer=result["disclaimer"],
        confidence_score=result["confidence"],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_owned_probable(db: Session, user_id: str, set_id: int) -> Optional[ProbableQuestionSet]:
    return (
        db.query(ProbableQuestionSet)
        .filter(ProbableQuestionSet.id == set_id, ProbableQuestionSet.user_id == user_id)
        .first()
    )


def list_probable(db: Session, user_id: str, *, limit: int = 50, offset: int = 0) -> tuple[int, List[ProbableQuestionSet]]:
    query = db.query(ProbableQuestionSet).filter(ProbableQuestionSet.user_id == user_id)
    total = query.count()
    rows = (
        query.order_by(ProbableQuestionSet.id.desc())
        .offset(max(0, offset))
        .limit(max(1, min(limit, 200)))
        .all()
    )
    return total, rows


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------
def serialize_pattern(row: ExamPatternAnalysis) -> Dict[str, Any]:
    return {
        "id": row.id,
        "class_level": row.class_level or "",
        "subject": row.subject or "",
        "chapter_id": row.chapter_id,
        "chapter_name": row.chapter_name or "",
        "source_paper_ids": list(row.source_paper_ids_json or []),
        "total_questions": row.total_questions or 0,
        "total_marks": row.total_marks,
        "marks_distribution": row.marks_distribution_json or {},
        "question_type_distribution": row.question_type_distribution_json or {},
        "chapter_weightage": row.chapter_weightage_json or {},
        "topic_frequency": row.topic_frequency_json or {},
        "repeated_concepts": list(row.repeated_concepts_json or []),
        "difficulty_distribution": row.difficulty_distribution_json or {},
        "pattern_summary": row.pattern_summary or "",
        "confidence_score": row.confidence_score or 0.0,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def serialize_probable(row: ProbableQuestionSet) -> Dict[str, Any]:
    return {
        "id": row.id,
        "class_level": row.class_level or "",
        "subject": row.subject or "",
        "chapter_id": row.chapter_id,
        "chapter_name": row.chapter_name or "",
        "source_analysis_ids": list(row.source_analysis_ids_json or []),
        "generation_mode": row.generation_mode or "",
        "probable_questions": list(row.probable_questions_json or []),
        "priority_topics": list(row.priority_topics_json or []),
        "strategy_summary": row.strategy_summary or "",
        "disclaimer": row.disclaimer or "",
        "confidence_score": row.confidence_score or 0.0,
        "created_at": row.created_at,
    }
