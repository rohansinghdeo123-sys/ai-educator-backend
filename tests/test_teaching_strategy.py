import unittest

from Logic.coach.teaching_strategy import (
    ANALOGY_DOMAINS,
    build_teaching_strategy,
    build_teaching_strategy_instruction,
)


class ArcSelectionTests(unittest.TestCase):
    def test_new_concept_gets_discovery_arc(self):
        strategy = build_teaching_strategy(
            question="Why do atoms form covalent bonds?",
            intent="concept",
            response_plan={"mode": "concept_teaching", "answer_length": "detailed", "tone": "deep_teaching"},
            user_id="u1",
        )
        self.assertEqual(strategy.arc_id, "discovery")
        self.assertTrue(strategy.analogy_domains)

    def test_short_answers_stay_direct_with_no_analogies(self):
        strategy = build_teaching_strategy(
            question="answer only: valency of carbon",
            response_plan={"mode": "doubt", "answer_length": "one_line"},
            user_id="u1",
        )
        self.assertEqual(strategy.arc_id, "direct")
        self.assertEqual(strategy.analogy_domains, [])
        self.assertEqual(strategy.visualization_hint, "")

    def test_confusion_triggers_reframe_over_direct(self):
        strategy = build_teaching_strategy(
            question="i don't understand, explain again simpler",
            intent="clarification",
            response_plan={"mode": "concept_teaching", "answer_length": "short"},
            user_id="u1",
        )
        self.assertEqual(strategy.arc_id, "reframe")

    def test_planner_modes_map_to_matching_arcs(self):
        cases = {
            "practice": "socratic_practice",
            "exam": "exam_coach",
            "revision": "memory_anchor",
        }
        for mode, expected in cases.items():
            strategy = build_teaching_strategy(
                question="teach me about mole concept",
                response_plan={"mode": mode, "answer_length": "medium"},
                user_id="u1",
            )
            self.assertEqual(strategy.arc_id, expected, mode)

    def test_follow_up_uses_continuity(self):
        strategy = build_teaching_strategy(
            question="why does that happen?",
            response_plan={"mode": "concept_teaching", "answer_length": "medium"},
            conversation_context={"is_follow_up": True},
            user_id="u1",
        )
        self.assertEqual(strategy.arc_id, "continuity")


class AnalogyFreshnessTests(unittest.TestCase):
    def test_domains_are_deterministic_per_user_and_topic(self):
        make = lambda: build_teaching_strategy(
            question="explain osmosis",
            response_plan={"mode": "concept_teaching", "answer_length": "detailed"},
            user_id="student-a",
            topic="osmosis",
        )
        self.assertEqual(make().analogy_domains, make().analogy_domains)

    def test_different_topics_rotate_domains(self):
        picks = {
            tuple(
                build_teaching_strategy(
                    question=f"explain {topic}",
                    response_plan={"mode": "concept_teaching", "answer_length": "detailed"},
                    user_id="student-a",
                    topic=topic,
                ).analogy_domains
            )
            for topic in ("osmosis", "acids", "vectors", "cells", "energy")
        }
        self.assertGreater(len(picks), 1)

    def test_recently_used_domains_are_avoided(self):
        context = {
            "lesson_memory": {
                "recent_turns": [
                    {"role": "assistant", "content": "Think of a cricket match where the team..."}
                ]
            }
        }
        strategy = build_teaching_strategy(
            question="explain diffusion",
            response_plan={"mode": "concept_teaching", "answer_length": "detailed"},
            conversation_context=context,
            user_id="student-a",
            topic="diffusion",
        )
        self.assertIn("sports", strategy.avoided_domains)
        self.assertNotIn("sports", strategy.analogy_domains)


class InstructionRenderingTests(unittest.TestCase):
    def test_instruction_contains_arc_domains_and_planner_precedence(self):
        strategy = build_teaching_strategy(
            question="explain the structure of the atom",
            response_plan={"mode": "concept_teaching", "answer_length": "detailed", "tone": "deep_teaching"},
            topic_snapshot={"weak_topics": [{"topic": "chemical_bonding", "accuracy": 45.0}]},
            user_id="u1",
            topic="atomic structure",
        )
        text = build_teaching_strategy_instruction(strategy)
        self.assertIn("TEACHING STRATEGY", text)
        self.assertIn("Response Planner still controls format", text)
        self.assertIn("chemical bonding", text)
        for domain in strategy.analogy_domains:
            self.assertIn(domain, text)
        # Visual/structural question gets storyboard guidance.
        self.assertIn("storyboard", text.lower())

    def test_none_strategy_renders_empty(self):
        self.assertEqual(build_teaching_strategy_instruction(None), "")

    def test_domain_pool_is_diverse(self):
        self.assertGreaterEqual(len(ANALOGY_DOMAINS), 10)


if __name__ == "__main__":
    unittest.main()
