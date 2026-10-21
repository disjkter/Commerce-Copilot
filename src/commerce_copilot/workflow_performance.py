import argparse
import asyncio
import json
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from .contracts import AgentRole, TurnResponse
from .database import get_catalog
from .orchestrator import handle_turn
from .performance import LatencyMetrics, percentile


@dataclass(frozen=True)
class WorkflowScenario:
    name: str
    message: str
    expected_roles: tuple[AgentRole, ...]
    expected_message_contains: str


@dataclass(frozen=True)
class WorkflowScenarioPerformance:
    scenario: str
    expected_agents: tuple[str, ...]
    requests: int
    successes: int
    failures: int
    success_rate: float
    qps: float
    latency: LatencyMetrics


@dataclass(frozen=True)
class WorkflowPerformanceReport:
    scope: str
    mode: str
    started_at: str
    requests: int
    concurrency: int
    warmup_requests: int
    duration_seconds: float
    successes: int
    failures: int
    success_rate: float
    qps: float
    latency: LatencyMetrics
    grounding_failures: int
    agent_failures: int
    planner_sources: dict[str, int]
    answer_sources: dict[str, int]
    cleanup_failures: int
    by_scenario: tuple[WorkflowScenarioPerformance, ...]


@dataclass(frozen=True)
class WorkflowSweepReport:
    scope: str
    mode: str
    requests_per_level: int
    warmup_requests: int
    concurrency_levels: tuple[int, ...]
    peak_qps: float
    peak_concurrency: int
    recommended_concurrency: int
    recommendation_method: str
    results: tuple[WorkflowPerformanceReport, ...]


@dataclass(frozen=True)
class _WorkflowSample:
    scenario: str
    elapsed_ms: float
    succeeded: bool
    grounding_passed: bool
    agent_failures: int
    planner_source: str
    answer_source: str


WorkflowExecutor = Callable[[WorkflowScenario, str, bool], Awaitable[TurnResponse]]


DEFAULT_WORKFLOW_SCENARIOS = (
    WorkflowScenario(
        name="greeting",
        message="你好",
        expected_roles=(AgentRole.CONVERSATION_GUIDE,),
        expected_message_contains="你好",
    ),
    WorkflowScenario(
        name="product_search",
        message="推荐一款500元以内的通勤耳机",
        expected_roles=(AgentRole.PRODUCT_SCOUT,),
        expected_message_contains="商品发现任务",
    ),
    WorkflowScenario(
        name="order_status",
        message="查询订单 ORD-2026-1001 的状态",
        expected_roles=(AgentRole.ORDER_CARE,),
        expected_message_contains="已发货",
    ),
    WorkflowScenario(
        name="multi_agent",
        message="推荐一款500元以内的通勤耳机，同时查询订单 ORD-2026-1001 的状态",
        expected_roles=(AgentRole.PRODUCT_SCOUT, AgentRole.ORDER_CARE),
        expected_message_contains="已发货",
    ),
)


async def _default_executor(
    scenario: WorkflowScenario,
    conversation_id: str,
    local_only: bool,
) -> TurnResponse:
    return await handle_turn(
        scenario.message,
        conversation_id,
        user_id="user-demo",
        local_only=local_only,
    )


def _latency_metrics(samples: Sequence[_WorkflowSample]) -> LatencyMetrics:
    values = [sample.elapsed_ms for sample in samples]
    return LatencyMetrics(
        min_ms=round(min(values), 3),
        average_ms=round(sum(values) / len(values), 3),
        p50_ms=round(percentile(values, 50), 3),
        p95_ms=round(percentile(values, 95), 3),
        p99_ms=round(percentile(values, 99), 3),
        max_ms=round(max(values), 3),
    )


def _round_robin(
    scenarios: Sequence[WorkflowScenario],
    count: int,
) -> tuple[WorkflowScenario, ...]:
    return tuple(scenarios[index % len(scenarios)] for index in range(count))


