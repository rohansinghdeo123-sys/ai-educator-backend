# prompts/difficulty_layers.py

DIFFICULTY_LAYERS = {
    "easy": """
DIFFICULTY: EASY
- Use very simple language. Short sentences.
- Assume the student has weak basics.
- Avoid complex terminology unless defining it.
- Use everyday analogies where possible.
""",

    "medium": """
DIFFICULTY: MEDIUM
- Standard Class 11 CBSE/ICSE level.
- Use proper chemical terminology with clarity.
- Balance between simplicity and technical accuracy.
""",

    "advanced": """
DIFFICULTY: ADVANCED
- Provide deeper conceptual reasoning.
- Include edge cases and exceptions where relevant.
- Reference reaction mechanisms briefly if applicable.
- Suitable for competitive exam preparation (JEE/NEET level).
""",
}
