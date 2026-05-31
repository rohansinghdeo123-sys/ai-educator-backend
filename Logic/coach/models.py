"""Typed internal models for the unified Study Lab coach."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class QueryUnderstanding:
    intent: str
    answer_format: str
    is_conversational: bool = False
    is_follow_up: bool = False
    needs_retrieval: bool = True
    needs_memory: bool = True
    needs_quality_review: bool = True
    requested_tools: List[str] = field(default_factory=list)
    anchor_terms: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RetrievalResult:
    context: str = ""
    section_id: str = ""
    source: str = ""
    paragraphs_found: int = 0
    keywords_used: List[str] = field(default_factory=list)
    scope: Dict[str, str] = field(default_factory=dict)
    supported: bool = False
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CoachPlan:
    route: str
    intent: str
    answer_format: str
    tools: List[str] = field(default_factory=list)
    steps: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QualityReport:
    score: float
    passed: bool
    relevance: float
    grounding: float
    completeness: float
    clarity: float
    student_friendliness: float
    formatting: float
    hallucination_risk: float
    issues: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
