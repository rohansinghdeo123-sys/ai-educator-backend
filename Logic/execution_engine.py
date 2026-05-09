# Logic/execution_engine.py

import logging
from sqlalchemy.orm import Session
from Logic.agents.tutor_agent import tutor_agent
from Logic.agents.revision_agent import revision_agent
from Logic.agents.exam_agent import exam_agent
from Logic.memory_store import save_plan, get_plan, increment_step, reset_plan

logger = logging.getLogger("ai_educator.execution")

# =====================================================
# 🔥 EXECUTION ENGINE (STATEFUL & ROBUST)
# =====================================================
def execute_plan(plan: dict, request, db: Session):
    user_id = request.session_id

    # STEP 1: CHECK IF PLAN EXISTS
    stored = get_plan(db, user_id)

    # FIRST TIME → SAVE PLAN
    if not stored:
        save_plan(db, user_id, plan)
        stored = get_plan(db, user_id)

    if not stored:
        return {"error": "Failed to initialize study plan."}

    steps = stored.get("plan", {}).get("steps", [])
    current_index = stored.get("current_step", 0)

    # STEP 2: CHECK COMPLETION
    if current_index >= len(steps):
        reset_plan(db, user_id)
        return {
            "message": "✅ Plan completed!",
            "next": "Ask for a new plan"
        }

    current_step = steps[current_index]
    action = current_step.get("action", "revise")
    topic = current_step.get("topic", "basics")

    # STEP 3: MODIFY REQUEST
    request.question = f"{action} {topic}"

    # STEP 4: EXECUTE STEP (WITH ERROR HANDLING)
    try:
        if action == "revise":
            result = revision_agent(request)
        elif action in ["practice", "test"]:
            result = exam_agent(request)
        else:
            result = tutor_agent(request)
    except Exception as e:
        logger.error(f"Agent Execution Failed: {e}")
        return {
            "error": "The AI agent encountered an error. Please try this step again.",
            "current_step": current_step
        }

    # STEP 5: MOVE TO NEXT STEP
    increment_step(db, user_id)

    # FINAL RESPONSE
    return {
        "current_step": current_step,
        "step_number": current_index + 1,
        "total_steps": len(steps),
        "result": result,
        "next_step_available": current_index + 1 < len(steps)
    }