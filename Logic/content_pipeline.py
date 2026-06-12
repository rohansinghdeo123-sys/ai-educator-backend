"""Production NCERT content ingestion and retrieval pipeline.

PDFs are the source of truth. Concept JSON and chunks are derived layers that
must pass validation and approval before Study Lab retrieval uses them.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from database import SessionLocal
from Logic import embeddings as embeddings_service
from models import (
    ContentChapter,
    ContentChunk,
    ContentConcept,
    ContentIngestionJob,
    ContentPage,
)

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - exercised in environments without pypdf
    PdfReader = None  # type: ignore[assignment]


logger = logging.getLogger("ai_educator.content_pipeline")

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
RAW_NCERT_DIR = DATA_DIR / "raw" / "ncert"
APPROVED_STATUSES = {"approved", "published"}
DEFAULT_VERSION = "v1"

STOPWORDS = {
    "what", "why", "how", "explain", "define", "describe", "tell", "give",
    "with", "from", "about", "than", "more", "less", "into", "this", "that",
    "these", "those", "your", "please", "simple", "simply", "the", "and",
    "are", "was", "were", "for", "does", "can", "chapter", "class", "subject",
    "only", "notes", "study", "material",
}


class ContentConceptPayload(BaseModel):
    concept_id: str = Field(min_length=2, max_length=140)
    title: str = Field(min_length=2, max_length=220)
    definition: str = ""
    core_explanation: str = ""
    key_points: List[str] = Field(default_factory=list)
    examples: List[str] = Field(default_factory=list)
    formulas: List[Any] = Field(default_factory=list)
    properties: List[str] = Field(default_factory=list)
    applications: List[str] = Field(default_factory=list)
    common_mistakes: List[Dict[str, Any]] = Field(default_factory=list)
    prerequisites: List[str] = Field(default_factory=list)
    related_concepts: List[Any] = Field(default_factory=list)
    learning_objectives: List[str] = Field(default_factory=list)
    source_pages: List[int] = Field(default_factory=list)
    difficulty_level: int = Field(default=1, ge=1, le=5)
    blooms_taxonomy: str = ""
    typical_exam_weightage: str = ""
    importance_level: str = ""

    @field_validator("concept_id")
    @classmethod
    def normalize_concept_id(cls, value: str) -> str:
        normalized = normalize_key(value)
        if not normalized:
            raise ValueError("concept_id cannot be empty after normalization")
        return normalized[:140]

    @field_validator("source_pages")
    @classmethod
    def normalize_source_pages(cls, value: List[int]) -> List[int]:
        pages = sorted({int(page) for page in value if int(page) > 0})
        return pages


def normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def titleize(value: str) -> str:
    cleaned = re.sub(r"[_\-]+", " ", value or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.title()


def content_terms(value: str) -> List[str]:
    terms = []
    for term in re.findall(r"[a-z0-9]+", str(value or "").lower()):
        if len(term) > 2 and term not in STOPWORDS:
            terms.append(term)
    return sorted(set(terms))


def safe_data_path(path_value: Optional[str], *, default: Path = RAW_NCERT_DIR) -> Path:
    candidate = Path(path_value or default)
    if not candidate.is_absolute():
        candidate = BASE_DIR / candidate
    resolved = candidate.resolve()
    data_root = DATA_DIR.resolve()
    if resolved != data_root and data_root not in resolved.parents:
        raise ValueError(f"Path must stay inside backend/data: {resolved}")
    return resolved


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def infer_metadata_from_pdf_path(pdf_path: Path, root_path: Optional[Path] = None) -> Dict[str, Any]:
    root = root_path.resolve() if root_path else RAW_NCERT_DIR.resolve()
    resolved = pdf_path.resolve()
    try:
        parts = list(resolved.relative_to(root).parts)
    except ValueError:
        parts = list(resolved.parts)

    filename = resolved.stem
    normalized_parts = [normalize_key(part) for part in parts]
    board = "NCERT" if "ncert" in normalized_parts or "raw" in normalized_parts else "NCERT"

    class_level = ""
    for part in normalized_parts:
        match = re.search(r"class_?(\d{1,2})", part)
        if match:
            class_level = match.group(1)
            break

    subject = ""
    if len(parts) >= 2:
        parent = normalize_key(parts[-2])
        if not parent.startswith("class"):
            subject = titleize(parent)

    chapter_number = None
    number_match = re.search(r"(?:chapter|ch|chap)[_\-\s]*(\d{1,3})", filename, re.IGNORECASE)
    if not number_match:
        number_match = re.search(r"\b(\d{1,3})\b", filename)
    if number_match:
        chapter_number = int(number_match.group(1))

    chapter_name = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE)
    chapter_name = re.sub(r"(?:chapter|chap|ch)[_\-\s]*\d{1,3}", "", chapter_name, flags=re.IGNORECASE)
    chapter_name = re.sub(r"^\d{1,3}[_\-\s]*", "", chapter_name).strip(" _-")
    chapter_name = titleize(chapter_name or f"Chapter {chapter_number or ''}".strip())

    book_name = ""
    if len(parts) >= 3:
        maybe_book = normalize_key(parts[-3])
        if maybe_book and not maybe_book.startswith("class"):
            book_name = titleize(maybe_book)

    slug_parts = [board, f"class_{class_level}" if class_level else "", subject, f"chapter_{chapter_number or ''}", chapter_name]
    slug = normalize_key("_".join(part for part in slug_parts if part))

    return {
        "board": board,
        "class_level": class_level,
        "subject": subject,
        "book_name": book_name,
        "chapter_number": chapter_number,
        "chapter_name": chapter_name,
        "slug": slug,
    }


def extract_pdf_pages(pdf_path: Path) -> List[Dict[str, Any]]:
    if PdfReader is None:
        raise RuntimeError("pypdf is not installed. Install pypdf to extract NCERT PDFs.")
    reader = PdfReader(str(pdf_path))
    pages: List[Dict[str, Any]] = []
    for index, page in enumerate(reader.pages, start=1):
        raw_text = page.extract_text() or ""
        text = re.sub(r"[ \t]+", " ", raw_text).strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        char_count = len(text)
        quality = 0.0 if char_count == 0 else min(1.0, char_count / 900)
        pages.append(
            {
                "page_number": index,
                "text": text,
                "char_count": char_count,
                "extraction_quality": round(quality, 3),
            }
        )
    return pages


def chunk_pages(
    pages: Sequence[Dict[str, Any]],
    *,
    max_chars: int = 1400,
    min_chars: int = 220,
) -> List[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []
    for page in pages:
        page_number = int(page["page_number"])
        text = str(page.get("text") or "").strip()
        if not text:
            continue
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n|(?<=\.)\s+(?=[A-Z0-9])", text) if part.strip()]
        buffer: List[str] = []
        buffer_len = 0
        chunk_index = 0

        def flush() -> None:
            nonlocal buffer, buffer_len, chunk_index
            combined = " ".join(buffer).strip()
            if len(combined) < min_chars and chunks:
                chunks[-1]["text"] = f'{chunks[-1]["text"]}\n\n{combined}'.strip()
                chunks[-1]["token_estimate"] = estimate_tokens(chunks[-1]["text"])
                chunks[-1]["lexical_terms"] = content_terms(chunks[-1]["text"])
            elif combined:
                chunk_index += 1
                chunks.append(
                    {
                        "chunk_id": "",
                        "text": combined,
                        "page_start": page_number,
                        "page_end": page_number,
                        "section_title": infer_section_title(combined),
                        "token_estimate": estimate_tokens(combined),
                        "lexical_terms": content_terms(combined),
                    }
                )
            buffer = []
            buffer_len = 0

        for paragraph in paragraphs:
            if buffer and buffer_len + len(paragraph) > max_chars:
                flush()
            buffer.append(paragraph)
            buffer_len += len(paragraph)
        flush()

    return chunks


def infer_section_title(text: str) -> str:
    first_line = str(text or "").strip().splitlines()[0] if text else ""
    candidate = first_line[:90].strip()
    if len(candidate.split()) <= 9 and not candidate.endswith("."):
        return candidate
    return ""


def estimate_tokens(text: str) -> int:
    return max(1, round(len(str(text or "")) / 4))


def validate_concept_payloads(
    payload: Any,
    *,
    available_pages: Iterable[int],
) -> Tuple[List[ContentConceptPayload], List[Dict[str, Any]]]:
    raw_items = payload if isinstance(payload, list) else payload.get("concepts", []) if isinstance(payload, dict) else []
    issues: List[Dict[str, Any]] = []
    validated: List[ContentConceptPayload] = []
    seen_ids: set[str] = set()
    page_set = {int(page) for page in available_pages}

    if not isinstance(raw_items, list):
        return [], [{"severity": "error", "message": "Concept payload must be a JSON array or an object with concepts[]."}]

    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            issues.append({"severity": "error", "index": index, "message": "Concept item must be an object."})
            continue
        try:
            concept = ContentConceptPayload.model_validate(item)
        except ValidationError as exc:
            issues.append({"severity": "error", "index": index, "message": "Concept schema validation failed.", "details": exc.errors()})
            continue
        concept_issues: List[str] = []
        if concept.concept_id in seen_ids:
            concept_issues.append("duplicate_concept_id")
        if not concept.definition and not concept.core_explanation and not concept.key_points:
            concept_issues.append("missing_teaching_content")
        if not concept.source_pages:
            concept_issues.append("missing_source_pages")
        elif any(page not in page_set for page in concept.source_pages):
            concept_issues.append("source_page_out_of_range")
        if concept_issues:
            issues.append(
                {
                    "severity": "error" if "missing_source_pages" in concept_issues else "warning",
                    "concept_id": concept.concept_id,
                    "message": "Concept has validation issues.",
                    "issues": concept_issues,
                }
            )
        seen_ids.add(concept.concept_id)
        validated.append(concept)

    return validated, issues


def build_coverage_report(
    pages: Sequence[Dict[str, Any]],
    concepts: Sequence[ContentConceptPayload] | Sequence[ContentConcept],
    chunks: Sequence[Dict[str, Any]] | Sequence[ContentChunk],
    issues: Sequence[Dict[str, Any]] = (),
) -> Dict[str, Any]:
    page_numbers = {int(page["page_number"] if isinstance(page, dict) else page.page_number) for page in pages}
    extracted_pages = {
        int(page["page_number"] if isinstance(page, dict) else page.page_number)
        for page in pages
        if int(page["char_count"] if isinstance(page, dict) else page.char_count or 0) > 0
    }
    concept_pages: set[int] = set()
    for concept in concepts:
        source_pages = concept.source_pages if not isinstance(concept, dict) else concept.get("source_pages", [])
        concept_pages.update(int(page) for page in source_pages or [] if int(page) > 0)

    covered_pages = extracted_pages & concept_pages if concepts else set()
    extraction_quality = 0.0
    if pages:
        extraction_quality = sum(float(page["extraction_quality"] if isinstance(page, dict) else page.extraction_quality or 0.0) for page in pages) / len(pages)
    coverage_score = (len(covered_pages) / len(extracted_pages)) if extracted_pages and concepts else 0.0
    blocking_issues = [issue for issue in issues if issue.get("severity") == "error"]
    missing_pages = sorted(extracted_pages - concept_pages) if concepts else sorted(extracted_pages)

    return {
        "page_count": len(page_numbers),
        "extracted_page_count": len(extracted_pages),
        "chunk_count": len(chunks),
        "concept_count": len(concepts),
        "pages_referenced_by_concepts": sorted(concept_pages),
        "missing_source_pages": missing_pages,
        "coverage_score": round(coverage_score, 3),
        "extraction_quality": round(extraction_quality, 3),
        "issues": list(issues),
        "blocking_issue_count": len(blocking_issues),
        "ready_for_approval": bool(concepts and not blocking_issues and coverage_score >= 0.65),
    }


def create_job(db: Session, *, job_type: str, source_path: str) -> ContentIngestionJob:
    job = ContentIngestionJob(
        job_id=f"content_job_{uuid.uuid4().hex[:14]}",
        job_type=job_type,
        status="running",
        source_path=source_path,
        summary={},
    )
    db.add(job)
    db.flush()
    return job


def ingest_pdf_file(
    db: Session,
    pdf_path: Path,
    *,
    root_path: Optional[Path] = None,
    replace: bool = True,
) -> ContentChapter:
    if not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"PDF file not found: {pdf_path}")

    metadata = infer_metadata_from_pdf_path(pdf_path, root_path)
    source_hash = file_sha256(pdf_path)
    chapter = db.query(ContentChapter).filter(ContentChapter.slug == metadata["slug"]).one_or_none()
    if chapter is None:
        chapter = ContentChapter(slug=metadata["slug"])
        db.add(chapter)

    for key, value in metadata.items():
        setattr(chapter, key, value)
    chapter.pdf_path = str(pdf_path)
    chapter.source_hash = source_hash
    chapter.status = "uploaded"
    # Keep the version across re-ingests; approval bumps it when the source
    # hash differs from what students last saw.
    chapter.version = chapter.version or DEFAULT_VERSION
    chapter.updated_at = datetime.utcnow()
    db.flush()

    if replace:
        db.query(ContentPage).filter(ContentPage.chapter_id == chapter.id).delete(synchronize_session=False)
        db.query(ContentChunk).filter(ContentChunk.chapter_id == chapter.id).delete(synchronize_session=False)

    pages = extract_pdf_pages(pdf_path)
    for page in pages:
        db.add(
            ContentPage(
                chapter_id=chapter.id,
                page_number=page["page_number"],
                text=page["text"],
                char_count=page["char_count"],
                extraction_quality=page["extraction_quality"],
                metadata_json={"source": "pdf_extraction"},
            )
        )
    db.flush()

    chunks = chunk_pages(pages)
    chunk_vectors: List[Optional[List[float]]] = [None] * len(chunks)
    if chunks and embeddings_service.embeddings_enabled():
        try:
            chunk_vectors = embeddings_service.embed_texts([chunk["text"] for chunk in chunks])
        except Exception:
            logger.exception(
                "Chunk embedding failed during ingest; storing chunks without embeddings | chapter=%s",
                chapter.slug,
            )
            chunk_vectors = [None] * len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        chunk_id = f"{chapter.slug}_chunk_{index:04d}"
        chunk["chunk_id"] = chunk_id
        vector = chunk_vectors[index - 1]
        metadata = {
            "board": chapter.board,
            "class": chapter.class_level,
            "subject": chapter.subject,
            "chapter": chapter.chapter_name,
            "source": "pdf",
        }
        if vector:
            metadata["embedding_model"] = embeddings_service.embedding_model()
        db.add(
            ContentChunk(
                chapter_id=chapter.id,
                chunk_id=chunk_id,
                text=chunk["text"],
                page_start=chunk["page_start"],
                page_end=chunk["page_end"],
                section_title=chunk["section_title"],
                token_estimate=chunk["token_estimate"],
                lexical_terms=chunk["lexical_terms"],
                embedding=vector,
                metadata_json=metadata,
            )
        )

    report = build_coverage_report(pages, [], chunks)
    chapter.page_count = report["page_count"]
    chapter.extracted_page_count = report["extracted_page_count"]
    chapter.chunk_count = report["chunk_count"]
    chapter.extraction_quality = report["extraction_quality"]
    chapter.coverage_score = report["coverage_score"]
    chapter.validation_report = report
    chapter.status = "indexed" if chunks else "failed"
    db.flush()
    return chapter


def run_ingest_folder_job(
    db: Session,
    job: ContentIngestionJob,
    *,
    root_path: Optional[str] = None,
    replace: bool = True,
) -> Dict[str, Any]:
    """Execute folder ingestion against an existing job row (sync or worker)."""
    root = safe_data_path(root_path)
    root.mkdir(parents=True, exist_ok=True)
    chapters: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    try:
        pdfs = sorted(root.rglob("*.pdf"))
        for pdf_path in pdfs:
            try:
                chapter = ingest_pdf_file(db, pdf_path, root_path=root, replace=replace)
                chapters.append(serialize_chapter(chapter))
            except Exception as exc:
                logger.exception("Content ingestion failed for %s", pdf_path)
                errors.append({"path": str(pdf_path), "error": str(exc)})
        job.status = "completed" if not errors else "needs_review"
        job.summary = {
            **(job.summary or {}),
            "root": str(root),
            "pdf_count": len(pdfs),
            "chapters": len(chapters),
            "errors": errors,
        }
        db.commit()
        return {"job": serialize_job(job), "chapters": chapters, "errors": errors}
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        db.commit()
        raise


def ingest_pdf_folder(db: Session, root_path: Optional[str] = None, *, replace: bool = True) -> Dict[str, Any]:
    root = safe_data_path(root_path)
    root.mkdir(parents=True, exist_ok=True)
    job = create_job(db, job_type="ingest_folder", source_path=str(root))
    return run_ingest_folder_job(db, job, root_path=root_path, replace=replace)


def import_concepts_for_chapter(
    db: Session,
    chapter_id: int,
    payload: Any,
    *,
    replace: bool = True,
) -> ContentChapter:
    chapter = db.query(ContentChapter).filter(ContentChapter.id == chapter_id).one_or_none()
    if chapter is None:
        raise ValueError(f"Chapter not found: {chapter_id}")
    pages = db.query(ContentPage).filter(ContentPage.chapter_id == chapter.id).order_by(ContentPage.page_number).all()
    available_pages = [page.page_number for page in pages]
    concepts, issues = validate_concept_payloads(payload, available_pages=available_pages)

    if replace:
        db.query(ContentConcept).filter(ContentConcept.chapter_id == chapter.id).delete(synchronize_session=False)
    for concept in concepts:
        raw = concept.model_dump()
        concept_issues = [issue for issue in issues if issue.get("concept_id") == concept.concept_id]
        db.add(
            ContentConcept(
                chapter_id=chapter.id,
                concept_id=concept.concept_id,
                title=concept.title,
                definition=concept.definition,
                core_explanation=concept.core_explanation,
                key_points=concept.key_points,
                examples=concept.examples,
                formulas=concept.formulas,
                properties=concept.properties,
                applications=concept.applications,
                common_mistakes=concept.common_mistakes,
                prerequisites=concept.prerequisites,
                related_concepts=concept.related_concepts,
                learning_objectives=concept.learning_objectives,
                source_pages=concept.source_pages,
                difficulty_level=concept.difficulty_level,
                blooms_taxonomy=concept.blooms_taxonomy,
                typical_exam_weightage=concept.typical_exam_weightage,
                importance_level=concept.importance_level,
                raw_json=raw,
                validation_issues=concept_issues,
            )
        )
    db.flush()
    chunks = db.query(ContentChunk).filter(ContentChunk.chapter_id == chapter.id).all()
    report = build_coverage_report(
        [{"page_number": page.page_number, "char_count": page.char_count, "extraction_quality": page.extraction_quality} for page in pages],
        concepts,
        chunks,
        issues,
    )
    chapter.concept_count = len(concepts)
    chapter.coverage_score = report["coverage_score"]
    chapter.extraction_quality = report["extraction_quality"]
    chapter.validation_report = report
    chapter.status = "validated" if report["ready_for_approval"] else "needs_review"
    chapter.updated_at = datetime.utcnow()
    db.flush()
    return chapter


def _extract_json_array(text: str) -> List[Dict[str, Any]]:
    cleaned = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start >= 0 and end > start:
        cleaned = cleaned[start:end + 1]
    data = json.loads(cleaned)
    if not isinstance(data, list):
        raise ValueError("Model response must be a JSON array of concepts.")
    return [item for item in data if isinstance(item, dict)]


def _page_batches(pages: Sequence[ContentPage], max_chars: int) -> List[List[ContentPage]]:
    batches: List[List[ContentPage]] = []
    current: List[ContentPage] = []
    current_len = 0
    for page in pages:
        page_text = (page.text or "").strip()
        if not page_text:
            continue
        if current and current_len + len(page_text) > max_chars:
            batches.append(current)
            current = []
            current_len = 0
        current.append(page)
        current_len += len(page_text)
    if current:
        batches.append(current)
    return batches


def generate_concepts_for_chapter(
    db: Session,
    chapter_id: int,
    *,
    replace: bool = True,
    max_batch_chars: int = 9000,
) -> ContentChapter:
    """Generate draft concept JSON from extracted pages using configured model routing."""
    chapter = db.query(ContentChapter).filter(ContentChapter.id == chapter_id).one_or_none()
    if chapter is None:
        raise ValueError(f"Chapter not found: {chapter_id}")
    pages = db.query(ContentPage).filter(ContentPage.chapter_id == chapter.id).order_by(ContentPage.page_number).all()
    if not pages:
        raise ValueError("Chapter has no extracted pages. Run ingestion first.")

    from Logic.coach.model_gateway import model_gateway

    generated: List[Dict[str, Any]] = []
    batches = _page_batches(pages, max_chars=max_batch_chars)
    for batch_index, batch in enumerate(batches, start=1):
        page_text = "\n\n".join(
            f"[PAGE {page.page_number}]\n{(page.text or '').strip()}"
            for page in batch
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "You convert NCERT textbook pages into strict structured concept JSON. "
                    "Use ONLY the supplied page text. Do not add outside facts. "
                    "Return ONLY a JSON array. Each item must include: concept_id, title, "
                    "definition, core_explanation, key_points, examples, formulas, "
                    "common_mistakes, learning_objectives, source_pages, difficulty_level, "
                    "blooms_taxonomy, typical_exam_weightage, importance_level. "
                    "Every concept must cite source_pages from the supplied [PAGE n] markers."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Board: {chapter.board}\n"
                    f"Class: {chapter.class_level}\n"
                    f"Subject: {chapter.subject}\n"
                    f"Chapter: {chapter.chapter_name}\n"
                    f"Batch: {batch_index}/{len(batches)}\n\n"
                    f"{page_text}"
                ),
            },
        ]
        response = model_gateway.complete(
            role="reviewer",
            complexity="balanced",
            agent_name="content_ingestion_agent",
            task=f"generate_content_concepts:{chapter.slug}:batch_{batch_index}",
            student_visible=False,
            safety_tier="strict_source_grounding",
            messages=messages,
            temperature=0.1,
            max_tokens=3000,
        )
        generated.extend(_extract_json_array(response))

    return import_concepts_for_chapter(db, chapter.id, generated, replace=replace)


def _next_version(value: str) -> str:
    match = re.fullmatch(r"v(\d+)", str(value or "").strip().lower())
    if match:
        return f"v{int(match.group(1)) + 1}"
    return "v2"


def approve_chapter(db: Session, chapter_id: int, *, approved_by: str = "") -> ContentChapter:
    chapter = db.query(ContentChapter).filter(ContentChapter.id == chapter_id).one_or_none()
    if chapter is None:
        raise ValueError(f"Chapter not found: {chapter_id}")
    report = chapter.validation_report or {}
    if not chapter.concept_count:
        raise ValueError("Chapter has no validated concepts. Import or generate concept JSON first.")
    if not report.get("ready_for_approval"):
        raise ValueError("Chapter is not ready for approval. Review validation_report first.")
    # Approval is the moment content goes live for students. If the PDF was
    # re-ingested since the last approval, bump the version so reports and
    # traces can say which source revision answered a question.
    if chapter.published_source_hash and chapter.published_source_hash != chapter.source_hash:
        chapter.version = _next_version(chapter.version)
    chapter.published_source_hash = chapter.source_hash
    chapter.status = "approved"
    chapter.approved_by = approved_by or "admin"
    chapter.approved_at = datetime.utcnow()
    chapter.updated_at = datetime.utcnow()
    db.flush()
    return chapter


def publish_chapter(db: Session, chapter_id: int, *, published_by: str = "") -> ContentChapter:
    chapter = approve_chapter(db, chapter_id, approved_by=published_by)
    chapter.status = "published"
    chapter.published_at = datetime.utcnow()
    db.flush()
    return chapter


def embed_missing_chunks(db: Session, *, chapter_id: Optional[int] = None) -> Dict[str, Any]:
    """Backfill embeddings for chunks ingested before embeddings were configured."""
    query = db.query(ContentChunk).filter(ContentChunk.embedding.is_(None))
    if chapter_id is not None:
        query = query.filter(ContentChunk.chapter_id == chapter_id)
    rows = query.order_by(ContentChunk.id).all()

    if not embeddings_service.embeddings_enabled():
        return {
            "enabled": False,
            "embedded": 0,
            "missing": len(rows),
            "message": "Embeddings are not configured. Set EMBEDDINGS_API_KEY (or OPENAI_API_KEY).",
        }
    if not rows:
        return {"enabled": True, "embedded": 0, "missing": 0, "model": embeddings_service.embedding_model()}

    vectors = embeddings_service.embed_texts([row.text or " " for row in rows])
    model_name = embeddings_service.embedding_model()
    for row, vector in zip(rows, vectors):
        row.embedding = vector
        metadata = dict(row.metadata_json or {})
        metadata["embedding_model"] = model_name
        row.metadata_json = metadata
    db.flush()
    return {"enabled": True, "embedded": len(rows), "missing": 0, "model": model_name}


def serialize_chapter(chapter: ContentChapter) -> Dict[str, Any]:
    return {
        "id": chapter.id,
        "board": chapter.board,
        "class_level": chapter.class_level,
        "subject": chapter.subject,
        "book_name": chapter.book_name,
        "chapter_number": chapter.chapter_number,
        "chapter_name": chapter.chapter_name,
        "slug": chapter.slug,
        "pdf_path": chapter.pdf_path,
        "status": chapter.status,
        "version": chapter.version,
        "published_source_hash": chapter.published_source_hash or "",
        "page_count": chapter.page_count,
        "extracted_page_count": chapter.extracted_page_count,
        "chunk_count": chapter.chunk_count,
        "concept_count": chapter.concept_count,
        "coverage_score": chapter.coverage_score,
        "extraction_quality": chapter.extraction_quality,
        "validation_report": chapter.validation_report or {},
        "approved_by": chapter.approved_by,
        "approved_at": chapter.approved_at.isoformat() if chapter.approved_at else None,
        "published_at": chapter.published_at.isoformat() if chapter.published_at else None,
        "updated_at": chapter.updated_at.isoformat() if chapter.updated_at else None,
    }


def serialize_job(job: ContentIngestionJob) -> Dict[str, Any]:
    return {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "status": job.status,
        "source_path": job.source_path,
        "summary": job.summary or {},
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def list_chapters(db: Session, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
    query = db.query(ContentChapter).order_by(
        ContentChapter.class_level,
        ContentChapter.subject,
        ContentChapter.chapter_number,
        ContentChapter.chapter_name,
    )
    if status:
        query = query.filter(ContentChapter.status == status)
    return [serialize_chapter(chapter) for chapter in query.all()]


def chapter_report(db: Session, chapter_id: int) -> Dict[str, Any]:
    chapter = db.query(ContentChapter).filter(ContentChapter.id == chapter_id).one_or_none()
    if chapter is None:
        raise ValueError(f"Chapter not found: {chapter_id}")
    pages = db.query(ContentPage).filter(ContentPage.chapter_id == chapter.id).order_by(ContentPage.page_number).all()
    concepts = db.query(ContentConcept).filter(ContentConcept.chapter_id == chapter.id).order_by(ContentConcept.concept_id).all()
    chunks = db.query(ContentChunk).filter(ContentChunk.chapter_id == chapter.id).all()
    report = build_coverage_report(
        [{"page_number": page.page_number, "char_count": page.char_count, "extraction_quality": page.extraction_quality} for page in pages],
        concepts,
        chunks,
        chapter.validation_report.get("issues", []) if chapter.validation_report else [],
    )
    return {
        "chapter": serialize_chapter(chapter),
        "report": report,
        "page_preview": [
            {"page_number": page.page_number, "char_count": page.char_count, "quality": page.extraction_quality}
            for page in pages[:20]
        ],
        "concept_preview": [
            {"concept_id": concept.concept_id, "title": concept.title, "source_pages": concept.source_pages}
            for concept in concepts[:30]
        ],
    }


def _chapter_scope_matches(chapter: ContentChapter, scope: Optional[Dict[str, Any]], section_id: str) -> bool:
    """Hard scope filters: subject and chapter must match when supplied."""
    if not scope:
        return True
    subject = normalize_key(scope.get("subject"))
    chapter_value = normalize_key(scope.get("chapter"))
    if subject and subject not in normalize_key(chapter.subject):
        return False
    if chapter_value and chapter_value not in normalize_key(f"{chapter.chapter_name} {chapter.slug} chapter_{chapter.chapter_number or ''}"):
        return False
    return True


def _chapter_matches_topic(chapter: ContentChapter, scope: Optional[Dict[str, Any]], section_id: str) -> bool:
    """Soft topic filter: True when the scope topic names this chapter.

    Topics are often finer-grained than chapter names (e.g. topic "alkanes"
    inside chapter "Hydrocarbons"), so callers must treat a miss as
    "no preference", not exclusion — see search_approved_content.
    """
    topic = normalize_key((scope or {}).get("topic") or (scope or {}).get("section_id") or section_id)
    if not topic or topic in {"general", "open", "any", "all"}:
        return False
    haystack = normalize_key(f"{chapter.chapter_name} {chapter.slug} {chapter.subject}")
    return topic in haystack


def _score_text(text: str, terms: Sequence[str]) -> int:
    normalized = str(text or "").lower()
    return sum(normalized.count(term) for term in terms)


def _min_semantic_similarity() -> float:
    try:
        return float(os.getenv("EMBEDDINGS_MIN_SIMILARITY", "0.25"))
    except ValueError:
        return 0.25


_RRF_K = 60.0


def search_approved_content(
    section_id: str,
    question: str,
    *,
    scope: Optional[Dict[str, Any]] = None,
    max_chars: int = 5000,
    limit: int = 6,
) -> Dict[str, Any]:
    """Hybrid retrieval over approved content.

    Candidates are ranked lexically (term frequency, as before) and — when an
    embedding provider is configured and chunks carry embeddings — semantically
    by cosine similarity to the question. The two rankings are fused with
    reciprocal rank fusion, so semantically-phrased questions match material
    they share no keywords with, while exact-term matches keep their edge.
    Without embeddings the behavior is identical to the old lexical search.
    """
    terms = content_terms(f"{section_id} {question}")
    if not terms:
        terms = content_terms(section_id)
    if not terms:
        return {"context": "", "source": "content_pipeline", "paragraphs_found": 0}

    db = SessionLocal()
    try:
        chapters = db.query(ContentChapter).filter(ContentChapter.status.in_(APPROVED_STATUSES)).all()
        chapters = [chapter for chapter in chapters if _chapter_scope_matches(chapter, scope, section_id)]
        # Topic narrows to matching chapters when it names one; otherwise the
        # topic is finer-grained than chapter names and ranking handles it.
        topic_matched = [chapter for chapter in chapters if _chapter_matches_topic(chapter, scope, section_id)]
        if topic_matched:
            chapters = topic_matched
        if not chapters:
            return {"context": "", "source": "content_pipeline", "paragraphs_found": 0}
        chapter_ids = [chapter.id for chapter in chapters]
        concept_rows = db.query(ContentConcept).filter(ContentConcept.chapter_id.in_(chapter_ids)).all()
        chunk_rows = db.query(ContentChunk).filter(ContentChunk.chapter_id.in_(chapter_ids)).all()
        chapter_by_id = {chapter.id: chapter for chapter in chapters}

        query_vector = embeddings_service.embed_query(question or section_id)
        min_similarity = _min_semantic_similarity()

        candidates: Dict[Tuple[str, int], Dict[str, Any]] = {}
        for concept in concept_rows:
            text = "\n".join(
                [
                    concept.title or "",
                    concept.definition or "",
                    concept.core_explanation or "",
                    " ".join(concept.key_points or []),
                    " ".join(map(str, concept.examples or [])),
                    " ".join(map(str, concept.formulas or [])),
                ]
            )
            lexical = _score_text(text, terms)
            if lexical:
                candidates[("concept", concept.id)] = {
                    "lexical": lexical + 3,
                    "semantic": 0.0,
                    "type": "concept",
                    "payload": {
                        "chapter": chapter_by_id.get(concept.chapter_id),
                        "title": concept.title,
                        "text": text,
                        "pages": concept.source_pages or [],
                        "section_id": concept.concept_id,
                    },
                }
        for chunk in chunk_rows:
            chunk_terms = set(chunk.lexical_terms or [])
            lexical = len(chunk_terms.intersection(terms)) * 2 + _score_text(chunk.text or "", terms)
            semantic = 0.0
            if query_vector is not None and chunk.embedding:
                semantic = embeddings_service.similarity(query_vector, chunk.embedding)
                if semantic < min_similarity:
                    semantic = 0.0
            if lexical or semantic:
                candidates[("chunk", chunk.id)] = {
                    "lexical": lexical,
                    "semantic": semantic,
                    "type": "chunk",
                    "payload": {
                        "chapter": chapter_by_id.get(chunk.chapter_id),
                        "title": chunk.section_title or f"Pages {chunk.page_start}-{chunk.page_end}",
                        "text": chunk.text,
                        "pages": [page for page in (chunk.page_start, chunk.page_end) if page],
                        "section_id": chunk.chunk_id,
                    },
                }

        lexical_ranking = [
            key
            for key, candidate in sorted(
                candidates.items(), key=lambda item: (-item[1]["lexical"], item[0])
            )
            if candidate["lexical"] > 0
        ]
        semantic_ranking = [
            key
            for key, candidate in sorted(
                candidates.items(), key=lambda item: (-item[1]["semantic"], item[0])
            )
            if candidate["semantic"] > 0
        ]

        fused: Dict[Tuple[str, int], float] = {}
        for ranking in (lexical_ranking, semantic_ranking):
            for rank, key in enumerate(ranking, start=1):
                fused[key] = fused.get(key, 0.0) + 1.0 / (_RRF_K + rank)
        ordered_keys = sorted(fused, key=lambda key: (-fused[key], key))

        blocks: List[str] = []
        used_pages: List[int] = []
        used_sections: List[str] = []
        total_chars = 0
        for key in ordered_keys[: limit * 2]:
            candidate = candidates[key]
            payload = candidate["payload"]
            chapter = payload["chapter"]
            if chapter is None:
                continue
            page_label = ", ".join(str(page) for page in sorted(set(payload["pages"]))) or "unknown"
            header = (
                f"## {payload['title']}\n"
                f"Source: {chapter.board} Class {chapter.class_level} {chapter.subject}, "
                f"{chapter.chapter_name}, page(s): {page_label}, type: {candidate['type']}\n"
            )
            block = f"{header}{payload['text']}".strip()
            if total_chars + len(block) > max_chars:
                continue
            blocks.append(block)
            used_pages.extend(int(page) for page in payload["pages"] if page)
            used_sections.append(str(payload["section_id"]))
            total_chars += len(block)
            if len(blocks) >= limit:
                break

        if not blocks:
            return {"context": "", "source": "content_pipeline", "paragraphs_found": 0}
        return {
            "context": "\n\n".join(blocks),
            "section_id": section_id,
            "paragraphs_found": len(blocks),
            "keywords_used": terms,
            "basics_context": "",
            "source": "approved_content_pipeline",
            "source_pages": sorted(set(used_pages)),
            "matched_sections": used_sections,
            "retrieval_mode": "hybrid" if query_vector is not None else "lexical",
            "semantic_matches": len(semantic_ranking),
        }
    finally:
        db.close()
