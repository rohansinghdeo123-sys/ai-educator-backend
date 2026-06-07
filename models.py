from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship

from database import Base


def _utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# =========================================================
# AGENT MEMORY
# =========================================================
class AgentChatMemory(Base):
    """Stores conversation history for the agentic system."""
    __tablename__ = "agent_chat_memory"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, index=True)
    role = Column(String)
    content = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)
    metadata_json = Column(JSON, nullable=True)


# =========================================================
# USER PROGRESS
# =========================================================
class UserProgress(Base):
    __tablename__ = "user_progress"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, unique=True, index=True)
    total_tests = Column(Integer, default=0)
    total_questions = Column(Integer, default=0)
    total_correct = Column(Integer, default=0)
    xp = Column(Integer, default=0)
    streak = Column(Integer, default=0)
    last_active_date = Column(Date, nullable=True)

    focus_score = Column(Float, default=0.0)
    consistency_index = Column(Float, default=0.0)
    learning_efficiency = Column(Float, default=0.0)

    coach_profile = relationship(
        "AICoachProfile",
        back_populates="progress",
        uselist=False,
        primaryjoin="UserProgress.user_id == foreign(AICoachProfile.user_id)",
    )

    @property
    def accuracy(self):
        if self.total_questions == 0:
            return 0.0
        return (self.total_correct / self.total_questions) * 100

    @property
    def level(self):
        return (self.xp // 100) + 1


# =========================================================
# TEST HISTORY
# =========================================================
class TestHistory(Base):
    __tablename__ = "test_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)
    date = Column(Date)
    topic = Column(String)
    score = Column(Integer)
    total_questions = Column(Integer)
    xp_earned = Column(Integer)

    time_spent_seconds = Column(Integer, default=0)
    accuracy_rate = Column(Float, default=0.0)
    focus_score = Column(Float, default=0.0)
    session_type = Column(String, default="exam")
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    response_latency_ms = Column(Integer, default=0)
    hint_count = Column(Integer, default=0)
    retry_count = Column(Integer, default=0)
    confidence_before = Column(Float, nullable=True)
    confidence_after = Column(Float, nullable=True)

    details = relationship(
        "SessionDetail",
        back_populates="test",
        uselist=False,
        cascade="all, delete-orphan",
    )


# =========================================================
# SESSION DETAIL
# =========================================================
class SessionDetail(Base):
    __tablename__ = "session_details"

    id = Column(Integer, primary_key=True, index=True)
    test_id = Column(Integer, ForeignKey("test_history.id"))

    replay_data = Column(JSON)

    test = relationship("TestHistory", back_populates="details")


# =========================================================
# TOPIC PERFORMANCE
# =========================================================
class TopicPerformance(Base):
    __tablename__ = "topic_performance"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)
    topic = Column(String, index=True)
    attempts = Column(Integer, default=0)
    correct = Column(Integer, default=0)
    weak = Column(Boolean, default=False)

    last_practiced = Column(DateTime, default=datetime.utcnow)
    avg_time_per_question = Column(Float, default=0.0)
    trend_score = Column(Float, default=0.0)

    @property
    def accuracy(self):
        if self.attempts == 0:
            return 0.0
        return (self.correct / self.attempts) * 100


# =========================================================
# PERSONAL AI COACH PROFILE
# =========================================================
class AICoachProfile(Base):
    """
    One durable AI coach identity per user.
    This is the coach's stable personality, preferences, and long-term learning state.
    """
    __tablename__ = "ai_coach_profiles"

    id = Column(Integer, primary_key=True, index=True)
    coach_id = Column(String, unique=True, index=True)
    user_id = Column(String, unique=True, index=True)

    coach_name = Column(String, default="Astra")
    coach_tone = Column(String, default="focused_supportive")
    coach_style = Column(String, default="exam_oriented")
    coach_status = Column(String, default="active")

    student_display_name = Column(String, nullable=True)
    target_exam = Column(String, nullable=True)
    target_exam_date = Column(Date, nullable=True)

    preferred_subjects = Column(JSON, default=list)
    weak_topics_snapshot = Column(JSON, default=list)
    strengths_snapshot = Column(JSON, default=list)
    active_goals = Column(JSON, default=list)

    motivation_profile = Column(JSON, default=dict)
    study_preferences = Column(JSON, default=dict)
    last_recommendation = Column(JSON, nullable=True)

    long_term_summary = Column(Text, default="")
    daily_strategy = Column(Text, default="")
    next_best_action = Column(Text, default="Start with a short focused study session.")

    last_learning_cycle_at = Column(DateTime, nullable=True)
    last_interaction_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    progress = relationship(
        "UserProgress",
        back_populates="coach_profile",
        uselist=False,
        primaryjoin="foreign(AICoachProfile.user_id) == UserProgress.user_id",
    )

    memories = relationship(
        "AICoachMemory",
        back_populates="coach",
        cascade="all, delete-orphan",
    )

    interactions = relationship(
        "AICoachInteraction",
        back_populates="coach",
        cascade="all, delete-orphan",
    )


