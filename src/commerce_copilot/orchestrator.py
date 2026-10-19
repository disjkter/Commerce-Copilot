import operator
from time import perf_counter
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .agents import execute_mission
from .contracts import AgentReport, AgentRole, Mission


class WorkflowState(TypedDict, total=False):
    message: str
    missions: list[Mission]
    mission: Mission
    reports: Annotated[list[AgentReport], operator.add]
    answer: str


PRODUCT_TERMS = ("推荐", "商品", "价格", "预算", "耳机", "电脑", "手机", "买")
CARE_TERMS = ("订单", "物流", "发货", "退款", "退货", "换货", "售后")


def plan(state: WorkflowState) -> dict[str, list[Mission]]:
    message = state["message"].strip()
    missions: list[Mission] = []
    if any(term in message for term in PRODUCT_TERMS):
        missions.append(Mission(role=AgentRole.PRODUCT_SCOUT, instruction=message))
    if any(term in message for term in CARE_TERMS):
        missions.append(Mission(role=AgentRole.ORDER_CARE, instruction=message))
    if not missions:
        missions.append(Mission(role=AgentRole.CONVERSATION_GUIDE, instruction=message))
    return {"missions": missions}


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


async def handle_turn(message: str) -> tuple[str, list[AgentReport], float]:
    started = perf_counter()
    result = await GRAPH.ainvoke({"message": message, "reports": []})
    elapsed_ms = round((perf_counter() - started) * 1000, 2)
    return result["answer"], result.get("reports", []), elapsed_ms