async def _run_workload(
    workload: Sequence[WorkflowScenario],
    concurrency: int,
    *,
    local_only: bool,
    executor: WorkflowExecutor,
) -> tuple[list[_WorkflowSample], list[str]]:
    run_id = uuid4().hex[:10]
    queue: asyncio.Queue[tuple[int, WorkflowScenario]] = asyncio.Queue()
    for index, scenario in enumerate(workload):
        queue.put_nowait((index, scenario))
    samples: list[_WorkflowSample] = []
    conversation_ids: list[str] = []

    async def worker() -> None:
        while True:
            try:
                index, scenario = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            conversation_id = f"perf-{run_id}-{index}"
            conversation_ids.append(conversation_id)
            started = perf_counter()
            try:
                response = await executor(scenario, conversation_id, local_only)
                roles = tuple(sorted(report.role.value for report in response.reports))
                expected_roles = tuple(
                    sorted(role.value for role in scenario.expected_roles)
                )
                agent_failures = sum(
                    report.status != "completed" for report in response.reports
                )
                grounding_passed = response.trace.grounding.passed
                succeeded = (
                    roles == expected_roles
                    and agent_failures == 0
                    and grounding_passed
                    and scenario.expected_message_contains in response.answer
                )
                planner_source = response.trace.planning_source
                answer_source = response.trace.answer_source
            except Exception:
                succeeded = False
                grounding_passed = False
                agent_failures = 1
                planner_source = "error"
                answer_source = "error"
            finally:
                samples.append(
                    _WorkflowSample(
                        scenario=scenario.name,
                        elapsed_ms=(perf_counter() - started) * 1000,
                        succeeded=succeeded,
                        grounding_passed=grounding_passed,
                        agent_failures=agent_failures,
                        planner_source=planner_source,
                        answer_source=answer_source,
                    )
                )
                queue.task_done()

    workers = [
        asyncio.create_task(worker())
        for _ in range(min(concurrency, len(workload)))
    ]
    await asyncio.gather(*workers)
    return samples, conversation_ids


async def _cleanup_conversations(conversation_ids: Sequence[str]) -> int:
    repository = get_catalog()
    results = await asyncio.gather(
        *(
            asyncio.to_thread(
                repository.delete_conversation,
                conversation_id,
                user_id="user-demo",
            )
            for conversation_id in conversation_ids
        )
    )
    return sum(not removed for removed in results)


def _summarize_scenario(
    scenario: WorkflowScenario,
    samples: Sequence[_WorkflowSample],
    duration_seconds: float,
) -> WorkflowScenarioPerformance:
    successes = sum(sample.succeeded for sample in samples)
    requests = len(samples)
    return WorkflowScenarioPerformance(
        scenario=scenario.name,
        expected_agents=tuple(role.value for role in scenario.expected_roles),
        requests=requests,
        successes=successes,
        failures=requests - successes,
        success_rate=round(successes / requests, 4),
        qps=round(requests / duration_seconds, 3),
        latency=_latency_metrics(samples),
    )


async def benchmark_workflows(
    scenarios: Sequence[WorkflowScenario] = DEFAULT_WORKFLOW_SCENARIOS,
    *,
    requests: int = 100,
    concurrency: int = 10,
    warmup_requests: int = 5,
    local_only: bool = True,
    executor: WorkflowExecutor = _default_executor,
    cleanup: bool = True,
) -> WorkflowPerformanceReport:
    if not scenarios:
        raise ValueError("at least one workflow scenario is required")
    if requests < 1:
        raise ValueError("requests must be at least 1")
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if warmup_requests < 0:
        raise ValueError("warmup_requests cannot be negative")

    cleanup_failures = 0
    if warmup_requests:
        _, warmup_ids = await _run_workload(
            _round_robin(scenarios, warmup_requests),
            1,
            local_only=local_only,
            executor=executor,
        )
        if cleanup:
            cleanup_failures += await _cleanup_conversations(warmup_ids)

    workload = _round_robin(scenarios, requests)
    started_at = datetime.now(UTC).isoformat()
    started = perf_counter()
    samples, conversation_ids = await _run_workload(
        workload,
        concurrency,
        local_only=local_only,
        executor=executor,
    )
    duration_seconds = max(perf_counter() - started, 1e-9)
    if cleanup:
        cleanup_failures += await _cleanup_conversations(conversation_ids)

    successes = sum(sample.succeeded for sample in samples)
    grouped: dict[str, list[_WorkflowSample]] = defaultdict(list)
    for sample in samples:
        grouped[sample.scenario].append(sample)
    scenario_by_name = {scenario.name: scenario for scenario in scenarios}

    return WorkflowPerformanceReport(
        scope="orchestrated_workflow",
        mode="local_only" if local_only else "configured_llm",
        started_at=started_at,
        requests=requests,
        concurrency=concurrency,
        warmup_requests=warmup_requests,
        duration_seconds=round(duration_seconds, 6),
        successes=successes,
        failures=requests - successes,
        success_rate=round(successes / requests, 4),
        qps=round(requests / duration_seconds, 3),
        latency=_latency_metrics(samples),
        grounding_failures=sum(not sample.grounding_passed for sample in samples),
        agent_failures=sum(sample.agent_failures for sample in samples),
        planner_sources=dict(sorted(Counter(sample.planner_source for sample in samples).items())),
        answer_sources=dict(sorted(Counter(sample.answer_source for sample in samples).items())),
        cleanup_failures=cleanup_failures,
        by_scenario=tuple(
            _summarize_scenario(
                scenario_by_name[name],
                scenario_samples,
                duration_seconds,
            )
            for name, scenario_samples in sorted(grouped.items())
        ),
    )


