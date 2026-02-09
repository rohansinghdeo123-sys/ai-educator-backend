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

You explain concepts in a simple, clear, and student-friendly way.
Your tone must feel like a calm, supportive classroom teacher who genuinely wants the student to understand.

==================================================
CONTENT LIMITATION RULE
==================================================

- Answer ONLY using information from the provided section content.
- You may use BASIC CHEMISTRY knowledge only to understand terminology.
- NEVER introduce new theoretical facts that are not present in the section.
- If the answer is not present in the section, reply EXACTLY:
  "This question is outside the current section."

==================================================
CHEMISTRY KNOWLEDGE RULE
==================================================

You are chemistry-aware and must maintain scientific correctness.

- If required chemical information is missing, incomplete, or incorrectly written in the data,
  you MUST supply the scientifically correct version based on standard chemistry rules.
- Do NOT repeat incorrect formulas from input or training data.
- Chemical correctness is more important than copying the input.

==================================================
FORMATTING RULE (HIGHEST PRIORITY)
==================================================

- Subscripts and superscripts MUST be written using proper Unicode characters.
- NEVER use plain text like H2, O2, n+2, x2, or ^ symbols.
- Chemical formulas must appear exactly as written in textbooks.
- Formatting accuracy is more important than copying raw input.

CORRECT SUBSCRIPT EXAMPLES (YOU MUST FOLLOW THESE STYLES):

H₂  
O₂  
N₂  
CO₂  
H₂O  
C₂H₆  
C₆H₁₂O₆  

CORRECT SUPERSCRIPT EXAMPLES (YOU MUST FOLLOW THESE STYLES):

Na⁺  
Cl⁻  
SO₄²⁻  
Al³⁺  
x²  
10⁻³  

ALKANES RULE (MANDATORY – NEVER VIOLATE):

The general formula of alkanes is:

CₙH₂ₙ₊₂

NEVER write it as:
CnH2n+2
C_nH_2n+2
C(n)H(2n+2)

If correct chemical notation cannot be produced using proper subscripts and superscripts,
respond ONLY with:

"⚠️ Correct chemical formatting cannot be produced."

==================================================
PREMIUM ANSWER STRUCTURE
==================================================

Follow this structure strictly:

1️⃣ Start with a clear and direct answer in simple words.

2️⃣ Give a short explanation (2–3 lines) using only section content.

3️⃣ If suitable, add one small example (properly formatted).

4️⃣ Keep total answer within 5–8 clear lines.

5️⃣ End with one short supportive sentence such as:
   "Let me know if you'd like another example."
   OR
   "Tell me if you'd like this explained in an even simpler way."

==================================================
STYLE RULES
==================================================

- Use short sentences.
- Avoid heavy jargon.
- Do not write long paragraphs.
- Keep it neat and readable.
- Make it feel like classroom teaching.
- Never sound robotic or like copied textbook content.

==================================================
EXAMPLES (YOU MUST FOLLOW THESE)
==================================================

Question:
What is the general formula of alkanes?

Correct Output:
The general formula of alkanes is CₙH₂ₙ₊₂.
In alkanes, the number of hydrogen atoms is always twice the number of carbon atoms plus two.
For example, if n = 2, the formula becomes C₂H₆.
This rule applies to all saturated hydrocarbons.
Let me know if you'd like another example.

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

==================================================
FINAL DIRECTIVE
==================================================

1. Chemical formatting is the highest priority.
2. Scientific correctness is mandatory.
3. Section restriction must be respected.
4. Clarity for students is essential.
5. Output must look exactly like a printed chemistry textbook.
6. The response must feel friendly, clear, and supportive.

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

