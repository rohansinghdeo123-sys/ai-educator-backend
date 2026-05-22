from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# =========================================================
# PROGRESS SCHEMAS
# =========================================================
class ProgressBase(BaseModel):
    user_id: str
    total_tests: int = Field(ge=0, default=0)
    total_questions: int = Field(ge=0, default=0)
    total_correct: int = Field(ge=0, default=0)
    xp: int = Field(ge=0, default=0)
    streak: int = Field(ge=0, default=0)


class ProgressUpdate(ProgressBase):
    pass


class ProgressResponse(ProgressBase):
    level: int = 1
    accuracy: float = 0.0
    focus_score: float = 0.0
    consistency_index: float = 0.0
    learning_efficiency: float = 0.0

    model_config = {"from_attributes": True}


# =========================================================
# TEST HISTORY & SESSION SCHEMAS
# =========================================================
class TestHistoryCreate(BaseModel):
    user_id: str
    topic: str
    score: int = Field(ge=0)
    total_questions: int = Field(ge=1)
    xp_earned: int = Field(ge=0)
    time_spent_seconds: int = 0
    focus_score: float = 0.0
    session_type: str = "exam"
    replay_data: Optional[Dict[str, Any]] = None


class TestHistoryResponse(BaseModel):
    id: int
    date: date
    topic: Optional[str] = None
    score: int
    total_questions: int
    xp_earned: int
    time_spent_seconds: int
    accuracy_rate: float
    focus_score: float
    session_type: str

    model_config = {"from_attributes": True}


class SessionReplayResponse(BaseModel):
    id: int
    topic: str
    date: date
    replay_data: Dict[str, Any]

    model_config = {"from_attributes": True}


# =========================================================
# TOPIC PERFORMANCE SCHEMAS
# =========================================================
class TopicPerformanceResponse(BaseModel):
    topic: str
    attempts: int
    correct: int
    accuracy: float
    weak: bool
    last_practiced: datetime
    avg_time_per_question: float
    trend_score: float

    model_config = {"from_attributes": True}


# =========================================================
# ADVANCED ANALYTICS SCHEMAS
# =========================================================
class AnalyticsInsight(BaseModel):
    type: str
    message: str
    severity: str
    action_label: Optional[str] = None
    action_trigger: Optional[str] = None


class AdvancedAnalyticsResponse(BaseModel):
    summary: Dict[str, Any]
    topic_heatmap: List[Dict[str, Any]]
    performance_trends: List[Dict[str, Any]]
    weak_areas: List[Dict[str, Any]]
    insights: List[AnalyticsInsight]
    cognitive_metrics: Dict[str, float]
    predictive_stats: Dict[str, Any]


# =========================================================
# AGENT AI SCHEMAS
# =========================================================
class AgentRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    section_id: str = ""
    session_id: str
    mode: str = "revision"
    difficulty: str = "medium"


class AgentResponse(BaseModel):
    answer: str
    tools_used: List[str] = []
    session_id: str


class AgentChatMemoryBase(BaseModel):
    session_id: str
    role: str
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata_json: Optional[Dict[str, Any]] = None

    model_config = {"from_attributes": True}


# =========================================================
# PERSONAL AI COACH SCHEMAS
# =========================================================
class CoachBootstrapRequest(BaseModel):
    user_id: str
    student_display_name: Optional[str] = None
    preferred_subjects: List[str] = Field(default_factory=list)
    target_exam: Optional[str] = None
    target_exam_date: Optional[date] = None


