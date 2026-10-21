import argparse
import asyncio
import json
import math
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from .agents import execute_mission
from .contracts import AgentReport, AgentRole, Mission


AgentExecutor = Callable[[Mission], Awaitable[AgentReport]]


@dataclass(frozen=True)
class LatencyMetrics:
    min_ms: float
    average_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float


@dataclass(frozen=True)
class RolePerformance:
    role: str
    requests: int
    successes: int
    failures: int
    success_rate: float
    qps: float
    latency: LatencyMetrics


@dataclass(frozen=True)
class ScenarioPerformance:
    scenario: str
    role: str
    requests: int
    successes: int
    failures: int
    success_rate: float
    qps: float
    latency: LatencyMetrics


@dataclass(frozen=True)
class PerformanceReport:
    scope: str
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
    by_role: tuple[RolePerformance, ...]
    by_scenario: tuple[ScenarioPerformance, ...]


@dataclass(frozen=True)
class SweepReport:
    scope: str
    requests_per_level: int
    warmup_requests: int
    concurrency_levels: tuple[int, ...]
    peak_qps: float
    peak_concurrency: int
    recommended_concurrency: int
    recommendation_method: str
    results: tuple[PerformanceReport, ...]


@dataclass(frozen=True)
class BenchmarkScenario:
    name: str
    mission: Mission
    expected_message_contains: str | None = None


@dataclass(frozen=True)
class _RequestSample:
    scenario: str
    role: AgentRole
    elapsed_ms: float
    succeeded: bool


DEFAULT_SCENARIOS = (
    BenchmarkScenario(
        name="product_search",
        mission=Mission(
            role=AgentRole.PRODUCT_SCOUT,
            instruction="推荐一款500元以内的通勤耳机",
            parameters={
                "query": "通勤无线耳机",
                "max_price_cents": 50000,
                "in_stock_only": True,
            },
        ),
    ),
    BenchmarkScenario(
        name="order_policy",
        mission=Mission(
            role=AgentRole.ORDER_CARE,
            instruction="告诉我发货规则",
            parameters={},
        ),
        expected_message_contains="发货",
    ),
    BenchmarkScenario(
        name="order_status_owned",
        mission=Mission(
            role=AgentRole.ORDER_CARE,
            instruction="查询订单 ORD-2026-1001 的状态",
            parameters={"user_id": "user-demo"},
        ),
        expected_message_contains="已发货",
    ),
    BenchmarkScenario(
        name="order_status_not_owned",
        mission=Mission(
            role=AgentRole.ORDER_CARE,
            instruction="查询订单 ORD-2026-1001 的状态",
            parameters={"user_id": "user-not-owner"},
        ),
        expected_message_contains="没有查询到订单",
    ),
    BenchmarkScenario(
        name="greeting",
        mission=Mission(
            role=AgentRole.CONVERSATION_GUIDE,
            instruction="你好",
            parameters={},
        ),
        expected_message_contains="你好",
    ),
)

DEFAULT_MISSIONS = tuple(scenario.mission for scenario in DEFAULT_SCENARIOS)


def percentile(values: Sequence[float], percent: float) -> float:
    """Return an interpolated percentile for a non-empty sample."""
    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")

    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _latency_metrics(samples: Sequence[_RequestSample]) -> LatencyMetrics:
    values = [sample.elapsed_ms for sample in samples]
    return LatencyMetrics(
        min_ms=round(min(values), 3),
        average_ms=round(sum(values) / len(values), 3),
        p50_ms=round(percentile(values, 50), 3),
        p95_ms=round(percentile(values, 95), 3),
        p99_ms=round(percentile(values, 99), 3),
        max_ms=round(max(values), 3),
    )


def _summarize_role(
    role: AgentRole,
    samples: Sequence[_RequestSample],
    duration_seconds: float,
) -> RolePerformance:
    successes = sum(sample.succeeded for sample in samples)
    requests = len(samples)
    return RolePerformance(
        role=role.value,
        requests=requests,
        successes=successes,
        failures=requests - successes,
        success_rate=round(successes / requests, 4),
        qps=round(requests / duration_seconds, 3),
        latency=_latency_metrics(samples),
    )


