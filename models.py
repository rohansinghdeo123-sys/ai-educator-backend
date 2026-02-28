from sqlalchemy import Column, Integer, String, Date
from database import Base


class UserProgress(Base):
    __tablename__ = "user_progress007"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, unique=True, index=True)

    total_tests = Column(Integer, default=0)
    total_questions = Column(Integer, default=0)
    total_correct = Column(Integer, default=0)

    xp = Column(Integer, default=0)
    streak = Column(Integer, default=0)

    last_active_date = Column(Date, nullable=True)


class TestHistory(Base):
    __tablename__ = "test_history007"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)
    date = Column(Date)

    score = Column(Integer)
    total_questions = Column(Integer)
    xp_earned = Column(Integer)