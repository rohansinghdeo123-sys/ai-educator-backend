from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import SessionLocal, engine
from models import Base, UserProgress

from Logic.section_doubt import section_doubt, reset_conversation
from Logic.final_mcq_ai import explain_mcq


# ================= INIT =================

Base.metadata.create_all(bind=engine)

app = FastAPI(title="AI Educator Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # safe for development
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ================= DATABASE DEPENDENCY =================

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
    return {"message": "Chat session reset successfully."}


# ================= FINAL MCQ =================

class FinalMCQRequest(BaseModel):
    question: str
    options: list[str]
    user_answer: str


@app.post("/final-mcqs-ai")
def final_mcqs_ai(request: FinalMCQRequest):
    return {
        "explanation": explain_mcq(
            question=request.question,
            options=request.options,
            user_answer=request.user_answer
        )
    }


# ================= UPDATE PROGRESS =================

class ProgressUpdate(BaseModel):
    user_id: str
    total_tests: int
    total_questions: int
    total_correct: int
    xp: int
    streak: int


@app.post("/update-progress")
def update_progress(progress: ProgressUpdate, db: Session = Depends(get_db)):
    user = db.query(UserProgress).filter(
        UserProgress.user_id == progress.user_id
    ).first()

    if not user:
        user = UserProgress(
            user_id=progress.user_id,
            total_tests=progress.total_tests,
            total_questions=progress.total_questions,
            total_correct=progress.total_correct,
            xp=progress.xp,
            streak=progress.streak,
        )
        db.add(user)
    else:
        user.total_tests = progress.total_tests
        user.total_questions = progress.total_questions
        user.total_correct = progress.total_correct
        user.xp = progress.xp
        user.streak = progress.streak

    db.commit()
    db.refresh(user)

    return {
        "message": "Progress updated successfully.",
        "streak": user.streak
    }


# ================= GET PROGRESS =================

@app.get("/get-progress/{user_id}")
def get_progress(user_id: str, db: Session = Depends(get_db)):
    user = db.query(UserProgress).filter(
        UserProgress.user_id == user_id
    ).first()

    if not user:
        return {
            "user_id": user_id,
            "total_tests": 0,
            "total_questions": 0,
            "total_correct": 0,
            "xp": 0,
            "streak": 0
        }

    return {
        "user_id": user.user_id,
        "total_tests": user.total_tests,
        "total_questions": user.total_questions,
        "total_correct": user.total_correct,
        "xp": user.xp,
        "streak": user.streak
    }


# ================= LEADERBOARD =================

@app.get("/leaderboard")
def leaderboard(db: Session = Depends(get_db)):
    users = (
        db.query(UserProgress)
        .order_by(UserProgress.xp.desc())
        .limit(10)
        .all()
    )

    return [
        {
            "user_id": u.user_id,
            "xp": u.xp,
            "streak": u.streak
        }
        for u in users
    ]