# =========================================================
# PERSONAL AI COACH MEMORY
# =========================================================
class AICoachMemory(Base):
    """
    Stores compact coach memory, not raw endless chat.
    Use this for daily learning, user patterns, study preferences, and weak-topic context.
    """
    __tablename__ = "ai_coach_memories"

    id = Column(Integer, primary_key=True, index=True)
    coach_id = Column(String, ForeignKey("ai_coach_profiles.coach_id"), index=True)
    user_id = Column(String, index=True)

    memory_type = Column(String, default="study_pattern")
    title = Column(String, default="")
    summary = Column(Text, default="")
    importance = Column(Float, default=0.5)
    confidence = Column(Float, default=0.5)

    source = Column(String, default="coach")
    metadata_json = Column(JSON, default=dict)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    coach = relationship("AICoachProfile", back_populates="memories")


# =========================================================
# PERSONAL AI COACH INTERACTIONS
# =========================================================
class AICoachInteraction(Base):
    """
    Stores coach conversations and advice events per user.
    This gives every user their own coach history without mixing sessions.
    """
    __tablename__ = "ai_coach_interactions"

    id = Column(Integer, primary_key=True, index=True)
    coach_id = Column(String, ForeignKey("ai_coach_profiles.coach_id"), index=True)
    user_id = Column(String, index=True)

    role = Column(String)
    message = Column(Text)
    intent = Column(String, default="general")
    mode = Column(String, default="coach")
    quality_score = Column(Float, default=0.0)

    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)

    coach = relationship("AICoachProfile", back_populates="interactions")


# =========================================================
# PERSONAL AI COACH DAILY LEARNING SIGNALS
# =========================================================
class AICoachDailySignal(Base):
    """
    One daily snapshot per user that lets the coach improve its advice over time.
    It can be generated after sessions or by a daily background job later.
    """
    __tablename__ = "ai_coach_daily_signals"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)
    coach_id = Column(String, index=True)

    signal_date = Column(Date, default=datetime.utcnow)
    sessions_count = Column(Integer, default=0)
    questions_attempted = Column(Integer, default=0)
    accuracy = Column(Float, default=0.0)
    focus_score = Column(Float, default=0.0)
    xp_earned = Column(Integer, default=0)

    weakest_topics = Column(JSON, default=list)
    strongest_topics = Column(JSON, default=list)
    recommendation = Column(Text, default="")
    risk_level = Column(String, default="normal")

    created_at = Column(DateTime, default=datetime.utcnow)


