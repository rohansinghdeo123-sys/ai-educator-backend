"""Service layer for uploaded exam papers: validation, safe storage, parsing,
synchronous pattern analysis, and ownership-scoped reads.

Persistence and ownership live here; parsing and LLM analysis live in
``Logic/exam``. Every read is filtered by ``user_id`` so one student can never
reach another student's paper (the router maps a miss to 404).
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app import config
from Logic.content_pipeline import DATA_DIR, safe_data_path
from Logic.exam import agents
from models import ExtractedExamQuestion, UploadedExamPaper

logger = logging.getLogger("ai_educator.services.exam_paper")

UPLOAD_ROOT = DATA_DIR / "uploads" / "exam_papers"

KNOWN_EXAM_TYPES = {
    "class_test", "unit_test", "school_exam", "pre_board",
    "board_exam", "chapter_wise", "subject_wise", "other",
}


class PaperValidationError(ValueError):
    """Raised for an unacceptable upload (bad type / size / empty)."""


def _utcnow() -> datetime:
    return datetime.utcnow()


def normalize_exam_type(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    if cleaned in KNOWN_EXAM_TYPES:
        return cleaned
    aliases = {
        "class": "class_test", "classtest": "class_test", "ct": "class_test",
        "unit": "unit_test", "ut": "unit_test",
        "school": "school_exam", "term": "school_exam", "final": "school_exam",
        "preboard": "pre_board", "pre_board_exam": "pre_board",
        "board": "board_exam", "boards": "board_exam",
        "chapter": "chapter_wise", "chapterwise": "chapter_wise",
        "subject": "subject_wise", "subjectwise": "subject_wise",
    }
    return aliases.get(cleaned, "other" if not cleaned else cleaned)


def validate_extension(filename: str, content_type: str) -> str:
    from Logic.exam.parsers import detect_extension

    ext = detect_extension(filename, content_type)
    if not ext:
        raise PaperValidationError(
            "Could not determine the file type. Upload a PDF, a text file, or an image."
        )
    if ext not in config.EXAM_UPLOAD_ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(config.EXAM_UPLOAD_ALLOWED_EXTENSIONS))
        raise PaperValidationError(f"Unsupported file type '.{ext}'. Allowed: {allowed}.")
    return ext


def validate_size(data: bytes) -> None:
    if not data:
        raise PaperValidationError("The uploaded file is empty.")
    if len(data) > config.EXAM_UPLOAD_MAX_BYTES:
        max_mb = config.EXAM_UPLOAD_MAX_BYTES / (1024 * 1024)
        raise PaperValidationError(f"File too large. Maximum size is {max_mb:.0f} MB.")


def _user_upload_dir(user_id: str) -> Path:
    user_hash = sha256(str(user_id).encode("utf-8")).hexdigest()[:16]
    target = UPLOAD_ROOT / user_hash
    # safe_data_path keeps the path provably inside backend/data.
    safe = safe_data_path(str(target))
    safe.mkdir(parents=True, exist_ok=True)
    return safe


def _store_file(user_id: str, data: bytes, ext: str) -> str:
    """Write the upload to a user-scoped path. Returns the storage path, or ""
    if the disk write fails (extracted text in the DB remains the source of truth)."""
    try:
        directory = _user_upload_dir(user_id)
        path = directory / f"{uuid.uuid4().hex}.{ext}"
        with path.open("wb") as handle:
            handle.write(data)
        return str(path)
    except Exception as exc:  # noqa: BLE001 - disk is best-effort, never fatal
        logger.warning("Could not persist uploaded paper to disk: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Analysis persistence
# ---------------------------------------------------------------------------
def _replace_questions(db: Session, paper: UploadedExamPaper, questions: List[Dict[str, Any]]) -> None:
    db.query(ExtractedExamQuestion).filter(
        ExtractedExamQuestion.paper_id == paper.id
    ).delete(synchronize_session=False)
    for item in questions:
        db.add(
            ExtractedExamQuestion(
                paper_id=paper.id,
                user_id=paper.user_id,
                question_number=item.get("question_number", ""),
                section_name=item.get("section_name", ""),
                question_text=item.get("question_text", ""),
                marks=item.get("marks"),
                question_type=item.get("question_type", ""),
                intent=item.get("intent", ""),
                difficulty=item.get("difficulty", ""),
                class_level=paper.class_level,
                subject=paper.subject,
                chapter_id=paper.chapter_id,
                chapter_name=paper.chapter_name,
                topic=item.get("topic", ""),
                concept_tags_json=item.get("concept_tags", []),
                expected_answer_style=item.get("expected_answer_style", ""),
                confidence_score=float(item.get("confidence") or 0.0),
                raw_block=item.get("raw_block", ""),
            )
        )


def _apply_analysis(db: Session, paper: UploadedExamPaper) -> Dict[str, Any]:
    """Run the analyzer on the paper's stored text and persist questions + analysis."""
    result = agents.analyze_paper(
        paper_text=paper.extracted_text or "",
        class_level=paper.class_level,
        subject=paper.subject,
        chapter_name=paper.chapter_name,
        exam_type=paper.exam_type,
    )
    questions = result.get("questions", [])
    _replace_questions(db, paper, questions)

    paper.analysis_json = result.get("analysis", {})
    paper.extracted_question_count = len(questions)
    paper.extraction_confidence = float(result.get("confidence") or paper.extraction_confidence or 0.0)
    warnings = list(result.get("warnings") or [])
    paper.warnings_json = (list(paper.warnings_json or []) + warnings)[:20]
    if result.get("paper_title") and not paper.paper_title:
        paper.paper_title = result["paper_title"]
    if result.get("exam_type") and paper.exam_type in {"", "unknown", "other"}:
        paper.exam_type = normalize_exam_type(result["exam_type"])
    paper.parse_status = "analyzed" if questions else "analyzed_empty"
    paper.parsed_at = _utcnow()
    paper.updated_at = _utcnow()
    return result


