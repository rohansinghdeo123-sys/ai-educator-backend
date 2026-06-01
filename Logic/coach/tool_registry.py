"""Plug-and-play tool registry for the Study Lab coach."""

from dataclasses import dataclass
from typing import Any, Callable, Dict

from .quality_scorer import score_coach_answer
from .retriever import grounded_retriever
from .specialist_tools import (
    answer_verifier,
    calculator,
    diagram_helper,
    formula_checker,
    practice_generator,
    safety_review,
)


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    handler: Callable[..., Any]
    safety_rule: str


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def describe(self) -> Dict[str, Dict[str, str]]:
        return {
            name: {
                "description": tool.description,
                "safety_rule": tool.safety_rule,
            }
            for name, tool in self._tools.items()
        }

    def run(self, name: str, **kwargs: Any) -> Any:
        if name not in self._tools:
            raise KeyError(f"Coach tool '{name}' is not registered.")
        return self._tools[name].handler(**kwargs)


coach_tool_registry = ToolRegistry()
coach_tool_registry.register(ToolDefinition(
    name="knowledge_search",
    description="Retrieve selected topic content from ingested platform study data.",
    handler=grounded_retriever.retrieve,
    safety_rule="Never retrieve or invent outside platform study data.",
))
coach_tool_registry.register(ToolDefinition(
    name="calculator",
    description="Safely evaluate an explicit arithmetic expression for a numerical doubt.",
    handler=calculator,
    safety_rule="Evaluate bounded arithmetic only. Never execute code or arbitrary expressions.",
))
coach_tool_registry.register(ToolDefinition(
    name="formula_checker",
    description="Detect chemistry formulas that require exact symbol, charge, and subscript handling.",
    handler=formula_checker,
    safety_rule="Treat normal words as prose. Validate only formula-shaped tokens.",
))
coach_tool_registry.register(ToolDefinition(
    name="practice_generator",
    description="Prepare a short adaptive practice instruction for the current concept.",
    handler=practice_generator,
    safety_rule="Ask one useful question at a time and avoid cognitive overload.",
))
coach_tool_registry.register(ToolDefinition(
    name="diagram_helper",
    description="Recommend a simple labelled learning visual when it improves understanding.",
    handler=diagram_helper,
    safety_rule="Use diagrams only when they clarify the concept.",
))
coach_tool_registry.register(ToolDefinition(
    name="answer_verifier",
    description="Verify answer quality, grounding, and numerical consistency before delivery.",
    handler=answer_verifier,
    safety_rule="Flag unsupported claims and calculation mismatches before delivery.",
))
coach_tool_registry.register(ToolDefinition(
    name="safety_review",
    description="Apply a student-safe response route for harmful or cheating requests.",
    handler=safety_review,
    safety_rule="Do not enable harm or academic cheating.",
))
coach_tool_registry.register(ToolDefinition(
    name="answer_quality",
    description="Score final answer relevance, grounding, clarity, and formatting.",
    handler=score_coach_answer,
    safety_rule="Reject unsupported claims when strict grounding is active.",
))
