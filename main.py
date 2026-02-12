from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from Logic.section_doubt import section_doubt, reset_conversation
from Logic.final_mcq_ai import explain_mcq

app = FastAPI(title="AI Educator Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------
# SECTION AI REQUEST (SESSION ENABLED)
# --------------------------------------------------
class SectionAIRequest(BaseModel):
    question: str
    section_id: str
    session_id: str
    mode: str = "classroom"
    difficulty: str = "medium"


# --------------------------------------------------
# FINAL MCQ REQUEST
# --------------------------------------------------
class FinalMCQRequest(BaseModel):
    question: str
    options: list[str]
    user_answer: str


# --------------------------------------------------
# RESET REQUEST MODEL (IMPORTANT FIX)
# --------------------------------------------------
class ResetRequest(BaseModel):
    session_id: str


# --------------------------------------------------
# SECTION AI ENDPOINT
# --------------------------------------------------
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


# --------------------------------------------------
# RESET CHAT ENDPOINT (PROPER JSON BODY)
# --------------------------------------------------
@app.post("/reset-chat")
def reset_chat(request: ResetRequest):
    reset_conversation(request.session_id)
    return {"message": "Chat session reset successfully."}


# --------------------------------------------------
# FINAL MCQ ENDPOINT
# --------------------------------------------------
@app.post("/final-mcqs-ai")
def final_mcqs_ai(request: FinalMCQRequest):
    return {
        "explanation": explain_mcq(
            question=request.question,
            options=request.options,
            user_answer=request.user_answer
        )
    }
