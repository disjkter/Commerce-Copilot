import asyncio

import pytest

from commerce_copilot.contracts import AgentReport, AgentRole, Mission
from commerce_copilot.performance import (
    BenchmarkScenario,
    benchmark_agents,
    benchmark_sweep,
    percentile,
)


def _mission(role: AgentRole) -> Mission:
    return Mission(role=role, instruction="test")


async def _successful_executor(mission: Mission) -> AgentReport:
    await asyncio.sleep(0)
    return AgentReport(
        role=mission.role,
        message="ok",
        elapsed_ms=0,
        status="completed",
    )


def test_percentile_uses_linear_interpolation() -> None:
    values = [10.0, 20.0, 30.0, 40.0]

    assert percentile(values, 50) == 25
    assert percentile(values, 95) == pytest.approx(38.5)


def test_benchmark_counts_requests_and_roles() -> None:
    report = asyncio.run(
        benchmark_agents(
            (
                _mission(AgentRole.PRODUCT_SCOUT),
                _mission(AgentRole.ORDER_CARE),
            ),
            requests=6,
            concurrency=2,
            warmup_requests=1,
            executor=_successful_executor,
        )
    )

    assert report.scope == "agent_execution"
    assert report.requests == 6
    assert report.successes == 6
    assert report.failures == 0
    assert report.success_rate == 1
    assert report.qps > 0
    assert {item.role: item.requests for item in report.by_role} == {
        "order_care": 3,
        "product_scout": 3,
    }
    assert {item.scenario: item.requests for item in report.by_scenario} == {
        "order_care": 3,
        "product_scout": 3,
    }


def test_benchmark_isolates_executor_failures() -> None:
    async def sometimes_fails(mission: Mission) -> AgentReport:
        if mission.role == AgentRole.ORDER_CARE:
            raise RuntimeError("simulated failure")
        return await _successful_executor(mission)

    report = asyncio.run(
        benchmark_agents(
            (
                _mission(AgentRole.PRODUCT_SCOUT),
                _mission(AgentRole.ORDER_CARE),
            ),
            requests=4,
            concurrency=4,
            warmup_requests=0,
            executor=sometimes_fails,
        )
    )

    assert report.successes == 2
    assert report.failures == 2
    order_metrics = next(item for item in report.by_role if item.role == "order_care")
    assert order_metrics.success_rate == 0


def test_benchmark_checks_expected_scenario_output() -> None:
    scenario = BenchmarkScenario(
        name="semantic_check",
        mission=_mission(AgentRole.ORDER_CARE),
        expected_message_contains="expected fact",
    )

    report = asyncio.run(
        benchmark_agents(
            (scenario,),
            requests=2,
            concurrency=1,
            warmup_requests=0,
            executor=_successful_executor,
        )
    )

    assert report.successes == 0
    assert report.by_scenario[0].scenario == "semantic_check"
    assert report.by_scenario[0].failures == 2


def test_sweep_normalizes_levels_and_returns_recommendation() -> None:
    report = asyncio.run(
        benchmark_sweep(
            (_mission(AgentRole.CONVERSATION_GUIDE),),
            concurrency_levels=(5, 1, 5),
            requests_per_level=10,
            warmup_requests=0,
            executor=_successful_executor,
        )
    )

    assert report.concurrency_levels == (1, 5)
    assert tuple(item.concurrency for item in report.results) == (1, 5)
    assert report.peak_concurrency in report.concurrency_levels
    assert report.recommended_concurrency in report.concurrency_levels


def test_sweep_rejects_invalid_concurrency_levels() -> None:
    with pytest.raises(ValueError):
        asyncio.run(
            benchmark_sweep(
                (_mission(AgentRole.CONVERSATION_GUIDE),),
                concurrency_levels=(0, 5),
                executor=_successful_executor,
            )
        )


@pytest.mark.parametrize(
    ("requests", "concurrency", "warmup_requests"),
    ((0, 1, 0), (1, 0, 0), (1, 1, -1)),
)
def test_benchmark_rejects_invalid_load_configuration(
    requests: int,
    concurrency: int,
    warmup_requests: int,
) -> None:
    with pytest.raises(ValueError):
        asyncio.run(
            benchmark_agents(
                (_mission(AgentRole.CONVERSATION_GUIDE),),
                requests=requests,
                concurrency=concurrency,
                warmup_requests=warmup_requests,
                executor=_successful_executor,
            )
        )
