"""Content ingestion report.

Produces a detailed inventory of everything in the study-content pipeline
(chapters -> pages -> concepts -> chunks/embeddings) so the team can review
exactly what data is live before testing in AgentifyAI.

Runs against whatever ``DATABASE_URL`` the environment is configured for (the
live Neon Postgres for the real corpus, or the local SQLite for dev). It loads
``backend/.env`` automatically, so on the live box it reports the live data.

Usage (from the backend/ directory):
    python scripts/content_report.py                  # console summary + report files
    python scripts/content_report.py --full           # include every concept per chapter
    python scripts/content_report.py --status published   # filter by status
    python scripts/content_report.py --no-files        # console only, write nothing
    python scripts/content_report.py --out myreport    # custom output basename

Outputs (unless --no-files):
    content_ingestion_report.md     human-readable, shareable
    content_ingestion_report.json   machine-readable, same data
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

# --- make the backend package importable and load its .env, like main.py does ---
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(BACKEND_DIR, ".env"))
except Exception:
    pass

from database import SessionLocal, engine  # noqa: E402
from Logic import embeddings as embeddings_service  # noqa: E402
from models import (  # noqa: E402
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


def _iso(value) -> str:
    return value.isoformat() if value else ""


def _safe(value) -> str:
    return str(value) if value is not None else ""


def collect(db, *, status_filter: str | None, include_full_concepts: bool) -> dict:
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

        concepts_view = []
        for concept in concept_rows:
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
            })

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
            "concepts": concepts_view if include_full_concepts else concepts_view[:15],
            "concepts_truncated": (not include_full_concepts) and len(concepts_view) > 15,
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


def render_markdown(data: dict) -> str:
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
        live = "🟢 LIVE" if ch["status"] in {"approved", "published"} else "⚪ not live"
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


def render_console(data: dict) -> str:
    t = data["totals"]
    out = [
        "=" * 64,
        " AGENTIFYAI CONTENT INGESTION REPORT",
        "=" * 64,
        f" Generated : {data['generated_at']}",
        f" Database  : {data['database_dialect']}",
        f" Embeddings: {'ENABLED (' + data['embeddings_model'] + ')' if data['embeddings_enabled'] else 'DISABLED (lexical-only)'}",
        f" Filter    : {data['status_filter']}",
        "-" * 64,
        f" Chapters {t['chapters']} | Pages {t['pages']} (extracted {t['extracted_pages']}) | "
        f"Concepts {t['concepts']} | Chunks {t['chunks']} (embedded {t['embedded_chunks']})",
        f" By status : " + ", ".join(f"{s}={n}" for s, n in data["by_status"].items()),
        f" By class  : " + ", ".join(f"{c}={n}" for c, n in sorted(data["by_class"].items())),
        f" By subject: " + ", ".join(f"{s}={n}" for s, n in sorted(data["by_subject"].items())),
        "=" * 64,
    ]
    for ch in data["chapters"]:
        live = "LIVE" if ch["status"] in {"approved", "published"} else "----"
        out.append(
            f" [{ch['id']}] {live} Class {ch['class_level']} {ch['subject']} - {ch['chapter_name']} "
            f"({ch['status']}, {ch['version']})"
        )
        out.append(
            f"        pages {ch['page_count']} | concepts {ch['concept_count']} | "
            f"chunks {ch['chunk_count']} (emb {ch['embedded_chunks']}) | cov {ch['coverage_score']} | "
            f"ready={ch['ready_for_approval']} blocking={ch['blocking_issue_count']}"
        )
    out.append("=" * 64)
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentifyAI content ingestion report")
    parser.add_argument("--status", default=None, help="filter by chapter status (e.g. published)")
    parser.add_argument("--full", action="store_true", help="list every concept per chapter")
    parser.add_argument("--no-files", action="store_true", help="print to console only")
    parser.add_argument("--out", default="content_ingestion_report", help="output file basename")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        data = collect(db, status_filter=args.status, include_full_concepts=args.full)
    finally:
        db.close()

    print(render_console(data))

    if not args.no_files:
        md_path = f"{args.out}.md"
        json_path = f"{args.out}.json"
        with open(md_path, "w", encoding="utf-8") as handle:
            handle.write(render_markdown(data))
        with open(json_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
        print(f"\nWrote: {md_path}\nWrote: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
