import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from Logic.coach.evaluation_suite import SCENARIOS, run_offline_coach_evaluation
from Logic.coach.attachments import prepare_attachments
from Logic.coach.mastery_engine import build_active_mastery_profile
from Logic.coach.multimodal_learning import infer_diagram_specs, parse_formula_signals
from Logic.coach.source_metadata import build_source_bundle
from Logic.coach.specialist_tools import calculator, diagram_helper, formula_checker
from Logic.coach.unified_orchestrator import build_orchestration_plan
from Logic.coach.llm_router import LLMRouter
from Logic.coach.mastery_store import build_mastery_signal, persist_mastery_signal
from Logic.coach.quality_scorer import score_coach_answer
from Logic.coach.query_understanding import understand_query
from Logic.coach.turn_engine import build_adaptive_answer_blocks, parse_semantic_event, semantic_event
from Logic.analytics_engine import get_user_analytics
from Logic.agent_event_bus import AgentEvent
from Logic.observability_store import (
    get_observability_events_since,
    get_observability_summary,
    persist_coach_trace,
    persist_observability_event,
)
from models import (
    AICoachMemory,
    AICoachProfile,
    ModelToolTrace,
    ObservabilityEvent,
    TestHistory,
    UserProgress,
)


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


class AlwaysFailCompletions:
    def create(self, **_kwargs):
        raise RuntimeError("provider unavailable")


class StaticCompletions:
    def __init__(self, content: str):
        self.content = content
        self.calls = 0
        self.requests = []

    def create(self, **kwargs):
        self.calls += 1
        self.requests.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
        )


class StaticStreamCompletions:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if kwargs.get("stream"):
            return [
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=chunk))])
                for chunk in self.chunks
            ]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="".join(self.chunks)))]
        )


class ProviderClient:
    def __init__(self, completions):
        self.chat = SimpleNamespace(completions=completions)


