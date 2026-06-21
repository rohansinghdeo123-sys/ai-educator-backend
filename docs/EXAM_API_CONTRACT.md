# AgentifyAI — Exam Mode Backend API Contract (Frontend Reference for Codex)

**Audience:** the frontend agent (Codex) building the Exam page.
**Backend commit:** `a614177` on `main`. **Status:** frozen, 163 tests passing.
**Scope:** this document is the canonical contract for the Exam page. It covers the
**existing MCQ flow (unchanged)** and the **two new feature areas** the backend now
exposes. Build to this; when in doubt, the live `/openapi.json` (Swagger `/docs`) is
the source of truth.

> The current Exam page ("Exam command center") already implements the MCQ Test
> tab. "Probable Questions" and "Review" are placeholders. The new backend lets you
> light those up **and** add two new surfaces: **Paper Upload + Pattern Intelligence**
> and **Written Answer Practice**. Nothing below changes the existing MCQ endpoints.

---

## 0. Golden rules (the usual mismatch points — read first)

1. **Auth:** every request sends `Authorization: Bearer <firebaseIdToken>`. Missing/expired → `401`; auth backend down → `503`.
2. **Paper upload is `multipart/form-data`** — file field name is `file`; metadata are sibling form fields (NOT JSON, NOT base64).
3. **Paper analysis is synchronous** — the upload response already contains the analysis. No polling/websocket.
4. **Field names are fixed:** `class_level` (string, e.g. `"Class 11"` — never `grade`/`class_id`), `subject`, `chapter_name`, `chapter_id` (int|null).
5. **`marks` / `total_marks` / `marks_awarded` can be `null`** when undetectable — render as "—", never `0` implicitly.
6. **Probable questions always include a `disclaimer` string you MUST display.** Never present them as "guaranteed"/"will come".
7. **Written practice hides the marking scheme:** the question response has **no `expected_points`**; they appear only inside feedback after submit.
8. **Cross-user resources return `404`** (not 403). Treat as "not found".
9. **All datetimes are ISO‑8601 strings.**
10. **Two different "probable questions" exist now** — see §3 vs §5. Don't confuse them.

---

## 1. Conventions

| Item | Value |
|---|---|
| Base URL | same backend host as MCQ/coach (e.g. `https://api.agentifyai.in`) |
| Auth header | `Authorization: Bearer <firebaseIdToken>` on every `/exam/*` call |
| Content type | `application/json` everywhere **except** `POST /exam/papers/upload` (`multipart/form-data`) |
| Datetimes | ISO‑8601 strings (`"2026-06-21T10:21:28"`) |
| Pagination | `?limit=&offset=` on list endpoints (defaults: limit 50, offset 0) |

### Error codes (handle all of these)
| Code | Meaning | Suggested UI |
|---|---|---|
| 400 | bad request (unsupported file type, empty file, no analyzed papers, bad submit combo) | show `detail` |
| 401 / 503 | not authenticated / auth backend unavailable | route to login |
| 404 | not found OR not owned by caller OR not yet evaluated | "not found" state |
| 413 | file too large (> 8 MB) | show size limit |
| 422 | validation error (e.g. empty answer) | inline field error |
| 429 | daily quota reached | show `detail`, respect `Retry-After` header |

Error body shape (FastAPI): `{ "detail": "human readable message", "request_id": "..." }`.

### Quotas (per user per day)
- Paper actions (`upload`, `reanalyze`, `pattern/analyze`, `probable-questions/generate`): **30/day**.
- Written practice (`question`, `submit`): **120/day**.

---

## 2. EXISTING MCQ flow (UNCHANGED — do not break)

These power the current "MCQ Test" tab. They are **not** part of the new feature set and were not modified.