def create_paper_from_upload(
    db: Session,
    user_id: str,
    *,
    filename: str,
    content_type: str,
    data: bytes,
    class_level: str = "",
    subject: str = "",
    chapter_name: str = "",
    chapter_id: Optional[int] = None,
    exam_type: str = "",
    paper_title: str = "",
) -> UploadedExamPaper:
    """Validate, store, parse, and (synchronously) analyze one uploaded paper."""
    ext = validate_extension(filename, content_type)
    validate_size(data)

    parsed = agents.parse_paper(data, filename=filename, content_type=content_type)
    storage_path = _store_file(user_id, data, ext)

    paper = UploadedExamPaper(
        user_id=user_id,
        class_level=class_level or "",
        subject=subject or "",
        chapter_id=chapter_id,
        chapter_name=chapter_name or "",
        exam_type=normalize_exam_type(exam_type),
        paper_title=paper_title or "",
        file_name=os.path.basename(filename or "")[:200],
        file_type=ext,
        file_size=len(data),
        storage_path=storage_path,
        upload_status="stored",
        parse_status="pending",
        uploaded_at=_utcnow(),
        extracted_text=parsed.text or "",
        extraction_confidence=parsed.confidence,
        warnings_json=list(parsed.warnings or []),
    )
    db.add(paper)
    db.flush()  # assign paper.id for question FK

    if parsed.text and parsed.text.strip():
        _apply_analysis(db, paper)
    else:
        # No text -> cannot analyze. Distinguish "needs OCR" from a hard failure.
        paper.parse_status = "needs_ocr" if getattr(parsed, "requires_ocr", False) else "failed"
        paper.parsed_at = _utcnow()

    db.commit()
    db.refresh(paper)
    return paper


