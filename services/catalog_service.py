"""Student-facing course catalog assembled from the content pipeline.

This is the single source of truth for what students can select in Study
Lab, Exam Mode, and Missions. Chapters come from admin-approved/published
content (ContentChapter + ContentConcept); until the database has published
content, the built-in starter catalog keeps every learning surface working
with the same chapters the app launched with.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from Logic.content_pipeline import APPROVED_STATUSES, normalize_key
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


def _normalized_class_level(value: Any) -> str:
    normalized = normalize_key(value)
    return normalized.removeprefix("class_")


def _chapter_matches_catalog_scope(
    chapter: ContentChapter,
    *,
    subject: Optional[str],
    chapter_ref: Optional[str],
    class_level: Optional[str],
) -> bool:
    requested_subject = normalize_key(subject)
    if requested_subject and requested_subject != normalize_key(chapter.subject):
        return False

    requested_class = _normalized_class_level(class_level)
    if requested_class and requested_class != _normalized_class_level(chapter.class_level):
        return False

    requested_chapter = normalize_key(chapter_ref)
    if requested_chapter:
        chapter_keys = {
            normalize_key(chapter.slug),
            normalize_key(chapter.chapter_name),
        }
        chapter_haystack = normalize_key(
            f"{chapter.slug} {chapter.chapter_name} chapter {chapter.chapter_number or ''}"
        )
        if requested_chapter not in chapter_keys and requested_chapter not in chapter_haystack:
            return False
    return True


def resolve_catalog_topic(
    db: Session,
    section_id: str,
    *,
    subject: Optional[str] = None,
    chapter: Optional[str] = None,
    topic: Optional[str] = None,
    class_level: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """Resolve a published catalog topic by its stable ID or display title.

    Frontends normally send ``concept_id`` as ``section_id``, but bookmarked
    links and older clients may send the human title instead. Resolution is
    constrained by the learner/catalog scope before choosing a concept, which
    prevents an identically named topic in another subject or chapter from
    being selected.
    """
    requested_keys = {
        normalize_key(value)
        for value in (section_id, topic)
        if normalize_key(value)
    }
    if not requested_keys:
        return None

    chapters = (
        db.query(ContentChapter)
        .filter(ContentChapter.status.in_(APPROVED_STATUSES))
        .all()
    )
    # Subject/chapter are explicit request scope. The profile class is only a
    # preference: accounts created before class onboarding commonly contain
    # ``Other`` (or a stale class), while the selected published chapter is
    # still unambiguous. The matched chapter remains the authority for the
    # resolved class/version returned below.
    chapters = [
        candidate
        for candidate in chapters
        if _chapter_matches_catalog_scope(
            candidate,
            subject=subject,
            chapter_ref=chapter,
            class_level=None,
        )
    ]
    if not chapters:
        return None

    requested_class = _normalized_class_level(class_level)
    if requested_class and requested_class not in {"other", "general", "unspecified"}:
        preferred_chapters = [
            candidate
            for candidate in chapters
            if _normalized_class_level(candidate.class_level) == requested_class
        ]
        if preferred_chapters:
            chapters = preferred_chapters

    chapter_by_id = {candidate.id: candidate for candidate in chapters}
    concepts = (
        db.query(ContentConcept)
        .filter(ContentConcept.chapter_id.in_(chapter_by_id))
        .all()
    )
    matches = [
        concept
        for concept in concepts
        if requested_keys.intersection(
            {normalize_key(concept.concept_id), normalize_key(concept.title)}
        )
    ]
    if not matches:
        return None

    # Prefer the stable ID over a title match when both are possible. Never
    # silently choose between chapters: older clients can omit scope and the
    # same concept ID/title can legitimately exist in more than one chapter.
    requested_section = normalize_key(section_id)
    stable_id_matches = [
        concept
        for concept in matches
        if normalize_key(concept.concept_id) == requested_section
    ]
    preferred_matches = stable_id_matches or matches
    if len({concept.chapter_id for concept in preferred_matches}) > 1:
        return None
    preferred_matches.sort(
        key=lambda concept: (
            normalize_key(concept.concept_id) != requested_section,
            concept.id,
        )
    )
    concept = preferred_matches[0]
    matched_chapter = chapter_by_id[concept.chapter_id]
    return {
        "section_id": concept.concept_id,
        "topic": concept.title or concept.concept_id,
        "subject": matched_chapter.subject or subject or "",
        "chapter": matched_chapter.chapter_name or chapter or "",
        "chapter_slug": matched_chapter.slug or "",
        "class_level": matched_chapter.class_level or class_level or "",
        "content_version": matched_chapter.version or "",
        "catalog_source": "published",
    }
