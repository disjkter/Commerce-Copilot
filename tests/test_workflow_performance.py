import asyncio

import pytest

from commerce_copilot.contracts import (
    AgentReport,
    GroundingSummary,
    TurnResponse,
    TurnTrace,
)
from commerce_copilot.workflow_performance import (
    WorkflowScenario,
    benchmark_workflow_sweep,
    benchmark_workflows,
)


async def _successful_executor(
    scenario: WorkflowScenario,
    conversation_id: str,
    local_only: bool,
) -> TurnResponse:
    assert local_only is True
    reports = [
        AgentReport(
            role=role,
            message="ok",
            elapsed_ms=1,
            status="completed",
        )
        for role in scenario.expected_roles
    ]
    return TurnResponse(
        conversation_id=conversation_id,
        answer=f"result: {scenario.expected_message_contains}",
        reports=reports,
        elapsed_ms=1,
        trace=TurnTrace(
            planning_source="local",
            answer_source="local",
            selected_agents=list(scenario.expected_roles),
            grounding=GroundingSummary(passed=True, checked_claims=0),
        ),
    )


def _scenario() -> WorkflowScenario:
    from commerce_copilot.contracts import AgentRole

    return WorkflowScenario(
        name="test_workflow",
        message="hello",
        expected_roles=(AgentRole.CONVERSATION_GUIDE,),
        expected_message_contains="expected",
    )


def test_workflow_benchmark_counts_business_success() -> None:
    report = asyncio.run(
        benchmark_workflows(
            (_scenario(),),
            requests=5,
            concurrency=2,
            warmup_requests=1,
            executor=_successful_executor,
            cleanup=False,
        )
    )

    assert report.scope == "orchestrated_workflow"
    assert report.mode == "local_only"
    assert report.successes == 5
    assert report.failures == 0
    assert report.grounding_failures == 0
    assert report.planner_sources == {"local": 5}
    assert report.by_scenario[0].success_rate == 1


def test_workflow_benchmark_rejects_semantically_wrong_answer() -> None:
    async def wrong_answer(
        scenario: WorkflowScenario,
        conversation_id: str,
        local_only: bool,
    ) -> TurnResponse:
        response = await _successful_executor(scenario, conversation_id, local_only)
        return response.model_copy(update={"answer": "wrong"})

    report = asyncio.run(
        benchmark_workflows(
            (_scenario(),),
            requests=2,
            concurrency=1,
            warmup_requests=0,
            executor=wrong_answer,
            cleanup=False,
        )
    )

    assert report.successes == 0
    assert report.failures == 2


def test_workflow_sweep_returns_peak_and_recommendation() -> None:
    report = asyncio.run(
        benchmark_workflow_sweep(
            (_scenario(),),
            concurrency_levels=(4, 1, 4),
            requests_per_level=8,
            warmup_requests=0,
            executor=_successful_executor,
            cleanup=False,
        )
    )

    assert report.concurrency_levels == (1, 4)
    assert report.peak_concurrency in report.concurrency_levels
    assert report.recommended_concurrency in report.concurrency_levels
    assert all(item.success_rate == 1 for item in report.results)


@pytest.mark.parametrize(
    ("requests", "concurrency", "warmup"),
    ((0, 1, 0), (1, 0, 0), (1, 1, -1)),
)
def test_workflow_benchmark_validates_configuration(
    requests: int,
    concurrency: int,
    warmup: int,
) -> None:
    with pytest.raises(ValueError):
        asyncio.run(
            benchmark_workflows(
                (_scenario(),),
                requests=requests,
                concurrency=concurrency,
                warmup_requests=warmup,
                executor=_successful_executor,
                cleanup=False,
            )
        )
