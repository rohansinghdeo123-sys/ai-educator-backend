from types import SimpleNamespace
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from Logic.coach.evaluation_suite import SCENARIOS, run_offline_coach_evaluation
from Logic.coach.attachments import prepare_attachments
from Logic.coach.mastery_engine import build_active_mastery_profile
from Logic.coach.specialist_tools import calculator, formula_checker
from Logic.coach.unified_orchestrator import build_orchestration_plan
from Logic.coach.llm_router import LLMRouter
from Logic.coach.mastery_store import build_mastery_signal, persist_mastery_signal
from Logic.coach.quality_scorer import score_coach_answer
from Logic.coach.query_understanding import understand_query
from Logic.coach.turn_engine import build_adaptive_answer_blocks, parse_semantic_event, semantic_event
from models import AICoachMemory, AICoachProfile


class FakeCompletions:
    def __init__(self):
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary route failure")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Recovered tutor response"))],
        )


class FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=FakeCompletions())


class CoachArchitectureTests(unittest.TestCase):
    def test_offline_routing_suite(self):
        report = run_offline_coach_evaluation()
        self.assertTrue(report["passed"], report)
        self.assertGreaterEqual(len(SCENARIOS), 150)

    def test_conversational_thanks_does_not_reopen_lesson(self):
        query = understand_query("Thank you", has_history=True)
        self.assertEqual(query.intent, "conversation")
        self.assertFalse(query.needs_retrieval)
        self.assertFalse(query.requires_grounding)

    def test_required_grounding_only_when_requested(self):
        regular = understand_query("Explain alkanes simply")
        grounded = understand_query("Explain alkanes from my notes only")
        self.assertEqual(regular.retrieval_policy, "none")
        self.assertEqual(grounded.retrieval_policy, "required")

    def test_quality_scorer_handles_conversation_and_grounding_guard(self):
        casual = score_coach_answer(
            question="Thanks",
            answer="You are welcome. Send the next doubt when you are ready.",
            strict_grounding=False,
            intent="conversation",
            answer_format="conversation",
        )
        unsupported = score_coach_answer(
            question="Explain alkanes from my notes",
            answer="Alkanes are saturated hydrocarbons.",
            strict_grounding=True,
            intent="definition",
            answer_format="definition",
        )
        self.assertTrue(casual.passed, casual.to_dict())
        self.assertGreaterEqual(unsupported.hallucination_risk, 0.65)

    def test_adaptive_answer_blocks_and_semantic_event(self):
        answer = "Core Idea:\nMatter has mass.\n\nExample:\n- Water occupies space.\n\nQuick Check:\n- Does air occupy space?"
        blocks = build_adaptive_answer_blocks(answer)
        event = parse_semantic_event(semantic_event("answer.completed", turn_id="turn_test", answer=answer, blocks=blocks))
        self.assertEqual(event["event"], "answer.completed")
        self.assertTrue(any(block["kind"] == "example" for block in blocks))
        self.assertTrue(any(block["kind"] == "checkpoint" for block in blocks))

    def test_router_uses_fallback_and_records_route(self):
        router = LLMRouter()
        router._groq = FakeClient()
        router.begin_turn("turn_router")
        answer = router.complete("tutor", [{"role": "user", "content": "Explain matter"}])
        records = router.records()
        self.assertEqual(answer, "Recovered tutor response")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["status"], "error")
        self.assertEqual(records[1]["status"], "success")
        self.assertTrue(records[1]["fallback"])

    def test_unified_orchestrator_routes_specialists_selectively(self):
        simple = build_orchestration_plan(understand_query("Define matter"), "Define matter")
        chemistry = build_orchestration_plan(understand_query("Explain C2H6"), "Explain C2H6")
        numerical = build_orchestration_plan(understand_query("Calculate 20 / 5"), "Calculate 20 / 5")
        homework = build_orchestration_plan(understand_query("Help me solve this homework"), "Help me solve this homework")
        direct = build_orchestration_plan(understand_query("Help me solve this homework"), "Help me solve this homework", direct_answer=True)
        self.assertEqual(simple["tools"], ["answer_verifier"])
        self.assertIn("formula_checker", chemistry["tools"])
        self.assertIn("calculator", numerical["tools"])
        self.assertIn("socratic_tutor", homework["tools"])
        self.assertNotIn("socratic_tutor", direct["tools"])

    def test_text_attachment_becomes_source_context(self):
        payload = "data:text/plain;base64,TWF0dGVyIGhhcyBtYXNzIGFuZCBvY2N1cGllcyBzcGFjZS4="
        bundle = prepare_attachments([{"name": "notes.txt", "mime_type": "text/plain", "data_url": payload}], "Define matter")
        self.assertIn("Matter has mass", bundle.context)
        self.assertEqual(bundle.document_count, 1)
        self.assertEqual(bundle.citations[0]["source"], "Uploaded material")

    def test_active_mastery_profile_simplifies_repeated_confusion(self):
        memory = SimpleNamespace(metadata_json={
            "topic": "alkanes",
            "observations": 3,
            "support_count": 2,
            "average_confidence": 42,
            "last_observed_at": "2026-05-30T00:00:00",
        })
        profile = build_active_mastery_profile([memory], {"topic": "alkanes"})
        self.assertEqual(profile["route"], "simplify_and_reinforce")
        unrelated = build_active_mastery_profile([memory], {"topic": "fractions"})
        self.assertEqual(unrelated["route"], "baseline")

    def test_specialist_calculator_and_formula_checker_are_bounded(self):
        calculation = calculator("Calculate 20 / 5")
        formulas = formula_checker("Explain CH4 and C2H6. Carbon remains a normal word.")
        unsafe = calculator("Run import os")
        self.assertEqual(calculation["result"], 4.0)
        self.assertEqual(formulas["formulas"], ["CH4", "C2H6"])
        self.assertFalse(unsafe["used"])

    def test_mastery_memory_is_deduplicated(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        coach = AICoachProfile(coach_id="coach_test", user_id="student_test")
        db.add(coach)
        db.commit()

        query = understand_query("I am confused about alkanes")
        signal = build_mastery_signal(
            query=query,
            adaptive_context={
                "student_state": {"emotional_state": "confused", "confidence": 35},
                "adaptive_strategy": {"weak_signals": ["confusion"], "answer_style": "simple explanation"},
            },
            scope={"topic": "alkanes"},
            quality={"score": 0.86},
            answer_blocks=[{"kind": "explanation"}],
        )
        persist_mastery_signal(db, coach, signal)
        persist_mastery_signal(db, coach, signal)
        memories = db.query(AICoachMemory).all()
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0].metadata_json["observations"], 2)
        self.assertEqual(memories[0].metadata_json["support_count"], 2)
        db.close()


if __name__ == "__main__":
    unittest.main()
