from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from Logic.section_doubt import section_doubt
from Logic.final_mcq_ai import explain_mcq

app = FastAPI(title="AI Educator Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class SectionAIRequest(BaseModel):
    question: str
    section_id: str


class FinalMCQRequest(BaseModel):
    question: str
    options: list[str]
    user_answer: str


@app.post("/section-ai")
def section_ai(request: SectionAIRequest):
    return {
        "answer": section_doubt(
            question=request.question,
            section_id=request.section_id
        )
    }


@app.post("/final-mcqs-ai")
def final_mcqs_ai(request: FinalMCQRequest):
    return {
        "explanation": explain_mcq(
            question=request.question,
            options=request.options,
            user_answer=request.user_answer
        )
    }
