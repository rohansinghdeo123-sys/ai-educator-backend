"""Provider-neutral model router for Study Lab coach calls."""

from typing import Any, Dict, Iterable

from groq import Groq
import os

from .settings import coach_settings


class LLMRouter:
    def __init__(self) -> None:
        self._groq = None

    def _client(self) -> Groq:
        if self._groq is None:
            api_key = os.getenv("GROQ_API_KEY")
            if not api_key:
                raise RuntimeError("GROQ_API_KEY is not configured.")
            self._groq = Groq(api_key=api_key)
        return self._groq

    def model_for(self, role: str) -> str:
        return {
            "profiler": coach_settings.fast_model,
            "tutor": coach_settings.tutor_model,
            "reviewer": coach_settings.review_model,
        }.get(role, coach_settings.tutor_model)

    def complete(self, role: str, messages: Iterable[Dict[str, str]], **kwargs: Any) -> str:
        response = self._client().chat.completions.create(
            model=self.model_for(role),
            messages=list(messages),
            stream=False,
            **kwargs,
        )
        return (response.choices[0].message.content or "").strip()

    def stream(self, role: str, messages: Iterable[Dict[str, str]], **kwargs: Any):
        return self._client().chat.completions.create(
            model=self.model_for(role),
            messages=list(messages),
            stream=True,
            **kwargs,
        )


llm_router = LLMRouter()
