"""Unit tests for the exam-intelligence engine (Logic/exam) — no DB, no network.

The LLM is mocked by patching ``Logic.exam.agents.model_gateway.complete`` so the
agents' sanitizers, fallbacks, and deterministic aggregation are exercised offline.
"""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("ALLOW_SQLITE_FALLBACK", "true")

from Logic.exam import agents, json_utils as J
from Logic.exam.parsers import (
    OCR_NOT_CONFIGURED_MESSAGE,
    ImageOCRPaperParser,
    PDFPaperParser,
    ParserFactory,
    TextPaperParser,
    parse_upload,
)

SAMPLE_PDF = Path("data/raw/ncert/class_11/chemistry/chapter_01_some_basic_concepts_of_chemistry.pdf")


class ParserTests(unittest.TestCase):
    def test_text_parser_reads_bytes(self):
        result = TextPaperParser().parse(b"Q1. Define matter. (2 marks)\nQ2. Explain mole concept. (3)")
        self.assertGreater(result.char_count, 0)
        self.assertEqual(result.page_count, 1)
        self.assertGreater(result.confidence, 0.0)

    def test_empty_text_warns(self):
        result = TextPaperParser().parse(b"")
        self.assertEqual(result.char_count, 0)
        self.assertTrue(result.warnings)

    def test_image_routes_to_ocr_stub(self):
        parser = ParserFactory.for_upload(filename="scan.png", content_type="image/png", data=b"\x89PNG")
        self.assertIsInstance(parser, ImageOCRPaperParser)
        result = parser.parse(b"\x89PNG")
        self.assertEqual(result.text, "")
        self.assertIn(OCR_NOT_CONFIGURED_MESSAGE, result.warnings)

    def test_unsupported_type_raises(self):
        with self.assertRaises(ValueError):
            ParserFactory.for_upload(filename="paper.docx", content_type="application/x-msword", data=b"PK")

    def test_pdf_magic_bytes_override_extension(self):
        # A file mislabeled .txt but containing a PDF header must use the PDF parser.
        parser = ParserFactory.for_upload(filename="paper.txt", content_type="text/plain", data=b"%PDF-1.7 ...")
        self.assertIsInstance(parser, PDFPaperParser)

    def test_pdf_invalid_bytes_degrades_gracefully(self):
        result = PDFPaperParser().parse(b"not a real pdf")
        self.assertEqual(result.char_count, 0)
        self.assertTrue(result.warnings)

    @unittest.skipUnless(SAMPLE_PDF.exists(), "sample NCERT PDF not present")
    def test_pdf_extracts_text_from_real_pdf(self):
        result = parse_upload(SAMPLE_PDF.read_bytes(), filename=SAMPLE_PDF.name, content_type="application/pdf")
        self.assertEqual(result.parser_name, "pdf")
        self.assertGreater(result.char_count, 200)
        self.assertGreater(result.page_count, 0)


class JsonUtilTests(unittest.TestCase):
    def test_extract_object_from_fenced_and_prose(self):
        text = 'Here is the result:\n```json\n{"a": 1, "b": [1,2]}\n```\nThanks!'
        self.assertEqual(J.extract_json_object(text), {"a": 1, "b": [1, 2]})

    def test_extract_array_salvages_truncated(self):
        # Trailing object is incomplete (truncated) — the first two survive.
        text = '[{"q":"a"}, {"q":"b"}, {"q":"c'
        items = J.extract_json_array(text)
        self.assertEqual([i["q"] for i in items], ["a", "b"])

    def test_marks_coercion(self):
        self.assertEqual(J.coerce_optional_marks("3 marks"), 3.0)
        self.assertIsNone(J.coerce_optional_marks("unknown"))
        self.assertIsNone(J.coerce_optional_marks(None))
        self.assertIsNone(J.coerce_optional_marks("250"))  # out of range -> unknown

    def test_clamp_and_lists(self):
        self.assertEqual(J.clamp_float("1.5", 0, 1, 0), 1.0)
        self.assertEqual(J.clamp_float("bad", 0, 1, 0.3), 0.3)
        self.assertEqual(J.as_str_list([{"point": "x"}, "y", None]), ["x", "y"])


