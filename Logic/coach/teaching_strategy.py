"""Dynamic teaching-strategy engine for the Study Lab tutor.

Chooses HOW to teach each turn — the pedagogical arc, analogy freshness,
visualization guidance, and personalization emphasis — before the tutor model
writes a word. The output is a compact instruction block for the draft prompt.

Design rules:
- Strategy is selected deterministically from turn signals (planner output,
  intent, student state, history), so it costs zero extra tokens or latency.
- The arc guides the model's thinking order. It is never a heading template:
  the Response Planner remains the only format authority.
- Analogy domains rotate per (student, week, topic) and avoid domains that
  already appeared in the recent lesson thread, so explanations stay fresh.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Dict, List, Optional

# Analogy source domains with a nudge for the kind of comparison that works.
ANALOGY_DOMAINS: Dict[str, str] = {
    "sports": "team roles, training, scoring, momentum",
    "gaming": "levels, power-ups, respawns, resource limits",
    "food & cooking": "recipes, ingredients, mixing, heat changing things",
    "nature": "rivers, weather, ecosystems, animal behaviour",
    "technology": "phones, batteries, wifi, apps and updates",
    "business & money": "savings, trade, profit and loss, queues at a shop",
    "travel": "routes, maps, tickets, luggage limits",
    "school life": "classrooms, timetables, exams, friend groups",
    "music": "rhythm, instruments in an orchestra, volume and pitch",
    "movies & stories": "heroes, plot twists, scenes building a story",
    "social media": "followers, viral posts, notifications, feeds",
    "everyday home life": "keys and locks, water taps, traffic, crowded buses",
}

_DOMAIN_KEYWORDS: Dict[str, tuple] = {
    "sports": ("cricket", "football", "match", "team", "goal", "player"),
    "gaming": ("game", "level up", "power-up", "video game", "player"),
    "food & cooking": ("recipe", "cooking", "kitchen", "ingredient", "dish"),
    "nature": ("river", "forest", "weather", "ecosystem", "tree"),
    "technology": ("phone", "battery", "wifi", "app ", "computer"),
    "business & money": ("shop", "money", "profit", "market", "price"),
    "travel": ("journey", "train", "map", "ticket", "luggage"),
    "school life": ("classroom", "timetable", "school", "teacher"),
    "music": ("orchestra", "rhythm", "melody", "instrument"),
    "movies & stories": ("movie", "film", "story", "hero", "plot"),
    "social media": ("instagram", "followers", "viral", "notification"),
    "everyday home life": ("tap", "lock", "traffic", "bus", "queue"),
}

# Teaching arcs: ordered thinking moves, not response headings.
TEACHING_ARCS: Dict[str, Dict[str, Any]] = {
    "discovery": {
        "label": "Discovery arc",
        "moves": [
            "Open with one line that makes the student curious about the idea (a surprising fact, question, or everyday situation).",
            "Invite one small prediction or guess before revealing the mechanism, so the student thinks first.",
            "Build intuition with a fresh analogy from the suggested domains before any formal definition.",
            "Help the student picture it: describe what they would see happening, step by step, like a short storyboard.",
            "Only then give the precise scientific explanation with correct terms and notation.",
            "Land it with one real application or exam connection so the idea feels useful.",
        ],
        "note": "Best for first-time concept teaching. Compress or skip moves naturally when the answer should be brief.",
    },
    "reframe": {
        "label": "Reframe arc",
        "moves": [
            "Acknowledge the confusion in one warm, non-judgmental line — never repeat the failed explanation.",
            "Re-teach with a NEW analogy from a suggested domain different from any used earlier in this lesson.",
            "Use smaller steps than before; make each step one idea only.",
            "Close with one tiny check question that confirms the specific point that was confusing.",
        ],
        "note": "The student did not get it the first time. Change the route, not just the words.",
    },
    "worked_reasoning": {
        "label": "Worked reasoning arc",
        "moves": [
            "State what the problem is really asking in one line before any formula.",
            "Ask the student to notice which quantities are given and which is missing (briefly, not as homework).",
            "Solve step by step: formula, substitution, calculation, units — and say WHY each step exists.",
            "Flag the one mistake students most commonly make on this problem type.",
        ],
        "note": "For numericals and derivations. Reasoning transparency beats speed.",
    },
    "exam_coach": {
        "label": "Exam coach arc",
        "moves": [
            "Lead with the exact content that earns marks, in the order an examiner expects.",
            "Attach one memory hook (pattern, contrast, or mnemonic built from the material) to the hardest part.",
            "Point out the trap or wording twist examiners use on this topic.",
        ],
        "note": "Marks-first teaching. Keep it tight and confidence-building.",
    },
    "memory_anchor": {
        "label": "Memory anchor arc",
        "moves": [
            "Compress the topic into its smallest set of load-bearing ideas.",
            "Give each key idea a recall anchor: a vivid contrast, image, or pattern from the material itself.",
            "End with one 10-second self-test the student can run mentally (recall, not re-read).",
        ],
        "note": "For revision. Optimize for recall a week from now, not for reading today.",
    },
    "socratic_practice": {
        "label": "Socratic practice arc",
        "moves": [
            "Ask exactly one question at a time, pitched slightly above the student's demonstrated level.",
            "After the student answers, give feedback that names what was right before what was wrong.",
            "Adjust difficulty based on their last answer — easier after a miss, deeper after a hit.",
        ],
        "note": "Guide with questions. Do not lecture between practice questions.",
    },
    "continuity": {
        "label": "Continuity arc",
        "moves": [
            "Continue the existing thread directly — no re-introduction of the topic, no repeated definitions.",
            "Resolve exactly what the follow-up points at ('why', 'example', 'again') from the last turns.",
            "Add one layer of depth or one new example, then stop.",
        ],
        "note": "Mid-conversation follow-up. The student is already oriented; respect their momentum.",
    },
    "direct": {
        "label": "Direct arc",
        "moves": [
            "Give the exact answer first, cleanly and correctly.",
            "Add at most one line of insight that prevents a misconception — only when it clearly helps.",
        ],
        "note": "The student asked for something short. Honour that completely.",
    },
}


@dataclass
class TeachingStrategy:
    arc_id: str = "discovery"
    arc_label: str = "Discovery arc"
    moves: List[str] = field(default_factory=list)
    arc_note: str = ""
    analogy_domains: List[str] = field(default_factory=list)
    avoided_domains: List[str] = field(default_factory=list)
    visualization_hint: str = ""
    personalization: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "arc_id": self.arc_id,
            "arc_label": self.arc_label,
            "analogy_domains": list(self.analogy_domains),
            "avoided_domains": list(self.avoided_domains),
            "visualization": bool(self.visualization_hint),
            "personalization_signals": len(self.personalization),
        }


def _recent_lesson_text(conversation_context: Optional[Dict[str, Any]]) -> str:
    context = conversation_context or {}
    parts: List[str] = [
        str(context.get("long_term_summary") or ""),
        str(context.get("durable_memory") or ""),
    ]
    lesson_memory = context.get("lesson_memory")
    if isinstance(lesson_memory, dict):
        recent = lesson_memory.get("recent_turns")
        if isinstance(recent, list):
            for turn in recent[-6:]:
                if isinstance(turn, dict):
                    parts.append(str(turn.get("content") or ""))
                else:
                    parts.append(str(turn or ""))
    return " ".join(parts).lower()


def _pick_analogy_domains(
    user_id: str,
    topic: str,
    conversation_context: Optional[Dict[str, Any]],
    count: int = 2,
) -> tuple:
    """Deterministic per-(student, week, topic) rotation with freshness.

    Domains that already appear in the recent lesson thread are skipped, so a
    student who just heard a cricket analogy gets food or travel next, not
    cricket again. Deterministic seeding keeps replays and tests stable.
    """
    from datetime import date

    recent_text = _recent_lesson_text(conversation_context)
    avoided = [
        domain
        for domain, keywords in _DOMAIN_KEYWORDS.items()
        if any(keyword in recent_text for keyword in keywords)
    ]

    iso = date.today().isocalendar()
    seed_material = f"{user_id}|{iso[0]}-w{iso[1]}|{(topic or '').strip().lower()}"
    seed = int.from_bytes(sha256(seed_material.encode("utf-8")).digest()[:8], "big")

    names = list(ANALOGY_DOMAINS)
    ordered = [names[(seed + step * 5) % len(names)] for step in range(len(names))]
    fresh = [name for name in dict.fromkeys(ordered) if name not in avoided]
    picked = (fresh or list(dict.fromkeys(ordered)))[:count]
    return picked, avoided


_VISUAL_TRIGGERS = (
    "structure", "cycle", "process", "flow", "steps", "stages", "diagram",
    "orbit", "circuit", "mechanism", "layers", "graph", "shape", "geometry",
    "anatomy", "pathway", "reaction", "chain", "network", "wave",
)


def _visualization_hint(question: str, response_plan: Optional[Dict[str, Any]]) -> str:
    plan = response_plan or {}
    q = (question or "").lower()
    if str(plan.get("format_style") or "") == "flowchart":
        return (
            "The student asked for a visual: build a clear text flowchart or labelled "
            "step diagram as the centrepiece of the answer."
        )
    if str(plan.get("answer_length") or "") in {"one_line", "short"}:
        return ""
    if any(trigger in q for trigger in _VISUAL_TRIGGERS):
        return (
            "This concept is visual/structural. Where it genuinely helps, add a compact "
            "storyboard moment — 2-4 numbered 'picture this' beats or a small labelled "
            "text diagram — instead of describing everything in prose."
        )
    return ""


def _personalization_notes(
    topic_snapshot: Optional[Dict[str, Any]],
    mastery_profile: Optional[Dict[str, Any]],
    adaptive_context: Optional[Dict[str, Any]],
    is_follow_up: bool,
) -> List[str]:
    notes: List[str] = []
    snapshot = topic_snapshot or {}
    mastery = mastery_profile or {}
    context = adaptive_context or {}
    student_state = context.get("student_state") if isinstance(context.get("student_state"), dict) else {}

    weak_topics = snapshot.get("weak_topics") or []
    if isinstance(weak_topics, list) and weak_topics:
        first = weak_topics[0] if isinstance(weak_topics[0], dict) else {}
        topic_name = str(first.get("topic") or "").replace("_", " ").strip()
        accuracy = first.get("accuracy")
        if topic_name:
            detail = f" (recent accuracy {round(float(accuracy))}%)" if isinstance(accuracy, (int, float)) else ""
            notes.append(
                f"This student's weakest area is '{topic_name}'{detail}. If today's concept touches it, "
                "reinforce the link explicitly instead of assuming it is known."
            )

    weak_anchor = str(mastery.get("weak_topic") or "").replace("_", " ").strip()
    if weak_anchor and all(weak_anchor not in note for note in notes):
        notes.append(
            f"Earlier sessions flagged '{weak_anchor}' as shaky. Prefer examples that quietly re-practice it."
        )

    confidence = str(student_state.get("confidence") or "").lower()
    emotional = str(student_state.get("emotional_state") or "").lower()
    if confidence in {"low", "shaky", "poor"} or emotional in {"frustrated", "anxious", "discouraged"}:
        notes.append(
            "Confidence is low right now: name what the student already got right before correcting, "
            "and keep the next step small enough to guarantee a win."
        )

    speed = str(student_state.get("learning_speed") or "").lower()
    if speed in {"fast", "quick"}:
        notes.append("This student moves fast — skip warm-up padding and get to the interesting part sooner.")
    elif speed in {"slow", "careful", "steady"}:
        notes.append("This student prefers a careful pace — one idea per step, no compressed jumps.")

    if is_follow_up:
        notes.append("Mid-lesson follow-up: build on what was just taught rather than restarting the topic.")

    return notes[:4]


def build_teaching_strategy(
    *,
    question: str,
    intent: str = "",
    response_plan: Optional[Dict[str, Any]] = None,
    adaptive_context: Optional[Dict[str, Any]] = None,
    conversation_context: Optional[Dict[str, Any]] = None,
    topic_snapshot: Optional[Dict[str, Any]] = None,
    mastery_profile: Optional[Dict[str, Any]] = None,
    user_id: str = "",
    topic: str = "",
) -> TeachingStrategy:
    """Select the teaching arc and supporting guidance for this exact turn."""
    plan = dict(response_plan or {})
    context = conversation_context or {}
    intent_lower = (intent or "").lower()
    planner_mode = str(plan.get("mode") or "").lower()
    answer_length = str(plan.get("answer_length") or "medium")
    tone = str(plan.get("tone") or "")
    is_follow_up = bool(context.get("is_follow_up"))
    q = (question or "").lower()

    confused = intent_lower == "clarification" or any(
        marker in q
        for marker in ("don't understand", "do not understand", "confused", "explain again", "not getting", "simpler")
    )

    if answer_length in {"one_line", "short"} and not confused:
        arc_id = "direct"
    elif confused:
        arc_id = "reframe"
    elif planner_mode == "practice":
        arc_id = "socratic_practice"
    elif planner_mode == "exam":
        arc_id = "exam_coach"
    elif planner_mode == "revision":
        arc_id = "memory_anchor"
    elif intent_lower == "numerical" or str(plan.get("format_style") or "") in {"numbered_steps", "derivation"}:
        arc_id = "worked_reasoning"
    elif is_follow_up:
        arc_id = "continuity"
    else:
        arc_id = "discovery"

    arc = TEACHING_ARCS[arc_id]

    # Analogies only help arcs that teach; drills and direct answers skip them.
    analogy_domains: List[str] = []
    avoided: List[str] = []
    if arc_id in {"discovery", "reframe", "continuity"} or (
        arc_id == "memory_anchor" and answer_length not in {"one_line", "short"}
    ):
        analogy_domains, avoided = _pick_analogy_domains(user_id, topic or question, conversation_context)

    visualization = "" if arc_id == "direct" else _visualization_hint(question, plan)

    # Deep-teaching tone earns the full discovery arc; medium answers compress it.
    moves = list(arc["moves"])
    if arc_id == "discovery" and answer_length == "medium" and tone != "deep_teaching":
        moves = [moves[0], moves[2], moves[4], moves[5]]

    return TeachingStrategy(
        arc_id=arc_id,
        arc_label=str(arc["label"]),
        moves=moves,
        arc_note=str(arc["note"]),
        analogy_domains=analogy_domains,
        avoided_domains=avoided,
        visualization_hint=visualization,
        personalization=_personalization_notes(
            topic_snapshot, mastery_profile, adaptive_context, is_follow_up
        ),
    )


def build_teaching_strategy_instruction(strategy: Optional[TeachingStrategy]) -> str:
    """Render the strategy as a compact prompt block (~120-180 tokens)."""
    if strategy is None:
        return ""

    lines: List[str] = [
        "TEACHING STRATEGY (private guidance — decides HOW you teach this turn):",
        f"Arc: {strategy.arc_label}. {strategy.arc_note}",
    ]
    lines.extend(f"{index}. {move}" for index, move in enumerate(strategy.moves, start=1))

    if strategy.analogy_domains:
        domains = "; ".join(
            f"{name} ({ANALOGY_DOMAINS.get(name, '')})" for name in strategy.analogy_domains
        )
        lines.append(f"Fresh analogy domains for this turn: {domains}.")
        if strategy.avoided_domains:
            lines.append(
                "Do NOT reuse analogies from: " + ", ".join(strategy.avoided_domains) + " — already used recently."
            )
        lines.append("Pick ONE domain that genuinely fits the concept; never force an analogy that distorts the science.")

    if strategy.visualization_hint:
        lines.append(strategy.visualization_hint)

    if strategy.personalization:
        lines.append("Personal notes on this student:")
        lines.extend(f"- {note}" for note in strategy.personalization)

    lines.append(
        "The arc orders your thinking, not your headings — the Response Planner still controls format and length. "
        "Blend the moves into natural teaching prose; never label them or mention this strategy."
    )
    return "\n".join(lines)
