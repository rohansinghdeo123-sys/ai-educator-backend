import unittest

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import telemetry


SAMPLE_OBSERVABILITY = {
    "latency_ms": 4200,
    "query": {"intent": "definition", "retrieval_policy": "optional"},
    "retrieval": {"policy": "optional", "paragraphs_found": 3},
    "quality": {"score": 0.84, "passed": True, "hallucination_risk": 0.0},
    "model_calls": [
        {
            "role": "profiler",
            "provider": "groq",
            "model": "fast-model",
            "mode": "complete",
            "status": "success",
            "latency_ms": 600,
            "attempt": 1,
            "fallback": False,
            "estimated_input_tokens": 400,
            "estimated_output_tokens": 120,
            "estimated_cost_usd": 0.0002,
        },
        {
            "role": "reviewer",
            "provider": "openrouter",
            "model": "review-model",
            "mode": "stream",
            "status": "error",
            "latency_ms": 900,
            "attempt": 2,
            "fallback": True,
            "error": "rate limited",
            "estimated_input_tokens": 900,
            "estimated_output_tokens": 0,
            "estimated_cost_usd": 0.0,
        },
    ],
    "trace": {"tools": [{"name": "knowledge_search", "policy": "optional", "latency_ms": 80}]},
}


class TelemetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        telemetry.shutdown_telemetry()
        cls.exporter = InMemorySpanExporter()
        assert telemetry.init_telemetry(span_exporter=cls.exporter)

    @classmethod
    def tearDownClass(cls):
        telemetry.shutdown_telemetry()

    def setUp(self):
        self.exporter.clear()

    def test_emit_coach_turn_trace_builds_span_tree(self):
        telemetry.emit_coach_turn_trace(
            user_id="user-1",
            session_id="coach-user-1-abc",
            turn_id="turn_123",
            observability=SAMPLE_OBSERVABILITY,
        )
        spans = self.exporter.get_finished_spans()
        by_name = {span.name: span for span in spans}

        self.assertIn("coach.turn", by_name)
        self.assertIn("gen_ai.profiler", by_name)
        self.assertIn("gen_ai.reviewer", by_name)
        self.assertIn("tool.knowledge_search", by_name)

        turn = by_name["coach.turn"]
        self.assertEqual(turn.attributes["ai_educator.turn_id"], "turn_123")
        self.assertEqual(turn.attributes["gen_ai.usage.input_tokens"], 1300)
        self.assertEqual(turn.attributes["gen_ai.usage.output_tokens"], 120)
        self.assertAlmostEqual(turn.attributes["ai_educator.cost.estimated_usd"], 0.0002)
        # Turn duration mirrors recorded latency (4200 ms).
        self.assertAlmostEqual((turn.end_time - turn.start_time) / 1e6, 4200, delta=5)

        profiler = by_name["gen_ai.profiler"]
        self.assertEqual(profiler.attributes["gen_ai.system"], "groq")
        self.assertEqual(profiler.attributes["gen_ai.request.model"], "fast-model")
        self.assertEqual(profiler.parent.span_id, turn.context.span_id)

        reviewer = by_name["gen_ai.reviewer"]
        self.assertTrue(reviewer.attributes["ai_educator.call.fallback"])
        self.assertEqual(reviewer.status.status_code.name, "ERROR")

    def test_disabled_telemetry_is_noop(self):
        telemetry.shutdown_telemetry()
        try:
            telemetry.emit_coach_turn_trace(
                user_id="u",
                session_id="s",
                turn_id="t",
                observability=SAMPLE_OBSERVABILITY,
            )  # must not raise
            self.assertFalse(telemetry.telemetry_enabled())
        finally:
            # The old exporter was shut down with the provider; use a fresh one.
            type(self).exporter = InMemorySpanExporter()
            assert telemetry.init_telemetry(span_exporter=type(self).exporter)

    def test_malformed_observability_does_not_raise(self):
        telemetry.emit_coach_turn_trace(
            user_id="u",
            session_id="s",
            turn_id="t",
            observability={"model_calls": "not-a-list", "trace": None, "quality": 5},
        )


if __name__ == "__main__":
    unittest.main()
