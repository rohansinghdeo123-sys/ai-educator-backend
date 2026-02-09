# Logic/final_mcq_ai.py

import os
from groq import Groq

groq_client = Groq(
    api_key=os.getenv("GROQ_API_KEY")
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

    response = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
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
