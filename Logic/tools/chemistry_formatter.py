# Logic/tools/chemistry_formatter.py

import re
import logging

logger = logging.getLogger("ai_educator.tools.chemistry_formatter")

# =====================================================
# UNICODE SUBSCRIPT/SUPERSCRIPT MAPS
# =====================================================
SUBSCRIPT_MAP = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
    "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "n": "ₙ", "m": "ₘ", "x": "ₓ",
    "+": "₊", "-": "₋",
}

SUPERSCRIPT_MAP = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻", "n": "ⁿ",
}

# =====================================================
# KNOWN FORMULA CORRECTIONS
# =====================================================
FORMULA_CORRECTIONS = {
    # Alkanes
    "CH4": "CH₄",
    "C2H6": "C₂H₆",
    "C3H8": "C₃H₈",
    "C4H10": "C₄H₁₀",
    "C5H12": "C₅H₁₂",
    "C6H14": "C₆H₁₄",
    "CnH2n+2": "CₙH₂ₙ₊₂",
    "CnH(2n+2)": "CₙH₂ₙ₊₂",
    "Cn H2n+2": "CₙH₂ₙ₊₂",

    # Alkenes
    "C2H4": "C₂H₄",
    "C3H6": "C₃H₆",
    "CnH2n": "CₙH₂ₙ",
    "Cn H2n": "CₙH₂ₙ",

    # Alkynes
    "C2H2": "C₂H₂",
    "C3H4": "C₃H₄",
    "CnH2n-2": "CₙH₂ₙ₋₂",
    "Cn H2n-2": "CₙH₂ₙ₋₂",

    # Common molecules
    "H2O": "H₂O",
    "CO2": "CO₂",
    "H2": "H₂",
    "O2": "O₂",
    "N2": "N₂",
    "Cl2": "Cl₂",
    "Br2": "Br₂",
    "HCl": "HCl",
    "H2SO4": "H₂SO₄",
    "Na2CO3": "Na₂CO₃",
    "NaOH": "NaOH",
    "KMnO4": "KMnO₄",
    "SO4^2-": "SO₄²⁻",
    "NH4+": "NH₄⁺",
    "OH-": "OH⁻",

    # Aromatic
    "C6H6": "C₆H₆",
    "C6H5": "C₆H₅",
}


def _fix_inline_formulas(text: str) -> str:
    """Fix common inline formula patterns like C2H6 → C₂H₆."""

    # Pattern: Element followed by number (e.g., C2, H6, O2)
    # This handles cases like "CH4 is methane" → "CH₄ is methane"
    def replace_element_number(match):
        element = match.group(1)
        number = match.group(2)
        subscripted = "".join(SUBSCRIPT_MAP.get(d, d) for d in number)
        return element + subscripted

    # Match: uppercase letter (optionally followed by lowercase) then digits
    # But NOT if preceded by another letter (to avoid matching words like "Step2")
    text = re.sub(
        r'(?<![a-zA-Z])([A-Z][a-z]?)(\d+)',
        replace_element_number,
        text
    )

    return text


def _fix_charge_notation(text: str) -> str:
    """Fix charge notations like 2+ → ²⁺, 2- → ²⁻."""
    # Pattern: digit followed by + or - in chemical context
    def replace_charge(match):
        num = match.group(1)
        sign = match.group(2)
        sup_num = SUPERSCRIPT_MAP.get(num, num) if num else ""
        sup_sign = SUPERSCRIPT_MAP.get(sign, sign)
        return sup_num + sup_sign

    text = re.sub(r'(\d?)([+-])(?=\s|$|,|\.|\))', replace_charge, text)
    return text


def format_chemistry_output(text: str) -> str:
    """
    TOOL: Post-process AI output to ensure correct chemical formatting.

    This tool:
    1. Replaces known incorrect formulas with correct Unicode versions
    2. Fixes inline element+number patterns
    3. Fixes charge notations
    4. Removes any markdown formatting that slipped through

    Args:
        text: Raw AI output text

    Returns:
        Cleaned and formatted text with proper Unicode chemistry
    """
    if not text:
        return text

    # Step 1: Apply known formula corrections (longest first to avoid partial matches)
    sorted_corrections = sorted(FORMULA_CORRECTIONS.items(), key=lambda x: len(x[0]), reverse=True)
    for wrong, correct in sorted_corrections:
        # Case-sensitive replacement
        text = text.replace(wrong, correct)

    # Step 2: Fix remaining inline formulas
    text = _fix_inline_formulas(text)

    # Step 3: Fix charge notations
    text = _fix_charge_notation(text)

    # Step 4: Remove markdown formatting
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # Remove bold
    text = re.sub(r'#{1,6}\s*', '', text)           # Remove headings
    text = re.sub(r'```[\s\S]*?```', '', text)      # Remove code blocks
    text = re.sub(r'`(.+?)`', r'\1', text)          # Remove inline code

    # Step 5: Clean up extra whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()

    logger.debug("Chemistry formatting applied successfully.")
    return text