import json
import os
from typing import Dict, List, Optional

class KnowledgeGraph:
    """
    In‑memory store for chapter knowledge graphs (JSON concept arrays).
    Each concept is a dict with at least: concept_id, title, definition,
    core_explanation, key_points, and optional fields like common_mistakes,
    difficulty_level, learning_objectives, etc.
    """

    def __init__(self):
        self.concepts: Dict[str, dict] = {}              # concept_id → concept dict
        self.chapter_concepts: Dict[str, List[str]] = {} # chapter_name → [concept_id, …]

    def load_chapter(self, filepath: str, chapter_name: str):
        """
        Load a JSON array of concepts and index them.
        Example:
            knowledge_graph.load_chapter(
                "data/chapters/basic_concepts_of_chemistry.json",
                "basic-concepts-of-chemistry"
            )
        """
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            raise ValueError(f"Chapter file {filepath} must contain a JSON array")

        ids = []
        for concept in data:
            cid = concept.get("concept_id")
            if not cid:
                raise ValueError("Every concept must have a 'concept_id' field")
            self.concepts[cid] = concept
            ids.append(cid)

        self.chapter_concepts[chapter_name] = ids

    def get_concept(self, concept_id: str) -> Optional[dict]:
        """Return a single concept by its id, or None if not found."""
        return self.concepts.get(concept_id)

    def get_concepts(self, concept_ids: List[str]) -> List[dict]:
        """Return multiple concepts. Missing IDs are silently skipped."""
        return [self.concepts[cid] for cid in concept_ids if cid in self.concepts]

    def get_chapter_concepts(self, chapter_name: str) -> List[dict]:
        """Return all concepts belonging to a chapter."""
        ids = self.chapter_concepts.get(chapter_name, [])
        return self.get_concepts(ids)

    def search_by_keyword(self, keyword: str, limit: int = 5) -> List[dict]:
        """
        Simple full‑text search in title and core_explanation.
        Used by AI agents to quickly find relevant concepts.
        """
        results = []
        keyword_lower = keyword.lower()
        for concept in self.concepts.values():
            if (keyword_lower in concept.get("title", "").lower() or
                keyword_lower in concept.get("core_explanation", "").lower()):
                results.append(concept)
                if len(results) >= limit:
                    break
        return results

    def list_chapters(self) -> List[str]:
        """Return names of all loaded chapters."""
        return list(self.chapter_concepts.keys())


# ── Global singleton ─────────────────────────────────────────────────────
# This is the single instance used throughout the backend.
# Loaded once when the module is first imported.
knowledge_graph = KnowledgeGraph()