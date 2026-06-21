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

# --- make the backend package importable and load its .env, like main.py does ---
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(BACKEND_DIR, ".env"))
except Exception:
    pass

from database import SessionLocal  # noqa: E402
# The report logic is shared with the admin API endpoint so both produce the
# identical report (services/content_report_service.py).
from services.content_report_service import build_content_report, render_markdown  # noqa: E402


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
        data = build_content_report(db, status_filter=args.status, include_full_concepts=args.full)
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
