"""Exam intelligence engine: paper parsing, pattern analysis, probable-question
generation, and teacher-style written-answer evaluation.

This package is deliberately decoupled from FastAPI and SQLAlchemy. Services in
``services/`` own persistence and ownership; this package owns parsing and the
LLM-backed agents, so it can be unit-tested without a database or network.
"""
