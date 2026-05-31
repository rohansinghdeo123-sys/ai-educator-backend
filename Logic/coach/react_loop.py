"""Small deterministic ReAct-style planner for the unified Study Lab coach."""

from .models import CoachPlan, QueryUnderstanding


def build_coach_plan(query: QueryUnderstanding) -> CoachPlan:
    if query.is_conversational:
        return CoachPlan(
            route="conversation",
            intent=query.intent,
            answer_format=query.answer_format,
            tools=[],
            steps=["Reply naturally", "Do not reopen the previous lesson"],
        )

    steps = ["Understand the student's exact need"]
    if query.needs_memory:
        steps.append("Use the recent lesson thread and compact learning memory")
    if query.needs_retrieval:
        steps.append("Retrieve the selected platform study data")
    steps.append("Draft the best student-facing answer shape")
    if query.needs_quality_review:
        steps.append("Score grounding, clarity, completeness, and formatting")
    steps.append("Deliver one clear next step")

    return CoachPlan(
        route="grounded_tutor",
        intent=query.intent,
        answer_format=query.answer_format,
        tools=list(query.requested_tools),
        steps=steps,
    )
