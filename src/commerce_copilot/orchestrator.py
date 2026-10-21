import asyncio
import logging
import operator
from collections.abc import Awaitable, Callable
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
    StageTiming,
    TurnResponse,
    TurnTrace,
    UserPreferences,
)
from .database import get_catalog
from .grounding import validate_reports
from .observability import bind_trace_id, log_event, reset_trace_id
from .planning import DeepSeekPlanner, local_plan
from .personalization import personalize_mission


EventSink = Callable[[dict[str, object]], Awaitable[None]]
LOGGER = logging.getLogger("commerce_copilot.workflow")


class WorkflowState(TypedDict, total=False):
    message: str
    user_id: str
    history: list[ConversationTurn]
    preferences: UserPreferences
    missions: list[Mission]
    mission: Mission
    reports: Annotated[list[AgentReport], operator.add]
    answer: str
    planning_source: str
    planner_error: str | None
    answer_source: str
    composer_error: str | None
    grounding: GroundingSummary
    local_only: bool
    trace_id: str
    event_sink: EventSink
    stage_timings: Annotated[list[StageTiming], operator.add]


async def _emit(
    state: WorkflowState,
    event: str,
    **data: object,
) -> None:
    trace_id = state.get("trace_id", "")
    log_event(LOGGER, event, trace_id=trace_id or None, **data)
    sink = state.get("event_sink")
    if sink is not None:
        await sink({"event": event, "trace_id": trace_id, **data})


PLANNER = DeepSeekPlanner()
ANSWER_COMPOSER = DeepSeekAnswerComposer()


async def plan(state: WorkflowState) -> dict[str, object]:
    started = perf_counter()
    await _emit(state, "planning_started")
    if state.get("local_only", False):
        decision = local_plan(
            state["message"].strip(),
            state.get("history", []),
        )
    else:
        decision = await PLANNER.create_plan(
            state["message"].strip(),
            state.get("history", []),
        )
    missions = [
        personalize_mission(
            mission.model_copy(
                update={
                    "parameters": {
                        **mission.parameters,
                        "user_id": state["user_id"],
                    }
                }
            ),
            state.get("preferences", UserPreferences()),
        )
        for mission in decision.missions
    ]
    elapsed_ms = round((perf_counter() - started) * 1000, 2)
    await _emit(
        state,
        "planning_completed",
        elapsed_ms=elapsed_ms,
        source=decision.source,
        agents=[mission.role.value for mission in missions],
    )
    return {
        "missions": missions,
        "planning_source": decision.source,
        "planner_error": decision.error,
        "stage_timings": [StageTiming(stage="planning", elapsed_ms=elapsed_ms)],
    }


def fan_out(state: WorkflowState) -> list[Send]:
    return [
        Send(
            "run_agent",
            {
                "mission": mission,
                "trace_id": state["trace_id"],
                "event_sink": state.get("event_sink"),
            },
        )
        for mission in state["missions"]
    ]


async def run_agent(state: WorkflowState) -> dict[str, object]:
    mission = state["mission"]
    await _emit(state, "agent_started", role=mission.role.value)
    report = await execute_mission(mission)
    await _emit(
        state,
        "agent_completed",
        role=mission.role.value,
        status=report.status,
        elapsed_ms=report.elapsed_ms,
        evidence_count=len(report.evidence),
    )
    return {
        "reports": [report],
        "stage_timings": [
            StageTiming(
                stage="agent",
                agent_role=mission.role,
                elapsed_ms=report.elapsed_ms,
            )
        ],
    }