- `POST /generate-mcqs` — body `{ topic, section_id?, session_id, difficulty?, count?, strict_grounding?, ... }` → `{ topic, section_id, difficulty, questions:[{id, question, options:[4], correct, explanation, source}], ... }`
- `POST /generate-probable-questions` — body `{ topic, section_id?, session_id, ... }` → `{ topic, questions:[{id, marks, question, source}], text, ... }`
  - **NOTE:** this is the *legacy, topic-grounded* probable-questions generator (from the selected chapter's study material). It is **different** from the new uploaded-paper probable-questions in §5. The "Probable Questions (SOON)" tab on the current page can use **either** — decide with the user; they answer different needs (syllabus topic vs. observed paper pattern).
- `session_id` must be owned by the user (prefix `exam-<uid>-...`) or you get `403`.

---

## 3. NEW · Paper Upload + Pattern Intelligence

### 3.1 Upload a paper — `POST /exam/papers/upload` (multipart/form-data)
Form fields:
| field | type | notes |
|---|---|---|
| `file` | file | **required**. `.pdf .txt .png .jpg .jpeg`, ≤ 8 MB |
| `class_level` | string | e.g. `"Class 11"` |
| `subject` | string | e.g. `"Chemistry"` |
| `chapter_name` | string | e.g. `"Hydrocarbons"` |
| `chapter_id` | int? | optional |
| `exam_type` | string | one of the exam-type enum (§6) or free text |
| `paper_title` | string | optional |

Response `200`:
```jsonc
{
  "paper": {
    "id": 12, "class_level": "Class 11", "subject": "Chemistry",
    "chapter_id": null, "chapter_name": "Hydrocarbons",
    "exam_type": "unit_test", "paper_title": "Unit Test 1",
    "file_name": "ut1.pdf", "file_type": "pdf", "file_size": 184220,
    "upload_status": "stored", "parse_status": "analyzed",
    "uploaded_at": "...", "parsed_at": "...",
    "extraction_confidence": 0.82, "extracted_question_count": 12,
    "warnings": [], "created_at": "...", "updated_at": "..."
    // storage_path is NEVER returned
  },
  "analysis": { /* §3.2 */ },
  "questions_extracted": 12,
  "warnings": ["..."],
  "message": "Paper uploaded and analyzed."
}
```
- `parse_status` ∈ `analyzed | analyzed_empty | needs_ocr | failed | pending`.
  - `needs_ocr` → show "Looks like a scanned image. OCR isn't enabled yet — upload a text-based PDF." (OCR is a stub; see §9.)
  - `analyzed_empty` → text was read but no structured questions found — show `warnings`.
- Errors: `400` (bad type / empty), `413` (too large), `429` (quota).

### 3.2 The `analysis` object (also returned by the analysis endpoint)
```jsonc
{
  "total_questions": 12,
  "total_marks": 40,            // nullable when not detectable from the paper
  "section_breakdown": { "A": { "questions": 5, "marks": 5 } },
  "marks_distribution": { "1": 5, "2": 3, "3": 2, "5": 2 },
  "question_type_distribution": { "short_answer": 6, "long_answer": 2, "numerical": 4 },
  "difficulty_distribution": { "easy": 4, "medium": 6, "hard": 2 },
  "topic_frequency": { "alkanes": 4, "alkenes": 3 },
  "repeated_concepts": ["alkanes", "isomerism"],
  "high_frequency_concepts": ["alkanes", "alkenes", "nomenclature"],
  "chapter_weightage": { "Hydrocarbons": "60%" },
  "short_vs_long": { "short_answer": 8, "long_answer": 4 },
  "pattern_style": "school_style",   // board_style | school_style | mixed
  "pattern_summary": "Mostly short-answer questions, weighted to alkanes...",
  "warnings": []
}
```

### 3.3 Other paper endpoints
| Method & path | Body | Returns |
|---|---|---|
| `GET /exam/papers?subject=&limit=&offset=` | — | `{ total, papers: [Paper] }` |
| `GET /exam/papers/{id}` | — | `{ paper: Paper, analysis: {} }` · `404` |
| `GET /exam/papers/{id}/questions` | — | `{ paper_id, count, questions: [ExtractedQuestion] }` |
| `GET /exam/papers/{id}/analysis` | — | `{ paper_id, parse_status, extraction_confidence, analysis:{}, warnings:[] }` |
| `POST /exam/papers/{id}/reanalyze` | `{ class_level?, subject?, chapter_name?, exam_type? }` | same envelope as upload · `429` |
| `DELETE /exam/papers/{id}` | — | `{ status:"deleted", id }` |

`ExtractedQuestion`: `{ id, paper_id, question_number, section_name, question_text, marks(nullable), question_type, intent, difficulty, topic, concept_tags:[], expected_answer_style, confidence_score }`.

### 3.4 Aggregate pattern across papers
| Method & path | Body | Returns |
|---|---|---|
| `POST /exam/pattern/analyze` | `{ paper_ids?:[int], class_level?, subject?, chapter_name? }` | `PatternAnalysis` · `400` if no analyzed papers |
| `GET /exam/pattern/summary` | — | `{ papers_total, papers_analyzed, subjects:[], latest_analysis: PatternAnalysis|null, analyses:[PatternAnalysis] }` |
| `GET /exam/pattern/by-subject` | — | `{ grouped_by:"subject", groups:[PatternGroup] }` |
| `GET /exam/pattern/by-chapter?subject=` | — | `{ grouped_by:"chapter", groups:[PatternGroup] }` |

`paper_ids` null = use ALL of the user's analyzed papers (optionally narrowed by `subject`/`chapter_name`).
`PatternAnalysis`: `{ id, class_level, subject, chapter_id, chapter_name, source_paper_ids:[], total_questions, total_marks(nullable), marks_distribution:{}, question_type_distribution:{}, chapter_weightage:{}, topic_frequency:{}, repeated_concepts:[], difficulty_distribution:{}, pattern_summary, confidence_score, created_at, updated_at }`.
`PatternGroup`: `{ key, label, paper_count, total_questions, top_concepts:[], marks_distribution:{}, question_type_distribution:{} }`.

---

## 4. NEW · Probable questions from uploaded papers

> This is the **new, paper-pattern-based** generator (distinct from the legacy
> topic-based `/generate-probable-questions` in §2).

| Method & path | Body | Returns |
|---|---|---|
| `POST /exam/probable-questions/generate` | see below | `ProbableQuestionSet` · `429` |
| `GET /exam/probable-questions?limit=&offset=` | — | `{ total, sets:[ProbableQuestionSet] }` |
| `GET /exam/probable-questions/{set_id}` | — | `ProbableQuestionSet` · `404` |

Generate body:
```jsonc
{
  "analysis_id": 7,            // use a stored pattern analysis, OR omit and pass paper_ids/subject
  "paper_ids": null,
  "class_level": "Class 11", "subject": "Chemistry", "chapter_name": "Hydrocarbons",
  "generation_mode": "mixed", // mixed | chapter_wise | marks_wise | section_wise
  "count": 8,                 // 1..20
  "use_syllabus_grounding": true
}
```
`ProbableQuestionSet`:
```jsonc
{
  "id": 3, "class_level": "Class 11", "subject": "Chemistry",
  "chapter_id": null, "chapter_name": "Hydrocarbons",
  "source_analysis_ids": [7], "generation_mode": "mixed",
  "probable_questions": [
    { "id":"P1", "question":"Explain the mechanism of...", "marks":3,
      "question_type":"long_answer", "intent":"explanation", "topic":"alkanes",
      "priority":"high", "based_on":"repeated concept across papers", "source":"uploaded_papers_pattern" }
  ],
  "priority_topics": [ { "topic":"alkanes", "reason":"asked in every paper", "weight":"high" } ],
  "strategy_summary": "Revise alkanes first...",
  "disclaimer": "These are the most probable questions based on your uploaded papers... not a guarantee...",
  "confidence_score": 0.62, "created_at": "..."
}
```
**Always render `disclaimer`.** `priority` and `weight` ∈ `high|medium|low`.

---

## 5. NEW · Written Answer Practice (3-step flow)

A separate workspace from MCQ. Flow: **start session → get question → submit answer → see feedback**.

### Step 1 — `POST /exam/written-practice/start`
Body: `{ class_level?, subject?, chapter_name?, chapter_id?, topic?, marks_focus? }`
Returns `Session`: `{ id, class_level, subject, chapter_id, chapter_name, topic, marks_focus, session_status, started_at, completed_at, attempt_count }`.

### Step 2 — `POST /exam/written-practice/question`  (`429` quota)
Body: `{ session_id (required), topic?, marks_focus?, question_type?, use_syllabus_grounding? }` · `404` if session not owned.
Returns `Question` — **no marking scheme exposed**:
```jsonc
{ "attempt_id": 51, "session_id": 9, "question_text": "Explain the mole concept with an example.",
  "question_type": "long_answer", "marks_total": 5, "topic": "Mole concept",
  "command_word": "explain", "evaluation_status": "awaiting_answer" }
```

### Step 3 — `POST /exam/written-practice/submit`  (`429` quota)
Two ways to call it:
- **Grade a generated question:** `{ attempt_id, answer }`
- **Ad-hoc (self-chosen question):** `{ session_id, question_text, marks_total, answer, question_type?, topic?, expected_points? }`

`answer`: required, 1–20000 chars (empty → `422`). Wrong/foreign id → `404`; bad combination → `400`.
Returns:
```jsonc
{
  "attempt_id": 51,
  "feedback": {
    "attempt_id": 51, "question_text": "...", "question_type": "long_answer",
    "student_answer": "...",
    "marks_awarded": 3, "marks_total": 5, "score_percentage": 60.0,
    "covered_points": ["..."], "missing_points": ["..."], "incorrect_points": [],
    "weak_explanation": ["units"],
    "presentation_feedback": "...", "teacher_feedback": "...",
    "model_answer": "...", "improve_to_full_marks": "...",
    "rubric_scores": {
      "concept_accuracy":0.6, "key_points_covered":0.5, "completeness":0.4,
      "formula_keyword_usage":0.7, "step_logic":0.6, "explanation_clarity":0.5, "exam_presentation":0.3
    },
    "next_question_suggestion": "Define molarity.", "created_at": "..."
  },
  "weaknesses_updated": 2
}
```
Each `rubric_scores` value is a fraction `0..1` (render as % or bars). `marks_awarded` is always clamped to `0..marks_total`.

### Step 4 — review/history
| Method & path | Returns |
|---|---|
| `GET /exam/written-practice/history?subject=&limit=&offset=` | `{ total, attempts:[AttemptSummary] }` |
| `GET /exam/written-practice/sessions?subject=&limit=&offset=` | `{ total, sessions:[Session] }` (each `Session` includes `attempt_count`) |
| `GET /exam/written-practice/sessions/{id}` | `{ session: Session, attempts:[AttemptSummary] }` · `404` |
| `POST /exam/written-practice/sessions/{id}/complete` | `Session` (sets `session_status:"completed"`, `completed_at`); idempotent · `404` |
| `GET /exam/written-practice/attempts/{id}/feedback` | `Feedback` · `404` if not owned or not yet evaluated |

`AttemptSummary`: `{ id, session_id, question_text, question_type, marks_total, marks_awarded(nullable), score_percentage(nullable), evaluation_status, topic, subject, submitted_at, created_at }`.
`evaluation_status` ∈ `awaiting_answer → evaluating → evaluated`.

---

## 6. NEW · Student Weakness Report

| Method & path | Returns |
|---|---|
| `GET /exam/student-weakness-report?subject=&limit=&offset=` | `{ total, weaknesses:[Weakness] }` |
| `GET /exam/student-weakness-report/by-topic` | `{ total_topics, topics:[{topic, subject, total_frequency, weakness_types:[], latest_suggestion}] }` |
| `POST /exam/student-weakness-report/recalculate` | `{ attempts_processed, signals_recorded }` |

`Weakness`: `{ id, class_level, subject, chapter_id, chapter_name, topic, weakness_type, weakness_summary, evidence:[], frequency_count, last_seen_at, improvement_suggestion, created_at, updated_at }`.
Weaknesses are produced automatically after each written submit; `recalculate` rebuilds them from full history.

---

## 7. Enums (use for chips/filters/copy)

- `parse_status`: `pending · analyzed · analyzed_empty · needs_ocr · failed`
- `exam_type`: `class_test · unit_test · school_exam · pre_board · board_exam · chapter_wise · subject_wise · other · unknown`
- `question_type`: `very_short_answer · short_answer · long_answer · numerical · mcq · case_based · assertion_reason · diagram · other`
- `intent`: `definition · explanation · reasoning · numerical · derivation · diagram · difference · short_note · case_based · assertion_reason · application · fill_in_blank · true_false · mcq · other`
- `difficulty`: `easy · medium · hard`
- `pattern_style`: `board_style · school_style · mixed`
- `generation_mode`: `mixed · chapter_wise · marks_wise · section_wise`
- `priority` / `weight`: `high · medium · low`
- `evaluation_status`: `awaiting_answer · evaluating · evaluated`
- `weakness_type`: `concept_gap · missing_key_points · incomplete · presentation · formula · step_logic · clarity`

---

## 8. TypeScript types (drop-in)

```ts
export type ParseStatus = "pending" | "analyzed" | "analyzed_empty" | "needs_ocr" | "failed";
export type Difficulty = "easy" | "medium" | "hard";
export type Priority = "high" | "medium" | "low";
export type EvaluationStatus = "awaiting_answer" | "evaluating" | "evaluated";
export type GenerationMode = "mixed" | "chapter_wise" | "marks_wise" | "section_wise";

export interface PaperOut {
  id: number; class_level: string; subject: string;
  chapter_id: number | null; chapter_name: string;
  exam_type: string; paper_title: string;
  file_name: string; file_type: string; file_size: number;
  upload_status: string; parse_status: ParseStatus;
  uploaded_at: string | null; parsed_at: string | null;
  extraction_confidence: number; extracted_question_count: number;
  warnings: string[]; created_at: string | null; updated_at: string | null;
}

export interface PaperAnalysis {
  total_questions: number; total_marks: number | null;
  section_breakdown: Record<string, { questions: number; marks: number }>;
  marks_distribution: Record<string, number>;
  question_type_distribution: Record<string, number>;
  difficulty_distribution: Record<string, number>;
  topic_frequency: Record<string, number>;
  repeated_concepts: string[]; high_frequency_concepts: string[];
  chapter_weightage: Record<string, string | number>;
  short_vs_long: Record<string, number>;
  pattern_style: string; pattern_summary: string; warnings: string[];
}

export interface ExtractedQuestion {
  id: number; paper_id: number; question_number: string; section_name: string;
  question_text: string; marks: number | null; question_type: string; intent: string;
  difficulty: string; topic: string; concept_tags: string[];
  expected_answer_style: string; confidence_score: number;
}

export interface PaperUploadResponse {
  paper: PaperOut; analysis: PaperAnalysis;
  questions_extracted: number; warnings: string[]; message: string;
}

export interface ProbableQuestion {
  id: string; question: string; marks: number | null; question_type: string;
  intent: string; topic: string; priority: Priority; based_on: string; source: string;
}
export interface ProbableQuestionSet {
  id: number; class_level: string; subject: string;
  chapter_id: number | null; chapter_name: string;
  source_analysis_ids: number[]; generation_mode: string;
  probable_questions: ProbableQuestion[];
  priority_topics: { topic: string; reason: string; weight: Priority }[];
  strategy_summary: string; disclaimer: string; confidence_score: number; created_at: string | null;
}

export interface WrittenQuestion {
  attempt_id: number; session_id: number; question_text: string;
  question_type: string; marks_total: number; topic: string;
  command_word: string; evaluation_status: EvaluationStatus;
}
export interface WrittenFeedback {
  attempt_id: number; question_text: string; question_type: string; student_answer: string;
  marks_awarded: number; marks_total: number; score_percentage: number;
  covered_points: string[]; missing_points: string[]; incorrect_points: string[]; weak_explanation: string[];
  presentation_feedback: string; teacher_feedback: string; model_answer: string; improve_to_full_marks: string;
  rubric_scores: Record<string, number>; next_question_suggestion: string; created_at: string | null;
}
export interface Weakness {
  id: number; class_level: string; subject: string; chapter_id: number | null; chapter_name: string;
  topic: string; weakness_type: string; weakness_summary: string; evidence: string[];
  frequency_count: number; last_seen_at: string | null; improvement_suggestion: string;
  created_at: string | null; updated_at: string | null;
}
```

---

## 9. Example calls

**Upload (multipart):**
```ts
const fd = new FormData();
fd.append("file", file);                 // File from <input type=file>
fd.append("subject", "Chemistry");
fd.append("class_level", "Class 11");
fd.append("chapter_name", "Hydrocarbons");
fd.append("exam_type", "unit_test");
await fetch(`${API}/exam/papers/upload`, {
  method: "POST",
  headers: { Authorization: `Bearer ${idToken}` },   // do NOT set Content-Type; the browser sets the boundary
  body: fd,
});
```

**Written practice (JSON):**
```ts
const { id: sessionId } = await post("/exam/written-practice/start", { subject:"Chemistry", chapter_name:"Hydrocarbons", topic:"Alkanes", marks_focus:"5" });
const q = await post("/exam/written-practice/question", { session_id: sessionId });
const res = await post("/exam/written-practice/submit", { attempt_id: q.attempt_id, answer: userText });
// res.feedback.rubric_scores, res.feedback.missing_points, res.feedback.model_answer, ...
```

---

## 10. Suggested fit into the current Exam page (UI is yours to design)

The current page has tabs **MCQ Test · Probable Questions (SOON) · Review (LOCKED)** with chapter/topic selectors. These new APIs map cleanly onto that shell:

- **MCQ Test** — unchanged (existing `/generate-mcqs`).
- **Probable Questions** — can now ship. Two options:
  - *Syllabus mode* → legacy `/generate-probable-questions` (topic-grounded; needs no upload).
  - *Paper-pattern mode* → new `/exam/probable-questions/generate` (needs uploaded papers). Show the `disclaimer`.
- **New: "Upload Papers"** surface — drag/drop → `POST /exam/papers/upload`; show the returned analysis (marks distribution, repeated concepts, pattern summary). List via `GET /exam/papers`.
- **New: "Pattern Intelligence"** panel — `POST /exam/pattern/analyze` then render `marks_distribution`, `chapter_weightage`, `topic_frequency`, `pattern_summary`; group views via `by-subject` / `by-chapter`.
- **New: "Written Practice"** workspace tab — the 3-step flow in §5, with a teacher-feedback card (marks, rubric bars, missing points, model answer).
- **New: "Weakness Report"** panel (could power the "Review" tab) — `GET /exam/student-weakness-report` + `by-topic`.

Keep passing the **same `class_level` / `subject` / `chapter_name`** the page already tracks, so paper/pattern/written features stay scoped to the chosen chapter.

---

## 11. Known limitations to surface in the UI
- **Image OCR is not enabled** — image uploads return `parse_status:"needs_ocr"`. Show a friendly "upload a text-based PDF" message (the backend interface is ready for OCR later).
- **Uploaded files are best-effort on disk** (may not persist across redeploys on free hosting), but extracted text + analysis are stored durably, so re-analysis still works.
- **Probable questions are guidance, not predictions** — always show the `disclaimer`.

---

_Source of truth: live Swagger at `/docs` and `/openapi.json`. If anything here disagrees with the running server, the server wins — tell the backend owner so this doc gets corrected._