# =========================================================
# DURABLE OBSERVABILITY
# =========================================================
class ObservabilityEvent(Base):
    """
    Durable copy of agent events that were previously only kept in memory.
    Ops can now recover recent activity after restarts and inspect historical failures.
    """
    __tablename__ = "observability_events"

    id = Column(Integer, primary_key=True, index=True)
    event_version = Column(Integer, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    agent_id = Column(String, index=True)
    event_type = Column(String, index=True)
    severity = Column(String, default="info", index=True)
    session_id = Column(String, default="", index=True)
    source = Column(String, default="event_bus")

    summary = Column(Text, default="")
    latency_ms = Column(Integer, default=0)
    estimated_cost_usd = Column(Float, default=0.0)
    data_json = Column(JSON, default=dict)


class ModelToolTrace(Base):
    """
    Durable trace row for model calls, tool calls, and full coach turns.
    Costs are estimated unless the provider later returns exact token accounting.
    """
    __tablename__ = "model_tool_traces"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user_id = Column(String, nullable=True, index=True)
    session_id = Column(String, nullable=True, index=True)
    turn_id = Column(String, nullable=True, index=True)

    trace_type = Column(String, index=True)  # model, tool, turn
    name = Column(String, index=True)
    provider = Column(String, nullable=True)
    model = Column(String, nullable=True)
    status = Column(String, default="success", index=True)

    latency_ms = Column(Integer, default=0)
    estimated_input_tokens = Column(Integer, default=0)
    estimated_output_tokens = Column(Integer, default=0)
    estimated_cost_usd = Column(Float, default=0.0)
    metadata_json = Column(JSON, default=dict)


class DailyQuotaUsage(Base):
    """Persistent per-user daily usage counters for AI routes."""
    __tablename__ = "daily_quota_usage"

    id = Column(Integer, primary_key=True, index=True)
    quota_key = Column(String, unique=True, index=True, nullable=False)
    user_hash = Column(String, nullable=False, index=True)
    quota_name = Column(String, nullable=False, index=True)
    quota_date = Column(Date, nullable=False, index=True)
    count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# =========================================================
# PRODUCTION CONTENT PIPELINE
# =========================================================
class ContentChapter(Base):
    """
    Registry row for one approved/draft study source chapter.
    Raw PDFs remain the source of truth; concepts/chunks are derived layers.
    """
    __tablename__ = "content_chapters"

    id = Column(Integer, primary_key=True, index=True)
    board = Column(String, default="NCERT", index=True)
    class_level = Column(String, default="", index=True)
    subject = Column(String, default="", index=True)
    book_name = Column(String, default="")
    chapter_number = Column(Integer, nullable=True, index=True)
    chapter_name = Column(String, default="", index=True)
    slug = Column(String, unique=True, index=True)

    pdf_path = Column(Text, default="")
    source_hash = Column(String, default="", index=True)
    status = Column(String, default="uploaded", index=True)
    version = Column(String, default="v1")

    page_count = Column(Integer, default=0)
    extracted_page_count = Column(Integer, default=0)
    chunk_count = Column(Integer, default=0)
    concept_count = Column(Integer, default=0)
    coverage_score = Column(Float, default=0.0)
    extraction_quality = Column(Float, default=0.0)
    validation_report = Column(JSON, default=dict)

    approved_by = Column(String, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow_naive)
    updated_at = Column(DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    pages = relationship(
        "ContentPage",
        back_populates="chapter",
        cascade="all, delete-orphan",
    )
    concepts = relationship(
        "ContentConcept",
        back_populates="chapter",
        cascade="all, delete-orphan",
    )
    chunks = relationship(
        "ContentChunk",
        back_populates="chapter",
        cascade="all, delete-orphan",
    )


class ContentPage(Base):
    """Page-level extracted text with source page mapping."""
    __tablename__ = "content_pages"

    id = Column(Integer, primary_key=True, index=True)
    chapter_id = Column(Integer, ForeignKey("content_chapters.id"), index=True)
    page_number = Column(Integer, index=True)
    text = Column(Text, default="")
    char_count = Column(Integer, default=0)
    extraction_quality = Column(Float, default=0.0)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utcnow_naive)

    chapter = relationship("ContentChapter", back_populates="pages")


class ContentConcept(Base):
    """Structured teaching layer generated from approved source text."""
    __tablename__ = "content_concepts"

    id = Column(Integer, primary_key=True, index=True)
    chapter_id = Column(Integer, ForeignKey("content_chapters.id"), index=True)
    concept_id = Column(String, index=True)
    title = Column(String, default="", index=True)
    definition = Column(Text, default="")
    core_explanation = Column(Text, default="")
    key_points = Column(JSON, default=list)
    examples = Column(JSON, default=list)
    formulas = Column(JSON, default=list)
    properties = Column(JSON, default=list)
    applications = Column(JSON, default=list)
    common_mistakes = Column(JSON, default=list)
    prerequisites = Column(JSON, default=list)
    related_concepts = Column(JSON, default=list)
    learning_objectives = Column(JSON, default=list)
    source_pages = Column(JSON, default=list)
    difficulty_level = Column(Integer, default=1)
    blooms_taxonomy = Column(String, default="")
    typical_exam_weightage = Column(String, default="")
    importance_level = Column(String, default="")
    raw_json = Column(JSON, default=dict)
    validation_issues = Column(JSON, default=list)
    created_at = Column(DateTime, default=_utcnow_naive)
    updated_at = Column(DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    chapter = relationship("ContentChapter", back_populates="concepts")


class ContentChunk(Base):
    """Searchable RAG chunk derived from extracted source text."""
    __tablename__ = "content_chunks"

    id = Column(Integer, primary_key=True, index=True)
    chapter_id = Column(Integer, ForeignKey("content_chapters.id"), index=True)
    chunk_id = Column(String, unique=True, index=True)
    text = Column(Text, default="")
    page_start = Column(Integer, nullable=True, index=True)
    page_end = Column(Integer, nullable=True, index=True)
    section_title = Column(String, default="", index=True)
    token_estimate = Column(Integer, default=0)
    lexical_terms = Column(JSON, default=list)
    embedding = Column(JSON, nullable=True)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utcnow_naive)

    chapter = relationship("ContentChapter", back_populates="chunks")


class ContentIngestionJob(Base):
    """Auditable ingestion/generation/validation job log."""
    __tablename__ = "content_ingestion_jobs"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(String, unique=True, index=True)
    job_type = Column(String, default="ingest", index=True)
    status = Column(String, default="queued", index=True)
    source_path = Column(Text, default="")
    summary = Column(JSON, default=dict)
    error = Column(Text, default="")
    created_at = Column(DateTime, default=_utcnow_naive)
    updated_at = Column(DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)


# =========================================================
# ADMIN AUDIT
# =========================================================
class AdminAuditLog(Base):
    """Founder/admin action audit trail for protected console operations."""
    __tablename__ = "admin_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=_utcnow_naive, index=True)
    actor_uid = Column(String, default="", index=True)
    actor_email = Column(String, default="", index=True)
    action = Column(String, default="", index=True)
    target_type = Column(String, default="", index=True)
    target_id = Column(String, default="", index=True)
    status = Column(String, default="success", index=True)
    ip_address = Column(String, default="")
    user_agent = Column(Text, default="")
    metadata_json = Column(JSON, default=dict)


# =========================================================
# AGENT RUNTIME
# =========================================================
class AgentRuntimeRun(Base):
    """
    Durable execution envelope for one controlled agent workflow.
    A run can represent a coach turn today and an autonomous mission later.
    """
    __tablename__ = "agent_runtime_runs"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String, unique=True, index=True)
    turn_id = Column(String, nullable=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    session_id = Column(String, nullable=True, index=True)

    workflow_name = Column(String, default="study_coach_turn", index=True)
    lead_agent = Column(String, default="lead_coach_orchestrator", index=True)
    mode = Column(String, default="coach")
    intent = Column(String, default="general", index=True)
    status = Column(String, default="running", index=True)

    started_at = Column(DateTime, default=_utcnow_naive, index=True)
    completed_at = Column(DateTime, nullable=True)
    latency_ms = Column(Integer, default=0)
    confidence_score = Column(Float, default=0.0)
    grounding_status = Column(String, default="not_required", index=True)

    final_answer_excerpt = Column(Text, default="")
    state_json = Column(JSON, default=dict)
    metadata_json = Column(JSON, default=dict)


class AgentRuntimeStep(Base):
    """One ordered backend-controlled step inside an agent runtime run."""
    __tablename__ = "agent_runtime_steps"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String, index=True)
    step_name = Column(String, index=True)
    agent_name = Column(String, default="", index=True)
    status = Column(String, default="success", index=True)
    step_order = Column(Integer, default=0)

    started_at = Column(DateTime, default=_utcnow_naive, index=True)
    completed_at = Column(DateTime, nullable=True)
    latency_ms = Column(Integer, default=0)
    input_json = Column(JSON, default=dict)
    output_json = Column(JSON, default=dict)
    error = Column(Text, default="")


