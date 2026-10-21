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


@dataclass(frozen=True)
class _RequestSample:
    role: AgentRole
    elapsed_ms: float
    succeeded: bool


DEFAULT_MISSIONS = (
    Mission(
        role=AgentRole.PRODUCT_SCOUT,
        instruction="推荐一款500元以内的通勤耳机",
        parameters={
            "query": "通勤无线耳机",
            "max_price_cents": 50000,
            "in_stock_only": True,
        },
    ),
    Mission(
        role=AgentRole.ORDER_CARE,
        instruction="告诉我发货规则",
        parameters={},
    ),
    Mission(
        role=AgentRole.CONVERSATION_GUIDE,
        instruction="你好",
        parameters={},
    ),
)


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


async def _run_workload(
    workload: Sequence[Mission],
    concurrency: int,
    executor: AgentExecutor,
) -> list[_RequestSample]:
    queue: asyncio.Queue[Mission] = asyncio.Queue()
    for mission in workload:
        queue.put_nowait(mission)
    samples: list[_RequestSample] = []

    async def worker() -> None:
        while True:
            try:
                mission = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            started = perf_counter()
            succeeded = False
            try:
                report = await executor(mission)
                succeeded = report.status == "completed"
            except Exception:
                succeeded = False
            finally:
                samples.append(
                    _RequestSample(
                        role=mission.role,
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


def _round_robin(missions: Sequence[Mission], count: int) -> tuple[Mission, ...]:
    return tuple(missions[index % len(missions)] for index in range(count))


async def benchmark_agents(
    missions: Sequence[Mission] = DEFAULT_MISSIONS,
    *,
    requests: int = 100,
    concurrency: int = 10,
    warmup_requests: int = 5,
    executor: AgentExecutor = execute_mission,
) -> PerformanceReport:
    if not missions:
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
            _round_robin(missions, warmup_requests),
            1,
            executor,
        )

    workload = _round_robin(missions, requests)
    started_at = datetime.now(UTC).isoformat()
    started = perf_counter()
    samples = await _run_workload(workload, concurrency, executor)
    duration_seconds = max(perf_counter() - started, 1e-9)

    successes = sum(sample.succeeded for sample in samples)
    grouped: dict[AgentRole, list[_RequestSample]] = defaultdict(list)
    for sample in samples:
        grouped[sample.role].append(sample)

    by_role = tuple(
        _summarize_role(role, role_samples, duration_seconds)
        for role, role_samples in sorted(grouped.items(), key=lambda item: item[0].value)
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
    )


def _select_missions(agent: str) -> tuple[Mission, ...]:
    if agent == "all":
        return DEFAULT_MISSIONS
    role = AgentRole(agent)
    return tuple(mission for mission in DEFAULT_MISSIONS if mission.role == role)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark Commerce Copilot Agent execution throughput and latency."
    )
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument(
        "--agent",
        choices=("all", *(role.value for role in AgentRole)),
        default="all",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = asyncio.run(
        benchmark_agents(
            _select_missions(args.agent),
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
