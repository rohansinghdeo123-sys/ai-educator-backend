import os
import unittest
from unittest.mock import patch

from prompts.registry import PromptRegistry, prompt_registry

EXPECTED_PROMPTS = {
    "tutor_agent",
    "revision_summary",
    "revision_explain",
    "revision_keypoints",
    "exam_mcq",
    "exam_probable",
    "orchestrator_intent",
}


class PromptRegistryTests(unittest.TestCase):
    def test_all_default_prompts_load(self):
        self.assertEqual(set(prompt_registry.names()), EXPECTED_PROMPTS)
        for name in EXPECTED_PROMPTS:
            self.assertEqual(prompt_registry.active_version(name), "v1")
            self.assertGreater(len(prompt_registry.get(name).text), 100)

    def test_shim_constants_match_registry_text(self):
        import prompts.agent_prompts as ap

        pairs = {
            "tutor_agent": ap.TUTOR_AGENT_PROMPT,
            "revision_summary": ap.SUMMARY_AGENT_PROMPT,
            "exam_mcq": ap.EXAM_MCQ_PROMPT,
            "orchestrator_intent": ap.ORCHESTRATOR_PROMPT,
        }
        for name, constant in pairs.items():
            self.assertEqual(constant, prompt_registry.get(name).text.rstrip("\n"))
        # Structural markers preserved through migration.
        self.assertIn("{context}", ap.TUTOR_AGENT_PROMPT)
        self.assertIn("{basics}", ap.TUTOR_AGENT_PROMPT)
        self.assertIn("CH₄", ap.EXAM_MCQ_PROMPT)
        self.assertIn("{message}", ap.ORCHESTRATOR_PROMPT)

    def test_render_substitutes_variables(self):
        rendered = prompt_registry.get("orchestrator_intent").render(message="quiz me on alkanes")
        self.assertIn("quiz me on alkanes", rendered)
        self.assertNotIn("{message}", rendered)

    def test_env_override_selects_version_and_falls_back(self):
        registry = PromptRegistry()
        with patch.dict(os.environ, {"PROMPT_VERSION_TUTOR_AGENT": "v1"}):
            self.assertEqual(registry.active_version("tutor_agent"), "v1")
        with patch.dict(os.environ, {"PROMPT_VERSION_TUTOR_AGENT": "v99"}):
            # Unknown version falls back to default instead of crashing.
            self.assertEqual(registry.active_version("tutor_agent"), "v1")

    def test_fingerprint_is_stable_and_version_sensitive(self):
        fp1 = prompt_registry.fingerprint()
        fp2 = prompt_registry.fingerprint()
        self.assertEqual(fp1, fp2)
        self.assertTrue(fp1.startswith("prompts-"))
        self.assertEqual(len(fp1), len("prompts-") + 12)

    def test_unknown_prompt_raises(self):
        with self.assertRaises(KeyError):
            prompt_registry.get("does_not_exist")

    def test_describe_shape(self):
        rows = prompt_registry.describe()
        self.assertEqual(len(rows), len(EXPECTED_PROMPTS))
        for row in rows:
            self.assertIn(row["active_version"], row["available_versions"])
            self.assertGreater(row["chars"], 0)


if __name__ == "__main__":
    unittest.main()
