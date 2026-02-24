from pydantic import BaseModel

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