import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from Logic.content_pipeline import (
    build_coverage_report,
    chunk_pages,
    infer_metadata_from_pdf_path,
    search_approved_content,
    validate_concept_payloads,
    approve_chapter,
)
from models import ContentChapter, ContentChunk, ContentConcept, ContentPage


class ContentPipelineTests(unittest.TestCase):
    def _session_factory(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        return sessionmaker(bind=engine)

    def test_infer_metadata_from_expected_ncert_path(self):
        root = Path("C:/repo/backend/data/raw/ncert")
        pdf = root / "class_11" / "chemistry" / "chapter_01_some_basic_concepts_of_chemistry.pdf"

        metadata = infer_metadata_from_pdf_path(pdf, root)

        self.assertEqual(metadata["board"], "NCERT")
        self.assertEqual(metadata["class_level"], "11")
        self.assertEqual(metadata["subject"], "Chemistry")
        self.assertEqual(metadata["chapter_number"], 1)
        self.assertEqual(metadata["chapter_name"], "Some Basic Concepts Of Chemistry")
        self.assertIn("class_11", metadata["slug"])

    def test_chunk_pages_and_coverage_report(self):
        pages = [
            {
                "page_number": 1,
                "text": "Photosynthesis is the process used by green plants. It needs sunlight and chlorophyll.",
                "char_count": 84,
                "extraction_quality": 0.8,
            },
            {
                "page_number": 2,
                "text": "The process produces glucose and oxygen. Chlorophyll captures light energy.",
                "char_count": 74,
                "extraction_quality": 0.7,
            },
        ]
        chunks = chunk_pages(pages, max_chars=120, min_chars=10)

        report = build_coverage_report(
            pages,
            [
                {
                    "source_pages": [1, 2],
                }
            ],
            chunks,
        )

        self.assertGreaterEqual(len(chunks), 1)
        self.assertEqual(report["coverage_score"], 1.0)
        self.assertTrue(report["ready_for_approval"])

    def test_validate_concepts_flags_missing_and_out_of_range_sources(self):
        payload = [
            {
                "concept_id": "photosynthesis",
                "title": "Photosynthesis",
                "definition": "Green plants make food using sunlight.",
                "source_pages": [1, 9],
            },
            {
                "concept_id": "empty-source",
                "title": "Empty Source",
                "definition": "A concept without page evidence.",
                "source_pages": [],
            },
        ]

        concepts, issues = validate_concept_payloads(payload, available_pages=[1, 2])

        self.assertEqual(len(concepts), 2)
        issue_names = {name for issue in issues for name in issue.get("issues", [])}
        self.assertIn("source_page_out_of_range", issue_names)
        self.assertIn("missing_source_pages", issue_names)

    def test_approval_requires_validated_concepts(self):
        SessionTesting = self._session_factory()
        db = SessionTesting()
        try:
            chapter = ContentChapter(
                slug="ncert_class_11_chemistry_chapter_1",
                status="indexed",
                validation_report={"ready_for_approval": True},
                concept_count=0,
            )
            db.add(chapter)
            db.commit()

            with self.assertRaisesRegex(ValueError, "no validated concepts"):
                approve_chapter(db, chapter.id, approved_by="tester")
        finally:
            db.close()

    def test_approval_bumps_version_only_when_source_pdf_changed(self):
        SessionTesting = self._session_factory()
        db = SessionTesting()
        try:
            chapter = ContentChapter(
                slug="ncert_class_11_chemistry_chapter_1",
                status="validated",
                source_hash="hash_a",
                validation_report={"ready_for_approval": True},
                concept_count=2,
            )
            db.add(chapter)
            db.commit()
            # Approval now re-evaluates coverage from real rows, so give the
            # chapter pages and concepts that fully cover them.
            for page_number in (1, 2):
                db.add(ContentPage(chapter_id=chapter.id, page_number=page_number,
                                   text="x", char_count=100, extraction_quality=1.0))
            for page_number in (1, 2):
                db.add(ContentConcept(chapter_id=chapter.id, concept_id=f"concept_{page_number}",
                                      title=f"Concept {page_number}", definition="d",
                                      source_pages=[page_number]))
            db.commit()

            # First approval: goes live as v1 and records the live hash.
            approve_chapter(db, chapter.id, approved_by="founder")
            db.commit()
            self.assertEqual(chapter.version, "v1")
            self.assertEqual(chapter.published_source_hash, "hash_a")

            # Re-approval without a PDF change must NOT bump the version.
            chapter.status = "validated"
            db.commit()
            approve_chapter(db, chapter.id, approved_by="founder")
            db.commit()
            self.assertEqual(chapter.version, "v1")

            # Re-ingest with a new PDF (new source hash), then re-approve:
            # the version must bump so traces can name the source revision.
            chapter.status = "validated"
            chapter.source_hash = "hash_b"
            db.commit()
            approve_chapter(db, chapter.id, approved_by="founder")
            db.commit()
            self.assertEqual(chapter.version, "v2")
            self.assertEqual(chapter.published_source_hash, "hash_b")
        finally:
            db.close()

    def test_search_approved_content_ignores_unapproved_chapters(self):
        SessionTesting = self._session_factory()
        db = SessionTesting()
        try:
            approved = ContentChapter(
                board="NCERT",
                class_level="10",
                subject="Science",
                chapter_name="Life Processes",
                slug="ncert_class_10_science_life_processes",
                status="approved",
            )
            draft = ContentChapter(
                board="NCERT",
                class_level="10",
                subject="Science",
                chapter_name="Draft Chapter",
                slug="draft_chapter",
                status="validated",
            )
            db.add_all([approved, draft])
            db.flush()
            db.add(
                ContentConcept(
                    chapter_id=approved.id,
                    concept_id="photosynthesis",
                    title="Photosynthesis",
                    definition="Photosynthesis lets green plants prepare food using light.",
                    core_explanation="Plants convert light energy into chemical energy.",
                    source_pages=[3],
                )
            )
            db.add(
                ContentChunk(
                    chapter_id=draft.id,
                    chunk_id="draft_chunk",
                    text="Draft-only photosynthesis text should not be returned.",
                    page_start=1,
                    page_end=1,
                    lexical_terms=["photosynthesis"],
                )
            )
            db.commit()

            with patch("Logic.content_pipeline.SessionLocal", SessionTesting):
                result = search_approved_content(
                    "photosynthesis",
                    "photosynthesis means",
                    scope={"subject": "Science", "chapter": "Life Processes"},
                )

            self.assertEqual(result["source"], "approved_content_pipeline")
            self.assertIn("green plants prepare food", result["context"])
            self.assertNotIn("Draft-only", result["context"])
        finally:
            db.close()

    def test_search_scope_topic_narrows_to_matching_chapter(self):
        SessionTesting = self._session_factory()
        db = SessionTesting()
        try:
            hydrocarbons = ContentChapter(
                board="NCERT",
                class_level="11",
                subject="Chemistry",
                chapter_name="Hydrocarbons",
                slug="ncert_class_11_chemistry_hydrocarbons",
                status="approved",
            )
            thermodynamics = ContentChapter(
                board="NCERT",
                class_level="11",
                subject="Chemistry",
                chapter_name="Thermodynamics",
                slug="ncert_class_11_chemistry_thermodynamics",
                status="approved",
            )
            db.add_all([hydrocarbons, thermodynamics])
            db.flush()
            db.add(
                ContentChunk(
                    chapter_id=hydrocarbons.id,
                    chunk_id="hydro_chunk",
                    text="Combustion of hydrocarbons releases energy as heat.",
                    page_start=4,
                    page_end=4,
                    lexical_terms=["combustion", "hydrocarbons", "energy"],
                )
            )
            db.add(
                ContentChunk(
                    chapter_id=thermodynamics.id,
                    chunk_id="thermo_chunk",
                    text="Thermodynamics studies combustion energy transfer in systems.",
                    page_start=9,
                    page_end=9,
                    lexical_terms=["combustion", "energy", "thermodynamics"],
                )
            )
            db.commit()

            with patch("Logic.content_pipeline.SessionLocal", SessionTesting):
                # Topic names a chapter: results must come only from it.
                narrowed = search_approved_content(
                    "combustion",
                    "what happens during combustion",
                    scope={"subject": "Chemistry", "topic": "Thermodynamics"},
                )
                # Topic finer-grained than any chapter name: keep all chapters.
                fallback = search_approved_content(
                    "combustion",
                    "what happens during combustion",
                    scope={"subject": "Chemistry", "topic": "alkanes"},
                )

            self.assertIn("Thermodynamics studies combustion", narrowed["context"])
            self.assertNotIn("Combustion of hydrocarbons", narrowed["context"])
            self.assertIn("Combustion of hydrocarbons", fallback["context"])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
