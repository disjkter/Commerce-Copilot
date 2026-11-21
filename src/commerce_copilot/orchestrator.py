import asyncio
import logging
import operator
from functools import lru_cache
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Annotated, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .agents import execute_mission
from .answering import LLMAnswerComposer
from .catalog import Product
from .config import get_settings
from .contracts import (
    AgentReport,
    ConversationTurn,
    GroundingSummary,
    LLMCallTrace,
    Mission,
    MemoryTrace,
    MemoryItem,
    StageTiming,
    TurnResponse,
    TurnTrace,
    UserPreferences,
)
from .database import get_catalog
from .grounding import validate_reports
from .metrics import (
    record_memory_compaction,
    record_turn_failure,
    record_turn_response,
)
from .memory import extract_memory_candidates
from .observability import bind_trace_id, log_event, reset_trace_id
from .planning import LLMPlanner, local_plan
from .personalization import personalize_mission


EventSink = Callable[[dict[str, object]], Awaitable[None]]
LOGGER = logging.getLogger("commerce_copilot.workflow")


class WorkflowState(TypedDict, total=False):
    message: str
    user_id: str
    history: list[ConversationTurn]
    preferences: UserPreferences
    memory_items: list[MemoryItem]
    missions: list[Mission]
    mission: Mission
    reports: Annotated[list[AgentReport], operator.add]
    answer: str
    planning_source: str
    query_understanding: object
    planner_error: str | None
    planner_llm: LLMCallTrace | None
    answer_source: str
    composer_error: str | None
    answer_llm: LLMCallTrace | None
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


async def _publish_validated_answer(
    state: WorkflowState,
    answer: str,
    source: str,
) -> None:
    """Publish an already-grounded answer without writing its content to logs."""

    sink = state.get("event_sink")
    if sink is not None:
        await sink(
            {
                "event": "answer_ready",
                "trace_id": state.get("trace_id", ""),
                "answer": answer,
                "source": source,
            }
        )


PLANNER = LLMPlanner()
ANSWER_COMPOSER = LLMAnswerComposer()


@lru_cache(maxsize=1)
def _catalog_products_for_entity_resolution() -> tuple[Product, ...]:
    """Cache stable catalog metadata used only for lightweight entity matching."""

    return tuple(get_catalog().list_products(in_stock_only=False))