class FakeVisionRouter:
    def complete(self, **_kwargs):
        return """
{
  "visible_text": "Find force when mass = 2 kg and acceleration = 3 m/s^2. CH4 is methane.",
  "handwritten_work": "F = ma",
  "math_lines": ["F = m × a", "F = 2 × 3"],
  "formulas": ["F = ma", "CH4"],
  "diagram_labels": ["object", "force arrow", "mass"],
  "likely_topic": "Newton's second law",
  "confidence": 0.86
}
""".strip()


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
        self.assertGreater(records[1]["estimated_input_tokens"], 0)
        self.assertGreater(records[1]["estimated_output_tokens"], 0)
        self.assertGreaterEqual(records[1]["estimated_cost_usd"], 0)

    def test_router_fails_over_across_providers(self):
        with patch.dict(
            "os.environ",
            {
                "COACH_PROVIDER_ORDER": "groq,openrouter",
                "COACH_LLM_MAX_ATTEMPTS": "2",
                "COACH_BUDGET_ROUTING": "false",
                "OPENROUTER_API_KEY": "test-key",
                "OPENROUTER_TUTOR_MODEL": "openrouter/test-tutor",
            },
            clear=False,
        ):
            router = LLMRouter()
            router._provider_clients["groq"] = ProviderClient(AlwaysFailCompletions())
            router._provider_clients["openrouter"] = ProviderClient(StaticCompletions("OpenRouter recovered"))
            router.begin_turn("turn_provider_failover")

            answer = router.complete("tutor", [{"role": "user", "content": "Explain force"}], max_tokens=120)
            records = router.records()

        self.assertEqual(answer, "OpenRouter recovered")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["provider"], "groq")
        self.assertEqual(records[0]["status"], "error")
        self.assertEqual(records[1]["provider"], "openrouter")
        self.assertEqual(records[1]["status"], "success")
        self.assertTrue(records[1]["fallback"])

    def test_router_stream_fails_over_across_providers(self):
        stream = StaticStreamCompletions(["Better", " answer"])
        with patch.dict(
            "os.environ",
            {
                "COACH_PROVIDER_ORDER": "groq,openrouter",
                "COACH_LLM_MAX_ATTEMPTS": "2",
                "COACH_BUDGET_ROUTING": "false",
                "OPENROUTER_API_KEY": "test-key",
                "OPENROUTER_REVIEW_MODEL": "openrouter/reviewer",
            },
            clear=False,
        ):
            router = LLMRouter()
            router._provider_clients["groq"] = ProviderClient(AlwaysFailCompletions())
            router._provider_clients["openrouter"] = ProviderClient(stream)
            router.begin_turn("turn_stream_failover")

            chunks = list(router.stream("reviewer", [{"role": "user", "content": "Review this"}], max_tokens=120))
            records = router.records()

        text = "".join(chunk.choices[0].delta.content for chunk in chunks)
        self.assertEqual(text, "Better answer")
        self.assertEqual(records[0]["provider"], "groq")
        self.assertEqual(records[0]["status"], "error")
        self.assertEqual(records[1]["provider"], "openrouter")
        self.assertEqual(records[1]["mode"], "stream")
        self.assertEqual(records[1]["status"], "success")
        self.assertGreater(records[1]["estimated_output_tokens"], 0)

    def test_router_prefers_lowest_cost_when_configured(self):
        cheap = StaticCompletions("Cheap route selected")
        with patch.dict(
            "os.environ",
            {
                "COACH_PROVIDER_ORDER": "groq,openrouter",
                "COACH_LLM_MAX_ATTEMPTS": "2",
                "COACH_BUDGET_ROUTING": "true",
                "COACH_ROUTE_PREFERENCE": "lowest_cost",
                "COACH_DAILY_BUDGET_USD": "0",
                "COACH_TURN_BUDGET_USD": "0",
                "OPENROUTER_API_KEY": "test-key",
                "OPENROUTER_TUTOR_MODEL": "openrouter/cheap",
                "COACH_MODEL_PRICES_PER_1M": (
                    "groq:openai/gpt-oss-120b=10:10;"
                    "openrouter:openrouter/cheap=0.001:0.001"
                ),
            },
            clear=False,
        ):
            router = LLMRouter()
            router._provider_clients["groq"] = ProviderClient(StaticCompletions("Expensive route"))
            router._provider_clients["openrouter"] = ProviderClient(cheap)
            router.begin_turn("turn_lowest_cost")

            answer = router.complete("tutor", [{"role": "user", "content": "Explain work"}], max_tokens=120)
            records = router.records()

        self.assertEqual(answer, "Cheap route selected")
        self.assertEqual(records[0]["provider"], "openrouter")
        self.assertEqual(records[0]["budget_action"], "allowed")
        self.assertEqual(cheap.calls, 1)

    def test_router_skips_over_budget_route_and_uses_cheaper_fallback_provider(self):
        cheap = StaticCompletions("Budget safe answer")
        with patch.dict(
            "os.environ",
            {
                "COACH_PROVIDER_ORDER": "groq,openrouter",
                "COACH_LLM_MAX_ATTEMPTS": "2",
                "COACH_BUDGET_ROUTING": "true",
                "COACH_ROUTE_PREFERENCE": "balanced",
                "COACH_DAILY_BUDGET_USD": "0",
                "COACH_TURN_BUDGET_USD": "0.0001",
                "OPENROUTER_API_KEY": "test-key",
                "OPENROUTER_TUTOR_MODEL": "openrouter/cheap",
                "COACH_MODEL_PRICES_PER_1M": (
                    "groq:openai/gpt-oss-120b=10:10;"
                    "openrouter:openrouter/cheap=0.001:0.001"
                ),
            },
            clear=False,
        ):
            router = LLMRouter()
            router._provider_clients["groq"] = ProviderClient(StaticCompletions("Should be skipped"))
            router._provider_clients["openrouter"] = ProviderClient(cheap)
            router.begin_turn("turn_budget_skip")

            answer = router.complete("tutor", [{"role": "user", "content": "Explain pressure"}], max_tokens=120)
            records = router.records()

        self.assertEqual(answer, "Budget safe answer")
        self.assertEqual(records[0]["provider"], "groq")
        self.assertEqual(records[0]["status"], "skipped")
        self.assertEqual(records[0]["budget_action"], "skipped")
        self.assertEqual(records[1]["provider"], "openrouter")
        self.assertEqual(records[1]["status"], "success")
        self.assertEqual(cheap.calls, 1)

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
        self.assertIn("multimodal", bundle.to_dict())

    def test_attachment_validation_rejects_mime_mismatch(self):
        payload = "data:application/pdf;base64,TWF0dGVyIGhhcyBtYXNzLg=="
        bundle = prepare_attachments([{"name": "fake.txt", "mime_type": "text/plain", "data_url": payload}], "Define matter")
        self.assertFalse(bundle.safe_attachments)
        self.assertTrue(any("does not match" in warning for warning in bundle.warnings))

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
        self.assertEqual(formulas["formatted"][0]["display"], "CH₄")
        self.assertFalse(unsafe["used"])

    def test_multimodal_image_extracts_ocr_math_formulas_and_diagram_specs(self):
        one_pixel_png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
        bundle = prepare_attachments(
            [{
                "name": "force-doubt.png",
                "mime_type": "image/png",
                "data_url": f"data:image/png;base64,{one_pixel_png}",
            }],
            "Solve this handwritten force question with a diagram",
            llm_router=FakeVisionRouter(),
        )

        self.assertEqual(bundle.image_count, 1)
        self.assertIn("MULTIMODAL EXTRACTION", bundle.context)
        self.assertIn("F = ma", bundle.context)
        self.assertTrue(bundle.multimodal["math_lines"])
        self.assertIn("CH4", [item["raw"] for item in bundle.multimodal["formulas"]])
        self.assertTrue(bundle.multimodal["diagram_specs"])

        sources = build_source_bundle(None, bundle)
        self.assertTrue(any(item["id"] == "upload-multimodal-extraction" for item in sources["citations"]))

    def test_multimodal_formula_parser_and_diagram_helper_are_student_safe(self):
        formulas = parse_formula_signals("Can carbon form CH4? Use v^2 = u^2 + 2as. Class remains text.")
        raw_values = [formula.raw for formula in formulas]
        self.assertIn("CH4", raw_values)
        self.assertIn("v^2 = u^2 + 2as", raw_values)
        self.assertFalse(any(value in {"Can", "Class"} for value in raw_values))
        self.assertTrue(any(formula.kind == "math" for formula in formulas))

        diagrams = infer_diagram_specs("Explain photosynthesis with a diagram", "CO2 + H2O -> C6H12O6 + O2")
        self.assertEqual(diagrams[0].diagram_type, "process_flow")
        helper = diagram_helper("Draw a circuit with battery and resistor")
        self.assertEqual(helper["diagram_specs"][0]["diagram_type"], "circuit_diagram")

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

    def test_learning_telemetry_uses_measured_session_signals(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        started_at = datetime(2026, 6, 3, 10, 0, 0)
        completed_at = started_at + timedelta(seconds=142)
        db.add(UserProgress(user_id="student_telemetry", total_tests=1, total_questions=1, total_correct=1, xp=10))
        db.add(
            TestHistory(
                user_id="student_telemetry",
                date=date.today(),
                topic="alkanes",
                score=1,
                total_questions=1,
                xp_earned=10,
                time_spent_seconds=142,
                accuracy_rate=100,
                focus_score=84,
                session_type="autonomous_mission",
                started_at=started_at,
                completed_at=completed_at,
                response_latency_ms=780,
                hint_count=1,
                retry_count=2,
                confidence_before=45,
                confidence_after=70,
            )
        )
        db.commit()

        analytics = get_user_analytics(db, "student_telemetry")

        self.assertEqual(analytics["learning_telemetry"]["measured_sessions"], 1)
        self.assertEqual(analytics["learning_telemetry"]["avg_session_seconds"], 142)
        self.assertEqual(analytics["learning_telemetry"]["avg_response_latency_ms"], 780)
        self.assertEqual(analytics["learning_telemetry"]["total_hints_used"], 1)
        self.assertEqual(analytics["learning_telemetry"]["total_retries"], 2)
        self.assertEqual(analytics["learning_telemetry"]["avg_confidence_change"], 25)
        db.close()

    def test_durable_observability_stores_events_and_trace_costs(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        db = Session()

        event = AgentEvent(
            version=7,
            timestamp=datetime.utcnow().isoformat(),
            agent_id="coach",
            event_type="metric",
            data={
                "message": "Coach answer scored and persisted.",
                "latency_ms": 640,
                "estimated_cost_usd": 0.00009,
            },
            session_id="session_obs",
        )
        persist_observability_event(db, event)
        events = get_observability_events_since(db, 0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["version"], 7)
        self.assertEqual(db.query(ObservabilityEvent).count(), 1)

        metrics = persist_coach_trace(
            db,
            user_id="student_obs",
            session_id="session_obs",
            turn_id="turn_obs",
            observability={
                "latency_ms": 1200,
                "query": {"intent": "definition"},
                "retrieval": {"policy": "none"},
                "plan": {"strategy": "simple"},
                "quality": {"passed": True, "score": 0.91},
                "trace": {
                    "phases_ms": {"received": 10, "answering": 1000},
                    "tools": [{"name": "answer_verifier", "passed": True}],
                },
                "model_calls": [
                    {
                        "role": "tutor",
                        "model": "openai/gpt-oss-120b",
                        "provider": "groq",
                        "mode": "complete",
                        "status": "success",
                        "latency_ms": 520,
                        "attempt": 1,
                        "estimated_input_tokens": 220,
                        "estimated_output_tokens": 95,
                        "estimated_cost_usd": 0.000101,
                    }
                ],
            },
        )

        self.assertEqual(metrics["model_calls"], 1)
        self.assertEqual(metrics["tool_calls"], 1)
        self.assertEqual(db.query(ModelToolTrace).count(), 3)
        summary = get_observability_summary(db)
        self.assertEqual(summary["model_calls"], 1)
        self.assertEqual(summary["tool_calls"], 1)
        self.assertEqual(summary["turns"], 1)
        self.assertGreater(summary["estimated_cost_usd"], 0)
        db.close()


if __name__ == "__main__":
    unittest.main()
