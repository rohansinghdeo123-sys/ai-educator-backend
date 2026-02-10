from prompts.base_prompt import BASE_PROMPT
from prompts.mode_layers import MODE_LAYERS
from prompts.difficulty_layers import DIFFICULTY_LAYERS


def build_prompt(question, section_content, mode, difficulty):

    mode_instruction = MODE_LAYERS.get(mode, MODE_LAYERS["classroom"])
    difficulty_instruction = DIFFICULTY_LAYERS.get(difficulty, DIFFICULTY_LAYERS["medium"])

    final_prompt = f"""
{BASE_PROMPT}

==================================================
ACTIVE MODE SETTINGS
==================================================

{mode_instruction}

{difficulty_instruction}

==================================================
SECTION CONTENT
==================================================

{section_content}

==================================================
STUDENT QUESTION
==================================================

{question}
"""

    return final_prompt