def _summarize_scenario(
    scenario: str,
    samples: Sequence[_RequestSample],
    duration_seconds: float,
) -> ScenarioPerformance:
    successes = sum(sample.succeeded for sample in samples)
    requests = len(samples)
    return ScenarioPerformance(
        scenario=scenario,
        role=samples[0].role.value,
        requests=requests,
        successes=successes,
        failures=requests - successes,
        success_rate=round(successes / requests, 4),
        qps=round(requests / duration_seconds, 3),
        latency=_latency_metrics(samples),
    )


async def _run_workload(
    workload: Sequence[BenchmarkScenario],
    concurrency: int,
    executor: AgentExecutor,
) -> list[_RequestSample]:
    queue: asyncio.Queue[BenchmarkScenario] = asyncio.Queue()
    for scenario in workload:
        queue.put_nowait(scenario)
    samples: list[_RequestSample] = []

    async def worker() -> None:
        while True:
            try:
                scenario = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            started = perf_counter()
            succeeded = False
            try:
                report = await executor(scenario.mission)
                succeeded = report.status == "completed" and (
                    scenario.expected_message_contains is None
                    or scenario.expected_message_contains in report.message
                )
            except Exception:
                succeeded = False
            finally:
                samples.append(
                    _RequestSample(
                        scenario=scenario.name,
                        role=scenario.mission.role,
                        elapsed_ms=(perf_counter() - started) * 1000,
                        succeeded=succeeded,
                    )
                )
                queue.task_done()

    workers = [
        asyncio.create_task(worker())
        for _ in range(min(concurrency, len(workload)))
    ]
    await asyncio.gather(*workers)
    return samples


def _as_scenarios(
    missions: Sequence[Mission | BenchmarkScenario],
) -> tuple[BenchmarkScenario, ...]:
    role_counts: dict[AgentRole, int] = defaultdict(int)
    scenarios: list[BenchmarkScenario] = []
    for item in missions:
        if isinstance(item, BenchmarkScenario):
            scenarios.append(item)
            continue
        index = role_counts[item.role]
        role_counts[item.role] += 1
        suffix = f"_{index + 1}" if index else ""
        scenarios.append(
            BenchmarkScenario(name=f"{item.role.value}{suffix}", mission=item)
        )
    return tuple(scenarios)


def _round_robin(
    scenarios: Sequence[BenchmarkScenario],
    count: int,
) -> tuple[BenchmarkScenario, ...]:
    return tuple(scenarios[index % len(scenarios)] for index in range(count))


async def benchmark_agents(
    missions: Sequence[Mission | BenchmarkScenario] = DEFAULT_SCENARIOS,
    *,
    requests: int = 100,
    concurrency: int = 10,
    warmup_requests: int = 5,
    executor: AgentExecutor = execute_mission,
) -> PerformanceReport:
    scenarios = _as_scenarios(missions)
    if not scenarios:
        raise ValueError("at least one mission is required")
    if requests < 1:
        raise ValueError("requests must be at least 1")
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if warmup_requests < 0:
        raise ValueError("warmup_requests cannot be negative")

    if warmup_requests:
        # Warm up lazily-created repositories and connection pools without
        # racing their one-time initialization. Warm-up samples are discarded.
        await _run_workload(
            _round_robin(scenarios, warmup_requests),
            1,
            executor,
        )

    workload = _round_robin(scenarios, requests)
    started_at = datetime.now(UTC).isoformat()
    started = perf_counter()
    samples = await _run_workload(workload, concurrency, executor)
    duration_seconds = max(perf_counter() - started, 1e-9)

    successes = sum(sample.succeeded for sample in samples)
    grouped: dict[AgentRole, list[_RequestSample]] = defaultdict(list)
    scenario_groups: dict[str, list[_RequestSample]] = defaultdict(list)
    for sample in samples:
        grouped[sample.role].append(sample)
        scenario_groups[sample.scenario].append(sample)

    by_role = tuple(
        _summarize_role(role, role_samples, duration_seconds)
        for role, role_samples in sorted(grouped.items(), key=lambda item: item[0].value)
    )
    by_scenario = tuple(
        _summarize_scenario(name, scenario_samples, duration_seconds)
        for name, scenario_samples in sorted(scenario_groups.items())
    )
    return PerformanceReport(
        scope="agent_execution",
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
        by_role=by_role,
        by_scenario=by_scenario,
    )


