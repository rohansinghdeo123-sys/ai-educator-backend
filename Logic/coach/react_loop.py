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

    steps = ["Understand the student's exact need and choose the teaching strategy"]
    if query.needs_memory:
        steps.append("Resolve follow-up meaning from the recent lesson thread and compact learning memory")
    if query.retrieval_policy == "required":
        steps.append("Retrieve and verify the requested platform study material before answering")
    elif query.retrieval_policy == "optional":
        steps.append("Retrieve platform study material as a useful enrichment source")
    else:
        steps.append("Reason from the question, lesson context, and reliable subject knowledge")
    steps.append("Draft the best student-facing answer shape")
    if query.needs_quality_review:
        steps.append("Score grounding, clarity, completeness, and formatting")
    steps.append("Deliver one clear next step")

    return CoachPlan(
        route="reasoning_tutor",
        intent=query.intent,
        answer_format=query.answer_format,
        tools=list(query.requested_tools),
        steps=steps,
    )
