import operator
from time import perf_counter
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .agents import execute_mission
from .contracts import AgentReport, Mission, TurnResponse, TurnTrace
from .planning import DeepSeekPlanner


class WorkflowState(TypedDict, total=False):
    message: str
    missions: list[Mission]
    mission: Mission
    reports: Annotated[list[AgentReport], operator.add]
    answer: str
    planning_source: str
    planner_error: str | None


PLANNER = DeepSeekPlanner()


async def plan(state: WorkflowState) -> dict[str, object]:
    decision = await PLANNER.create_plan(state["message"].strip())
    return {
        "missions": decision.missions,
        "planning_source": decision.source,
        "planner_error": decision.error,
    }


def fan_out(state: WorkflowState) -> list[Send]:
    return [Send("run_agent", {"mission": mission}) for mission in state["missions"]]


async def run_agent(state: WorkflowState) -> dict[str, list[AgentReport]]:
    return {"reports": [await execute_mission(state["mission"])]}


def compose(state: WorkflowState) -> dict[str, str]:
    reports = state.get("reports", [])
    successful = [report.message for report in reports if report.status == "completed"]
    if not successful:
        return {"answer": "本轮任务暂时无法完成，请稍后重试。"}
    return {"answer": "\n\n".join(successful)}


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


async def handle_turn(message: str) -> TurnResponse:
    started = perf_counter()
    result = await GRAPH.ainvoke({"message": message, "reports": []})
    reports = result.get("reports", [])
    return TurnResponse(
        answer=result["answer"],
        reports=reports,
        elapsed_ms=round((perf_counter() - started) * 1000, 2),
        trace=TurnTrace(
            planning_source=result.get("planning_source", "local"),
            selected_agents=[report.role for report in reports],
            planner_error=result.get("planner_error"),
        ),
    )
