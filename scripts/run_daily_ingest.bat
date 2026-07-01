@echo off
REM ===================================================================
REM AgentifyAI - daily NCERT content ingestion (free-tier, resumable).
REM
REM Re-runs the automation once. The skip/reuse logic means it picks up
REM exactly where it left off: chapters already published are skipped,
REM already-ingested chapters only re-run concept-generation (no re-embed),
REM and it naturally stops when the day's free LLM/embedding quota is spent
REM (~2 chapters of concepts per day on free Groq).
REM
REM Edit the scope on the python line below to expand past Class 11 Chemistry
REM (e.g. --classes 11,12 --subjects Physics,Chemistry,Maths).
REM Output is appended to backend\logs\daily_ingest.log for review.
REM ===================================================================

cd /d "%~dp0.."
if not exist logs mkdir logs

echo. >> logs\daily_ingest.log
echo ===== %DATE% %TIME% : starting daily ingest ===== >> logs\daily_ingest.log
python scripts\automate_content.py --classes 11 --subjects Chemistry >> logs\daily_ingest.log 2>&1
echo ===== %DATE% %TIME% : finished daily ingest ===== >> logs\daily_ingest.log
