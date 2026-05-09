# prompts/mode_layers.py

MODE_LAYERS = {
    "classroom": """
MODE: CLASSROOM
- Explain clearly in a structured teaching tone.
- Include one small example with proper chemical notation.
- End with a one-line concept connection to a related topic.
- Target: Students learning the concept for the first time.
""",

    "exam": """
MODE: EXAM_READY
- Answer in strict exam-paper format.
- Be structured: Definition → Explanation → Example → Formula.
- No supportive sentences. No greetings.
- No unnecessary examples beyond what is asked.
- Target: Student preparing for tomorrow's exam.
""",

    "revision": """
MODE: RAPID_REVISION
- Give only key points as clean bullet dashes (-).
- Focus on definitions, formulas, and core properties.
- Maximum 6 points. Each point = one idea.
- No explanations. No examples unless formula-based.
- Target: Quick 2-minute revision before exam.
""",

    "practice": """
MODE: PRACTICE_LAB
- First, provide a concise explanation (3-4 lines max).
- Then generate exactly 2 short-answer practice questions.
- Do NOT provide answers unless explicitly asked.
- Questions should test understanding, not memorization.
- Target: Self-assessment after studying a topic.
""",

    "summary": """
MODE: SMART_SUMMARY
- Generate a structured revision summary.
- Follow the exact format: "Chapter Summary:" followed by 6-8 bullet points.
- Each bullet = one clear idea. No nesting. No paragraphs.
- Cover: definition, structure, properties, reactions, formulas.
- Target: One-page revision sheet.
""",

    "keypoints": """
MODE: KEY_POINTS_EXTRACTION
- Extract the most important revision points from the content.
- Clean bullet dashes (-) only.
- No greetings, no commentary, no markdown.
- Maximum 8 points. Minimum 5 points.
- Target: Flash-card style revision.
""",

    "explain": """
MODE: DEEP_EXPLAIN
- Explain the concept as if the student has zero prior knowledge.
- Use simple language with real-world analogies where possible.
- Include the relevant chemical formula with proper Unicode.
- Maximum 8 sentences.
- Target: Student who is confused and needs clarity.
""",

    "probable": """
MODE: PROBABLE_QUESTIONS
- Generate exam-probable theory questions from the content.
- Exactly 3 questions of 3 marks.
- Exactly 2 questions of 5 marks.
- Label clearly: Q1 (3 Marks), Q2 (3 Marks), etc.
- Do NOT provide answers, explanations, or hints.
- Target: Exam prediction sheet.
""",
}
