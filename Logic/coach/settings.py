"""Environment-backed settings for the unified Study Lab coach."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class CoachSettings:
    provider: str = os.getenv("COACH_LLM_PROVIDER", "groq")
    fast_model: str = os.getenv("GROQ_FAST_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
    tutor_model: str = os.getenv("GROQ_TUTOR_MODEL", "openai/gpt-oss-120b")
    review_model: str = os.getenv("GROQ_REVIEW_MODEL", "llama-3.3-70b-versatile")
    fallback_model: str = os.getenv("GROQ_FALLBACK_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
    llm_timeout_seconds: float = float(os.getenv("COACH_LLM_TIMEOUT_SECONDS", "22"))
    llm_max_attempts: int = int(os.getenv("COACH_LLM_MAX_ATTEMPTS", "2"))
    max_retrieval_chars: int = int(os.getenv("COACH_MAX_RETRIEVAL_CHARS", "5000"))
    max_retrieval_paragraphs: int = int(os.getenv("COACH_MAX_RETRIEVAL_PARAGRAPHS", "8"))
    memory_limit: int = int(os.getenv("COACH_MEMORY_LIMIT", "6"))
    interaction_limit: int = int(os.getenv("COACH_INTERACTION_LIMIT", "8"))
    strict_grounding_default: bool = os.getenv("COACH_STRICT_GROUNDING", "false").lower() == "true"
    not_found_message: str = os.getenv(
        "COACH_NOT_FOUND_MESSAGE",
        "I could not find this in your study material. Please upload or select the correct chapter/data.",
    )


coach_settings = CoachSettings()
