from types import SimpleNamespace
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from Logic.coach.evaluation_suite import run_offline_coach_evaluation
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
