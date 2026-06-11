# prompts/agent_prompts.py

"""Backward-compatible prompt constants, now served by the versioned registry.

The prompt text lives in prompts/templates/*.yaml. Pin a different version per
prompt without a deploy via environment variables, e.g.:

    PROMPT_VERSION_TUTOR_AGENT=v2

Existing imports (`from prompts.agent_prompts import TUTOR_AGENT_PROMPT`) keep
working unchanged; the constants below resolve to the active version's text.
"""

from prompts.registry import prompt_registry


def _text(name: str) -> str:
    # Literal block YAML adds a trailing newline; the original constants had none.
    return prompt_registry.get(name).text.rstrip("\n")


TUTOR_AGENT_PROMPT = _text("tutor_agent")
SUMMARY_AGENT_PROMPT = _text("revision_summary")
EXPLAIN_AGENT_PROMPT = _text("revision_explain")
KEYPOINTS_AGENT_PROMPT = _text("revision_keypoints")
EXAM_MCQ_PROMPT = _text("exam_mcq")
EXAM_PROBABLE_PROMPT = _text("exam_probable")
ORCHESTRATOR_PROMPT = _text("orchestrator_intent")
