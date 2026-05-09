# Logic/agents/planner_agent.py

import os
import json
import re
from groq import Groq
from Logic.analytics_engine import get_user_analytics

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def planner_agent(request, db):
    user_id = request.session_id
    analytics = get_user_analytics(db, user_id)

    weak = analytics.get("weak_topics", [])
    medium = analytics.get("medium_topics", [])
    strong = analytics.get("strong_topics", [])

    prompt = f"""
You are an elite AI study planner.

Student performance:
- Weak topics: {weak}
- Medium topics: {medium}
- Strong topics: {strong}

Create a structured study plan.

Rules:
- 3 to 5 steps
- Each step must include: step number, action (revise / practice / test), and topic.
- Focus heavily on weak topics first.

Return ONLY valid JSON. No markdown, no explanations.
{{
  "steps": [
    {{ "step": 1, "action": "revise", "topic": "alkanes" }}
  ]
}}
"""

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=300
        )
        raw_output = response.choices[0].message.content.strip()
        
        # 🔥 ROBUST JSON EXTRACTION (Strips markdown blocks if present)
        json_match = re.search(r'\{.*\}', raw_output, re.DOTALL)
        if json_match:
            plan_json = json.loads(json_match.group(0))
        else:
            plan_json = json.loads(raw_output)
            
    except Exception as e:
        # Fallback if model messes up or API fails
        default_topic = weak[0] if weak else "basics"
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