async def plan(state: WorkflowState) -> dict[str, object]:
    started = perf_counter()
    await _emit(state, "planning_started")
    catalog_products = list(
        await asyncio.to_thread(_catalog_products_for_entity_resolution)
    )
    if state.get("local_only", False):
        decision = local_plan(
            state["message"].strip(),
            state.get("history", []),
            catalog_products=catalog_products,
        )
    else:
        decision = await PLANNER.create_plan(
            state["message"].strip(),
            state.get("history", []),
            catalog_products=catalog_products,
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
            state.get("memory_items", []),
        )
        for mission in decision.missions
    ]
    elapsed_ms = round((perf_counter() - started) * 1000, 2)
    await _emit(
        state,
        "planning_completed",
        elapsed_ms=elapsed_ms,
        source=decision.source,
        provider=(decision.llm_trace.provider if decision.llm_trace else None),
        model=(decision.llm_trace.model if decision.llm_trace else None),
        agents=[mission.role.value for mission in missions],
    )
    return {
        "missions": missions,
        "planning_source": decision.source,
        "query_understanding": decision.understanding,
        "planner_error": decision.error,
        "planner_llm": decision.llm_trace,
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
    report = await execute_mission(mission, trace_id=state.get("trace_id", ""))
    await _emit(
        state,
        "agent_completed",
        role=mission.role.value,
        status=report.status,
        elapsed_ms=report.elapsed_ms,
        evidence_count=len(report.evidence),
        tool_call_count=len(report.tool_calls),
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
        if reports and all(report.status != "completed" for report in reports):
            fallback_answer = "本轮业务服务响应超时或执行失败，请稍后重试。"
        elif grounding.issues:
            fallback_answer = "本轮结果未通过事实校验，暂时无法提供确定性回答。"
        else:
            fallback_answer = "暂时没有找到足够的可靠信息，请补充更具体的需求。"
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
        answer_llm = None
    else:
        decision = await ANSWER_COMPOSER.create_answer(
            state.get("message", ""),
            valid_reports,
            fallback_answer,
        )
        answer = decision.answer
        answer_source = decision.source
        composer_error = decision.error
        answer_llm = decision.llm_trace
    answering_elapsed_ms = round((perf_counter() - answering_started) * 1000, 2)
    await _emit(
        state,
        "answer_completed",
        elapsed_ms=answering_elapsed_ms,
        source=answer_source,
        provider=(answer_llm.provider if answer_llm else None),
        model=(answer_llm.model if answer_llm else None),
    )
    await _publish_validated_answer(state, answer, answer_source)
    return {
        "answer": answer,
        "answer_source": answer_source,
        "composer_error": composer_error,
        "answer_llm": answer_llm,
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
        response = await _handle_turn(
            message,
            conversation_id,
            user_id=user_id,
            local_only=local_only,
            event_sink=event_sink,
            trace_id=trace_id,
            started=started,
        )
        try:
            record_turn_response(response)
        except Exception as metrics_error:
            log_event(
                LOGGER,
                "metrics_recording_failed",
                level=logging.WARNING,
                trace_id=trace_id,
                error_type=type(metrics_error).__name__,
            )
        return response
    except Exception as exc:
        elapsed_ms = round((perf_counter() - started) * 1000, 2)
        try:
            record_turn_failure(type(exc).__name__, elapsed_ms)
        except Exception as metrics_error:
            log_event(
                LOGGER,
                "metrics_recording_failed",
                level=logging.WARNING,
                trace_id=trace_id,
                error_type=type(metrics_error).__name__,
            )
        log_event(
            LOGGER,
            "workflow_failed",
            level=logging.ERROR,
            trace_id=trace_id,
            elapsed_ms=elapsed_ms,
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
    settings = get_settings()
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
    if settings.context_compaction_enabled:
        history, loaded_memory, recent_turn_count = await asyncio.to_thread(
            repository.get_conversation_context,
            active_conversation_id,
            recent_limit=settings.context_recent_turns,
        )
    else:
        history = await asyncio.to_thread(
            repository.get_recent_conversation_turns,
            active_conversation_id,
            limit=settings.context_recent_turns,
        )
        loaded_memory = None
        recent_turn_count = len(history)
    history_elapsed_ms = round((perf_counter() - history_started) * 1000, 2)
    log_event(
        LOGGER,
        "repository_completed",
        operation="get_recent_conversation_turns",
        elapsed_ms=history_elapsed_ms,
        result_count=len(history),
        recent_turn_count=recent_turn_count,
        summary_loaded=loaded_memory is not None,
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
    memory_items_started = perf_counter()
    memory_items = await asyncio.to_thread(
        repository.list_memory_items,
        user_id=user_id,
        conversation_id=active_conversation_id,
    )
    memory_items_elapsed_ms = round(
        (perf_counter() - memory_items_started) * 1000,
        2,
    )
    log_event(
        LOGGER,
        "repository_completed",
        operation="list_memory_items",
        elapsed_ms=memory_items_elapsed_ms,
        result_count=len(memory_items),
    )
    result = await GRAPH.ainvoke(
        {
            "message": message,
            "user_id": user_id,
            "history": history,
            "preferences": preferences,
            "memory_items": memory_items,
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
            planner_llm=result.get("planner_llm"),
            answer_llm=result.get("answer_llm"),
            selected_agents=[report.role for report in reports],
            planner_error=result.get("planner_error"),
            composer_error=result.get("composer_error"),
            applied_preferences=_collect_applied_preferences(
                result.get("missions", [])
            ),
            query_understanding=result.get("query_understanding"),
            grounding=result["grounding"],
            memory=MemoryTrace(
                summary_loaded=loaded_memory is not None,
                recent_turns=recent_turn_count,
                source_turns=(
                    loaded_memory.source_turn_count if loaded_memory is not None else 0
                ),
            ),
            stage_timings=[
                StageTiming(
                    stage="repository_setup",
                    elapsed_ms=(
                        ownership_elapsed_ms
                        + history_elapsed_ms
                        + preferences_elapsed_ms
                        + memory_items_elapsed_ms
                    ),
                ),
                *result.get("stage_timings", []),
            ],
        ),
    )
    persistence_started = perf_counter()
    turn_evidence = [item for report in reports for item in report.evidence]
    turn_id = await asyncio.to_thread(
        repository.add_conversation_turn,
        active_conversation_id,
        message,
        response.answer,
        turn_evidence,
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
        evidence_count=len(turn_evidence),
    )
    memory_item_started = perf_counter()
    try:
        candidates = extract_memory_candidates(message, turn_evidence)
        recorded_memories = await asyncio.to_thread(
            repository.record_memory_candidates,
            user_id=user_id,
            conversation_id=active_conversation_id,
            source_turn_id=turn_id,
            candidates=candidates,
        )
        memory_item_elapsed_ms = round(
            (perf_counter() - memory_item_started) * 1000,
            2,
        )
        response.trace.stage_timings.append(
            StageTiming(
                stage="memory_item_persistence",
                elapsed_ms=memory_item_elapsed_ms,
            )
        )
        log_event(
            LOGGER,
            "memory_items_recorded",
            elapsed_ms=memory_item_elapsed_ms,
            candidate_count=len(candidates),
            recorded_count=len(recorded_memories),
        )
    except Exception as memory_item_error:
        memory_item_elapsed_ms = round(
            (perf_counter() - memory_item_started) * 1000,
            2,
        )
        response.trace.stage_timings.append(
            StageTiming(
                stage="memory_item_persistence",
                elapsed_ms=memory_item_elapsed_ms,
            )
        )
        log_event(
            LOGGER,
            "memory_items_recording_failed",
            level=logging.WARNING,
            elapsed_ms=memory_item_elapsed_ms,
            error_type=type(memory_item_error).__name__,
        )
    if settings.context_compaction_enabled:
        compaction_started = perf_counter()
        previous_source_turns = response.trace.memory.source_turns
        try:
            compacted_memory = await asyncio.to_thread(
                repository.compact_conversation_memory,
                active_conversation_id,
                recent_limit=settings.context_recent_turns,
            )
            compaction_elapsed_ms = round(
                (perf_counter() - compaction_started) * 1000,
                2,
            )
            if compacted_memory is not None:
                response.trace.memory.compacted_turns = max(
                    0,
                    compacted_memory.source_turn_count - previous_source_turns,
                )
                response.trace.memory.source_turns = compacted_memory.source_turn_count
            response.trace.stage_timings.append(
                StageTiming(
                    stage="memory_compaction",
                    elapsed_ms=compaction_elapsed_ms,
                )
            )
            try:
                record_memory_compaction(response.trace.memory.compacted_turns)
            except Exception as metrics_error:
                log_event(
                    LOGGER,
                    "metrics_recording_failed",
                    level=logging.WARNING,
                    error_type=type(metrics_error).__name__,
                    metric_scope="memory_compaction",
                )
            log_event(
                LOGGER,
                "memory_compaction_completed",
                elapsed_ms=compaction_elapsed_ms,
                compacted_turns=response.trace.memory.compacted_turns,
                source_turns=response.trace.memory.source_turns,
            )
        except Exception as memory_error:
            compaction_elapsed_ms = round(
                (perf_counter() - compaction_started) * 1000,
                2,
            )
            response.trace.stage_timings.append(
                StageTiming(
                    stage="memory_compaction",
                    elapsed_ms=compaction_elapsed_ms,
                )
            )
            try:
                record_memory_compaction(0, failed=True)
            except Exception:
                pass
            log_event(
                LOGGER,
                "memory_compaction_failed",
                level=logging.WARNING,
                elapsed_ms=compaction_elapsed_ms,
                error_type=type(memory_error).__name__,
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
