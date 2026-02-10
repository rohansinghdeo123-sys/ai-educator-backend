# Logic/section_doubt.py
from prompts.prompt_builder import build_prompt
import os
from groq import Groq

# --------------------------------------------------
# BASE DIRECTORY (ROBUST PATH HANDLING)
# --------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --------------------------------------------------
# GROQ CLIENT
# --------------------------------------------------
print("GROQ_API_KEY loaded:", bool(os.getenv("GROQ_API_KEY")))

groq_client = Groq(
    api_key=os.getenv("GROQ_API_KEY")
)

# --------------------------------------------------
# SIMPLE GLOBAL MEMORY (LEVEL 1 MEMORY)
# --------------------------------------------------
conversation_memory = []

# --------------------------------------------------
# SECTION ID → FILE PATH
# --------------------------------------------------
SECTION_FILE_MAP = {
    "alkanes": os.path.join(
        BASE_DIR,
        "data",
        "chemistry",
        "hydrocarbon",
        "part1_alkanes.md"
    ),
    "alkenes": os.path.join(
        BASE_DIR,
        "data",
        "chemistry",
        "hydrocarbon",
        "part2_alkenes.txt"
    ),
    "alkynes": os.path.join(
        BASE_DIR,
        "data",
        "chemistry",
        "hydrocarbon",
        "part3_alkynes.txt"
    ),
    "aromatics": os.path.join(
        BASE_DIR,
        "data",
        "chemistry",
        "hydrocarbon",
        "part4_aromatics.txt"
    ),
}

BASICS_PATH = os.path.join(
    BASE_DIR,
    "data",
    "datachemistry_basics.txt"
)

# --------------------------------------------------
# LOAD FILE
# --------------------------------------------------
def load_text(path: str) -> str:
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        return f.read()

# --------------------------------------------------
# SIMPLE CONTEXT LIMITER (RAILWAY SAFE)
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
# ASK AI WITH MEMORY
# --------------------------------------------------
def ask_ai(question, context_text, basics_text, mode="classroom", difficulty="medium"):

    global conversation_memory

    # Build structured prompt
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

    # Build message list with memory
    messages = []

    # Add previous conversation
    for msg in conversation_memory:
        messages.append(msg)

    # Add current question
    messages.append({"role": "user", "content": final_prompt})

    response = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=messages,
        temperature=0.2,
        max_tokens=400
    )

    answer = response.choices[0].message.content.strip()

    # Save conversation to memory
    conversation_memory.append({"role": "user", "content": question})
    conversation_memory.append({"role": "assistant", "content": answer})

    return answer

# --------------------------------------------------
# RESET MEMORY FUNCTION
# --------------------------------------------------
def reset_conversation():
    global conversation_memory
    conversation_memory.clear()

# --------------------------------------------------
# MAIN FUNCTION (FRONTEND SAFE)
# --------------------------------------------------
def section_doubt(question: str, section_id: str, mode="classroom", difficulty="medium"):

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

    return ask_ai(question, context_text, basics_text, mode, difficulty)
