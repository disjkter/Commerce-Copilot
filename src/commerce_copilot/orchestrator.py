import asyncio
import operator
from time import perf_counter
from typing import Annotated, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .agents import execute_mission
from .answering import DeepSeekAnswerComposer
from .contracts import (
    AgentReport,
    ConversationTurn,
    GroundingSummary,
    Mission,
    TurnResponse,
    TurnTrace,
)
from .database import get_catalog
from .grounding import validate_reports
from .planning import DeepSeekPlanner


class WorkflowState(TypedDict, total=False):
    message: str
    user_id: str
    history: list[ConversationTurn]
    missions: list[Mission]
    mission: Mission
    reports: Annotated[list[AgentReport], operator.add]
    answer: str
    planning_source: str
    planner_error: str | None
    answer_source: str
    composer_error: str | None
    grounding: GroundingSummary


PLANNER = DeepSeekPlanner()
ANSWER_COMPOSER = DeepSeekAnswerComposer()


async def plan(state: WorkflowState) -> dict[str, object]:
    decision = await PLANNER.create_plan(
        state["message"].strip(),
        state.get("history", []),
    )
    missions = [
        mission.model_copy(
            update={
                "parameters": {
                    **mission.parameters,
                    "user_id": state["user_id"],
                }
            }
        )
        for mission in decision.missions
    ]
    return {
        "missions": missions,
        "planning_source": decision.source,
        "planner_error": decision.error,
    }


def fan_out(state: WorkflowState) -> list[Send]:
    return [Send("run_agent", {"mission": mission}) for mission in state["missions"]]


async def run_agent(state: WorkflowState) -> dict[str, list[AgentReport]]:
    return {"reports": [await execute_mission(state["mission"])]}


async def compose(state: WorkflowState) -> dict[str, object]:
    reports = state.get("reports", [])
    grounding = validate_reports(reports)
    blocked_roles = {issue.role for issue in grounding.issues}
    successful = [
        report.message
        for report in reports
        if report.status == "completed" and report.role not in blocked_roles
    ]
    if not successful:
        fallback_answer = "本轮结果未通过事实校验，暂时无法提供确定性回答。"
    else:
        fallback_answer = "\n\n".join(successful)
    valid_reports = [
        report
        for report in reports
        if report.status == "completed" and report.role not in blocked_roles
    ]
    decision = await ANSWER_COMPOSER.create_answer(
        state.get("message", ""),
        valid_reports,
        fallback_answer,
    )
    return {
        "answer": decision.answer,
        "answer_source": decision.source,
        "composer_error": decision.error,
        "grounding": grounding,
    }


def build_graph():
    builder = StateGraph(WorkflowState)
    builder.add_node("plan", plan)
    builder.add_node("run_agent", run_agent)
    builder.add_node("compose", compose)
    builder.add_edge(START, "plan")
    builder.add_conditional_edges("plan", fan_out, ["run_agent"])
    builder.add_edge("run_agent", "compose")
    builder.add_edge("compose", END)
    return builder.compile()


GRAPH = build_graph()


async def handle_turn(
    message: str,
    conversation_id: str | None = None,
    *,
    user_id: str = "user-demo",
) -> TurnResponse:
    started = perf_counter()
    active_conversation_id = conversation_id or uuid4().hex
    repository = get_catalog()
    owns_conversation = await asyncio.to_thread(
        repository.ensure_conversation_owner,
        active_conversation_id,
        user_id,
    )
    if not owns_conversation:
        raise PermissionError("conversation belongs to another user")
    history = await asyncio.to_thread(
        repository.get_recent_conversation_turns,
        active_conversation_id,
    )
    result = await GRAPH.ainvoke(
        {
            "message": message,
            "user_id": user_id,
            "history": history,
            "reports": [],
        }
    )
    reports = result.get("reports", [])
    response = TurnResponse(
        conversation_id=active_conversation_id,
        answer=result["answer"],
        reports=reports,
        elapsed_ms=round((perf_counter() - started) * 1000, 2),
        trace=TurnTrace(
            planning_source=result.get("planning_source", "local"),
            answer_source=result.get("answer_source", "local"),
            selected_agents=[report.role for report in reports],
            planner_error=result.get("planner_error"),
            composer_error=result.get("composer_error"),
            grounding=result["grounding"],
        ),
    )
    await asyncio.to_thread(
        repository.add_conversation_turn,
        active_conversation_id,
        message,
        response.answer,
        [item for report in reports for item in report.evidence],
    )
    response.elapsed_ms = round((perf_counter() - started) * 1000, 2)
    return response
