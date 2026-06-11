"""OpenTelemetry trace export for coach turns, model calls, and tool calls.

Design: zero hot-path cost. The coach turn engine already collects complete
observability data (model calls, tools, tokens, cost, latency, quality). This
module mirrors that data into standards-compliant OTel spans at persist time,
using explicit timestamps reconstructed from recorded latencies.

Enabled only when OTEL_EXPORTER_OTLP_ENDPOINT is set (any OTLP/HTTP backend:
Langfuse, Grafana Tempo, Jaeger, LangSmith, ...) or OTEL_TRACES_EXPORTER=console
for local debugging. Otherwise every call here is a cheap no-op.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ai_educator.telemetry")

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor
    from opentelemetry.trace import SpanKind, Status, StatusCode

    _OTEL_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency guard
    _OTEL_AVAILABLE = False

_provider: Optional["TracerProvider"] = None
_tracer = None
_NS_PER_MS = 1_000_000


def telemetry_enabled() -> bool:
    return _tracer is not None


def init_telemetry(span_exporter: Any = None) -> bool:
    """Initialize the tracer provider. Returns True when tracing is active.

    ``span_exporter`` lets tests inject an in-memory exporter; production wiring
    is controlled purely by standard OTEL_* environment variables.
    """
    global _provider, _tracer

    if not _OTEL_AVAILABLE:
        return False
    if _tracer is not None:
        return True

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    console_mode = os.getenv("OTEL_TRACES_EXPORTER", "").strip().lower() == "console"
    if span_exporter is None and not endpoint and not console_mode:
        logger.info("Telemetry disabled: no OTLP endpoint configured.")
        return False

    try:
        resource = Resource.create({
            "service.name": os.getenv("OTEL_SERVICE_NAME", "ai-educator-backend"),
            "service.version": "2.5.0-production-guardrails",
            "deployment.environment": os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "development",
        })
        provider = TracerProvider(resource=resource)
        if span_exporter is not None:
            provider.add_span_processor(SimpleSpanProcessor(span_exporter))
        elif console_mode and not endpoint:
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        else:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        try:
            trace.set_tracer_provider(provider)  # global set is once-per-process; best effort
        except Exception:
            pass
        _provider = provider
        _tracer = provider.get_tracer("ai_educator.coach")
        logger.info("Telemetry enabled (endpoint=%s console=%s)", endpoint or "-", console_mode)
        return True
    except Exception as exc:
        logger.warning("Telemetry initialization failed: %s", exc)
        _provider = None
        _tracer = None
        return False


def shutdown_telemetry() -> None:
    global _provider, _tracer
    if _provider is not None:
        try:
            _provider.shutdown()
        except Exception:
            pass
    _provider = None
    _tracer = None


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def emit_coach_turn_trace(
    *,
    user_id: str,
    session_id: str,
    turn_id: str,
    observability: Dict[str, Any],
) -> None:
    """Mirror one completed coach turn into an OTel span tree.

    Span durations are reconstructed from recorded latencies, so the trace tree
    reflects real timing even though it is emitted after the turn finishes.
    """
    if _tracer is None:
        return

    try:
        observability = _as_dict(observability)
        model_calls: List[Dict[str, Any]] = [
            _as_dict(call) for call in observability.get("model_calls") or []
        ]
        turn_trace = _as_dict(observability.get("trace"))
        tools: List[Dict[str, Any]] = [_as_dict(tool) for tool in turn_trace.get("tools") or []]
        quality = _as_dict(observability.get("quality"))
        retrieval = _as_dict(observability.get("retrieval"))
        query = _as_dict(observability.get("query"))

        turn_latency_ms = _int(observability.get("latency_ms")) or sum(
            _int(call.get("latency_ms")) for call in model_calls
        )
        end_ns = time.time_ns()
        start_ns = end_ns - max(1, turn_latency_ms) * _NS_PER_MS

        total_input = sum(_int(call.get("estimated_input_tokens")) for call in model_calls)
        total_output = sum(_int(call.get("estimated_output_tokens")) for call in model_calls)
        total_cost = round(sum(_float(call.get("estimated_cost_usd")) for call in model_calls), 8)

        turn_span = _tracer.start_span(
            "coach.turn",
            kind=SpanKind.SERVER,
            start_time=start_ns,
            attributes={
                "ai_educator.turn_id": turn_id,
                "ai_educator.session_id": session_id,
                "ai_educator.user_id": user_id,
                "ai_educator.intent": str(query.get("intent") or ""),
                "ai_educator.retrieval_policy": str(retrieval.get("policy") or query.get("retrieval_policy") or ""),
                "ai_educator.retrieval.paragraphs_found": _int(retrieval.get("paragraphs_found")),
                "ai_educator.quality.score": _float(quality.get("score")),
                "ai_educator.quality.passed": bool(quality.get("passed", True)),
                "ai_educator.quality.hallucination_risk": _float(quality.get("hallucination_risk")),
                "gen_ai.usage.input_tokens": total_input,
                "gen_ai.usage.output_tokens": total_output,
                "ai_educator.cost.estimated_usd": total_cost,
            },
        )
        if not quality.get("passed", True):
            turn_span.set_status(Status(StatusCode.ERROR, "quality_needs_review"))

        parent_context = trace.set_span_in_context(turn_span)

        # Model call child spans, laid out sequentially from the turn start.
        cursor_ns = start_ns
        for call in model_calls:
            latency_ms = max(1, _int(call.get("latency_ms")))
            call_status = str(call.get("status") or "success")
            span = _tracer.start_span(
                f"gen_ai.{str(call.get('role') or 'model')}",
                kind=SpanKind.CLIENT,
                context=parent_context,
                start_time=cursor_ns,
                attributes={
                    "gen_ai.operation.name": "chat",
                    "gen_ai.system": str(call.get("provider") or ""),
                    "gen_ai.request.model": str(call.get("model") or ""),
                    "gen_ai.usage.input_tokens": _int(call.get("estimated_input_tokens")),
                    "gen_ai.usage.output_tokens": _int(call.get("estimated_output_tokens")),
                    "ai_educator.cost.estimated_usd": _float(call.get("estimated_cost_usd")),
                    "ai_educator.call.mode": str(call.get("mode") or ""),
                    "ai_educator.call.attempt": _int(call.get("attempt")) or 1,
                    "ai_educator.call.fallback": bool(call.get("fallback")),
                    "ai_educator.call.status": call_status,
                },
            )
            if call_status == "error":
                span.set_status(Status(StatusCode.ERROR, str(call.get("error") or "model_call_failed")[:200]))
            span.end(end_time=cursor_ns + latency_ms * _NS_PER_MS)
            cursor_ns += latency_ms * _NS_PER_MS

        # Tool call child spans.
        for tool in tools:
            latency_ms = max(1, _int(tool.get("latency_ms")))
            span = _tracer.start_span(
                f"tool.{str(tool.get('name') or 'tool')}",
                kind=SpanKind.INTERNAL,
                context=parent_context,
                start_time=cursor_ns,
                attributes={
                    "ai_educator.tool.name": str(tool.get("name") or ""),
                    "ai_educator.tool.policy": str(tool.get("policy") or ""),
                },
            )
            span.end(end_time=cursor_ns + latency_ms * _NS_PER_MS)
            cursor_ns += latency_ms * _NS_PER_MS

        turn_span.end(end_time=end_ns)
    except Exception as exc:
        logger.warning("Could not emit coach turn telemetry: %s", exc)
