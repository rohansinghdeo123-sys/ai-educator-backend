"""Lightweight token and cost estimation for coach model calls."""

from __future__ import annotations

import json
import math
import os
from typing import Any, Iterable, Mapping


DEFAULT_INPUT_USD_PER_1M = float(os.getenv("COACH_DEFAULT_INPUT_USD_PER_1M", "0.20"))
DEFAULT_OUTPUT_USD_PER_1M = float(os.getenv("COACH_DEFAULT_OUTPUT_USD_PER_1M", "0.60"))
VISION_IMAGE_TOKEN_ESTIMATE = int(os.getenv("COACH_VISION_IMAGE_TOKEN_ESTIMATE", "800"))


def _price_overrides() -> dict[str, tuple[float, float]]:
    """
    Parse COACH_MODEL_PRICES_PER_1M.

    Format:
        model=input_usd:output_usd;other-model=input_usd:output_usd
    """
    raw = os.getenv("COACH_MODEL_PRICES_PER_1M", "").strip()
    overrides: dict[str, tuple[float, float]] = {}
    if not raw:
        return overrides

    for row in raw.split(";"):
        if "=" not in row or ":" not in row:
            continue
        model, prices = row.split("=", 1)
        input_price, output_price = prices.split(":", 1)
        try:
            overrides[model.strip()] = (float(input_price), float(output_price))
        except ValueError:
            continue
    return overrides


def _extract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        if value.startswith("data:"):
            return ""
        return value
    if isinstance(value, Mapping):
        if value.get("type") in {"image_url", "input_image"}:
            return ""
        return " ".join(_extract_text(item) for key, item in value.items() if key not in {"url", "data_url"})
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        return " ".join(_extract_text(item) for item in value)
    try:
        return json.dumps(value, ensure_ascii=True)
    except TypeError:
        return str(value)


def estimate_text_tokens(value: Any) -> int:
    text = _extract_text(value).strip()
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def estimate_messages_tokens(messages: Iterable[Mapping[str, Any]]) -> int:
    total = 0
    image_count = 0
    for message in messages:
        total += estimate_text_tokens(message.get("role", ""))
        content = message.get("content", "")
        total += estimate_text_tokens(content)
        if isinstance(content, list):
            image_count += sum(
                1
                for item in content
                if isinstance(item, Mapping) and item.get("type") in {"image_url", "input_image"}
            )
    return total + image_count * VISION_IMAGE_TOKEN_ESTIMATE


def estimate_model_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    provider: str = "",
) -> float:
    overrides = _price_overrides()
    provider_key = f"{provider}:{model}" if provider else ""
    input_price, output_price = overrides.get(
        provider_key,
        overrides.get(model, (DEFAULT_INPUT_USD_PER_1M, DEFAULT_OUTPUT_USD_PER_1M)),
    )
    cost = (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price
    return round(cost, 8)
