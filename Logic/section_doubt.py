# Logic/section_doubt.py

from prompts.prompt_builder import build_prompt
from prompts.summary_prompt import SUMMARY_PROMPT
import os
from groq import Groq

# --------------------------------------------------
# BASE DIRECTORY
# --------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --------------------------------------------------
# GROQ CLIENT
# --------------------------------------------------
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# --------------------------------------------------
# SESSION MEMORY STORAGE
# --------------------------------------------------
chat_sessions = {}

# --------------------------------------------------
# SECTION FILE MAP
# --------------------------------------------------
SECTION_FILE_MAP = {
    "alkanes": os.path.join(BASE_DIR, "data", "chemistry", "hydrocarbon", "part1_alkanes.md"),
    "alkenes": os.path.join(BASE_DIR, "data", "chemistry", "hydrocarbon", "part2_alkenes.txt"),
    "alkynes": os.path.join(BASE_DIR, "data", "chemistry", "hydrocarbon", "part3_alkynes.txt"),
    "aromatics": os.path.join(BASE_DIR, "data", "chemistry", "hydrocarbon", "part4_aromatics.txt"),
}

BASICS_PATH = os.path.join(BASE_DIR, "data", "datachemistry_basics.txt")

# --------------------------------------------------
# LOAD FILE
# --------------------------------------------------
def load_text(path: str) -> str:
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

# --------------------------------------------------
# CONTEXT LIMITER
# --------------------------------------------------
def get_relevant_context(text: str, question: str, max_chars: int = 1500):
    if len(text) <= max_chars:
        return text

    question_words = question.lower().split()
    paragraphs = text.split("\n\n")

    scored = []
    for para in paragraphs:
        score = sum(word in para.lower() for word in question_words)
        scored.append((score, para))

    scored.sort(reverse=True)

    selected = []
    total_len = 0
    for score, para in scored:
        if total_len + len(para) > max_chars:
            break
        selected.append(para)
        total_len += len(para)

    return "\n\n".join(selected) if selected else text[:max_chars]

# --------------------------------------------------
# ASK AI FUNCTION
# --------------------------------------------------
def ask_ai(question, context_text, basics_text, session_id,
           mode="revision", difficulty="medium"):

    global chat_sessions

    if session_id not in chat_sessions:
        chat_sessions[session_id] = []

    conversation_memory = chat_sessions[session_id]

    # ==================================================
    # SUMMARY MODE
    # ==================================================
    if mode == "summary":

        summary_prompt = f"""
{SUMMARY_PROMPT}

SECTION CONTENT:
{context_text}
"""

        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": summary_prompt}],
            temperature=0.3,
            max_tokens=300
        )

        return response.choices[0].message.content.strip()

    # ==================================================
    # KEY POINTS MODE
    # ==================================================
    if mode == "keypoints":

        keypoints_prompt = f"""
Generate concise key revision bullet points from the following content.

Rules:
- No greetings
- No markdown
- No bold text
- Clear bullet points only

CONTENT:
{context_text}
"""

        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": keypoints_prompt}],
            temperature=0.3,
            max_tokens=300
        )

        return response.choices[0].message.content.strip()

    # ==================================================
    # EXPLAIN MODE
    # ==================================================
    if mode == "explain":

        explain_prompt = f"""
Explain the following concept clearly and simply for a student.

Question:
{question}

CONTENT:
{context_text}
"""

        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": explain_prompt}],
            temperature=0.3,
            max_tokens=400
        )

        return response.choices[0].message.content.strip()

    # ==================================================
    # EXAM MODE (MCQs)
    # ==================================================
    if mode == "exam":

        exam_prompt = f"""
You are an expert chemistry exam paper setter.

Generate exactly 5 high-quality exam-level MCQs.

STRICT RULES:
- No bold text
- No markdown formatting
- No introduction
- No greetings
- No commentary
- Plain clean text only

FORMAT STRICTLY LIKE THIS:

Q1. Question text?
A. Option
B. Option
C. Option
D. Option
Answer: Correct option letter
Explanation: Short explanation

CONTENT:
{context_text}
"""

        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": exam_prompt}],
            temperature=0.4,
            max_tokens=700
        )

        return response.choices[0].message.content.strip()

    # ==================================================
    # PROBABLE THEORY QUESTIONS
    # ==================================================
    if mode == "probable":

        probable_prompt = f"""
Generate probable exam theory questions.

Rules:
- Generate 3 questions of 3 marks
- Generate 2 questions of 5 marks
- No markdown
- No greetings
- No extra commentary

CONTENT:
{context_text}
"""

        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": probable_prompt}],
            temperature=0.4,
            max_tokens=500
        )

        return response.choices[0].message.content.strip()

    # ==================================================
    # DEFAULT REVISION / ASK AI MODE
    # ==================================================

    core_prompt = build_prompt(
        question=question,
        section_content=context_text,
        mode=mode,
        difficulty=difficulty
    )

    final_prompt = f"""
{core_prompt}

--------------------------------------------------
BASIC CHEMISTRY (support only)
{basics_text}
--------------------------------------------------
"""

    messages = conversation_memory.copy()
    messages.append({"role": "user", "content": final_prompt})

    response = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=messages,
        temperature=0.2,
        max_tokens=400
    )

    answer = response.choices[0].message.content.strip()

    conversation_memory.append({"role": "user", "content": question})
    conversation_memory.append({"role": "assistant", "content": answer})

    if len(conversation_memory) > 10:
        chat_sessions[session_id] = conversation_memory[-10:]

    # 🔥 Add welcoming message ONLY for revision (Ask AI)
    if mode == "revision":
        greeting = "Nice question! Let’s understand this step by step.\n\n"
        return greeting + answer

    return answer


# --------------------------------------------------
# RESET
# --------------------------------------------------
def reset_conversation(session_id: str):
    global chat_sessions
    if session_id in chat_sessions:
        chat_sessions.pop(session_id)


# --------------------------------------------------
# MAIN FUNCTION
# --------------------------------------------------
def section_doubt(question: str, section_id: str,
                  session_id: str,
                  mode="revision",
                  difficulty="medium"):

    if not section_id:
        return "Invalid section selected."

    section_id = section_id.strip().lower()

    if section_id not in SECTION_FILE_MAP:
        return "Invalid section selected."

    try:
        section_text = load_text(SECTION_FILE_MAP[section_id])
        basics_text = load_text(BASICS_PATH)
    except Exception:
        return "⚠️ Section content could not be loaded."

    context_text = get_relevant_context(section_text, question)

    return ask_ai(
        question,
        context_text,
        basics_text,
        session_id,
        mode,
        difficulty
    )