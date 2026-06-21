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

    chapter_reports = []
    totals = {
        "chapters": 0, "pages": 0, "extracted_pages": 0,
        "concepts": 0, "chunks": 0, "embedded_chunks": 0,
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
        chunk_count = db.query(ContentChunk).filter(ContentChunk.chapter_id == chapter.id).count()
        embedded = (
            db.query(ContentChunk)
            .filter(ContentChunk.chapter_id == chapter.id, ContentChunk.embedding.isnot(None))
            .count()
        )
        page_count = db.query(ContentPage).filter(ContentPage.chapter_id == chapter.id).count()
        report = chapter.validation_report or {}

        concepts_view = [
            {
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
            }
            for concept in concept_rows
        ]
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
            "concepts": shown_concepts,
            "concepts_truncated": (not include_full_concepts) and len(concepts_view) > CONCEPT_PREVIEW_LIMIT,
        })

        totals["chapters"] += 1
        totals["pages"] += page_count
        totals["extracted_pages"] += chapter.extracted_page_count or 0
        totals["concepts"] += len(concept_rows)
        totals["chunks"] += chunk_count
        totals["embedded_chunks"] += embedded
        by_status[chapter.status] += 1
        by_class[str(chapter.class_level or "?")] += 1
        by_subject[str(chapter.subject or "?")] += 1

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