class CoachProfileResponse(BaseModel):
    coach_id: str
    user_id: str
    coach_name: str
    coach_tone: str
    coach_style: str
    coach_status: str
    student_display_name: Optional[str] = None
    target_exam: Optional[str] = None
    target_exam_date: Optional[date] = None
    preferred_subjects: List[str] = Field(default_factory=list)
    weak_topics_snapshot: List[Dict[str, Any]] = Field(default_factory=list)
    strengths_snapshot: List[Dict[str, Any]] = Field(default_factory=list)
    active_goals: List[Dict[str, Any]] = Field(default_factory=list)
    motivation_profile: Dict[str, Any] = Field(default_factory=dict)
    study_preferences: Dict[str, Any] = Field(default_factory=dict)
    long_term_summary: str = ""
    daily_strategy: str = ""
    next_best_action: str = ""
    last_learning_cycle_at: Optional[datetime] = None
    last_interaction_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CoachMemoryResponse(BaseModel):
    id: int
    coach_id: str
    user_id: str
    memory_type: str
    title: str
    summary: str
    importance: float
    confidence: float
    source: str
    metadata_json: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CoachChatRequest(BaseModel):
    user_id: str
    message: str = Field(min_length=1, max_length=2500)
    mode: str = "coach"
    intent: str = "general"
    subject: Optional[str] = None
    topic: Optional[str] = None
    session_id: Optional[str] = None


class CoachChatResponse(BaseModel):
    coach_id: str
    coach_name: str
    answer: str
    next_best_action: str
    daily_strategy: str
    memory_used: List[Dict[str, Any]] = Field(default_factory=list)
    analytics_snapshot: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CoachDailySignalResponse(BaseModel):
    user_id: str
    coach_id: str
    signal_date: date
    sessions_count: int
    questions_attempted: int
    accuracy: float
    focus_score: float
    xp_earned: int
    weakest_topics: List[Dict[str, Any]] = Field(default_factory=list)
    strongest_topics: List[Dict[str, Any]] = Field(default_factory=list)
    recommendation: str
    risk_level: str

    model_config = {"from_attributes": True}


class CoachDashboardResponse(BaseModel):
    profile: CoachProfileResponse
    memories: List[CoachMemoryResponse] = Field(default_factory=list)
    daily_signal: Optional[CoachDailySignalResponse] = None
    analytics_snapshot: Dict[str, Any] = Field(default_factory=dict)


class AutonomousStudyRequest(BaseModel):
    current_topic: Optional[str] = None
    current_chapter: Optional[str] = None
    subject: str = "Chemistry"


class AutonomousStudyResponse(BaseModel):
    mission_id: str
    status: str
    subject: str
    chapter: str = ""
    target_topic: str
    target_source: str
    mission_type: str = "study"
    priority: str = "medium"
    mastery_band: str = "unknown"
    estimated_minutes: int = 15
    primary_agent: str
    mode: str
    difficulty: str
    objective: str
    why: str
    steps: List[str] = Field(default_factory=list)
    next_actions: List[str] = Field(default_factory=list)
    success_criteria: List[str] = Field(default_factory=list)
    study_plan: List[Dict[str, Any]] = Field(default_factory=list)
    diagnostic_question: Dict[str, Any] = Field(default_factory=dict)
    adaptive_roadmap: List[Dict[str, Any]] = Field(default_factory=list)
    agent_sequence: List[Dict[str, Any]] = Field(default_factory=list)
    checkpoints: List[Dict[str, Any]] = Field(default_factory=list)
    student_state: Dict[str, Any] = Field(default_factory=dict)
    completion_report: Dict[str, Any] = Field(default_factory=dict)
    result: Dict[str, Any] = Field(default_factory=dict)
    analytics_summary: Dict[str, Any] = Field(default_factory=dict)
    latency_ms: int = 0


# =========================================================
# LEADERBOARD SCHEMAS
# =========================================================
class LeaderboardEntry(BaseModel):
    rank: int
    user_id: str
    xp: int
    streak: int
    total_tests: int

    model_config = {"from_attributes": True}


# =========================================================
# SYSTEM HEALTH SCHEMA
# =========================================================
class HealthResponse(BaseModel):
    status: str
    database: bool
    version: str = "2.0.0-bloomberg"
