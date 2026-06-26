"""Automate the whole study-content pipeline from NCERT to published.

Downloads NCERT chapter PDFs and runs ingest -> generate concepts -> embed ->
auto-publish (only chapters that pass the quality gate; the rest are left as
``needs_review`` for a human to check in the admin page). Runs slowly and
politely, is resumable, and isolates per-chapter failures.

Run against whatever DATABASE_URL is configured (loads backend/.env). On the
live DB this needs the LLM + embeddings keys set (GROQ + EMBEDDINGS_API_KEY).

Usage (from the backend/ directory):
    python scripts/automate_content.py                       # Class 11 & 12 PCM, auto-publish
    python scripts/automate_content.py --classes 11 --subjects Chemistry
    python scripts/automate_content.py --download-only       # just fetch PDFs
    python scripts/automate_content.py --no-publish          # ingest+generate+embed, manual approve
    python scripts/automate_content.py --delay 6 --max-chapters 25
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(BACKEND_DIR, ".env"))
except Exception:
    pass

from Logic.content_automation import DEFAULT_CLASSES, DEFAULT_SUBJECTS, run_automation  # noqa: E402


def _csv(value: str, default):
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentifyAI NCERT content automation")
    parser.add_argument("--classes", default="", help="comma list, e.g. 11,12 (default both)")
    parser.add_argument("--subjects", default="", help="comma list, e.g. Physics,Chemistry,Maths")
    parser.add_argument("--delay", type=float, default=4.0, help="seconds between downloads (politeness)")
    parser.add_argument("--max-chapters", type=int, default=30, help="max chapters probed per book part")
    parser.add_argument("--download-only", action="store_true", help="only download PDFs, no ingestion")
    parser.add_argument("--no-publish", action="store_true", help="ingest+generate+embed but do not auto-publish")
    parser.add_argument("--no-skip", action="store_true", help="re-download/re-process even if present")
    parser.add_argument("--out", default="content_automation_run.json", help="run summary output file")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-5s | %(name)s | %(message)s")

    classes = _csv(args.classes, DEFAULT_CLASSES)
    subjects = _csv(args.subjects, DEFAULT_SUBJECTS)
    print(f"Scope: classes={classes} subjects={subjects} | delay={args.delay}s | "
          f"download_only={args.download_only} auto_publish={not args.no_publish}")

    summary = run_automation(
        classes=classes,
        subjects=subjects,
        delay_seconds=args.delay,
        max_chapters=args.max_chapters,
        auto_publish=not args.no_publish,
        download_only=args.download_only,
        skip_existing=not args.no_skip,
    )
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print(f" Downloaded : {summary['downloaded']}")
    print(f" Published  : {summary['published']}")
    print(f" Needs review: {summary['needs_review']}")
    print(f" Failed     : {len(summary['failed'])}")
    print(f" Summary written to {args.out}")
    print("=" * 60)
    print(" Monitor everything in the admin page (Operations → Data & content pipeline)")
    print(" or run: python scripts/content_report.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
