"""MCQ quality guarantees: no guessed answer keys, no degenerate options,
and deterministic answer-position shuffling."""

import unittest

from Logic.agents.exam_agent import normalize_structured_mcqs
from Logic.section_doubt import (
    mcq_options_are_valid,
    normalize_mcq_questions,
    parse_text_mcqs,
    shuffle_mcq_options,
)


def _question(**overrides):
    base = {
        "id": "Q1",
        "question": "Which formula represents methane?",
        "options": [
            {"key": "A", "text": "CH₄"},
            {"key": "B", "text": "C₂H₆"},
            {"key": "C", "text": "C₃H₈"},
            {"key": "D", "text": "C₂H₄"},
        ],
        "answer": "A",
        "explanation": "Methane is the simplest alkane.",
    }
    base.update(overrides)
    return base


class AnswerKeyTests(unittest.TestCase):
    def test_invalid_answer_key_drops_question_instead_of_defaulting_to_a(self):
        payload = {"questions": [_question(answer="E"), _question(id="Q2", answer=None)]}
        self.assertEqual(normalize_mcq_questions(payload, 5), [])

    def test_exam_agent_normalizer_also_drops_keyless_questions(self):
        payload = {"questions": [_question(answer="not-a-key")]}
        self.assertEqual(normalize_structured_mcqs(payload), [])

    def test_valid_question_survives_with_correct_text_preserved(self):
        payload = {"questions": [_question()]}
        result = normalize_mcq_questions(payload, 5)
        self.assertEqual(len(result), 1)
        correct_key = result[0]["correct"]
        correct_option = result[0]["options"][ord(correct_key) - 65]
        self.assertIn("CH₄", correct_option)

    def test_text_fallback_skips_blocks_without_answer_marker(self):
        text = (
            "Q1. Pick one? A. one B. two C. three D. four "
            "Q2. Real question? A. w B. x C. y D. z Answer: C Explanation: because."
        )
        parsed = parse_text_mcqs(text, 5)
        self.assertEqual(len(parsed), 1)
        correct_key = parsed[0]["correct"]
        self.assertIn("y", parsed[0]["options"][ord(correct_key) - 65])


class OptionValidationTests(unittest.TestCase):
    def test_duplicate_options_are_rejected(self):
        self.assertFalse(mcq_options_are_valid(["A. same", "B. same", "C. other", "D. more"]))

    def test_empty_or_placeholder_options_are_rejected(self):
        self.assertFalse(mcq_options_are_valid(["A. x", "B. ", "C. y", "D. z"]))
        self.assertFalse(mcq_options_are_valid(["A. x", "B. Option unavailable", "C. y", "D. z"]))

    def test_clean_options_pass(self):
        self.assertTrue(mcq_options_are_valid(["A. one", "B. two", "C. three", "D. four"]))

    def test_duplicate_option_question_is_dropped_end_to_end(self):
        bad = _question(
            options=[
                {"key": "A", "text": "same"},
                {"key": "B", "text": "same"},
                {"key": "C", "text": "three"},
                {"key": "D", "text": "four"},
            ]
        )
        self.assertEqual(normalize_mcq_questions({"questions": [bad]}, 5), [])


class ShuffleTests(unittest.TestCase):
    def _base(self):
        return {
            "id": "Q1",
            "question": "Which is the general formula of alkanes?",
            "options": ["A. CₙH₂ₙ₊₂", "B. CₙH₂ₙ", "C. CₙH₂ₙ₋₂", "D. CₙHₙ"],
            "correct": "A",
            "explanation": "Alkanes are saturated hydrocarbons.",
            "source": "alkanes",
        }

    def test_shuffle_is_deterministic_for_the_same_question(self):
        first = shuffle_mcq_options(self._base())
        second = shuffle_mcq_options(self._base())
        self.assertEqual(first, second)

    def test_shuffle_preserves_the_correct_option_text(self):
        shuffled = shuffle_mcq_options(self._base())
        key = shuffled["correct"]
        self.assertIn("CₙH₂ₙ₊₂", shuffled["options"][ord(key) - 65])
        self.assertEqual(len(shuffled["options"]), 4)
        self.assertEqual(
            [option[:3] for option in shuffled["options"]],
            ["A. ", "B. ", "C. ", "D. "],
        )

    def test_shuffle_varies_position_across_questions(self):
        keys = set()
        for index in range(12):
            item = self._base()
            item["question"] = f"Question variant {index}: which formula fits?"
            keys.add(shuffle_mcq_options(item)["correct"])
        self.assertGreater(len(keys), 1, "correct answer never moved off one letter")

    def test_letter_referencing_explanations_are_not_shuffled(self):
        item = self._base()
        item["explanation"] = "Option A is correct because alkanes are saturated."
        self.assertEqual(shuffle_mcq_options(item), item)


if __name__ == "__main__":
    unittest.main()
