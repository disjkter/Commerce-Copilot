import asyncio
import json

import pytest

from commerce_copilot.agent_evaluation import (
    AgentEvalCase,
    compare_with_baseline,
    evaluate_dataset,
    load_cases,
)
from commerce_copilot.contracts import (
    AgentReport,
    AgentRole,
    Evidence,
    GroundingSummary,
    TurnResponse,
    TurnTrace,
)
from commerce_copilot.query_understanding import (
    QueryIntent,
    QueryUnderstanding,
    ResponseFocus,
)


DATASET = "evals/agent_cases.jsonl"


def test_agent_dataset_has_broad_unique_coverage():
    cases = load_cases(DATASET)

    assert len(cases) == 132
    assert len({case.case_id for case in cases}) == len(cases)
    assert len({case.category for case in cases}) == 11
    assert {case.difficulty.value for case in cases} == {"easy", "medium", "hard"}
    assert sum(len(case.turns) > 1 for case in cases) >= 20
    assert sum(case.fault is not None for case in cases) >= 6


def test_duplicate_case_ids_are_rejected(tmp_path):
    case = AgentEvalCase.model_validate_json(
        open(DATASET, encoding="utf-8").readline()
    )
    path = tmp_path / "duplicates.jsonl"
    rendered = case.model_dump_json()
    path.write_text(f"{rendered}\n{rendered}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate evaluation case id"):
        load_cases(path)


def test_agent_evaluator_scores_routing_intent_evidence_and_grounding():
    case = AgentEvalCase.model_validate(
        {
            "case_id": "unit-product-001",
            "category": "unit",
            "difficulty": "easy",
            "turns": ["推荐耳机"],
            "expected": {
                "agents": ["product_scout"],
                "intents": ["product"],
                "focus": "summary",
                "required_evidence_refs": ["CP-AUD-101"],
                "grounding_passed": True,
            },
        }
    )

    async def executor(*_, **__):
        return TurnResponse(
            conversation_id="eval-unit",
            answer="推荐云雀 Lite。",
            reports=[
                AgentReport(
                    role=AgentRole.PRODUCT_SCOUT,
                    message="推荐云雀 Lite。",
                    evidence=[
                        Evidence(source="local_catalog", reference="CP-AUD-101")
                    ],
                    elapsed_ms=1,
                )
            ],
            elapsed_ms=2,
            trace=TurnTrace(
                trace_id="trace-unit",
                planning_source="local",
                answer_source="local",
                selected_agents=[AgentRole.PRODUCT_SCOUT],
                query_understanding=QueryUnderstanding(
                    intents=[QueryIntent.PRODUCT],
                    focus=ResponseFocus.SUMMARY,
                    confidence=0.9,
                ),
                grounding=GroundingSummary(passed=True, checked_claims=1),
            ),
        )

    report = asyncio.run(
        evaluate_dataset(
            [case],
            dataset_name="unit",
            executor=executor,
        )
    )

    assert report.pass_rate == 1
    assert report.routing_accuracy == 1
    assert report.intent_accuracy == 1
    assert report.evidence_accuracy == 1
    assert report.grounding_accuracy == 1


def test_baseline_comparison_reports_regressions_and_improvements():
    baseline = {
        "pass_rate": 0.5,
        "average_score": 0.7,
        "routing_accuracy": 0.8,
        "intent_accuracy": 0.8,
        "evidence_accuracy": 0.6,
        "grounding_accuracy": 1.0,
        "p95_latency_ms": 100,
        "results": [
            {"case_id": "a", "passed": True},
            {"case_id": "b", "passed": False},
        ],
    }
    current = {
        **baseline,
        "pass_rate": 0.75,
        "p95_latency_ms": 80,
        "results": [
            {"case_id": "a", "passed": False},
            {"case_id": "b", "passed": True},
        ],
    }

    comparison = compare_with_baseline(current, baseline)

    assert comparison["metric_deltas"]["pass_rate"] == 0.25
    assert comparison["metric_deltas"]["p95_latency_ms"] == -20
    assert comparison["regressed_cases"] == ["a"]
    assert comparison["improved_cases"] == ["b"]


def test_one_execution_error_does_not_abort_the_dataset():
    case = AgentEvalCase.model_validate(
        {
            "case_id": "unit-error-001",
            "category": "unit",
            "difficulty": "hard",
            "turns": ["触发异常"],
            "expected": {"agents": ["conversation_guide"]},
        }
    )

    async def failing_executor(*_, **__):
        raise TimeoutError("simulated timeout")

    report = asyncio.run(
        evaluate_dataset(
            [case],
            dataset_name="unit",
            executor=failing_executor,
            case_timeout_seconds=0.1,
        )
    )

    assert report.total_cases == 1
    assert report.pass_rate == 0
    assert report.results[0].checks[0].name == "execution_error"
    assert report.results[0].checks[0].actual == "TimeoutError"
