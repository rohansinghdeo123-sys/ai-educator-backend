"""Offline regression scenarios for the Study Lab coach routing layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List

from .query_understanding import understand_query
from .quality_scorer import score_coach_answer
from .turn_engine import build_adaptive_answer_blocks


@dataclass(frozen=True)
class RoutingScenario:
    name: str
    prompt: str
    expected_intent: str
    has_history: bool = False
    expected_follow_up: bool = False
    expected_retrieval_policy: str = "none"


SCENARIOS = [
    RoutingScenario("greeting", "Hi", "conversation"),
    RoutingScenario("gratitude", "Thank you", "conversation", has_history=True),
    RoutingScenario("definition", "Define matter", "definition"),
    RoutingScenario("comparison", "Differentiate between mass and weight", "comparison"),
    RoutingScenario("numerical", "Calculate density if mass is 20 g and volume is 5 cm3", "numerical"),
    RoutingScenario("confusion", "I am confused. Explain it again simply", "clarification", has_history=True, expected_follow_up=True),
    RoutingScenario("follow_up", "Why is that?", "concept", has_history=True, expected_follow_up=True),
    RoutingScenario("revision", "Give me quick revision notes for alkanes", "revision"),
    RoutingScenario("exam", "Write a 5 marks exam answer for states of matter", "exam"),
    RoutingScenario("practice", "Quiz me on hydrocarbons", "practice"),
    RoutingScenario("planning", "Make a study plan for chemistry", "planning"),
    RoutingScenario(
        "grounded_notes",
        "Explain alkanes from my notes only",
        "concept",
        expected_retrieval_policy="required",
    ),
    RoutingScenario(
        "optional_chapter_recap",
        "Give me a chapter recap of hydrocarbons",
        "concept",
        expected_retrieval_policy="optional",
    ),
]


def run_offline_coach_evaluation() -> Dict[str, Any]:
    routing_results: List[Dict[str, Any]] = []
    for scenario in SCENARIOS:
        query = understand_query(scenario.prompt, has_history=scenario.has_history)
        passed = (
            query.intent == scenario.expected_intent
            and query.is_follow_up == scenario.expected_follow_up
            and query.retrieval_policy == scenario.expected_retrieval_policy
        )
        routing_results.append({
            **asdict(scenario),
            "passed": passed,
            "actual": query.to_dict(),
        })

    formatted_answer = (
        "Definition:\nMatter is anything that has mass and occupies space.\n\n"
        "Example:\n- Water occupies space in a bottle.\n\n"
        "Quick Check:\n- Does air occupy space?"
    )
    quality = score_coach_answer(
        question="Define matter with an example",
        answer=formatted_answer,
        strict_grounding=False,
        intent="definition",
        answer_format="definition",
    )
    blocks = build_adaptive_answer_blocks(formatted_answer)
    checks = {
        "quality_passed": quality.passed,
        "intent_satisfaction": quality.intent_satisfaction >= 0.7,
        "readability": quality.readability >= 0.75,
        "adaptive_blocks": len(blocks) == 3,
        "example_block": any(block["kind"] == "example" for block in blocks),
        "checkpoint_block": any(block["kind"] == "checkpoint" for block in blocks),
    }
    passed_count = sum(1 for result in routing_results if result["passed"]) + sum(bool(value) for value in checks.values())
    total_count = len(routing_results) + len(checks)
    return {
        "passed": passed_count == total_count,
        "passed_checks": passed_count,
        "total_checks": total_count,
        "routing": routing_results,
        "quality": quality.to_dict(),
        "blocks": blocks,
        "checks": checks,
    }
