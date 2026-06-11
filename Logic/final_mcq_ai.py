# Logic/final_mcq_ai.py

import os
from groq import Groq

_groq_client = None


def _get_groq_client() -> Groq:
    """Lazy client so importing this module never requires GROQ_API_KEY."""
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    return _groq_client

MCQ_FEEDBACK_MODEL = os.getenv(
    "GROQ_FEEDBACK_MODEL",
    os.getenv("GROQ_FAST_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
)

def explain_mcq(
    question: str,
    options: dict,
    correct_answer: str,
    user_answer: str
) -> str:

    prompt = f"""
You are a Class 11 Chemistry teacher.

You are given:
- One MCQ question
- Four options
- Correct answer
- Student's selected answer

STRICT RULES:
- Use ONLY the information given here
- Do NOT use outside knowledge
- If student answer is correct:
  - Briefly explain why it is correct
  - Give encouraging feedback
- If student answer is wrong:
  - Explain where the student went wrong
  - Explain the correct concept clearly
  - Suggest what to revise

MCQ QUESTION:
{question}

OPTIONS:
A. {options['A']}
B. {options['B']}
C. {options['C']}
D. {options['D']}

CORRECT ANSWER:
{correct_answer}

STUDENT ANSWER:
{user_answer}

FINAL FEEDBACK:
"""

    response = _get_groq_client().chat.completions.create(
        model=MCQ_FEEDBACK_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=300
    )

    return response.choices[0].message.content.strip()


# -------------------- TEST --------------------
if __name__ == "__main__":
    feedback = explain_mcq(
        question="Which of the following is an alkene?",
        options={
            "A": "Ethane",
            "B": "Ethene",
            "C": "Ethyne",
            "D": "Benzene"
        },
        correct_answer="B",
        user_answer="C"
    )

    print(feedback)
