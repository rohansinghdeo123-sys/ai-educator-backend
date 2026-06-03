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
- `costing.py` estimates model input/output tokens and cost for each coach call.
- `Logic/observability_store.py` persists Ops events plus model/tool/turn traces
  so monitoring survives backend restarts.

### Reasoning-first answer policy

Open Coach mode resolves intent, follow-up context, memory, and teaching
strategy before answering. It uses reliable model reasoning by default and
invokes RAG only when source-grounded study material is useful or explicitly
requested. Retrieval is tracked as `none`, `optional`, or `required`.

Revision, Exam, and Artifact workspaces remain strict material-grounded flows.
When a student explicitly asks Open Coach to answer from notes, textbook data,
or uploaded material, Open Coach also switches to strict grounding and returns
the configured not-found message if the requested source is unavailable.

## Production guardrails

Set these environment variables before deploying:

- `ALLOWED_ORIGINS`: comma-separated frontend origins. Defaults include local
  development plus `https://agentifyai.in` and `https://www.agentifyai.in`.
- `RATE_LIMIT_ENABLED`: defaults to `true`.
- `RATE_LIMIT_PER_MINUTE`: default general limit is `120`.
- `AI_RATE_LIMIT_PER_MINUTE`: default AI endpoint limit is `24`.
- `ADMIN_RATE_LIMIT_PER_MINUTE`: default admin limit is `180`.
- `AI_DAILY_QUOTA_PER_USER`: default daily coach quota is `180`.
- `EXAM_DAILY_QUOTA_PER_USER`: default daily exam-generation quota is `80`.
- `ARTIFACT_DAILY_QUOTA_PER_USER`: default daily artifact quota is `40`.
- `COACH_DEFAULT_INPUT_USD_PER_1M` and `COACH_DEFAULT_OUTPUT_USD_PER_1M`:
  default estimated model prices when a specific model override is not set.
- `COACH_MODEL_PRICES_PER_1M`: optional semicolon-separated model prices, for
  example `model-a=0.20:0.60;model-b=0.10:0.30`.

Every response includes `X-Request-ID` and `X-Response-Time-ms`. Pass your own
`X-Request-ID` from the frontend or gateway when you want to correlate logs.

Ops observability is durable. `/admin/poll`, `/admin/events`, and `/admin/stats`
now include database-backed observability summaries, model-call counts,
tool-call counts, average latency, and estimated cost.

Health probes:

- `GET /health/live`: liveness check for process uptime.
- `GET /health/ready`: readiness check for database and Firebase Admin.
- `GET /health`: public safe status without secret/debug details.

Uploads are bounded and validated by MIME type, data URL type, file signature,
file size, PDF page count, and extracted text size before entering tutor context.

## Database migrations

Alembic is configured under `migrations/`.

Run migrations from the backend root:

```bash
alembic upgrade head
```

The app still contains a small compatibility backfill for session telemetry
columns so existing deployments do not break before the first migration is run.
Once deployments consistently run Alembic, that compatibility block can be
removed.
