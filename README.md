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

## Unified Study Lab coach architecture

The Study Lab coach keeps the existing `/coach/chat` and `/coach/chat/stream`
API routes, but its internal services are split into focused modules under
`Logic/coach/`:

- `settings.py` centralizes environment-backed coach configuration.
- `models.py` defines typed internal query, retrieval, plan, and quality models.
- `llm_router.py` routes profiler, tutor, and reviewer calls without coupling
  agent logic to a specific model.
- `query_understanding.py` identifies conversation turns, follow-ups, doubts,
  revision requests, practice requests, exam intent, and planning intent.
- `retriever.py` searches only ingested platform study sources. Open Coach mode
  can auto-detect the relevant source; selected Revision and Exam modes keep
  their explicit topic scope.
- `context_manager.py` creates a compact learning context snapshot.
- `memory_store.py` compresses recent durable memories and lesson turns.
- `tool_registry.py` provides plug-and-play coach tools.
- `react_loop.py` creates the minimal action plan needed for each request.
- `quality_scorer.py` scores relevance, grounding, completeness, clarity,
  student friendliness, formatting, and hallucination risk.
- `observability.py` records structured coach metrics through the event bus.

### Database-only answer policy

The coach must answer curriculum questions only from ingested platform data.
If open Coach mode cannot route a question to a stored source, or selected mode
cannot find the requested section, it returns the configured not-found message.
It never falls back to general model knowledge.