ANALYZER_JSON = {
    "paper_title": "Unit Test 1",
    "exam_type": "unit_test",
    "questions": [
        {"question_number": "1", "section_name": "A", "question_text": "Define mole.", "marks": 2,
         "question_type": "short_answer", "intent": "definition", "difficulty": "easy", "topic": "Mole concept",
         "concept_tags": ["mole"], "confidence": 0.9},
        {"question_number": "2", "section_name": "A", "question_text": "Explain mole concept.", "marks": 3,
         "question_type": "short_answer", "intent": "explanation", "difficulty": "medium", "topic": "Mole concept",
         "confidence": 0.8},
        {"question_number": "3", "section_name": "B", "question_text": "Numerical on molarity.", "marks": "unknown",
         "question_type": "numerical", "intent": "numerical", "difficulty": "hard", "topic": "Concentration",
         "confidence": 0.7},
    ],
    "analysis": {"pattern_style": "school_style", "pattern_summary": "Mostly short answers."},
    "confidence": 0.85,
}


class AnalyzerAgentTests(unittest.TestCase):
    def test_analyze_recomputes_aggregates(self):
        with patch.object(agents.model_gateway, "complete", lambda *a, **k: json.dumps(ANALYZER_JSON)):
            result = agents.analyze_paper(paper_text="some paper text", subject="Chemistry")
        self.assertEqual(len(result["questions"]), 3)
        # marks "unknown" preserved as null
        self.assertIsNone(result["questions"][2]["marks"])
        analysis = result["analysis"]
        self.assertEqual(analysis["total_questions"], 3)
        # one mark is unknown -> total not fully detectable
        self.assertIsNone(analysis["total_marks"])
        self.assertEqual(analysis["marks_distribution"], {"2": 1, "3": 1})
        # "Mole concept" appears twice -> repeated concept
        self.assertIn("mole concept", analysis["repeated_concepts"])
        self.assertTrue(any("partial or unknown" in w for w in analysis["warnings"]))

    def test_analyze_llm_failure_is_safe(self):
        def boom(*a, **k):
            raise RuntimeError("provider down")
        with patch.object(agents.model_gateway, "complete", boom):
            result = agents.analyze_paper(paper_text="some paper text")
        self.assertEqual(result["questions"], [])
        self.assertTrue(result["warnings"])  # no crash, reports the problem

    def test_aggregate_two_papers(self):
        with patch.object(agents.model_gateway, "complete", lambda *a, **k: json.dumps(ANALYZER_JSON)):
            a1 = agents.analyze_paper(paper_text="p1")
            a2 = agents.analyze_paper(paper_text="p2")
        agg = agents.aggregate_analyses([a1, a2], papers_meta=[{"chapter_name": "Ch1"}, {"chapter_name": "Ch1"}])
        self.assertEqual(agg["total_questions"], 6)
        self.assertEqual(agg["marks_distribution"], {"2": 2, "3": 2})
        self.assertIn("mole concept", agg["topic_frequency"])
        self.assertTrue(agg["pattern_summary"])


