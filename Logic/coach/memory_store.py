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
