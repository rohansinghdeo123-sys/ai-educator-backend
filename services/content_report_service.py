"""Content ingestion report builder.

Single source of truth for the content-pipeline inventory used by BOTH the CLI
(`scripts/content_report.py`) and the admin API (`GET /admin/content/ingestion-report`),
so the report shown in the app's admin page is identical to the one the team runs
in the terminal.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from database import engine
from Logic import embeddings as embeddings_service
from models import (
    ContentChapter,
    ContentChunk,
    ContentConcept,
    ContentIngestionJob,
    ContentPage,
)

STATUS_ORDER = [
    "uploaded", "indexed", "json_generated", "validated",
    "needs_review", "approved", "published", "failed",
]
LIVE_STATUSES = {"approved", "published"}
CONCEPT_PREVIEW_LIMIT = 15


def _iso(value) -> str:
    return value.isoformat() if value else ""


# Estimated bytes per stored embedding component (float32 vector storage).
EMBEDDING_BYTES_PER_DIM = 4


def _concept_chars(concept: ContentConcept) -> int:
    """Approximate the stored teaching-content size of one concept/subtopic
    (its definition, explanation, and list fields), used as its memory size."""
    total = len(concept.definition or "") + len(concept.core_explanation or "")
    for field in (
        concept.key_points,
        concept.examples,
        concept.formulas,
        concept.properties,
        concept.applications,
    ):
        for item in field or []:
            total += len(str(item))
    return total


def build_content_report(
    db: Session,
    *,
    status_filter: Optional[str] = None,
    include_full_concepts: bool = False,
) -> Dict[str, Any]:
    """Build the full content-ingestion report as a JSON-serializable dict."""
    chapters_q = db.query(ContentChapter).order_by(
        ContentChapter.class_level,
        ContentChapter.subject,
        ContentChapter.chapter_number,
        ContentChapter.chapter_name,
    )
    if status_filter:
        chapters_q = chapters_q.filter(ContentChapter.status == status_filter)
    chapters = chapters_q.all()

    # Embedding dimensionality (same across the corpus) — sampled once to size
    # the vector "memory" footprint without loading every vector.
    sample_embedding = (
        db.query(ContentChunk.embedding).filter(ContentChunk.embedding.isnot(None)).first()
    )
    embedding_dims = len(sample_embedding[0]) if sample_embedding and sample_embedding[0] else 0

    chapter_reports = []
    totals = {
        "chapters": 0, "pages": 0, "extracted_pages": 0,
        "concepts": 0, "chunks": 0, "embedded_chunks": 0,
        "chunk_chars": 0, "page_chars": 0, "concept_chars": 0,
        "tokens": 0, "embedding_bytes": 0, "memory_bytes": 0,
        "validation_issues": 0, "concepts_with_issues": 0,
        "embedding_dims": embedding_dims,
    }
    by_status: Counter = Counter()
    by_class: Counter = Counter()
    by_subject: Counter = Counter()

    for chapter in chapters:
        concept_rows = (
            db.query(ContentConcept)
            .filter(ContentConcept.chapter_id == chapter.id)
            .order_by(ContentConcept.concept_id)
            .all()
        )
        # One aggregate for chunk count, embedded count, indexed text size, tokens.
        chunk_chars, chunk_tokens, chunk_count, embedded = db.query(
            func.coalesce(func.sum(func.length(ContentChunk.text)), 0),
            func.coalesce(func.sum(ContentChunk.token_estimate), 0),
            func.count(ContentChunk.id),
            func.count(ContentChunk.embedding),
        ).filter(ContentChunk.chapter_id == chapter.id).one()
        chunk_chars, chunk_tokens, chunk_count, embedded = (
            int(chunk_chars or 0), int(chunk_tokens or 0), int(chunk_count or 0), int(embedded or 0),
        )
        page_count, page_chars = db.query(
            func.count(ContentPage.id),
            func.coalesce(func.sum(func.length(ContentPage.text)), 0),
        ).filter(ContentPage.chapter_id == chapter.id).one()
        page_count, page_chars = int(page_count or 0), int(page_chars or 0)

        report = chapter.validation_report or {}
        embedding_bytes = embedded * embedding_dims * EMBEDDING_BYTES_PER_DIM
        # Retrievable "memory" footprint of the chapter: indexed chunk text + vectors.
        memory_bytes = chunk_chars + embedding_bytes
        concepts_with_issues = sum(1 for c in concept_rows if c.validation_issues)
        chapter_concept_chars = 0

        concepts_view = []
        for concept in concept_rows:
            concept_chars = _concept_chars(concept)
            chapter_concept_chars += concept_chars
            concepts_view.append({
                "concept_id": concept.concept_id,
                "title": concept.title,
                "difficulty_level": concept.difficulty_level,
                "importance_level": concept.importance_level or "",
                "typical_exam_weightage": concept.typical_exam_weightage or "",
                "blooms_taxonomy": concept.blooms_taxonomy or "",
                "key_points": len(concept.key_points or []),
                "examples": len(concept.examples or []),
                "formulas": len(concept.formulas or []),
                "source_pages": concept.source_pages or [],
                "has_definition": bool((concept.definition or "").strip()),
                "validation_issues": len(concept.validation_issues or []),
                "chars": concept_chars,            # subtopic memory size (chars ≈ bytes)
                "tokens": concept_chars // 4,
            })
        shown_concepts = concepts_view if include_full_concepts else concepts_view[:CONCEPT_PREVIEW_LIMIT]

        chapter_reports.append({
            "id": chapter.id,
            "board": chapter.board,
            "class_level": chapter.class_level,
            "subject": chapter.subject,
            "book_name": chapter.book_name,
            "chapter_number": chapter.chapter_number,
            "chapter_name": chapter.chapter_name,
            "slug": chapter.slug,
            "status": chapter.status,
            "is_live": chapter.status in LIVE_STATUSES,
            "version": chapter.version,
            "page_count": page_count,
            "extracted_page_count": chapter.extracted_page_count,
            "concept_count": len(concept_rows),
            "chunk_count": chunk_count,
            "embedded_chunks": embedded,
            "coverage_score": chapter.coverage_score,
            "extraction_quality": chapter.extraction_quality,
            "ready_for_approval": report.get("ready_for_approval"),
            "blocking_issue_count": report.get("blocking_issue_count"),
            "missing_source_pages": report.get("missing_source_pages", []),
            "approved_by": chapter.approved_by,
            "approved_at": _iso(chapter.approved_at),
            "published_at": _iso(chapter.published_at),
            "updated_at": _iso(chapter.updated_at),
            "source_hash": (chapter.source_hash or "")[:12],
            "published_source_hash": (chapter.published_source_hash or "")[:12],
            # ── data / memory sizing ──
            "chunk_chars": chunk_chars,
            "page_chars": page_chars,
            "concept_chars": chapter_concept_chars,
            "chunk_tokens": chunk_tokens,
            "embedding_dims": embedding_dims,
            "embedding_bytes": embedding_bytes,
            "memory_bytes": memory_bytes,
            "concepts_with_issues": concepts_with_issues,
            "error_rate": round(concepts_with_issues / len(concept_rows), 4) if concept_rows else 0.0,
            "concepts": shown_concepts,
            "concepts_truncated": (not include_full_concepts) and len(concepts_view) > CONCEPT_PREVIEW_LIMIT,
        })

        totals["chapters"] += 1
        totals["pages"] += page_count
        totals["extracted_pages"] += chapter.extracted_page_count or 0
        totals["concepts"] += len(concept_rows)
        totals["chunks"] += chunk_count
        totals["embedded_chunks"] += embedded
        totals["chunk_chars"] += chunk_chars
        totals["page_chars"] += page_chars
        totals["concept_chars"] += chapter_concept_chars
        totals["tokens"] += chunk_tokens
        totals["embedding_bytes"] += embedding_bytes
        totals["memory_bytes"] += memory_bytes
        totals["validation_issues"] += sum(len(c.validation_issues or []) for c in concept_rows)
        totals["concepts_with_issues"] += concepts_with_issues
        by_status[chapter.status] += 1
        by_class[str(chapter.class_level or "?")] += 1
        by_subject[str(chapter.subject or "?")] += 1

    totals["error_rate"] = (
        round(totals["concepts_with_issues"] / totals["concepts"], 4) if totals["concepts"] else 0.0
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database_dialect": engine.dialect.name,
        "embeddings_enabled": embeddings_service.embeddings_enabled(),
        "embeddings_model": embeddings_service.embedding_model() if embeddings_service.embeddings_enabled() else "",
        "status_filter": status_filter or "all",
        "totals": totals,
        "by_status": dict(by_status),
        "by_class": dict(by_class),
        "by_subject": dict(by_subject),
        "jobs_total": db.query(ContentIngestionJob).count(),
        "chapters": chapter_reports,
    }


def render_markdown(data: Dict[str, Any]) -> str:
    """Render a report dict as a shareable Markdown document."""
    t = data["totals"]
    lines = [
        "# AgentifyAI — Content Ingestion Report",
        "",
        f"- Generated: `{data['generated_at']}`",
        f"- Database: `{data['database_dialect']}`",
        f"- Embeddings: {'ENABLED — ' + data['embeddings_model'] if data['embeddings_enabled'] else 'DISABLED (lexical-only retrieval)'}",
        f"- Status filter: `{data['status_filter']}`",
        "",
        "## Overview",
        "",
        f"- **Chapters:** {t['chapters']}",
        f"- **Pages:** {t['pages']} (extracted {t['extracted_pages']})",
        f"- **Concepts:** {t['concepts']}",
        f"- **Chunks:** {t['chunks']} (embedded {t['embedded_chunks']} / {t['chunks']})",
        f"- **Ingestion jobs logged:** {data['jobs_total']}",
        "",
        "| By status | Count |",
        "|---|---|",
    ]
    for status in STATUS_ORDER:
        if status in data["by_status"]:
            lines.append(f"| {status} | {data['by_status'][status]} |")
    lines += ["", "| By class | Count |", "|---|---|"]
    for cls, count in sorted(data["by_class"].items()):
        lines.append(f"| Class {cls} | {count} |")
    lines += ["", "| By subject | Count |", "|---|---|"]
    for subject, count in sorted(data["by_subject"].items()):
        lines.append(f"| {subject} | {count} |")

    lines += ["", "## Chapters", ""]
    for ch in data["chapters"]:
        live = "🟢 LIVE" if ch["is_live"] else "⚪ not live"
        lines += [
            f"### [{ch['id']}] {ch['board']} · Class {ch['class_level']} · {ch['subject']} · {ch['chapter_name']} ({ch['version']})",
            "",
            f"- Status: **{ch['status']}** ({live}) · slug `{ch['slug']}`",
            f"- Pages: {ch['page_count']} (extracted {ch['extracted_page_count']}) · "
            f"Concepts: {ch['concept_count']} · Chunks: {ch['chunk_count']} (embedded {ch['embedded_chunks']})",
            f"- Coverage score: {ch['coverage_score']} · Extraction quality: {ch['extraction_quality']}",
            f"- Ready for approval: {ch['ready_for_approval']} · Blocking issues: {ch['blocking_issue_count']} · "
            f"Missing source pages: {ch['missing_source_pages'] or 'none'}",
            f"- Approved by: {ch['approved_by'] or '—'} at {ch['approved_at'] or '—'} · Published at: {ch['published_at'] or '—'}",
            f"- Source hash: `{ch['source_hash']}` · Published source hash: `{ch['published_source_hash']}`",
            "",
            "| Concept | Difficulty | Importance | Exam weightage | KP/Ex/Fm | Pages | Issues |",
            "|---|---|---|---|---|---|---|",
        ]
        for c in ch["concepts"]:
            lines.append(
                f"| {c['title'] or c['concept_id']} | {c['difficulty_level']} | {c['importance_level'] or '—'} | "
                f"{c['typical_exam_weightage'] or '—'} | {c['key_points']}/{c['examples']}/{c['formulas']} | "
                f"{','.join(map(str, c['source_pages'])) or '—'} | {c['validation_issues']} |"
            )
        if ch.get("concepts_truncated"):
            lines.append(f"| _…{ch['concept_count'] - len(ch['concepts'])} more concepts (run with --full)_ | | | | | | |")
        lines.append("")
    lines += [
        "---",
        "_KP/Ex/Fm = key points / examples / formulas counts. 🟢 LIVE means the chapter "
        "is approved/published and used by Study Lab + exam retrieval._",
    ]
    return "\n".join(lines)
