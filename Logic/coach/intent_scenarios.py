"""Data-backed intent scenario retrieval for Study Lab routing."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from functools import lru_cache
import json
from difflib import SequenceMatcher
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set


SCENARIO_BANK_PATH = Path(__file__).resolve().parents[2] / "data" / "coach_intents" / "scenarios.jsonl"

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "for",
    "i",
    "is",
    "it",
    "me",
    "my",
    "now",
    "of",
    "on",
    "the",
    "to",
    "you",
    "your",
}
_ALLOWED_RETRIEVAL_POLICIES = {"none", "optional", "required"}


@dataclass(frozen=True)
class IntentScenario:
    id: str
    message: str
    primary_intent: str
    dialogue_act: str
    emotion: str = "neutral"
    requires_tutor_answer: bool = True
    expected_route: str = "tutor"
    answer_format: str = "concept"
    retrieval_policy: str = "none"
    is_follow_up: bool = False
    response_template: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ScenarioMatch:
    scenario: IntentScenario
    score: float

    def to_dict(self) -> Dict[str, Any]:
        payload = self.scenario.to_dict()
        payload["score"] = self.score
        return payload


@dataclass
class ScenarioIntentProfile:
    primary_intent: str = "unknown"
    dialogue_act: str = ""
    emotion: str = "neutral"
    requires_tutor_answer: bool = True
    expected_route: str = "tutor"
    answer_format: str = "concept"
    retrieval_policy: str = "none"
    is_follow_up: bool = False
    confidence: float = 0.0
    matched_scenarios: List[Dict[str, Any]] = field(default_factory=list)
    response_template: str = ""
    source: str = "scenario_bank"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _normalize(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9\s']", " ", str(value or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: str) -> Set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Z0-9']+", _normalize(value))
        if len(token) > 1 and token not in _STOPWORDS
    }


def _char_ngrams(value: str, size: int = 3) -> Set[str]:
    compact = _normalize(value).replace(" ", "")
    if len(compact) <= size:
        return {compact} if compact else set()
    return {compact[index : index + size] for index in range(len(compact) - size + 1)}


def _jaccard(left: Set[str], right: Set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _coverage(query_tokens: Set[str], scenario_tokens: Set[str]) -> float:
    if not query_tokens:
        return 0.0
    return len(query_tokens & scenario_tokens) / len(query_tokens)


def _scenario_score(message: str, scenario: IntentScenario) -> float:
    query_text = _normalize(message)
    scenario_text = _normalize(scenario.message)
    if not query_text or not scenario_text:
        return 0.0

    query_tokens = _tokens(query_text)
    scenario_tokens = _tokens(scenario_text)
    ratio = SequenceMatcher(None, query_text, scenario_text).ratio()
    token_jaccard = _jaccard(query_tokens, scenario_tokens)
    token_coverage = _coverage(query_tokens, scenario_tokens)
    char_jaccard = _jaccard(_char_ngrams(query_text), _char_ngrams(scenario_text))

    score = (0.38 * ratio) + (0.28 * token_jaccard) + (0.20 * token_coverage) + (0.14 * char_jaccard)
    if query_text == scenario_text:
        score = 1.0
    elif query_text in scenario_text or scenario_text in query_text:
        score = min(1.0, score + 0.08)
    return round(score, 4)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


@lru_cache(maxsize=4)
def load_intent_scenarios(path: str = str(SCENARIO_BANK_PATH)) -> List[IntentScenario]:
    scenarios: List[IntentScenario] = []
    for payload in _read_jsonl(Path(path)):
        retrieval_policy = str(payload.get("retrieval_policy") or "none").strip().lower()
        scenarios.append(
            IntentScenario(
                id=str(payload.get("id") or f"scenario_{len(scenarios) + 1}"),
                message=str(payload.get("message") or ""),
                primary_intent=str(payload.get("primary_intent") or "concept").strip().lower(),
                dialogue_act=str(payload.get("dialogue_act") or "question").strip().lower(),
                emotion=str(payload.get("emotion") or "neutral").strip().lower(),
                requires_tutor_answer=bool(payload.get("requires_tutor_answer", True)),
                expected_route=str(payload.get("expected_route") or "tutor").strip().lower(),
                answer_format=str(payload.get("answer_format") or "concept").strip().lower(),
                retrieval_policy=retrieval_policy if retrieval_policy in _ALLOWED_RETRIEVAL_POLICIES else "none",
                is_follow_up=bool(payload.get("is_follow_up", False)),
                response_template=str(payload.get("response_template") or "").strip(),
            )
        )
    return [scenario for scenario in scenarios if scenario.message.strip()]


def rank_intent_scenarios(
    message: str,
    *,
    scenarios: Optional[Sequence[IntentScenario]] = None,
    limit: int = 5,
) -> List[ScenarioMatch]:
    source = list(scenarios) if scenarios is not None else load_intent_scenarios()
    matches = [
        ScenarioMatch(scenario=scenario, score=_scenario_score(message, scenario))
        for scenario in source
    ]
    matches.sort(key=lambda item: item.score, reverse=True)
    return [match for match in matches[: max(1, limit)] if match.score > 0]


def build_scenario_intent_profile(
    message: str,
    *,
    has_history: bool = False,
    min_confidence: float = 0.58,
    scenarios: Optional[Sequence[IntentScenario]] = None,
) -> ScenarioIntentProfile:
    matches = rank_intent_scenarios(message, scenarios=scenarios, limit=5)
    if not matches or matches[0].score < min_confidence:
        return ScenarioIntentProfile(
            confidence=matches[0].score if matches else 0.0,
            matched_scenarios=[match.to_dict() for match in matches],
        )

    top = matches[0].scenario
    return ScenarioIntentProfile(
        primary_intent=top.primary_intent,
        dialogue_act=top.dialogue_act,
        emotion=top.emotion,
        requires_tutor_answer=top.requires_tutor_answer,
        expected_route=top.expected_route,
        answer_format=top.answer_format,
        retrieval_policy=top.retrieval_policy,
        is_follow_up=bool(top.is_follow_up and has_history),
        confidence=matches[0].score,
        matched_scenarios=[match.to_dict() for match in matches],
        response_template=top.response_template,
    )


def build_conversation_response(profile: ScenarioIntentProfile) -> Optional[str]:
    if profile.requires_tutor_answer or profile.expected_route not in {"conversation_responder", "platform_command"}:
        return None
    if profile.confidence < 0.58:
        return None
    if profile.response_template:
        return profile.response_template
    if profile.expected_route == "platform_command":
        return "That action is handled by the app controls."
    return "Okay. Ask me the next doubt whenever you're ready."
