from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship

from database import Base


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
