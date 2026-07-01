"""End-to-end content automation: download NCERT PDFs and run the full pipeline.

For each (class, subject) in scope this:
  1. politely downloads NCERT chapter PDFs (rate-limited, resumable, validated),
  2. ingests each PDF (pages + chunks),
  3. generates structured concepts via the content agents,
  4. embeds chunks for semantic retrieval,
  5. auto-publishes ONLY chapters that clear the existing quality gate
     (``publish_chapter`` raises if coverage/validation gates fail), otherwise
     leaves them as ``needs_review`` for a human to inspect in the admin report.

Designed to run as a CLI (``scripts/automate_content.py``) against the live DB.
Every chapter is isolated: one failure never stops the whole run.
"""

from __future__ import annotations

import logging
import time
import urllib.robotparser
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

from database import SessionLocal
from Logic.content_pipeline import (
    RAW_NCERT_DIR,
    embed_missing_chunks,
    generate_concepts_for_chapter,
    ingest_pdf_file,
    publish_chapter,
    serialize_chapter,
    titleize,
)
from models import ContentChapter, ContentPage

logger = logging.getLogger("ai_educator.content_automation")

NCERT_PDF_BASE = "https://ncert.nic.in/textbook/pdf"
USER_AGENT = (
    "AgentifyAI-EducationalContentBot/1.0 "
    "(NCERT study-material ingestion for an education app; +https://agentifyai.in)"
)

# NCERT book codes per (class_level, subject). Each subject may span parts; the
# orchestrator numbers chapters continuously across parts. Config-driven so the
# scope can be extended without code changes.
NCERT_BOOKS: Dict[Tuple[str, str], List[str]] = {
    ("11", "Physics"): ["keph1", "keph2"],
    ("11", "Chemistry"): ["kech1", "kech2"],
    ("11", "Maths"): ["kemh1"],
    ("12", "Physics"): ["leph1", "leph2"],
    ("12", "Chemistry"): ["lech1", "lech2"],
    ("12", "Maths"): ["lemh1", "lemh2"],
}

DEFAULT_CLASSES = ["11", "12"]
DEFAULT_SUBJECTS = ["Physics", "Chemistry", "Maths"]
MIN_PDF_BYTES = 10_000


# ---------------------------------------------------------------------------
# Polite downloading
# ---------------------------------------------------------------------------
def _make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def robots_allows(session: requests.Session) -> bool:
    """Respect robots.txt for the textbook PDF path. Empty/missing robots = allow."""
    try:
        resp = session.get("https://ncert.nic.in/robots.txt", timeout=20)
        if resp.status_code != 200 or not resp.text.strip():
            return True
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(resp.text.splitlines())
        return parser.can_fetch(USER_AGENT, f"{NCERT_PDF_BASE}/test.pdf")
    except Exception:
        return True


def _is_valid_pdf(path: Path) -> bool:
    try:
        if path.stat().st_size < MIN_PDF_BYTES:
            return False
        with path.open("rb") as handle:
            return handle.read(5).startswith(b"%PDF")
    except Exception:
        return False


def _download_pdf(session: requests.Session, url: str, dest: Path) -> bool:
    """Download a single PDF to ``dest``; returns True only on a valid PDF."""
    try:
        resp = session.get(url, timeout=90, stream=True)
        if resp.status_code != 200:
            return False
        tmp = dest.with_suffix(".part")
        size = 0
        with tmp.open("wb") as handle:
            for chunk in resp.iter_content(chunk_size=1024 * 64):
                if chunk:
                    handle.write(chunk)
                    size += len(chunk)
        if not _is_valid_pdf(tmp):
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(dest)
        logger.info("downloaded %s (%.1f MB)", dest.name, size / (1024 * 1024))
        return True
    except Exception as exc:  # noqa: BLE001 - one bad download must not stop the run
        logger.warning("download failed %s: %s", url, exc)
        return False


def _remote_pdf_exists(session: requests.Session, code: str, nn: int) -> bool:
    """HEAD-probe a chapter URL (no download) to discover if it exists."""
    try:
        resp = session.head(f"{NCERT_PDF_BASE}/{code}{nn:02d}.pdf", timeout=30, allow_redirects=True)
        return resp.status_code == 200 and "pdf" in resp.headers.get("content-type", "").lower()
    except Exception:
        return False


