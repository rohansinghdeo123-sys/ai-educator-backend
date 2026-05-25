# ai-educator-backend

## Study Page model routing

The Study Page backend uses a small multi-agent model stack on Groq so routine work stays fast and affordable while harder tutor answers get a stronger model.

Default routing:

- `GROQ_FAST_MODEL=meta-llama/llama-4-scout-17b-16e-instruct` for intent profiling, lightweight planning, revision, and quick feedback.
- `GROQ_TUTOR_MODEL=openai/gpt-oss-120b` for main tutor answers and exam-quality generation.
- `GROQ_REVIEW_MODEL=llama-3.3-70b-versatile` for answer review and polish inside the autonomous Study Page coach.

Optional overrides:

- `GROQ_REVISION_MODEL`
- `GROQ_EXAM_MODEL`
- `GROQ_PLANNER_MODEL`
- `GROQ_FEEDBACK_MODEL`
- `GROQ_CASUAL_MODEL`

`GROQ_MODEL` is no longer used as the Study Page default. Use the specific variables above when changing model routing.
