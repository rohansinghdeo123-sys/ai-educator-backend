"""Plug-and-play tool registry for the Study Lab coach."""

from dataclasses import dataclass
from typing import Any, Callable, Dict

from .quality_scorer import score_coach_answer
from .retriever import grounded_retriever


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
    name="answer_quality",
    description="Score final answer relevance, grounding, clarity, and formatting.",
    handler=score_coach_answer,
    safety_rule="Reject unsupported claims when strict grounding is active.",
))
