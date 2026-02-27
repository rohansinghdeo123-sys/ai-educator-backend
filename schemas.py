from pydantic import BaseModel
from datetime import date
from typing import List


class ProgressUpdate(BaseModel):
    user_id: str
    total_tests: int
    total_questions: int
    total_correct: int
    xp: int
    streak: int


class ProgressResponse(BaseModel):
    user_id: str
    total_tests: int
    total_questions: int
    total_correct: int
    xp: int
    streak: int


class TestHistoryCreate(BaseModel):
    user_id: str
    score: int
    total_questions: int
    xp_earned: int


class TestHistoryResponse(BaseModel):
    date: date
    score: int
    total_questions: int
    xp_earned: int