def download_subject(
    session: requests.Session,
    class_level: str,
    subject: str,
    book_codes: Sequence[str],
    *,
    dest_root: Path = RAW_NCERT_DIR,
    delay_seconds: float = 4.0,
    max_chapters: int = 30,
    skip_existing: bool = True,
) -> List[Path]:
    """Download all chapters for one subject, numbered continuously across parts.

    Discovery is done FIRST (cheap HEAD probes, two-miss stop) to build a stable
    chapter list, THEN files are downloaded to ``chapter_<index>.pdf``. Because
    the index comes from the deterministic discovered list, re-runs are
    resume-safe and never duplicate a chapter under a new number."""
    subject_dir = dest_root / f"class_{class_level}" / subject.lower()
    subject_dir.mkdir(parents=True, exist_ok=True)

    # Phase 1: discover every real chapter across all book parts.
    discovered: List[Tuple[str, int]] = []
    for code in book_codes:
        misses = 0
        for nn in range(1, max_chapters + 1):
            if _remote_pdf_exists(session, code, nn):
                discovered.append((code, nn))
                misses = 0
            else:
                misses += 1
                if misses >= 2:
                    break
            time.sleep(min(delay_seconds, 1.5))

    # Phase 2: download to stable sequential filenames.
    paths: List[Path] = []
    for index, (code, nn) in enumerate(discovered, start=1):
        dest = subject_dir / f"chapter_{index:02d}.pdf"
        if skip_existing and dest.exists() and _is_valid_pdf(dest):
            logger.info("skip (exists) %s", dest.name)
            paths.append(dest)
            continue
        if _download_pdf(session, f"{NCERT_PDF_BASE}/{code}{nn:02d}.pdf", dest):
            paths.append(dest)
        time.sleep(delay_seconds)
    return paths


# ---------------------------------------------------------------------------
# Per-chapter pipeline
# ---------------------------------------------------------------------------
# Lines that begin a sentence/epigraph, not a title — NCERT chapters often open
# with a quotation, so a title must not look like prose.
_TITLE_SKIP_STARTERS = (
    "the ", "it ", "a ", "an ", "in ", "this ", "these ", "those ", "when ",
    "as ", "after ", "every", "chemical", "chemistry deals", "scientists",
)


def _infer_title(db, chapter: ContentChapter) -> str:
    """Best-effort chapter title from the first page. Deliberately strict: only a
    short, clean title-like line qualifies, otherwise "" (caller keeps "Chapter N")."""
    page = (
        db.query(ContentPage)
        .filter(ContentPage.chapter_id == chapter.id)
        .order_by(ContentPage.page_number)
        .first()
    )
    if not page or not page.text:
        return ""
    for raw in [line.strip() for line in page.text.splitlines() if line.strip()][:4]:
        low = raw.lower()
        words = raw.split()
        alpha_ratio = sum(c.isalpha() or c.isspace() for c in raw) / max(len(raw), 1)
        if (
            1 <= len(words) <= 5           # a title, not a sentence
            and len(raw) <= 45
            and raw[0].isupper()           # titles are capitalized; rejects "able to"
            and "." not in raw             # rejects author lines like "Glenn T. Seaborg"
            and "�" not in raw        # rejects garbled-encoding lines
            and not raw.rstrip().endswith((",", ";", ":"))
            and not low.startswith(("chapter", "unit", "page", "ncert"))
            and not low.startswith(_TITLE_SKIP_STARTERS)
            and alpha_ratio > 0.9
        ):
            return titleize(raw)
    return ""