async def benchmark_workflow_sweep(
    scenarios: Sequence[WorkflowScenario] = DEFAULT_WORKFLOW_SCENARIOS,
    *,
    concurrency_levels: Sequence[int] = (1, 5, 10, 20, 50),
    requests_per_level: int = 300,
    warmup_requests: int = 10,
    local_only: bool = True,
    executor: WorkflowExecutor = _default_executor,
    cleanup: bool = True,
) -> WorkflowSweepReport:
    levels = tuple(sorted(set(concurrency_levels)))
    if not levels or any(level < 1 for level in levels):
        raise ValueError("concurrency levels must be positive integers")

    result_list: list[WorkflowPerformanceReport] = []
    for level in levels:
        result_list.append(
            await benchmark_workflows(
                scenarios,
                requests=requests_per_level,
                concurrency=level,
                warmup_requests=warmup_requests,
                local_only=local_only,
                executor=executor,
                cleanup=cleanup,
            )
        )
    results = tuple(result_list)
    peak = max(results, key=lambda result: result.qps)
    eligible = [
        result
        for result in results
        if result.qps >= peak.qps * 0.9 and result.success_rate >= 0.99
    ]
    if eligible:
        recommended = min(eligible, key=lambda result: result.concurrency)
        method = (
            "lowest concurrency reaching at least 90% of measured peak QPS "
            "with at least 99% business success"
        )
    else:
        recommended = max(results, key=lambda result: (result.success_rate, result.qps))
        method = (
            "no level reached 99% business success; selected the highest-success "
            "result, breaking ties by QPS"
        )
    return WorkflowSweepReport(
        scope="orchestrated_workflow_concurrency_sweep",
        mode="local_only" if local_only else "configured_llm",
        requests_per_level=requests_per_level,
        warmup_requests=warmup_requests,
        concurrency_levels=levels,
        peak_qps=peak.qps,
        peak_concurrency=peak.concurrency,
        recommended_concurrency=recommended.concurrency,
        recommendation_method=method,
        results=results,
    )


def _parse_levels(value: str) -> tuple[int, ...]:
    try:
        levels = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use comma-separated integers") from exc
    if not levels or any(level < 1 for level in levels):
        raise argparse.ArgumentTypeError("concurrency levels must be positive")
    return levels


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark the complete orchestrated Commerce Copilot workflow."
    )
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--sweep", type=_parse_levels)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument(
        "--scenario",
        choices=("all", *(item.name for item in DEFAULT_WORKFLOW_SCENARIOS)),
        default="all",
    )
    parser.add_argument(
        "--allow-llm",
        action="store_true",
        help="Allow configured external LLM calls. Disabled by default to prevent cost.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    scenarios = (
        DEFAULT_WORKFLOW_SCENARIOS
        if args.scenario == "all"
        else tuple(
            item for item in DEFAULT_WORKFLOW_SCENARIOS if item.name == args.scenario
        )
    )
    common = {
        "scenarios": scenarios,
        "warmup_requests": args.warmup,
        "local_only": not args.allow_llm,
    }
    if args.sweep:
        report: WorkflowPerformanceReport | WorkflowSweepReport = asyncio.run(
            benchmark_workflow_sweep(
                concurrency_levels=args.sweep,
                requests_per_level=args.requests,
                **common,
            )
        )
    else:
        report = asyncio.run(
            benchmark_workflows(
                requests=args.requests,
                concurrency=args.concurrency,
                **common,
            )
        )

    output = json.dumps(asdict(report), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