class AgentRuntimeMessage(Base):
    """Structured internal message or task packet between agent roles."""
    __tablename__ = "agent_runtime_messages"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String, index=True)
    created_at = Column(DateTime, default=_utcnow_naive, index=True)

    sender_agent = Column(String, index=True)
    receiver_agent = Column(String, index=True)
    message_type = Column(String, index=True)
    task = Column(Text, default="")
    confidence = Column(Float, default=0.0)
    required_action = Column(Text, default="")
    evidence_json = Column(JSON, default=dict)
    result_json = Column(JSON, default=dict)


class AgentRuntimeToolCall(Base):
    """Durable record for deterministic tools selected during a run."""
    __tablename__ = "agent_runtime_tool_calls"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String, index=True)
    tool_name = Column(String, index=True)
    agent_name = Column(String, default="", index=True)
    status = Column(String, default="success", index=True)

    started_at = Column(DateTime, default=_utcnow_naive, index=True)
    completed_at = Column(DateTime, nullable=True)
    latency_ms = Column(Integer, default=0)
    input_json = Column(JSON, default=dict)
    output_json = Column(JSON, default=dict)
    error = Column(Text, default="")


class AgentRuntimeHandoff(Base):
    """Durable handoff contract between agent roles."""
    __tablename__ = "agent_runtime_handoffs"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String, index=True)
    created_at = Column(DateTime, default=_utcnow_naive, index=True)

    from_agent = Column(String, index=True)
    to_agent = Column(String, index=True)
    reason = Column(Text, default="")
    status = Column(String, default="requested", index=True)
    input_json = Column(JSON, default=dict)
    result_json = Column(JSON, default=dict)