def process_chapter(
    db,
    pdf_path: Path,
    *,
    auto_publish: bool = True,
    max_batch_chars: int = 6000,
) -> Dict[str, Any]:
    """Ingest -> generate concepts -> embed -> (gated) publish one chapter."""
    chapter = ingest_pdf_file(db, pdf_path, root_path=RAW_NCERT_DIR)
    db.commit()
    db.refresh(chapter)

    title = _infer_title(db, chapter)
    if title and title.lower() not in chapter.chapter_name.lower():
        chapter.chapter_name = title
        db.commit()

    generate_concepts_for_chapter(db, chapter.id, max_batch_chars=max_batch_chars)
    db.commit()
    db.refresh(chapter)

    embed_result = embed_missing_chunks(db, chapter_id=chapter.id)
    db.commit()
    db.refresh(chapter)

    final_status = chapter.status
    publish_error = ""
    if auto_publish:
        try:
            publish_chapter(db, chapter.id, published_by="automation")
            db.commit()
            db.refresh(chapter)
            final_status = "published"
        except ValueError as exc:
            # Gate not passed — persist the recomputed report so the admin page
            # can show exactly why, and leave it for human review.
            db.commit()
            db.refresh(chapter)
            final_status = chapter.status or "needs_review"
            publish_error = str(exc)
            logger.info("held for review: %s (%s)", chapter.slug, exc)

    return {
        "chapter": serialize_chapter(chapter),
        "status": final_status,
        "embeddings": embed_result,
        "publish_error": publish_error,
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def run_automation(
    *,
    classes: Optional[Sequence[str]] = None,
    subjects: Optional[Sequence[str]] = None,
    delay_seconds: float = 4.0,
    max_chapters: int = 30,
    auto_publish: bool = True,
    download_only: bool = False,
    skip_existing: bool = True,
    dest_root: Path = RAW_NCERT_DIR,
    db_factory=SessionLocal,
) -> Dict[str, Any]:
    """Run the full automation for the configured scope. Returns a run summary."""
    classes = list(classes or DEFAULT_CLASSES)
    subjects = list(subjects or DEFAULT_SUBJECTS)
    session = _make_session()
    if not robots_allows(session):
        raise RuntimeError("NCERT robots.txt disallows fetching textbook PDFs.")

    summary: Dict[str, Any] = {
        "classes": classes,
        "subjects": subjects,
        "downloaded": 0,
        "published": 0,
        "needs_review": 0,
        "failed": [],
        "chapters": [],
    }

    for class_level in classes:
        for subject in subjects:
            book_codes = NCERT_BOOKS.get((class_level, subject))
            if not book_codes:
                logger.warning("no NCERT books configured for Class %s %s", class_level, subject)
                continue
            logger.info("=== Class %s %s (%s) ===", class_level, subject, ", ".join(book_codes))
            pdf_paths = download_subject(
                session, class_level, subject, book_codes,
                dest_root=dest_root, delay_seconds=delay_seconds,
                max_chapters=max_chapters, skip_existing=skip_existing,
            )
            summary["downloaded"] += len(pdf_paths)
            if download_only:
                continue

            for pdf_path in pdf_paths:
                db = db_factory()
                try:
                    result = process_chapter(db, pdf_path, auto_publish=auto_publish)
                    status = result["status"]
                    if status == "published":
                        summary["published"] += 1
                    else:
                        summary["needs_review"] += 1
                    summary["chapters"].append({
                        "class": class_level, "subject": subject,
                        "file": pdf_path.name, "status": status,
                        "slug": result["chapter"].get("slug"),
                        "coverage": result["chapter"].get("coverage_score"),
                        "concepts": result["chapter"].get("concept_count"),
                        "publish_error": result.get("publish_error", ""),
                    })
                    logger.info("[%s] Class %s %s %s -> %s",
                                status.upper(), class_level, subject, pdf_path.name, result["chapter"].get("slug"))
                except Exception as exc:  # noqa: BLE001 - isolate per-chapter failures
                    db.rollback()
                    logger.exception("FAILED Class %s %s %s", class_level, subject, pdf_path.name)
                    summary["failed"].append({"class": class_level, "subject": subject, "file": pdf_path.name, "error": str(exc)})
                finally:
                    db.close()

    logger.info(
        "Automation done: downloaded=%d published=%d needs_review=%d failed=%d",
        summary["downloaded"], summary["published"], summary["needs_review"], len(summary["failed"]),
    )
    return summary
