"""Compact semantic-style memory helpers for the unified Study Lab coach."""

from typing import Any, Dict, Iterable, List


def build_memory_summary(memories: Iterable[Any], interactions: Iterable[Any], limit: int = 6) -> str:
    lines: List[str] = []
    for memory in list(memories)[:limit]:
        title = str(getattr(memory, "title", "") or "").strip()
        summary = str(getattr(memory, "summary", "") or "").strip()
        if summary:
            lines.append(f"- {title}: {summary}" if title else f"- {summary}")

    recent = []
    for interaction in list(interactions)[-4:]:
        role = "Student" if getattr(interaction, "role", "") == "user" else "Tutor"
        message = str(getattr(interaction, "message", "") or "").strip().replace("\n", " ")
        if message:
            recent.append(f"{role}: {message[:240]}")

    sections = []
    if lines:
        sections.append("Learning memory:\n" + "\n".join(lines))
    if recent:
        sections.append("Recent thread:\n" + "\n".join(recent))
    return "\n\n".join(sections) or "No compact learning memory yet."


def interaction_messages(interactions: Iterable[Any], limit: int = 6) -> List[Dict[str, str]]:
    messages = []
    for interaction in list(interactions)[-limit:]:
        role = str(getattr(interaction, "role", "") or "message")
        content = str(getattr(interaction, "message", "") or "").strip()
        if content:
            messages.append({"role": role, "content": content})
    return messages


def build_layered_lesson_memory(
    coach: Any,
    memories: Iterable[Any],
    interactions: Iterable[Any],
    current_question: str = "",
) -> Dict[str, Any]:
    """Build small memory layers so follow-ups continue the lesson without replaying everything."""
    memory_rows = list(memories)
    interaction_rows = list(interactions)
    recent_turns = interaction_messages(interaction_rows, limit=6)
    current_topic = ""
    unresolved_doubt = ""
    misconceptions: List[str] = []

    for interaction in reversed(interaction_rows):
        metadata = getattr(interaction, "metadata_json", {}) or {}
        learning_context = metadata.get("learning_context") if isinstance(metadata, dict) else {}
        if isinstance(learning_context, dict):
            current_topic = str(
                learning_context.get("selected_topic")
                or learning_context.get("topic")
                or current_topic
            ).strip()
        if unresolved_doubt or getattr(interaction, "role", "") != "user":
            continue
        unresolved_doubt = str(getattr(interaction, "message", "") or "").strip()

    for memory in memory_rows:
        memory_type = str(getattr(memory, "memory_type", "") or "").lower()
        title = str(getattr(memory, "title", "") or "").strip()
        summary = str(getattr(memory, "summary", "") or "").strip()
        if any(term in memory_type for term in ("misconception", "weak", "mistake", "concept_watch")) and summary:
            misconceptions.append(f"{title}: {summary}" if title else summary)

    preferences = getattr(coach, "study_preferences", {}) or {}
    return {
        "recent_turns": recent_turns,
        "current_topic": current_topic or "Open tutor topic",
        "unresolved_doubt": unresolved_doubt or str(current_question or "").strip(),
        "misconceptions": misconceptions[:4],
        "preferences": preferences if isinstance(preferences, dict) else {},
        "long_term_summary": str(getattr(coach, "long_term_summary", "") or "").strip(),
    }


def format_layered_lesson_memory(memory: Dict[str, Any], include_recent_turns: bool = True) -> str:
    """Render lesson memory for a prompt.

    Pass ``include_recent_turns=False`` when the caller already supplies the
    recent thread as real conversation messages, so the same turns are not
    paid for twice in the prompt.
    """
    misconceptions = memory.get("misconceptions") or []
    lines = [
        f"Current topic: {memory.get('current_topic') or 'Open tutor topic'}",
        f"Unresolved doubt: {memory.get('unresolved_doubt') or 'None'}",
        f"Known misconceptions: {', '.join(misconceptions) if misconceptions else 'None recorded'}",
        f"Study preferences: {memory.get('preferences') or 'No preference saved'}",
        f"Long-term summary: {memory.get('long_term_summary') or 'No long-term summary yet'}",
    ]
    if include_recent_turns:
        turns = memory.get("recent_turns") or []
        recent = "\n".join(
            f"- {item.get('role', 'message')}: {str(item.get('content') or '')[:240]}"
            for item in turns
            if isinstance(item, dict)
        )
        lines.extend(["Recent lesson turns:", recent or "- No earlier turns in this lesson."])
    return "\n".join(lines)