class ProbableAgentTests(unittest.TestCase):
    def test_probable_questions_have_disclaimer(self):
        canned = {"probable_questions": [
            {"id": "P1", "question": "Define mole concept.", "marks": 3, "topic": "Mole", "priority": "high"}],
            "priority_topics": [{"topic": "Mole", "reason": "frequent", "weight": "high"}],
            "strategy_summary": "Focus on mole.", "confidence": 0.7}
        with patch.object(agents.model_gateway, "complete", lambda *a, **k: json.dumps(canned)):
            result = agents.generate_probable_questions(analysis_payload={"high_frequency_concepts": ["Mole"]})
        self.assertEqual(len(result["probable_questions"]), 1)
        self.assertIn("not a prediction or guarantee", result["disclaimer"])
        self.assertFalse(result["fallback_used"])

    def test_probable_fallback_when_llm_junk(self):
        with patch.object(agents.model_gateway, "complete", lambda *a, **k: "no json here"):
            result = agents.generate_probable_questions(
                analysis_payload={"topic_frequency": {"acids": 3, "bases": 2}, "high_frequency_concepts": ["acids", "bases"]}
            )
        self.assertTrue(result["fallback_used"])
        self.assertTrue(result["probable_questions"])
        self.assertTrue(result["disclaimer"])


class WrittenEvalTests(unittest.TestCase):
    EVAL_JSON = {
        "marks_awarded": 99,  # deliberately over marks_total -> must clamp
        "marks_total": 5,
        "covered_points": ["definition"],
        "missing_points": ["example", "formula"],
        "incorrect_points": [],
        "weak_explanation_areas": ["units"],
        "presentation_feedback": "Add steps.",
        "teacher_feedback": "Good start.",
        "model_answer": "Mole is ...",
        "improve_to_full_marks": "Add an example and the formula.",
        "rubric_scores": {"concept_accuracy": 1.5, "completeness": 0.4},
        "next_question_suggestion": "Try molarity.",
        "weakness_tags": [{"topic": "Mole", "weakness_type": "missing_key_points", "note": "no example"}],
    }

    def test_eval_clamps_and_sanitizes(self):
        with patch.object(agents.model_gateway, "complete", lambda *a, **k: json.dumps(self.EVAL_JSON)):
            result = agents.evaluate_written_answer(
                question_text="Explain mole concept.", question_type="long_answer", marks_total=5,
                student_answer="The mole is a unit.", expected_points=["definition", "example", "formula"],
            )
        self.assertLessEqual(result["marks_awarded"], 5.0)
        self.assertLessEqual(result["rubric_scores"]["concept_accuracy"], 1.0)
        self.assertEqual(result["missing_points"], ["example", "formula"])

    def test_empty_answer_scores_zero(self):
        # No LLM call should be needed for an empty answer.
        result = agents.evaluate_written_answer(
            question_text="Q", question_type="short_answer", marks_total=3,
            student_answer="   ", expected_points=["a", "b"],
        )
        self.assertEqual(result["marks_awarded"], 0.0)
        self.assertEqual(result["missing_points"], ["a", "b"])

    def test_eval_fallback_when_llm_fails(self):
        def boom(*a, **k):
            raise RuntimeError("down")
        with patch.object(agents.model_gateway, "complete", boom):
            result = agents.evaluate_written_answer(
                question_text="Explain mole concept.", question_type="long_answer", marks_total=4,
                student_answer="A mole is a counting unit equal to Avogadro number of particles.",
                expected_points=["mole is a counting unit", "equal to avogadro number", "used for amount of substance"],
            )
        self.assertTrue(result["fallback_used"])
        self.assertGreater(result["marks_awarded"], 0.0)
        self.assertLessEqual(result["marks_awarded"], 4.0)

    def test_derive_weaknesses_deterministic(self):
        evaluation = {
            "marks_awarded": 1, "marks_total": 5,
            "missing_points": ["example"], "incorrect_points": ["wrong unit"],
            "weak_explanation_areas": [], "rubric_scores": {"exam_presentation": 0.2, "step_logic": 0.9},
            "weakness_tags": [],
        }
        signals = agents.derive_weaknesses(evaluation=evaluation, subject="Chemistry", topic="Mole")
        types = {s["weakness_type"] for s in signals}
        self.assertIn("missing_key_points", types)
        self.assertIn("concept_gap", types)
        self.assertIn("presentation", types)


if __name__ == "__main__":
    unittest.main()
