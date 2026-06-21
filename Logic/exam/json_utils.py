"""Tolerant JSON extraction and value coercion for LLM output.

LLMs return loosely-typed JSON: fenced in markdown, wrapped in prose, truncated
mid-array, or with numbers where strings are expected. These helpers recover the
usable structure and never raise on bad model output, so an agent can always fall
back to a safe deterministic result. Mirrors the salvage approach already used in
``Logic/content_pipeline`` and ``Logic/section_doubt`` but kept dependency-free.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


def strip_code_fences(text: str) -> str:
    cleaned = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()
    return cleaned


def salvage_json_objects(text: str) -> List[Dict[str, Any]]:
    """Recover every well-formed top-level ``{...}`` object from a string.

    Scans with brace-depth tracking that respects strings/escapes, so a dropped
    comma or a response truncated past max_tokens still yields the complete
    objects emitted before the break.
    """
    objects: List[Dict[str, Any]] = []
    depth = 0
    start: Optional[int] = None
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    obj = None
                if isinstance(obj, dict):
                    objects.append(obj)
                start = None
    return objects


def extract_json_object(value: Any) -> Optional[Dict[str, Any]]:
    """Return a JSON object from a dict, a raw JSON string, or text containing one."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None

    text = strip_code_fences(value)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    salvaged = salvage_json_objects(text)
    return salvaged[0] if salvaged else None


def extract_json_array(value: Any, *, list_keys: tuple[str, ...] = ("items", "questions")) -> List[Dict[str, Any]]:
    """Return a list of objects from a list, an ``{"items": [...]}`` wrapper, or text."""
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in list_keys:
            if isinstance(value.get(key), list):
                return [item for item in value[key] if isinstance(item, dict)]
        return [value]
    if not isinstance(value, str):
        return []

    text = strip_code_fences(value)
    start = text.find("[")
    end = text.rfind("]")
    candidate = text[start : end + 1] if start >= 0 and end > start else text
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return salvage_json_objects(text)
    if isinstance(data, dict):
        for key in list_keys:
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        return salvage_json_objects(text)
    return [item for item in data if isinstance(item, dict)]


# ----------------------------------------------------------------------------
# Value coercion
# ----------------------------------------------------------------------------
def clean_text(value: Any, *, max_len: int = 6000) -> str:
    text = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    return text[:max_len]


def as_str_list(value: Any, *, max_items: int = 50, max_len: int = 600) -> List[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out: List[str] = []
    for item in items:
        if item is None:
            continue
        if isinstance(item, dict):
            # common shapes: {"point": "..."} / {"text": "..."} / {"name": "..."}
            text = item.get("point") or item.get("text") or item.get("name") or item.get("topic") or json.dumps(item)
        else:
            text = str(item)
        text = clean_text(text, max_len=max_len)
        if text:
            out.append(text)
        if len(out) >= max_items:
            break
    return out


def clamp_float(value: Any, lo: float, hi: float, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN guard
        return default
    return max(lo, min(hi, number))


def coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        match = re.search(r"-?\d+", str(value or ""))
        return int(match.group()) if match else default


def coerce_optional_marks(value: Any) -> Optional[float]:
    """Return marks as a float, or ``None`` when the model could not detect them.

    Keeps observed facts honest: an unknown mark must surface as null, never a
    fabricated number.
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"", "null", "none", "n/a", "na", "unknown", "?", "-"}:
        return None
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        marks = float(match.group())
    except ValueError:
        return None
    # Reject implausible values; an out-of-range mark is treated as "unknown".
    return marks if 0 < marks <= 100 else None
