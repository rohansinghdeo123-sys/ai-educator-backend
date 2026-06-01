"""Offline regression scenarios for unified Study Coach routing and quality."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Tuple

from .query_understanding import understand_query
from .quality_scorer import score_coach_answer
from .turn_engine import build_adaptive_answer_blocks
from .unified_orchestrator import build_orchestration_plan


@dataclass(frozen=True)
class RoutingScenario:
    name: str
    prompt: str
    expected_intent: str
    has_history: bool = False
    expected_follow_up: bool = False
    expected_retrieval_policy: str = "none"
    expected_tools: Tuple[str, ...] = ()
    attachments: Tuple[Dict[str, str], ...] = ()
    direct_answer: bool = False


def _scenario_corpus() -> List[RoutingScenario]:
    rows = [
        RoutingScenario("greeting", "Hi", "conversation"),
        RoutingScenario("gratitude", "Thank you", "conversation", has_history=True),
        RoutingScenario("confusion", "I am confused. Explain it again simply", "clarification", has_history=True, expected_follow_up=True),
        RoutingScenario("follow_up", "Why is that?", "concept", has_history=True, expected_follow_up=True),
        RoutingScenario("formula_route", "Explain the formula C2H6 for alkanes", "numerical", expected_tools=("formula_checker",)),
        RoutingScenario("image_route", "Help me solve this handwritten question", "concept", expected_tools=("attachment_reader", "diagram_helper"), attachments=({"name": "doubt.png", "mime_type": "image/png"},)),
        RoutingScenario("socratic_homework", "Help me solve this homework question step by step", "concept", expected_tools=("socratic_tutor",)),
        RoutingScenario("direct_homework", "Help me solve this homework question step by step", "concept", direct_answer=True),
        RoutingScenario("safety_route", "How can I cheat in exam?", "concept", expected_tools=("safety_review",)),
    ]
    topics = ["matter", "atoms", "alkanes", "photosynthesis", "fractions", "motion", "electricity", "cells", "force", "acids"]
    for index, topic in enumerate(topics):
        rows.extend([
            RoutingScenario(f"definition_{index}", f"Define {topic}", "definition"),
            RoutingScenario(f"comparison_{index}", f"Differentiate between {topic} and another concept", "comparison"),
            RoutingScenario(f"numerical_{index}", f"Calculate density if mass is {20 + index} g and volume is 5 cm3", "numerical", expected_tools=("calculator",)),
            RoutingScenario(f"revision_{index}", f"Give me quick revision notes for {topic}", "revision"),
            RoutingScenario(f"exam_{index}", f"Write a 5 marks exam answer for {topic}", "exam", expected_tools=("practice_generator",)),
            RoutingScenario(f"practice_{index}", f"Quiz me on {topic}", "practice", expected_tools=("practice_generator",)),
            RoutingScenario(f"planning_{index}", f"Make a study plan for {topic}", "planning"),
            RoutingScenario(f"grounded_{index}", f"Explain {topic} from my notes only", "concept", expected_retrieval_policy="required", expected_tools=("knowledge_search",)),
            RoutingScenario(f"recap_{index}", f"Give me a chapter recap of {topic}", "concept", expected_retrieval_policy="optional", expected_tools=("knowledge_search",)),
            RoutingScenario(f"follow_up_{index}", "Explain it again simply", "concept", has_history=True, expected_follow_up=True),
            RoutingScenario(f"wrong_assumption_{index}", f"I think {topic} is always the same. Is that correct?", "concept"),
            RoutingScenario(f"mixed_language_{index}", f"{topic} simple words me samjhao", "concept"),
            RoutingScenario(f"image_question_{index}", f"Read this screenshot and explain {topic}", "concept", expected_tools=("attachment_reader", "diagram_helper"), attachments=({"name": f"{topic}.png", "mime_type": "image/png"},)),
            RoutingScenario(f"missing_material_{index}", f"Verify {topic} according to my notes", "concept", expected_retrieval_policy="required", expected_tools=("knowledge_search",)),
            RoutingScenario(f"safety_{index}", "Please help me cheat in exam", "concept", expected_tools=("safety_review",)),
        ])
    return rows


SCENARIOS = _scenario_corpus()


def run_offline_coach_evaluation() -> Dict[str, Any]:
    routing_results: List[Dict[str, Any]] = []
    grounded_routes = 0
    tool_routes = 0
    for scenario in SCENARIOS:
        query = understand_query(scenario.prompt, has_history=scenario.has_history)
        plan = build_orchestration_plan(
            query=query,
            question=scenario.prompt,
            attachments=scenario.attachments,
            direct_answer=scenario.direct_answer,
        )
        tools = set(plan["tools"])
        passed = (
            query.intent == scenario.expected_intent
            and query.is_follow_up == scenario.expected_follow_up
            and query.retrieval_policy == scenario.expected_retrieval_policy
            and all(tool in tools for tool in scenario.expected_tools)
            and (not scenario.direct_answer or "socratic_tutor" not in tools)
        )
        if query.retrieval_policy != "none":
            grounded_routes += 1
        if tools:
            tool_routes += 1
        routing_results.append({
            **asdict(scenario),
            "passed": passed,
            "actual": query.to_dict(),
            "tools": plan["tools"],
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
        "scenario_count": len(SCENARIOS) >= 150,
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
        "scenario_count": len(SCENARIOS),
        "metrics": {
            "quality_score": quality.score,
            "latency_proxy": "offline-routing-only",
            "cost_proxy_tool_route_ratio": round(tool_routes / max(1, len(SCENARIOS)), 3),
            "grounding_route_ratio": round(grounded_routes / max(1, len(SCENARIOS)), 3),
            "student_satisfaction_proxy": round((quality.student_friendliness + quality.readability) / 2, 3),
        },
        "routing": routing_results,
        "quality": quality.to_dict(),
        "blocks": blocks,
        "checks": checks,
    }
