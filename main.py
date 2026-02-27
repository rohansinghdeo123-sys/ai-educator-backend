from datetime import date
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from database import SessionLocal, engine
from models import Base, UserProgress, TestHistory
from schemas import (
    ProgressUpdate,
    ProgressResponse,
    TestHistoryCreate,
    TestHistoryResponse,
)

from pydantic import BaseModel
from typing import List

from Logic.section_doubt import section_doubt, reset_conversation
from Logic.final_mcq_ai import explain_mcq

Base.metadata.create_all(bind=engine)

app = FastAPI(title="AI Educator Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ================= DATABASE =================

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ================= SECTION AI =================

class SectionAIRequest(BaseModel):
    question: str
    section_id: str
    session_id: str
    mode: str = "revision"
    difficulty: str = "medium"


@app.post("/section-ai")
def section_ai(request: SectionAIRequest):
    return {
        "answer": section_doubt(
            question=request.question,
            section_id=request.section_id,
            session_id=request.session_id,
            mode=request.mode,
            difficulty=request.difficulty
        )
    }


# ================= RESET CHAT =================

class ResetRequest(BaseModel):
    session_id: str


@app.post("/reset-chat")
def reset_chat(request: ResetRequest):
    reset_conversation(request.session_id)
    return {"message": "Chat reset successfully"}


# ================= UPDATE PROGRESS =================

@app.post("/update-progress")
def update_progress(progress: ProgressUpdate, db: Session = Depends(get_db)):
    user = db.query(UserProgress).filter(UserProgress.user_id == progress.user_id).first()

    today = date.today()

    if not user:
        user = UserProgress(
            user_id=progress.user_id,
            total_tests=0,
            total_questions=0,
            total_correct=0,
            xp=0,
            streak=0,
            last_active_date=today
        )
        db.add(user)

    user.total_tests = progress.total_tests
    user.total_questions = progress.total_questions
    user.total_correct = progress.total_correct
    user.xp = progress.xp

    if user.last_active_date:
        difference = (today - user.last_active_date).days
        if difference == 1:
            user.streak += 1
        elif difference > 1:
            user.streak = 1
    else:
        user.streak = 1

    user.last_active_date = today

    db.commit()
    db.refresh(user)

    return {"message": "Progress updated successfully", "streak": user.streak}


# ================= SAVE TEST HISTORY =================

@app.post("/save-test")
def save_test(test: TestHistoryCreate, db: Session = Depends(get_db)):
    new_test = TestHistory(
        user_id=test.user_id,
        date=date.today(),
        score=test.score,
        total_questions=test.total_questions,
        xp_earned=test.xp_earned
    )

    db.add(new_test)
    db.commit()

    return {"message": "Test saved successfully"}


# ================= GET PROGRESS =================

@app.get("/get-progress/{user_id}", response_model=ProgressResponse)
def get_progress(user_id: str, db: Session = Depends(get_db)):
    user = db.query(UserProgress).filter(UserProgress.user_id == user_id).first()

    if not user:
        return ProgressResponse(
            user_id=user_id,
            total_tests=0,
            total_questions=0,
            total_correct=0,
            xp=0,
            streak=0
        )

    return user


# ================= GET TEST HISTORY =================

@app.get("/test-history/{user_id}", response_model=List[TestHistoryResponse])
def get_test_history(user_id: str, db: Session = Depends(get_db)):
    tests = (
        db.query(TestHistory)
        .filter(TestHistory.user_id == user_id)
        .order_by(TestHistory.date.asc())
        .all()
    )

    return tests


# ================= LEADERBOARD =================

@app.get("/leaderboard")
def leaderboard(db: Session = Depends(get_db)):
    users = db.query(UserProgress).order_by(UserProgress.xp.desc()).limit(10).all()

    return [
        {
            "user_id": u.user_id,
            "xp": u.xp,
            "streak": u.streak
        }
        for u in users
    ]