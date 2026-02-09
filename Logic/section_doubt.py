# Logic/section_doubt.py

import os
import faiss
import numpy as np
from sentence_transformers import SentenceTransformer
from groq import Groq

# --------------------------------------------------
# BASE DIRECTORY (ROBUST PATH HANDLING)
# --------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# BASE_DIR → AI_Educater/backend

# --------------------------------------------------
# MODELS
# --------------------------------------------------
embedding_model = SentenceTransformer("all-MiniLM-L6-v2")

print("GROQ_API_KEY loaded:", bool(os.getenv("GROQ_API_KEY")))

groq_client = Groq(
    api_key=os.getenv("GROQ_API_KEY")
)

# --------------------------------------------------
# SECTION ID → FILE PATH (ABSOLUTE PATHS)
# --------------------------------------------------
SECTION_FILE_MAP = {
    "alkanes": os.path.join(
        BASE_DIR,
        "data",
        "chemistry",
        "hydrocarbon",
        "part1_alkanes.md"
    ),
    # future-ready
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
# SMART PARAGRAPH CHUNKING (CHEMISTRY SAFE)
# --------------------------------------------------
def chunk_text(text: str, min_len: int = 60):
    """
    Preserve equations, definitions, reactions.
    """
    paras = [
        p.strip()
        for p in text.split("\n\n")
        if len(p.strip()) >= min_len
    ]
    return paras

# --------------------------------------------------
# BUILD FAISS INDEX
# --------------------------------------------------
def build_index(chunks):
    embeddings = embedding_model.encode(
        chunks, normalize_embeddings=True
    )

    index = faiss.IndexFlatIP(embeddings.shape[1])
    index.add(np.array(embeddings))

    return index

# --------------------------------------------------
# RETRIEVE CONTEXT
# --------------------------------------------------
def retrieve_chunks(question, chunks, index, k=5):
    q_embedding = embedding_model.encode(
        [question], normalize_embeddings=True
    )

    _, indices = index.search(np.array(q_embedding), k)

    return [chunks[i] for i in indices[0] if i < len(chunks)]

# --------------------------------------------------
# ASK AI
# --------------------------------------------------
def ask_ai(question, context_chunks, basics_text):
    context = "\n\n".join(context_chunks)

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
{context}

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
# MAIN FUNCTION (FRONTEND CALL)
# --------------------------------------------------
def section_doubt(question: str, section_id: str):

    print("SECTION ID RECEIVED:", section_id)

    if not section_id:
        return "Invalid section selected."

    section_id = section_id.strip().lower()

    if section_id not in SECTION_FILE_MAP:
        return "Invalid section selected."

    try:
        section_text = load_text(SECTION_FILE_MAP[section_id])
        basics_text = load_text(BASICS_PATH)
    except Exception as e:
        print("FILE ERROR:", e)
        return "⚠️ Section content could not be loaded."

    chunks = chunk_text(section_text)
    if not chunks:
        return "This question is outside the current section."

    index = build_index(chunks)
    context_chunks = retrieve_chunks(question, chunks, index)

    if not context_chunks:
        return "This question is outside the current section."

    return ask_ai(question, context_chunks, basics_text)


# --------------------------------------------------
# LOCAL TEST
# --------------------------------------------------
if __name__ == "__main__":
    print(
        section_doubt(
            question="What is the general formula of alkanes?",
            section_id="alkanes"
        )
    )
