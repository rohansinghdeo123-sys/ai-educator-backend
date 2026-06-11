# Logic/agents/planner_agent.py

import os
import json
import re
from groq import Groq
from Logic.analytics_engine import get_user_analytics
from Logic.knowledge_graph import knowledge_graph   # <-- NEW
from Logic.agent_event_bus import event_bus

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
            if c.get('prerequisites'):
                hint += f" | Prerequisites: {', '.join(c['prerequisites'])}"
            if c.get('common_mistakes'):
                mistakes = [m['mistake'] for m in c['common_mistakes']]
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

        # Robust JSON extraction
        json_match = re.search(r'\{.*\}', raw_output, re.DOTALL)
        if json_match:
            plan_json = json.loads(json_match.group(0))
        else:
            plan_json = json.loads(raw_output)

    except Exception as e:
        # Fallback if model fails
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
