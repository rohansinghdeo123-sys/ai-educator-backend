# Logic/tools/answer_evaluator.py

import re
import logging

logger = logging.getLogger("ai_educator.tools.answer_evaluator")


def evaluate_answer_quality(
    question: str,
    answer: str,
    mode: str,
    context: str = "",
) -> dict:
    """
    TOOL: Evaluate the quality of an AI-generated answer.

    Checks:
    1. Is the answer empty or too short?
    2. Does it contain broken chemical formulas?
    3. Does it match the expected structure for the mode?
    4. Does it contain hallucination markers?

    Returns:
        dict with keys:
        - "passed": bool — Whether the answer meets quality standards
        - "score": float — Quality score from 0.0 to 1.0
        - "issues": list — List of detected issues
        - "suggestion": str — What to fix if failed
    """
    issues = []
    score = 1.0

    # ---- CHECK 1: Empty or too short ----
    if not answer or len(answer.strip()) < 20:
        issues.append("EMPTY_OR_SHORT: Answer is too short to be useful.")
        score -= 0.5

    # ---- CHECK 2: Broken chemical formulas ----
    broken_patterns = [
        r'(?<![a-zA-Z])[A-Z][a-z]?\d+[A-Z]',  # e.g., C2H6 (should be C₂H₆)
        r'H2O(?!₂)',                              # H2O without subscript
        r'CO2(?!₂)',                              # CO2 without subscript
        r'\^[0-9+-]',                             # Caret notation like ^2+
    ]
    for pattern in broken_patterns:
        if re.search(pattern, answer):
            issues.append(f"BROKEN_FORMULA: Pattern '{pattern}' detected in answer.")
            score -= 0.15

    # ---- CHECK 3: Mode-specific structure ----
    if mode == "summary":
        bullet_count = answer.count("- ")
        if bullet_count < 4:
            issues.append(f"STRUCTURE: Summary has only {bullet_count} bullet points (expected 6-8).")
            score -= 0.2

    elif mode == "explain":
        if len(answer) < 150:
            issues.append("STRUCTURE: Explanation is too brief for Deep Explain mode.")
            score -= 0.2

    elif mode == "exam":
        if "Q" not in answer and "question" not in answer.lower():
            issues.append("STRUCTURE: Exam output doesn't contain question markers.")
            score -= 0.3

    elif mode in ("keypoints", "key"):
        bullet_count = answer.count("- ") + answer.count("• ")
        if bullet_count < 3:
            issues.append(f"STRUCTURE: Key points has only {bullet_count} points (expected 5+).")
            score -= 0.2

    # ---- CHECK 4: Hallucination markers ----
    hallucination_phrases = [
        "as an ai",
        "i don't have access",
        "i cannot",
        "i'm not sure",
        "based on my training",
        "as a language model",
    ]
    answer_lower = answer.lower()
    for phrase in hallucination_phrases:
        if phrase in answer_lower:
            issues.append(f"HALLUCINATION: Contains '{phrase}'.")
            score -= 0.3

    # Clamp score
    score = max(0.0, min(1.0, score))
    passed = score >= 0.5 and "EMPTY_OR_SHORT" not in str(issues)

    result = {
        "passed": passed,
        "score": round(score, 2),
        "issues": issues,
        "suggestion": "",
    }

    if not passed:
        result["suggestion"] = (
            "The answer did not meet quality standards. "
            "Consider: (1) Providing more context, (2) Adjusting the prompt, "
            f"(3) Fixing these issues: {'; '.join(issues)}"
        )
        logger.warning(f"Answer quality check FAILED: score={score}, issues={issues}")
    else:
        logger.info(f"Answer quality check PASSED: score={score}")

    return result