def reanalyze_paper(
    db: Session,
    paper: UploadedExamPaper,
    *,
    class_level: Optional[str] = None,
    subject: Optional[str] = None,
    chapter_name: Optional[str] = None,
    exam_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Re-run analysis from the stored extracted text (no re-upload needed)."""
    if class_level is not None:
        paper.class_level = class_level
    if subject is not None:
        paper.subject = subject
    if chapter_name is not None:
        paper.chapter_name = chapter_name
    if exam_type is not None:
        paper.exam_type = normalize_exam_type(exam_type)

    if not (paper.extracted_text or "").strip():
        paper.parse_status = "needs_ocr" if paper.file_type in {"png", "jpg", "jpeg", "webp"} else "failed"
        db.commit()
        return {"questions": [], "analysis": paper.analysis_json or {}, "warnings": paper.warnings_json or []}

    # Reset stale parse warnings before recomputing.
    paper.warnings_json = []
    result = _apply_analysis(db, paper)
    db.commit()
    db.refresh(paper)
    return result


# ---------------------------------------------------------------------------
# Ownership-scoped reads / deletes
# ---------------------------------------------------------------------------
def get_owned_paper(db: Session, user_id: str, paper_id: int) -> Optional[UploadedExamPaper]:
    return (
        db.query(UploadedExamPaper)
        .filter(UploadedExamPaper.id == paper_id, UploadedExamPaper.user_id == user_id)
        .first()
    )


def list_papers(
    db: Session,
    user_id: str,
    *,
    subject: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, List[UploadedExamPaper]]:
    query = db.query(UploadedExamPaper).filter(UploadedExamPaper.user_id == user_id)
    if subject:
        query = query.filter(UploadedExamPaper.subject == subject)
    total = query.count()
    rows = (
        query.order_by(UploadedExamPaper.id.desc())
        .offset(max(0, offset))
        .limit(max(1, min(limit, 200)))
        .all()
    )
    return total, rows


def list_questions(db: Session, paper: UploadedExamPaper) -> List[ExtractedExamQuestion]:
    return (
        db.query(ExtractedExamQuestion)
        .filter(ExtractedExamQuestion.paper_id == paper.id)
        .order_by(ExtractedExamQuestion.id.asc())
        .all()
    )


def delete_paper(db: Session, paper: UploadedExamPaper) -> None:
    # Remove the on-disk file best-effort; questions cascade via the relationship.
    if paper.storage_path:
        try:
            path = Path(paper.storage_path)
            if path.exists():
                path.unlink()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not delete stored paper file: %s", exc)
    db.delete(paper)
    db.commit()


# ---------------------------------------------------------------------------
# Serializers (never expose storage_path)
# ---------------------------------------------------------------------------
def serialize_paper(paper: UploadedExamPaper) -> Dict[str, Any]:
    return {
        "id": paper.id,
        "class_level": paper.class_level or "",
        "subject": paper.subject or "",
        "chapter_id": paper.chapter_id,
        "chapter_name": paper.chapter_name or "",
        "exam_type": paper.exam_type or "",
        "paper_title": paper.paper_title or "",
        "file_name": paper.file_name or "",
        "file_type": paper.file_type or "",
        "file_size": paper.file_size or 0,
        "upload_status": paper.upload_status or "",
        "parse_status": paper.parse_status or "",
        "uploaded_at": paper.uploaded_at,
        "parsed_at": paper.parsed_at,
        "extraction_confidence": paper.extraction_confidence or 0.0,
        "extracted_question_count": paper.extracted_question_count or 0,
        "warnings": list(paper.warnings_json or []),
        "created_at": paper.created_at,
        "updated_at": paper.updated_at,
    }


def serialize_question(question: ExtractedExamQuestion) -> Dict[str, Any]:
    return {
        "id": question.id,
        "paper_id": question.paper_id,
        "question_number": question.question_number or "",
        "section_name": question.section_name or "",
        "question_text": question.question_text or "",
        "marks": question.marks,
        "question_type": question.question_type or "",
        "intent": question.intent or "",
        "difficulty": question.difficulty or "",
        "topic": question.topic or "",
        "concept_tags": list(question.concept_tags_json or []),
        "expected_answer_style": question.expected_answer_style or "",
        "confidence_score": question.confidence_score or 0.0,
    }