async def benchmark_sweep(
    missions: Sequence[Mission | BenchmarkScenario] = DEFAULT_SCENARIOS,
    *,
    concurrency_levels: Sequence[int] = (1, 5, 10, 20, 50),
    requests_per_level: int = 300,
    warmup_requests: int = 10,
    executor: AgentExecutor = execute_mission,
) -> SweepReport:
    levels = tuple(sorted(set(concurrency_levels)))
    if not levels or any(level < 1 for level in levels):
        raise ValueError("concurrency levels must be positive integers")

    result_list: list[PerformanceReport] = []
    for level in levels:
        result_list.append(
            await benchmark_agents(
                missions,
                requests=requests_per_level,
                concurrency=level,
                warmup_requests=warmup_requests,
                executor=executor,
            )
        )
    results = tuple(result_list)
    peak = max(results, key=lambda result: result.qps)
    target_qps = peak.qps * 0.9
    eligible = [
        result
        for result in results
        if result.qps >= target_qps and result.success_rate >= 0.99
    ]
    if eligible:
        recommended = min(eligible, key=lambda result: result.concurrency)
        recommendation_method = (
            "lowest concurrency reaching at least 90% of measured peak QPS "
            "with at least 99% success"
        )
    else:
        recommended = max(results, key=lambda result: (result.success_rate, result.qps))
        recommendation_method = (
            "no level reached 99% success; selected the highest-success result, "
            "breaking ties by QPS"
        )
    return SweepReport(
        scope="agent_execution_concurrency_sweep",
        requests_per_level=requests_per_level,
        warmup_requests=warmup_requests,
        concurrency_levels=levels,
        peak_qps=peak.qps,
        peak_concurrency=peak.concurrency,
        recommended_concurrency=recommended.concurrency,
        recommendation_method=recommendation_method,
        results=results,
    )


def _select_scenarios(agent: str, scenario: str | None) -> tuple[BenchmarkScenario, ...]:
    if scenario:
        return tuple(item for item in DEFAULT_SCENARIOS if item.name == scenario)
    if agent == "all":
        return DEFAULT_SCENARIOS
    role = AgentRole(agent)
    return tuple(item for item in DEFAULT_SCENARIOS if item.mission.role == role)


def _parse_concurrency_levels(value: str) -> tuple[int, ...]:
    try:
        levels = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use comma-separated integers") from exc
    if not levels or any(level < 1 for level in levels):
        raise argparse.ArgumentTypeError("concurrency levels must be positive")
    return levels


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark Commerce Copilot Agent execution throughput and latency."
    )
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument(
        "--sweep",
        type=_parse_concurrency_levels,
        help="Run a concurrency sweep, for example: 1,5,10,20,50,100.",
    )
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument(
        "--agent",
        choices=("all", *(role.value for role in AgentRole)),
        default="all",
    )
    parser.add_argument(
        "--scenario",
        choices=tuple(item.name for item in DEFAULT_SCENARIOS),
        help="Benchmark one named scenario; overrides --agent filtering.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    scenarios = _select_scenarios(args.agent, args.scenario)
    if args.sweep:
        report: PerformanceReport | SweepReport = asyncio.run(
            benchmark_sweep(
                scenarios,
                concurrency_levels=args.sweep,
                requests_per_level=args.requests,
                warmup_requests=args.warmup,
            )
        )
    else:
        report = asyncio.run(
            benchmark_agents(
                scenarios,
                requests=args.requests,
                concurrency=args.concurrency,
                warmup_requests=args.warmup,
            )
        )
    output = json.dumps(asdict(report), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
