"""add exam intelligence tables (paper upload + written practice)

Revision ID: 20260620_0010
Revises: 20260614_0009
Create Date: 2026-06-20
"""

from alembic import op
import sqlalchemy as sa


revision = "20260620_0010"
down_revision = "20260614_0009"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("uploaded_exam_papers"):
        op.create_table(
            "uploaded_exam_papers",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("class_level", sa.String(), server_default="", nullable=True),
            sa.Column("subject", sa.String(), server_default="", nullable=True),
            sa.Column("chapter_id", sa.Integer(), nullable=True),
            sa.Column("chapter_name", sa.String(), server_default="", nullable=True),
            sa.Column("exam_type", sa.String(), server_default="unknown", nullable=True),
            sa.Column("paper_title", sa.String(), server_default="", nullable=True),
            sa.Column("file_name", sa.String(), server_default="", nullable=True),
            sa.Column("file_type", sa.String(), server_default="", nullable=True),
            sa.Column("file_size", sa.Integer(), server_default="0", nullable=True),
            sa.Column("storage_path", sa.Text(), server_default="", nullable=True),
            sa.Column("upload_status", sa.String(), server_default="uploaded", nullable=True),
            sa.Column("parse_status", sa.String(), server_default="pending", nullable=True),
            sa.Column("uploaded_at", sa.DateTime(), nullable=True),
            sa.Column("parsed_at", sa.DateTime(), nullable=True),
            sa.Column("extracted_text", sa.Text(), server_default="", nullable=True),
            sa.Column("extraction_confidence", sa.Float(), server_default="0", nullable=True),
            sa.Column("extracted_question_count", sa.Integer(), server_default="0", nullable=True),
            sa.Column("analysis_json", sa.JSON(), nullable=True),
            sa.Column("warnings_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_uploaded_exam_papers_user_id", "uploaded_exam_papers", ["user_id"])
        op.create_index("ix_uploaded_exam_papers_subject", "uploaded_exam_papers", ["subject"])
        op.create_index("ix_uploaded_exam_papers_class_level", "uploaded_exam_papers", ["class_level"])
        op.create_index("ix_uploaded_exam_papers_exam_type", "uploaded_exam_papers", ["exam_type"])
        op.create_index("ix_uploaded_exam_papers_parse_status", "uploaded_exam_papers", ["parse_status"])

    if not _has_table("extracted_exam_questions"):
        op.create_table(
            "extracted_exam_questions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("paper_id", sa.Integer(), nullable=True),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("question_number", sa.String(), server_default="", nullable=True),
            sa.Column("section_name", sa.String(), server_default="", nullable=True),
            sa.Column("question_text", sa.Text(), server_default="", nullable=True),
            sa.Column("marks", sa.Float(), nullable=True),
            sa.Column("question_type", sa.String(), server_default="", nullable=True),
            sa.Column("intent", sa.String(), server_default="", nullable=True),
            sa.Column("difficulty", sa.String(), server_default="", nullable=True),
            sa.Column("class_level", sa.String(), server_default="", nullable=True),
            sa.Column("subject", sa.String(), server_default="", nullable=True),
            sa.Column("chapter_id", sa.Integer(), nullable=True),
            sa.Column("chapter_name", sa.String(), server_default="", nullable=True),
            sa.Column("topic", sa.String(), server_default="", nullable=True),
            sa.Column("concept_tags_json", sa.JSON(), nullable=True),
            sa.Column("expected_answer_style", sa.String(), server_default="", nullable=True),
            sa.Column("confidence_score", sa.Float(), server_default="0", nullable=True),
            sa.Column("raw_block", sa.Text(), server_default="", nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["paper_id"], ["uploaded_exam_papers.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_extracted_exam_questions_paper_id", "extracted_exam_questions", ["paper_id"])
        op.create_index("ix_extracted_exam_questions_user_id", "extracted_exam_questions", ["user_id"])
        op.create_index("ix_extracted_exam_questions_topic", "extracted_exam_questions", ["topic"])

    if not _has_table("exam_pattern_analysis"):
        op.create_table(
            "exam_pattern_analysis",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("class_level", sa.String(), server_default="", nullable=True),
            sa.Column("subject", sa.String(), server_default="", nullable=True),
            sa.Column("chapter_id", sa.Integer(), nullable=True),
            sa.Column("chapter_name", sa.String(), server_default="", nullable=True),
            sa.Column("source_paper_ids_json", sa.JSON(), nullable=True),
            sa.Column("total_questions", sa.Integer(), server_default="0", nullable=True),
            sa.Column("total_marks", sa.Float(), nullable=True),
            sa.Column("marks_distribution_json", sa.JSON(), nullable=True),
            sa.Column("question_type_distribution_json", sa.JSON(), nullable=True),
            sa.Column("chapter_weightage_json", sa.JSON(), nullable=True),
            sa.Column("topic_frequency_json", sa.JSON(), nullable=True),
            sa.Column("repeated_concepts_json", sa.JSON(), nullable=True),
            sa.Column("difficulty_distribution_json", sa.JSON(), nullable=True),
            sa.Column("pattern_summary", sa.Text(), server_default="", nullable=True),
            sa.Column("confidence_score", sa.Float(), server_default="0", nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_exam_pattern_analysis_user_id", "exam_pattern_analysis", ["user_id"])
        op.create_index("ix_exam_pattern_analysis_subject", "exam_pattern_analysis", ["subject"])
        op.create_index("ix_exam_pattern_analysis_class_level", "exam_pattern_analysis", ["class_level"])

    if not _has_table("probable_question_sets"):
        op.create_table(
            "probable_question_sets",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("class_level", sa.String(), server_default="", nullable=True),
            sa.Column("subject", sa.String(), server_default="", nullable=True),
            sa.Column("chapter_id", sa.Integer(), nullable=True),
            sa.Column("chapter_name", sa.String(), server_default="", nullable=True),
            sa.Column("source_analysis_ids_json", sa.JSON(), nullable=True),
            sa.Column("generation_mode", sa.String(), server_default="mixed", nullable=True),
            sa.Column("probable_questions_json", sa.JSON(), nullable=True),
            sa.Column("priority_topics_json", sa.JSON(), nullable=True),
            sa.Column("strategy_summary", sa.Text(), server_default="", nullable=True),
            sa.Column("disclaimer", sa.Text(), server_default="", nullable=True),
            sa.Column("confidence_score", sa.Float(), server_default="0", nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_probable_question_sets_user_id", "probable_question_sets", ["user_id"])
        op.create_index("ix_probable_question_sets_subject", "probable_question_sets", ["subject"])
        op.create_index("ix_probable_question_sets_class_level", "probable_question_sets", ["class_level"])
        op.create_index("ix_probable_question_sets_generation_mode", "probable_question_sets", ["generation_mode"])

    if not _has_table("written_practice_sessions"):
        op.create_table(
            "written_practice_sessions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("class_level", sa.String(), server_default="", nullable=True),
            sa.Column("subject", sa.String(), server_default="", nullable=True),
            sa.Column("chapter_id", sa.Integer(), nullable=True),
            sa.Column("chapter_name", sa.String(), server_default="", nullable=True),
            sa.Column("topic", sa.String(), server_default="", nullable=True),
            sa.Column("marks_focus", sa.String(), server_default="", nullable=True),
            sa.Column("session_status", sa.String(), server_default="active", nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_written_practice_sessions_user_id", "written_practice_sessions", ["user_id"])
        op.create_index("ix_written_practice_sessions_subject", "written_practice_sessions", ["subject"])
        op.create_index("ix_written_practice_sessions_session_status", "written_practice_sessions", ["session_status"])

    if not _has_table("written_answer_attempts"):
        op.create_table(
            "written_answer_attempts",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("session_id", sa.Integer(), nullable=True),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("question_text", sa.Text(), server_default="", nullable=True),
            sa.Column("question_type", sa.String(), server_default="", nullable=True),
            sa.Column("marks_total", sa.Float(), server_default="0", nullable=True),
            sa.Column("student_answer", sa.Text(), server_default="", nullable=True),
            sa.Column("expected_points_json", sa.JSON(), nullable=True),
            sa.Column("class_level", sa.String(), server_default="", nullable=True),
            sa.Column("subject", sa.String(), server_default="", nullable=True),
            sa.Column("chapter_id", sa.Integer(), nullable=True),
            sa.Column("chapter_name", sa.String(), server_default="", nullable=True),
            sa.Column("topic", sa.String(), server_default="", nullable=True),
            sa.Column("submitted_at", sa.DateTime(), nullable=True),
            sa.Column("evaluation_status", sa.String(), server_default="pending", nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["session_id"], ["written_practice_sessions.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_written_answer_attempts_session_id", "written_answer_attempts", ["session_id"])
        op.create_index("ix_written_answer_attempts_user_id", "written_answer_attempts", ["user_id"])
        op.create_index("ix_written_answer_attempts_topic", "written_answer_attempts", ["topic"])
        op.create_index("ix_written_answer_attempts_evaluation_status", "written_answer_attempts", ["evaluation_status"])

    if not _has_table("written_answer_feedback"):
        op.create_table(
            "written_answer_feedback",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("attempt_id", sa.Integer(), nullable=True),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("marks_awarded", sa.Float(), server_default="0", nullable=True),
            sa.Column("marks_total", sa.Float(), server_default="0", nullable=True),
            sa.Column("score_percentage", sa.Float(), server_default="0", nullable=True),
            sa.Column("covered_points_json", sa.JSON(), nullable=True),
            sa.Column("missing_points_json", sa.JSON(), nullable=True),
            sa.Column("incorrect_points_json", sa.JSON(), nullable=True),
            sa.Column("weak_explanation_json", sa.JSON(), nullable=True),
            sa.Column("presentation_feedback", sa.Text(), server_default="", nullable=True),
            sa.Column("teacher_feedback", sa.Text(), server_default="", nullable=True),
            sa.Column("model_answer", sa.Text(), server_default="", nullable=True),
            sa.Column("improve_to_full_marks", sa.Text(), server_default="", nullable=True),
            sa.Column("rubric_scores_json", sa.JSON(), nullable=True),
            sa.Column("next_question_suggestion", sa.Text(), server_default="", nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["attempt_id"], ["written_answer_attempts.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_written_answer_feedback_attempt_id", "written_answer_feedback", ["attempt_id"])
        op.create_index("ix_written_answer_feedback_user_id", "written_answer_feedback", ["user_id"])

    if not _has_table("student_exam_weaknesses"):
        op.create_table(
            "student_exam_weaknesses",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("class_level", sa.String(), server_default="", nullable=True),
            sa.Column("subject", sa.String(), server_default="", nullable=True),
            sa.Column("chapter_id", sa.Integer(), nullable=True),
            sa.Column("chapter_name", sa.String(), server_default="", nullable=True),
            sa.Column("topic", sa.String(), server_default="", nullable=True),
            sa.Column("weakness_type", sa.String(), server_default="", nullable=True),
            sa.Column("weakness_summary", sa.Text(), server_default="", nullable=True),
            sa.Column("evidence_json", sa.JSON(), nullable=True),
            sa.Column("frequency_count", sa.Integer(), server_default="1", nullable=True),
            sa.Column("last_seen_at", sa.DateTime(), nullable=True),
            sa.Column("improvement_suggestion", sa.Text(), server_default="", nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_student_exam_weaknesses_user_id", "student_exam_weaknesses", ["user_id"])
        op.create_index("ix_student_exam_weaknesses_subject", "student_exam_weaknesses", ["subject"])
        op.create_index("ix_student_exam_weaknesses_topic", "student_exam_weaknesses", ["topic"])
        op.create_index("ix_student_exam_weaknesses_weakness_type", "student_exam_weaknesses", ["weakness_type"])


def downgrade() -> None:
    for table_name in (
        "student_exam_weaknesses",
        "written_answer_feedback",
        "written_answer_attempts",
        "written_practice_sessions",
        "probable_question_sets",
        "exam_pattern_analysis",
        "extracted_exam_questions",
        "uploaded_exam_papers",
    ):
        if _has_table(table_name):
            op.drop_table(table_name)
