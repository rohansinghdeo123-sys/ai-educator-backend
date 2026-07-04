"""Student-facing course catalog assembled from the content pipeline.

This is the single source of truth for what students can select in Study
Lab, Exam Mode, and Missions. Chapters come from admin-approved/published
content (ContentChapter + ContentConcept); until the database has published
content, the built-in starter catalog keeps every learning surface working
with the same chapters the app launched with.
"""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy.orm import Session

from Logic.content_pipeline import APPROVED_STATUSES
from models import ContentChapter, ContentConcept

# Mirrors the catalog the frontend shipped with, so removing the hard-coded
# frontend lists never leaves a student with empty selectors.
BUILTIN_SUBJECT = "Chemistry"
BUILTIN_CLASS_LEVEL = "Class 11"
BUILTIN_CHAPTERS: List[Dict[str, Any]] = [
    {
        "slug": "hydrocarbon",
        "name": "Hydrocarbons",
        "chapter_number": None,
        "topics": [
            {"id": "alkanes", "label": "Alkanes"},
            {"id": "alkenes", "label": "Alkenes"},
            {"id": "alkynes", "label": "Alkynes"},
            {"id": "aromatics", "label": "Aromatic Hydrocarbons"},
        ],
    },
    {
        "slug": "matter",
        "name": "Basic Concepts of Chemistry",
        "chapter_number": None,
        "topics": [
            {"id": "chemistry_definition", "label": "Definition of Chemistry"},
            {"id": "historical_alchemy", "label": "Alchemy and Iatrochemistry"},
            {"id": "ancient_indian_chemistry", "label": "Ancient Indian Chemistry"},
            {"id": "importance_of_chemistry", "label": "Role and Importance of Chemistry"},
            {"id": "matter_definition", "label": "Matter Definition"},
            {"id": "properties_of_matter", "label": "Properties of Matter"},
            {"id": "states_of_matter", "label": "States of Matter"},
            {"id": "solid_state", "label": "Solid State"},
            {"id": "liquid_state", "label": "Liquid State"},
            {"id": "gaseous_state", "label": "Gaseous State"},
            {"id": "interconversion_of_states", "label": "Interconversion of States"},
            {"id": "classification_of_matter", "label": "Classification of Matter"},
        ],
    },
]


def _builtin_catalog() -> Dict[str, Any]:
    return {
        "source": "builtin",
        "subjects": [
            {
                "subject": BUILTIN_SUBJECT,
                "class_level": BUILTIN_CLASS_LEVEL,
                "chapters": BUILTIN_CHAPTERS,
            }
        ],
    }


def build_catalog(db: Session) -> Dict[str, Any]:
    """Published catalog grouped by (subject, class_level); builtin fallback."""
    chapters = (
        db.query(ContentChapter)
        .filter(ContentChapter.status.in_(APPROVED_STATUSES))
        .order_by(ContentChapter.subject, ContentChapter.chapter_number, ContentChapter.id)
        .all()
    )
    if not chapters:
        return _builtin_catalog()

    concept_rows = (
        db.query(ContentConcept)
        .filter(ContentConcept.chapter_id.in_([chapter.id for chapter in chapters]))
        .order_by(ContentConcept.chapter_id, ContentConcept.concept_id)
        .all()
    )
    concepts_by_chapter: Dict[int, List[ContentConcept]] = {}
    for concept in concept_rows:
        concepts_by_chapter.setdefault(concept.chapter_id, []).append(concept)

    groups: Dict[tuple, Dict[str, Any]] = {}
    for chapter in chapters:
        key = (chapter.subject or BUILTIN_SUBJECT, chapter.class_level or "")
        group = groups.setdefault(
            key,
            {"subject": key[0], "class_level": key[1], "chapters": []},
        )
        topics = [
            {"id": concept.concept_id, "label": concept.title or concept.concept_id}
            for concept in concepts_by_chapter.get(chapter.id, [])
            if concept.concept_id
        ]
        if not topics:
            # A chapter without generated concepts is still searchable by its
            # slug, so students can select the whole chapter as one topic.
            topics = [{"id": chapter.slug, "label": chapter.chapter_name or chapter.slug}]
        group["chapters"].append(
            {
                "slug": chapter.slug,
                "name": chapter.chapter_name or chapter.slug,
                "chapter_number": chapter.chapter_number,
                "topics": topics,
            }
        )

    return {"source": "published", "subjects": list(groups.values())}
