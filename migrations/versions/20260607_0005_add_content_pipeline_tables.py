"""add content pipeline tables

Revision ID: 20260607_0005
Revises: 20260607_0004
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa


revision = "20260607_0005"
down_revision = "20260607_0004"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _create_index(name: str, table: str, columns: list[str], unique: bool = False) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {index["name"] for index in inspector.get_indexes(table)}
    if name not in existing:
        op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    if not _has_table("content_chapters"):
        op.create_table(
            "content_chapters",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("board", sa.String(), nullable=True),
            sa.Column("class_level", sa.String(), nullable=True),
            sa.Column("subject", sa.String(), nullable=True),
            sa.Column("book_name", sa.String(), nullable=True),
            sa.Column("chapter_number", sa.Integer(), nullable=True),
            sa.Column("chapter_name", sa.String(), nullable=True),
            sa.Column("slug", sa.String(), nullable=True),
            sa.Column("pdf_path", sa.Text(), nullable=True),
            sa.Column("source_hash", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("version", sa.String(), nullable=True),
            sa.Column("page_count", sa.Integer(), nullable=True),
            sa.Column("extracted_page_count", sa.Integer(), nullable=True),
            sa.Column("chunk_count", sa.Integer(), nullable=True),
            sa.Column("concept_count", sa.Integer(), nullable=True),
            sa.Column("coverage_score", sa.Float(), nullable=True),
            sa.Column("extraction_quality", sa.Float(), nullable=True),
            sa.Column("validation_report", sa.JSON(), nullable=True),
            sa.Column("approved_by", sa.String(), nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("published_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        _create_index("ix_content_chapters_board", "content_chapters", ["board"])
        _create_index("ix_content_chapters_class_level", "content_chapters", ["class_level"])
        _create_index("ix_content_chapters_subject", "content_chapters", ["subject"])
        _create_index("ix_content_chapters_chapter_number", "content_chapters", ["chapter_number"])
        _create_index("ix_content_chapters_chapter_name", "content_chapters", ["chapter_name"])
        _create_index("ix_content_chapters_slug", "content_chapters", ["slug"], unique=True)
        _create_index("ix_content_chapters_source_hash", "content_chapters", ["source_hash"])
        _create_index("ix_content_chapters_status", "content_chapters", ["status"])

    if not _has_table("content_pages"):
        op.create_table(
            "content_pages",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("chapter_id", sa.Integer(), sa.ForeignKey("content_chapters.id"), nullable=True),
            sa.Column("page_number", sa.Integer(), nullable=True),
            sa.Column("text", sa.Text(), nullable=True),
            sa.Column("char_count", sa.Integer(), nullable=True),
            sa.Column("extraction_quality", sa.Float(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        _create_index("ix_content_pages_chapter_id", "content_pages", ["chapter_id"])
        _create_index("ix_content_pages_page_number", "content_pages", ["page_number"])

    if not _has_table("content_concepts"):
        op.create_table(
            "content_concepts",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("chapter_id", sa.Integer(), sa.ForeignKey("content_chapters.id"), nullable=True),
            sa.Column("concept_id", sa.String(), nullable=True),
            sa.Column("title", sa.String(), nullable=True),
            sa.Column("definition", sa.Text(), nullable=True),
            sa.Column("core_explanation", sa.Text(), nullable=True),
            sa.Column("key_points", sa.JSON(), nullable=True),
            sa.Column("examples", sa.JSON(), nullable=True),
            sa.Column("formulas", sa.JSON(), nullable=True),
            sa.Column("properties", sa.JSON(), nullable=True),
            sa.Column("applications", sa.JSON(), nullable=True),
            sa.Column("common_mistakes", sa.JSON(), nullable=True),
            sa.Column("prerequisites", sa.JSON(), nullable=True),
            sa.Column("related_concepts", sa.JSON(), nullable=True),
            sa.Column("learning_objectives", sa.JSON(), nullable=True),
            sa.Column("source_pages", sa.JSON(), nullable=True),
            sa.Column("difficulty_level", sa.Integer(), nullable=True),
            sa.Column("blooms_taxonomy", sa.String(), nullable=True),
            sa.Column("typical_exam_weightage", sa.String(), nullable=True),
            sa.Column("importance_level", sa.String(), nullable=True),
            sa.Column("raw_json", sa.JSON(), nullable=True),
            sa.Column("validation_issues", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        _create_index("ix_content_concepts_chapter_id", "content_concepts", ["chapter_id"])
        _create_index("ix_content_concepts_concept_id", "content_concepts", ["concept_id"])
        _create_index("ix_content_concepts_title", "content_concepts", ["title"])

    if not _has_table("content_chunks"):
        op.create_table(
            "content_chunks",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("chapter_id", sa.Integer(), sa.ForeignKey("content_chapters.id"), nullable=True),
            sa.Column("chunk_id", sa.String(), nullable=True),
            sa.Column("text", sa.Text(), nullable=True),
            sa.Column("page_start", sa.Integer(), nullable=True),
            sa.Column("page_end", sa.Integer(), nullable=True),
            sa.Column("section_title", sa.String(), nullable=True),
            sa.Column("token_estimate", sa.Integer(), nullable=True),
            sa.Column("lexical_terms", sa.JSON(), nullable=True),
            sa.Column("embedding", sa.JSON(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        _create_index("ix_content_chunks_chapter_id", "content_chunks", ["chapter_id"])
        _create_index("ix_content_chunks_chunk_id", "content_chunks", ["chunk_id"], unique=True)
        _create_index("ix_content_chunks_page_start", "content_chunks", ["page_start"])
        _create_index("ix_content_chunks_page_end", "content_chunks", ["page_end"])
        _create_index("ix_content_chunks_section_title", "content_chunks", ["section_title"])

    if not _has_table("content_ingestion_jobs"):
        op.create_table(
            "content_ingestion_jobs",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("job_id", sa.String(), nullable=True),
            sa.Column("job_type", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("source_path", sa.Text(), nullable=True),
            sa.Column("summary", sa.JSON(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        _create_index("ix_content_ingestion_jobs_job_id", "content_ingestion_jobs", ["job_id"], unique=True)
        _create_index("ix_content_ingestion_jobs_job_type", "content_ingestion_jobs", ["job_type"])
        _create_index("ix_content_ingestion_jobs_status", "content_ingestion_jobs", ["status"])


def downgrade() -> None:
    for table_name in (
        "content_ingestion_jobs",
        "content_chunks",
        "content_concepts",
        "content_pages",
        "content_chapters",
    ):
        if _has_table(table_name):
            op.drop_table(table_name)
