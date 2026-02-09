# Logic/section_doubt.py

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
def get_relevant_context(text: str, question: str, max_chars: int = 4000):
    """
    Lightweight context selection without FAISS.
    """
    if len(text) <= max_chars:
        return text

    # Simple keyword matching
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
# ASK AI (SYSTEM PROMPT UNCHANGED)
# --------------------------------------------------
def ask_ai(question, context_text, basics_text):

    prompt = f"""
You are an expert Class 11 Chemistry teacher.

Answer ONLY from the section content.

You may use BASIC CHEMISTRY only to understand terms,
but NEVER introduce facts not present in the section.

RULES:
- Explain in simple student-friendly language
- Answer in 4–6 clear lines
- Use ONLY section content
- If answer is not present, reply EXACTLY:
  "This question is outside the current section."

You are a chemistry-aware AI with correct domain knowledge.

CHEMISTRY KNOWLEDGE RULE:
- If required chemical information is missing, incomplete, or incorrectly written in the data,
  you MUST supply the scientifically correct version from standard chemistry rules.
- Do NOT repeat incorrect formulas from input or training data.

FORMATTING RULE (HIGHEST PRIORITY):
- Subscripts and superscripts MUST be written using proper Unicode characters.
- Never use plain text like H2, n+2, or ^ symbols.
- Chemical formulas must appear exactly as they are written in textbooks.

ALKANES RULE (MANDATORY):
- The general formula of alkanes is:
  CₙH₂ₙ₊₂
- NEVER write it as:
  CnH2n+2
  C_nH_2n+2
  C(n)H(2n+2)

EXAMPLES (YOU MUST FOLLOW THESE):

Question:
What is the general formula of alkanes?

Correct Output:
The general formula of alkanes is CₙH₂ₙ₊₂.

---

Incorrect Input:
CnH2n+2

Correct Output:
CₙH₂ₙ₊₂

---

Question:
Give the general formula even if it is not present in the data.

Correct Output:
CₙH₂ₙ₊₂

FAIL-SAFE:
If correct chemical notation cannot be produced using subscripts and superscripts,
respond only with:
"⚠️ Correct chemical formatting cannot be produced."

FINAL DIRECTIVE:
Chemical correctness is more important than copying the input.
Chemical formulas must be both scientifically correct and perfectly formatted.

-------------------------
BASIC CHEMISTRY (support only)
{basics_text}
-------------------------

SECTION CONTENT
{context_text}

QUESTION:
{question}

ANSWER:
"""

    response = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=400
    )

    return response.choices[0].message.content.strip()

# --------------------------------------------------
# MAIN FUNCTION
# --------------------------------------------------
def section_doubt(question: str, section_id: str):

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

    return ask_ai(question, context_text, basics_text)
