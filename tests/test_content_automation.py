"""Tests for the NCERT content automation orchestrator (no network, no LLM)."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "true")
os.environ["DATABASE_URL"] = ""

from Logic import content_automation as automation


class DownloadTests(unittest.TestCase):
    def test_discovers_chapters_and_stops_after_two_misses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_dl(session, url, dest):
                # Chapters 01-03 exist; 04+ are 404 (two misses → stop).
                if url.endswith(("01.pdf", "02.pdf", "03.pdf")):
                    dest.write_bytes(b"%PDF-1.4" + b"0" * 20000)
                    return True
                return False

            with patch.object(automation, "_download_pdf", side_effect=fake_dl):
                paths = automation.download_subject(
                    automation._make_session(), "11", "Chemistry", ["kech1"],
                    dest_root=root, delay_seconds=0, max_chapters=10,
                )
            self.assertEqual([p.name for p in paths], ["chapter_01.pdf", "chapter_02.pdf", "chapter_03.pdf"])

    def test_skip_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = root / "class_11" / "chemistry" / "chapter_01.pdf"
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b"%PDF-1.4" + b"0" * 20000)

            calls = []
            with patch.object(automation, "_download_pdf", side_effect=lambda s, u, d: calls.append(u) or False):
                paths = automation.download_subject(
                    automation._make_session(), "11", "Chemistry", ["kech1"],
                    dest_root=root, delay_seconds=0, max_chapters=3,
                )
            self.assertIn(existing, paths)  # pre-existing valid PDF kept
            self.assertNotIn("kech101", "".join(calls))  # chapter 01 not re-downloaded


class ProcessChapterTests(unittest.TestCase):
    def _patches(self, publish_side_effect=None):
        chapter = SimpleNamespace(id=1, status="validated", chapter_name="Chapter 1", slug="ncert_11_chem_1")
        db = MagicMock()
        pub = patch.object(automation, "publish_chapter", side_effect=publish_side_effect)
        return chapter, db, pub

    def test_publishes_when_gate_passes(self):
        chapter, db, pub_patch = self._patches(publish_side_effect=lambda *a, **k: None)
        with patch.object(automation, "ingest_pdf_file", return_value=chapter), \
             patch.object(automation, "_infer_title", return_value=""), \
             patch.object(automation, "generate_concepts_for_chapter"), \
             patch.object(automation, "embed_missing_chunks", return_value={"embedded": 5}), \
             pub_patch, \
             patch.object(automation, "serialize_chapter", return_value={"slug": chapter.slug, "coverage_score": 0.8, "concept_count": 5}):
            result = automation.process_chapter(db, Path("x.pdf"))
        self.assertEqual(result["status"], "published")

    def test_needs_review_when_gate_fails(self):
        chapter, db, pub_patch = self._patches(publish_side_effect=ValueError("not ready for approval"))
        chapter.status = "needs_review"
        with patch.object(automation, "ingest_pdf_file", return_value=chapter), \
             patch.object(automation, "_infer_title", return_value=""), \
             patch.object(automation, "generate_concepts_for_chapter"), \
             patch.object(automation, "embed_missing_chunks", return_value={"embedded": 0}), \
             pub_patch, \
             patch.object(automation, "serialize_chapter", return_value={"slug": chapter.slug, "coverage_score": 0.4, "concept_count": 3}):
            result = automation.process_chapter(db, Path("x.pdf"))
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("not ready", result["publish_error"])


class OrchestratorTests(unittest.TestCase):
    def test_run_summary_counts_and_isolates_failures(self):
        results = [
            {"status": "published", "chapter": {"slug": "s1", "coverage_score": 0.8, "concept_count": 5}, "publish_error": ""},
            ValueError("boom"),  # this chapter raises -> isolated as failed
            {"status": "needs_review", "chapter": {"slug": "s3", "coverage_score": 0.4, "concept_count": 3}, "publish_error": "not ready"},
        ]

        def fake_process(db, path, **kwargs):
            outcome = results.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with patch.object(automation, "robots_allows", return_value=True), \
             patch.object(automation, "download_subject", return_value=[Path("a.pdf"), Path("b.pdf"), Path("c.pdf")]), \
             patch.object(automation, "process_chapter", side_effect=fake_process):
            summary = automation.run_automation(
                classes=["11"], subjects=["Chemistry"], delay_seconds=0,
                db_factory=lambda: MagicMock(),
            )
        self.assertEqual(summary["downloaded"], 3)
        self.assertEqual(summary["published"], 1)
        self.assertEqual(summary["needs_review"], 1)
        self.assertEqual(len(summary["failed"]), 1)

    def test_download_only_skips_processing(self):
        with patch.object(automation, "robots_allows", return_value=True), \
             patch.object(automation, "download_subject", return_value=[Path("a.pdf")]), \
             patch.object(automation, "process_chapter") as proc:
            summary = automation.run_automation(classes=["11"], subjects=["Chemistry"], download_only=True, db_factory=lambda: MagicMock())
        proc.assert_not_called()
        self.assertEqual(summary["downloaded"], 1)


if __name__ == "__main__":
    unittest.main()
