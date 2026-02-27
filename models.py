from sqlalchemy import Column, Integer, String, Date, ForeignKey
from sqlalchemy.orm import relationship
from database import Base


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

    tests = relationship("TestHistory", back_populates="user")


class TestHistory(Base):
    __tablename__ = "test_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("user_progress.user_id"))
    date = Column(Date)

    score = Column(Integer)
    total_questions = Column(Integer)
    xp_earned = Column(Integer)

    user = relationship("UserProgress", back_populates="tests")