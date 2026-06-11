"""Versioned prompt registry backed by YAML files in prompts/templates/.

Each YAML file defines one named prompt with one or more versions:

    name: tutor_agent
    description: ...
    default_version: v1
    versions:
      v1:
        template: |
          ...prompt text...

The active version is ``default_version`` unless overridden per prompt with an
environment variable (no deploy needed):

    PROMPT_VERSION_TUTOR_AGENT=v2

``fingerprint()`` hashes every active (name, version, text) so deployments and
the admin model registry can report exactly which prompt set is live.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List

import yaml

logger = logging.getLogger("ai_educator.prompts.registry")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: str
    text: str
    description: str = ""

    def render(self, **variables: Any) -> str:
        return self.text.format(**variables)


class PromptRegistry:
    def __init__(self, templates_dir: Path = TEMPLATES_DIR) -> None:
        self._templates_dir = templates_dir
        self._prompts: Dict[str, Dict[str, PromptTemplate]] = {}
        self._defaults: Dict[str, str] = {}
        self._descriptions: Dict[str, str] = {}
        self.load()

    def load(self) -> None:
        self._prompts.clear()
        self._defaults.clear()
        self._descriptions.clear()
        if not self._templates_dir.is_dir():
            logger.warning("Prompt templates directory missing: %s", self._templates_dir)
            return
        for path in sorted(self._templates_dir.glob("*.yaml")):
            try:
                payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                name = str(payload.get("name") or path.stem)
                description = str(payload.get("description") or "")
                versions = payload.get("versions") or {}
                if not isinstance(versions, dict) or not versions:
                    raise ValueError("prompt file must define a non-empty 'versions' mapping")
                parsed: Dict[str, PromptTemplate] = {}
                for version, body in versions.items():
                    text = str((body or {}).get("template") or "")
                    if not text.strip():
                        raise ValueError(f"version '{version}' has an empty template")
                    parsed[str(version)] = PromptTemplate(
                        name=name, version=str(version), text=text, description=description
                    )
                default_version = str(payload.get("default_version") or sorted(parsed)[0])
                if default_version not in parsed:
                    raise ValueError(f"default_version '{default_version}' not in versions")
                self._prompts[name] = parsed
                self._defaults[name] = default_version
                self._descriptions[name] = description
            except Exception as exc:
                logger.error("Could not load prompt file %s: %s", path, exc)
        logger.info("Prompt registry loaded %d prompt(s)", len(self._prompts))

    def _env_override(self, name: str) -> str:
        env_key = "PROMPT_VERSION_" + re.sub(r"[^A-Z0-9]+", "_", name.upper())
        return os.getenv(env_key, "").strip()

    def active_version(self, name: str) -> str:
        if name not in self._prompts:
            raise KeyError(f"Unknown prompt '{name}'")
        override = self._env_override(name)
        if override and override in self._prompts[name]:
            return override
        if override:
            logger.warning(
                "Prompt '%s' has no version '%s'; using default '%s'",
                name, override, self._defaults[name],
            )
        return self._defaults[name]

    def get(self, name: str, version: str = "") -> PromptTemplate:
        if name not in self._prompts:
            raise KeyError(f"Unknown prompt '{name}'")
        selected = version or self.active_version(name)
        if selected not in self._prompts[name]:
            raise KeyError(f"Prompt '{name}' has no version '{selected}'")
        return self._prompts[name][selected]

    def names(self) -> List[str]:
        return sorted(self._prompts)

    def active_versions(self) -> Dict[str, str]:
        return {name: self.active_version(name) for name in self.names()}

    def fingerprint(self) -> str:
        digest = sha256()
        for name in self.names():
            template = self.get(name)
            digest.update(name.encode("utf-8"))
            digest.update(template.version.encode("utf-8"))
            digest.update(template.text.encode("utf-8"))
        return f"prompts-{digest.hexdigest()[:12]}" if self._prompts else "prompts-empty"

    def describe(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": name,
                "description": self._descriptions.get(name, ""),
                "active_version": self.active_version(name),
                "default_version": self._defaults[name],
                "available_versions": sorted(self._prompts[name]),
                "env_override": self._env_override(name) or None,
                "chars": len(self.get(name).text),
            }
            for name in self.names()
        ]


prompt_registry = PromptRegistry()
