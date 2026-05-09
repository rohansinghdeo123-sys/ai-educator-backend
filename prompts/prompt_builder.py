from prompts.base_prompt import BASE_PROMPT
from prompts.mode_layers import MODE_LAYERS
from prompts.difficulty_layers import DIFFICULTY_LAYERS


VALID_MODES = {"classroom", "exam", "revision", "practice", "summary", "explain", "keypoints"}
VALID_DIFFICULTIES = {"easy", "medium", "advanced"}


def normalize_mode(mode: str = "classroom") -> str:
    mode = (mode or "classroom").strip().lower()
    if mode == "key":
        return "keypoints"
    return mode if mode in VALID_MODES else "classroom"


def normalize_difficulty(difficulty: str = "medium") -> str:
    difficulty = (difficulty or "medium").strip().lower()
    return difficulty if difficulty in VALID_DIFFICULTIES else "medium"


def build_prompt(
    question: str,
    section_content: str,
    mode: str = "classroom",
    difficulty: str = "medium",
) -> list[dict[str, str]]:
    """
    Build a normal teaching/revision prompt as Groq-compatible chat messages.
    Keep this as a message list. Do not convert it into a string before calling the model.
    """

    normalized_mode = normalize_mode(mode)
    normalized_difficulty = normalize_difficulty(difficulty)

    mode_instruction = MODE_LAYERS.get(
        normalized_mode,
        MODE_LAYERS["classroom"],
    )
    difficulty_instruction = DIFFICULTY_LAYERS.get(
        normalized_difficulty,
        DIFFICULTY_LAYERS["medium"],
    )

    system_message = f"""{BASE_PROMPT}

==================================================
ACTIVE SESSION CONFIGURATION
==================================================

{mode_instruction.strip()}

{difficulty_instruction.strip()}
"""

    user_message = f"""SECTION CONTENT:
{section_content.strip()}

STUDENT QUESTION:
{question.strip()}

RESPONSE RULES:
- Use only the section content above.
- Follow the active mode and difficulty settings.
- Ensure correct Unicode chemical formatting.
- No markdown.
- No greeting.
- Plain text only."""

    return [
        {"role": "system", "content": system_message.strip()},
        {"role": "user", "content": user_message.strip()},
    ]


def build_mcq_prompt(
    section_content: str,
    topic: str,
    difficulty: str = "medium",
    count: int = 5,
) -> list[dict[str, str]]:
    """
    Build a strict structured MCQ prompt.
    The frontend should receive parsed JSON, not raw AI text.
    """

    normalized_difficulty = normalize_difficulty(difficulty)
    safe_count = max(1, min(count, 10))

    system_message = f"""{BASE_PROMPT}

You are a senior Class 11 Chemistry exam designer.

Your task is to generate high-quality MCQs from the provided section content.

STRICT SOURCE RULE:
- Use only the provided SECTION CONTENT.
- Do not add facts outside the section.
- If the section does not support a valid question, avoid that question.

STRICT OUTPUT RULE:
- Return valid JSON only.
- Do not wrap JSON in markdown.
- Do not include comments.
- Do not include trailing commas.
- Do not include any text before or after the JSON.

CHEMISTRY FORMAT RULE:
- Use proper Unicode subscripts and superscripts.
- Write CH₄, C₂H₆, CₙH₂ₙ₊₂, not CH4, C2H6, CnH2n+2.

QUESTION QUALITY RULE:
- Questions must test understanding, not only memory.
- Distractors must be plausible.
- Each explanation must teach why the correct option is correct.
- Avoid duplicate questions.
- Avoid vague wording such as "which is true" unless all options are precise.
"""

    user_message = f"""TOPIC:
{topic.strip()}

DIFFICULTY:
{normalized_difficulty}

NUMBER_OF_QUESTIONS:
{safe_count}

SECTION CONTENT:
{section_content.strip()}

Return JSON in exactly this shape:
{{
  "topic": "{topic.strip()}",
  "difficulty": "{normalized_difficulty}",
  "questions": [
    {{
      "id": "Q1",
      "question": "Question text",
      "options": [
        {{ "key": "A", "text": "Option text" }},
        {{ "key": "B", "text": "Option text" }},
        {{ "key": "C", "text": "Option text" }},
        {{ "key": "D", "text": "Option text" }}
      ],
      "answer": "A",
      "explanation": "Short explanation"
    }}
  ]
}}"""

    return [
        {"role": "system", "content": system_message.strip()},
        {"role": "user", "content": user_message.strip()},
    ]


def build_probable_questions_prompt(
    section_content: str,
    topic: str,
    difficulty: str = "medium",
) -> list[dict[str, str]]:
    """
    Build structured probable theory questions for exam preparation.
    """

    normalized_difficulty = normalize_difficulty(difficulty)

    system_message = f"""{BASE_PROMPT}

You are a senior Class 11 Chemistry exam paper setter.

STRICT OUTPUT RULE:
- Return valid JSON only.
- Do not wrap JSON in markdown.
- Do not include comments.
- Do not include any text before or after the JSON.

STRICT SOURCE RULE:
- Use only the provided SECTION CONTENT.
- Do not invent questions from outside the section.

QUALITY RULE:
- Questions must be exam-realistic.
- Keep wording clear and direct.
- Do not provide answers, hints, or explanations.
"""

    user_message = f"""TOPIC:
{topic.strip()}

DIFFICULTY:
{normalized_difficulty}

SECTION CONTENT:
{section_content.strip()}

Return JSON in exactly this shape:
{{
  "topic": "{topic.strip()}",
  "difficulty": "{normalized_difficulty}",
  "questions": [
    {{ "id": "Q1", "marks": 3, "question": "Question text" }},
    {{ "id": "Q2", "marks": 3, "question": "Question text" }},
    {{ "id": "Q3", "marks": 3, "question": "Question text" }},
    {{ "id": "Q4", "marks": 5, "question": "Question text" }},
    {{ "id": "Q5", "marks": 5, "question": "Question text" }}
  ]
}}"""

    return [
        {"role": "system", "content": system_message.strip()},
        {"role": "user", "content": user_message.strip()},
    ]


def build_agent_system_prompt(
    mode: str = "classroom",
    difficulty: str = "medium",
) -> str:
    normalized_mode = normalize_mode(mode)
    normalized_difficulty = normalize_difficulty(difficulty)

    mode_instruction = MODE_LAYERS.get(
        normalized_mode,
        MODE_LAYERS["classroom"],
    )
    difficulty_instruction = DIFFICULTY_LAYERS.get(
        normalized_difficulty,
        DIFFICULTY_LAYERS["medium"],
    )

    return f"""{BASE_PROMPT}

==================================================
ACTIVE SESSION CONFIGURATION
==================================================

{mode_instruction.strip()}

{difficulty_instruction.strip()}

==================================================
AGENT TOOL USAGE RULES
==================================================

- When a student asks about a specific topic, use the `search_study_material` tool.
- When you need to personalize advice, use the `get_student_progress` tool.
- Always answer based on tool results, never from your own training data.
- If no tool is needed, answer directly from the provided context.
""".strip()
