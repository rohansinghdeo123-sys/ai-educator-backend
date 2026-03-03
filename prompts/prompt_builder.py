from prompts.base_prompt import BASE_PROMPT
from prompts.mode_layers import MODE_LAYERS
from prompts.difficulty_layers import DIFFICULTY_LAYERS


def build_messages(question, section_content, mode, difficulty):

    mode_instruction = MODE_LAYERS.get(mode, MODE_LAYERS["classroom"])
    difficulty_instruction = DIFFICULTY_LAYERS.get(difficulty, DIFFICULTY_LAYERS["medium"])

    system_message = f"""
{BASE_PROMPT}

==================================================
ACTIVE MODE SETTINGS
==================================================

{mode_instruction}

{difficulty_instruction}
"""

    user_message = f"""
SECTION CONTENT:
{section_content}

STUDENT QUESTION:
{question}

Remember:
• Use only the provided section content.
• Follow the required structure.
• Ensure correct chemical formatting.
"""

    return [
        {"role": "system", "content": system_message.strip()},
        {"role": "user", "content": user_message.strip()}
    ]