async def compose(state: WorkflowState) -> dict[str, object]:
    reports = state.get("reports", [])
    grounding_started = perf_counter()
    grounding = validate_reports(reports)
    grounding_elapsed_ms = round((perf_counter() - grounding_started) * 1000, 2)
    await _emit(
        state,
        "grounding_completed",
        elapsed_ms=grounding_elapsed_ms,
        passed=grounding.passed,
        checked_claims=grounding.checked_claims,
        issue_count=len(grounding.issues),
    )
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
    answering_started = perf_counter()
    if state.get("local_only", False):
        answer = fallback_answer
        answer_source = "local"
        composer_error = None
    else:
        decision = await ANSWER_COMPOSER.create_answer(
            state.get("message", ""),
            valid_reports,
            fallback_answer,
        )
        answer = decision.answer
        answer_source = decision.source
        composer_error = decision.error
    answering_elapsed_ms = round((perf_counter() - answering_started) * 1000, 2)
    await _emit(
        state,
        "answer_completed",
        elapsed_ms=answering_elapsed_ms,
        source=answer_source,
    )
    return {
        "answer": answer,
        "answer_source": answer_source,
        "composer_error": composer_error,
        "grounding": grounding,
        "stage_timings": [
            StageTiming(stage="grounding", elapsed_ms=grounding_elapsed_ms),
            StageTiming(stage="answering", elapsed_ms=answering_elapsed_ms),
        ],
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
    local_only: bool = False,
    event_sink: EventSink | None = None,
) -> TurnResponse:
    trace_id = uuid4().hex
    token = bind_trace_id(trace_id)
    started = perf_counter()
    log_event(
        LOGGER,
        "workflow_started",
        trace_id=trace_id,
        message_length=len(message),
        has_conversation_id=conversation_id is not None,
        local_only=local_only,
    )
    try:
        return await _handle_turn(
            message,
            conversation_id,
            user_id=user_id,
            local_only=local_only,
            event_sink=event_sink,
            trace_id=trace_id,
            started=started,
        )
    except Exception as exc:
        log_event(
            LOGGER,
            "workflow_failed",
            level=logging.ERROR,
            trace_id=trace_id,
            elapsed_ms=round((perf_counter() - started) * 1000, 2),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise
    finally:
        reset_trace_id(token)


async def _handle_turn(
    message: str,
    conversation_id: str | None,
    *,
    user_id: str,
    local_only: bool,
    event_sink: EventSink | None,
    trace_id: str,
    started: float,
) -> TurnResponse:
    active_conversation_id = conversation_id or uuid4().hex
    repository = get_catalog()
    repository_started = perf_counter()
    owns_conversation = await asyncio.to_thread(
        repository.ensure_conversation_owner,
        active_conversation_id,
        user_id,
    )
    ownership_elapsed_ms = round((perf_counter() - repository_started) * 1000, 2)
    log_event(
        LOGGER,
        "repository_completed",
        operation="ensure_conversation_owner",
        elapsed_ms=ownership_elapsed_ms,
        success=owns_conversation,
    )
    if not owns_conversation:
        raise PermissionError("conversation belongs to another user")
    history_started = perf_counter()
    history = await asyncio.to_thread(
        repository.get_recent_conversation_turns,
        active_conversation_id,
    )
    history_elapsed_ms = round((perf_counter() - history_started) * 1000, 2)
    log_event(
        LOGGER,
        "repository_completed",
        operation="get_recent_conversation_turns",
        elapsed_ms=history_elapsed_ms,
        result_count=len(history),
    )
    preferences_started = perf_counter()
    preferences = await asyncio.to_thread(
        repository.get_user_preferences,
        user_id,
    )
    preferences_elapsed_ms = round((perf_counter() - preferences_started) * 1000, 2)
    log_event(
        LOGGER,
        "repository_completed",
        operation="get_user_preferences",
        elapsed_ms=preferences_elapsed_ms,
    )
    result = await GRAPH.ainvoke(
        {
            "message": message,
            "user_id": user_id,
            "history": history,
            "preferences": preferences,
            "reports": [],
            "local_only": local_only,
            "trace_id": trace_id,
            "event_sink": event_sink,
            "stage_timings": [],
        }
    )
    reports = result.get("reports", [])
    response = TurnResponse(
        conversation_id=active_conversation_id,
        answer=result["answer"],
        reports=reports,
        elapsed_ms=round((perf_counter() - started) * 1000, 2),
        trace=TurnTrace(
            trace_id=trace_id,
            planning_source=result.get("planning_source", "local"),
            answer_source=result.get("answer_source", "local"),
            selected_agents=[report.role for report in reports],
            planner_error=result.get("planner_error"),
            composer_error=result.get("composer_error"),
            applied_preferences=_collect_applied_preferences(
                result.get("missions", [])
            ),
            grounding=result["grounding"],
            stage_timings=[
                StageTiming(
                    stage="repository_setup",
                    elapsed_ms=(
                        ownership_elapsed_ms
                        + history_elapsed_ms
                        + preferences_elapsed_ms
                    ),
                ),
                *result.get("stage_timings", []),
            ],
        ),
    )
    persistence_started = perf_counter()
    await asyncio.to_thread(
        repository.add_conversation_turn,
        active_conversation_id,
        message,
        response.answer,
        [item for report in reports for item in report.evidence],
    )
    persistence_elapsed_ms = round((perf_counter() - persistence_started) * 1000, 2)
    response.trace.stage_timings.append(
        StageTiming(stage="persistence", elapsed_ms=persistence_elapsed_ms)
    )
    log_event(
        LOGGER,
        "repository_completed",
        operation="add_conversation_turn",
        elapsed_ms=persistence_elapsed_ms,
        evidence_count=sum(len(report.evidence) for report in reports),
    )
    response.elapsed_ms = round((perf_counter() - started) * 1000, 2)
    await _emit(
        {"trace_id": trace_id, "event_sink": event_sink},
        "workflow_completed",
        elapsed_ms=response.elapsed_ms,
        conversation_id=active_conversation_id,
    )
    return response


def _collect_applied_preferences(missions: list[Mission]) -> dict[str, object]:
    applied: dict[str, object] = {}
    for mission in missions:
        values = mission.parameters.get("applied_preferences")
        if isinstance(values, dict):
            applied.update(values)
    return applied
