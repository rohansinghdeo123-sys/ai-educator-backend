# Logic/agents/planner_agent.py

import os
import json
import re
import logging
from groq import Groq
from Logic.analytics_engine import get_user_analytics
from Logic.knowledge_graph import knowledge_graph
from Logic.agent_event_bus import event_bus

logger = logging.getLogger(__name__)

_groq_client = None


def _get_groq_client() -> Groq:
    """Lazy client so importing this module never requires GROQ_API_KEY."""
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    return _groq_client
PLANNER_MODEL = os.getenv(
    "GROQ_PLANNER_MODEL",
    os.getenv("GROQ_FAST_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
)


def planner_agent(request, db):
    user_id = request.session_id
    analytics = get_user_analytics(db, user_id)

    weak_topics = analytics.get("weak_topics", [])
    medium_topics = analytics.get("medium_topics", [])
    strong_topics = analytics.get("strong_topics", [])

    # ── Enrich weak topics with Knowledge Graph data ──────────────────────
    concept_hints = []
    for topic_name in weak_topics:
        # Search the knowledge graph for concepts related to this topic
        concepts = knowledge_graph.search_by_keyword(topic_name, limit=3)
        for c in concepts:
            hint = f"- {c['title']} (importance: {c.get('importance_level', 'medium')}, weightage: {c.get('typical_exam_weightage', 'medium')})"
            prereqs = c.get('prerequisites')
            if prereqs and isinstance(prereqs, (list, tuple)):
                hint += f" | Prerequisites: {', '.join(prereqs)}"
            mistakes_raw = c.get('common_mistakes')
            if mistakes_raw and isinstance(mistakes_raw, list):
                mistakes = [m.get('mistake', '') for m in mistakes_raw if isinstance(m, dict) and m.get('mistake')]
                if mistakes:
                    hint += f" | Watch for: {', '.join(mistakes[:2])}"
            concept_hints.append(hint)

    graph_context = ""
    if concept_hints:
        graph_context = "Curriculum concepts related to weak topics:\n" + "\n".join(concept_hints)

    # ── Build a smarter prompt with graph enrichment ──────────────────────
    prompt = f"""
You are an elite AI study planner for school students. Your goal is to create a friendly, personalised study plan that helps the student improve quickly.

Student performance:
- Weak topics: {weak_topics}
- Medium topics: {medium_topics}
- Strong topics: {strong_topics}

{graph_context if graph_context else ""}

Create a structured study plan.

Rules:
- 3 to 5 steps.
- Each step must include: step number, action (revise / practice / test), and topic.
- Focus heavily on weak topics first.
- If prerequisites are listed for a topic, suggest revising those first.
- Prioritise topics marked as 'core' importance or 'high' exam weightage.
- Use simple, encouraging language in the plan.

Return ONLY valid JSON. No markdown, no explanations.
{{
  "steps": [
    {{ "step": 1, "action": "revise", "topic": "alkanes" }}
  ]
}}
"""

    try:
        response = _get_groq_client().chat.completions.create(
            model=PLANNER_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=300
        )
        raw_output = response.choices[0].message.content.strip()

        # Extract first complete JSON object without greedy over-matching
        try:
            decoder = json.JSONDecoder()
            start = raw_output.index('{')
            plan_json, _ = decoder.raw_decode(raw_output, start)
        except (ValueError, json.JSONDecodeError):
            plan_json = json.loads(raw_output)

        if not isinstance(plan_json, dict) or not isinstance(plan_json.get("steps"), list):
            raise ValueError(f"LLM returned unexpected structure: {plan_json}")

    except Exception as e:
        logger.error("planner_agent failed for user %s: %s", user_id, e, exc_info=True)
        default_topic = weak_topics[0] if weak_topics else "basics"
        plan_json = {
            "steps": [
                {"step": 1, "action": "revise", "topic": default_topic},
                {"step": 2, "action": "practice", "topic": default_topic},
                {"step": 3, "action": "test", "topic": default_topic}
            ]
        }

    return {
        "plan": plan_json,
        "analytics": analytics,
        "next_action": analytics.get("next_action", "")
    }
