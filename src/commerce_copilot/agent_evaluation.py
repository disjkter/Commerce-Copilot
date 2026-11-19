import argparse
import asyncio
import json
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from statistics import mean
from time import perf_counter
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field, model_validator

from . import agents, orchestrator
from .answering import DeepSeekAnswerComposer
from .config import get_settings
from .contracts import AgentRole, TurnResponse
from .planning import DeepSeekPlanner
from .query_understanding import QueryIntent, ResponseFocus


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class FaultMode(StrEnum):
    PRODUCT_AGENT_TIMEOUT = "product_agent_timeout"
    ORDER_AGENT_TIMEOUT = "order_agent_timeout"
    PLANNER_INVALID_JSON = "planner_invalid_json"
    COMPOSER_INVALID_JSON = "composer_invalid_json"


class ExpectedBehavior(BaseModel):
    agents: list[AgentRole] = Field(default_factory=list, max_length=3)
    agent_match: str = Field(default="exact", pattern=r"^(exact|contains)$")
    intents: list[QueryIntent] = Field(default_factory=list, max_length=3)
    focus: ResponseFocus | None = None
    required_evidence_refs: list[str] = Field(default_factory=list, max_length=20)
    forbidden_evidence_refs: list[str] = Field(default_factory=list, max_length=20)
    require_evidence: bool | None = None
    grounding_passed: bool | None = True
    planning_source: str | None = Field(
        default=None,
        pattern=r"^(llm|local|local_after_error)$",
    )
    answer_source: str | None = Field(
        default=None,
        pattern=r"^(llm|local|local_after_error)$",
    )
    failed_agents: list[AgentRole] = Field(default_factory=list, max_length=3)
    answer_contains_all: list[str] = Field(default_factory=list, max_length=20)
    answer_contains_any: list[str] = Field(default_factory=list, max_length=20)
    answer_excludes: list[str] = Field(default_factory=list, max_length=20)


class AgentEvalCase(BaseModel):
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    category: str = Field(min_length=1, max_length=50)
    difficulty: Difficulty
    turns: list[str] = Field(min_length=1, max_length=5)
    expected: ExpectedBehavior
    tags: list[str] = Field(default_factory=list, max_length=20)
    fault: FaultMode | None = None

    @model_validator(mode="after")
    def validate_turns(self) -> "AgentEvalCase":
        if any(not turn.strip() for turn in self.turns):
            raise ValueError("evaluation turns cannot be blank")
        return self


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    expected: object
    actual: object


@dataclass(frozen=True)
class AgentCaseResult:
    case_id: str
    category: str
    difficulty: str
    passed: bool
    score: float
    elapsed_ms: float
    checks: tuple[CheckResult, ...]
    answer: str
    trace_id: str


@dataclass(frozen=True)
class GroupMetrics:
    cases: int
    passed: int
    pass_rate: float
    average_score: float


@dataclass(frozen=True)
class AgentEvaluationReport:
    started_at: str
    dataset: str
    mode: str
    total_cases: int
    passed_cases: int
    pass_rate: float
    average_score: float
    routing_accuracy: float
    intent_accuracy: float
    evidence_accuracy: float
    grounding_accuracy: float
    average_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    by_category: dict[str, GroupMetrics]
    by_difficulty: dict[str, GroupMetrics]
    results: tuple[AgentCaseResult, ...]


TurnExecutor = Callable[..., Awaitable[TurnResponse]]


def load_cases(path: str | Path) -> list[AgentEvalCase]:
    dataset_path = Path(path)
    cases: list[AgentEvalCase] = []
    case_ids: set[str] = set()
    with dataset_path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                case = AgentEvalCase.model_validate_json(line)
            except Exception as exc:
                raise ValueError(
                    f"invalid evaluation case at {dataset_path}:{line_number}: {exc}"
                ) from exc
            if case.case_id in case_ids:
                raise ValueError(f"duplicate evaluation case id: {case.case_id}")
            case_ids.add(case.case_id)
            cases.append(case)
    if not cases:
        raise ValueError("evaluation dataset is empty")
    return cases


async def evaluate_case(
    case: AgentEvalCase,
    *,
    executor: TurnExecutor = orchestrator.handle_turn,
    local_only: bool = True,
    user_id: str = "user-demo",
) -> AgentCaseResult:
    conversation_id = f"eval-{case.case_id}-{uuid4().hex[:10]}"
    started = perf_counter()
    response: TurnResponse | None = None
    with _inject_fault(case.fault):
        for message in case.turns:
            response = await executor(
                message,
                conversation_id,
                user_id=user_id,
                local_only=(
                    False
                    if case.fault
                    in {FaultMode.PLANNER_INVALID_JSON, FaultMode.COMPOSER_INVALID_JSON}
                    else local_only
                ),
            )
    if response is None:  # guarded by the Pydantic min_length constraint
        raise RuntimeError("evaluation case produced no response")
    elapsed_ms = round((perf_counter() - started) * 1000, 2)
    checks = tuple(_evaluate_expected(case.expected, response))
    score = sum(check.passed for check in checks) / len(checks) if checks else 1.0
    return AgentCaseResult(
        case_id=case.case_id,
        category=case.category,
        difficulty=case.difficulty.value,
        passed=all(check.passed for check in checks),
        score=round(score, 4),
        elapsed_ms=elapsed_ms,
        checks=checks,
        answer=response.answer,
        trace_id=response.trace.trace_id,
    )


async def evaluate_dataset(
    cases: Sequence[AgentEvalCase],
    *,
    dataset_name: str,
    executor: TurnExecutor = orchestrator.handle_turn,
    local_only: bool = True,
    user_id: str = "user-demo",
    case_timeout_seconds: float = 30.0,
) -> AgentEvaluationReport:
    started_at = datetime.now(UTC).isoformat()
    results = []
    for case in cases:
        case_started = perf_counter()
        try:
            result = await asyncio.wait_for(
                evaluate_case(
                    case,
                    executor=executor,
                    local_only=local_only,
                    user_id=user_id,
                ),
                timeout=case_timeout_seconds,
            )
        except Exception as exc:
            result = AgentCaseResult(
                case_id=case.case_id,
                category=case.category,
                difficulty=case.difficulty.value,
                passed=False,
                score=0.0,
                elapsed_ms=round((perf_counter() - case_started) * 1000, 2),
                checks=(
                    CheckResult(
                        name="execution_error",
                        passed=False,
                        expected="case completes without unhandled exception",
                        actual=type(exc).__name__,
                    ),
                ),
                answer="",
                trace_id="",
            )
        results.append(result)
    latencies = [result.elapsed_ms for result in results]
    return AgentEvaluationReport(
        started_at=started_at,
        dataset=dataset_name,
        mode="local" if local_only else "live_llm",
        total_cases=len(results),
        passed_cases=sum(result.passed for result in results),
        pass_rate=_rate(result.passed for result in results),
        average_score=round(mean(result.score for result in results), 4),
        routing_accuracy=_named_check_rate(results, "agents"),
        intent_accuracy=_named_check_rate(results, "intents"),
        evidence_accuracy=_named_check_rate(results, "required_evidence_refs"),
        grounding_accuracy=_named_check_rate(results, "grounding_passed"),
        average_latency_ms=round(mean(latencies), 3),
        p50_latency_ms=round(_percentile(latencies, 0.50), 3),
        p95_latency_ms=round(_percentile(latencies, 0.95), 3),
        by_category=_group_metrics(results, lambda result: result.category),
        by_difficulty=_group_metrics(results, lambda result: result.difficulty),
        results=tuple(results),
    )


def report_to_dict(
    report: AgentEvaluationReport,
    *,
    include_passed: bool = False,
) -> dict[str, object]:
    payload = asdict(report)
    if not include_passed:
        payload["results"] = [
            result for result in payload["results"] if not result["passed"]
        ]
    return payload


def compare_with_baseline(
    current: dict[str, object],
    baseline: dict[str, object],
) -> dict[str, object]:
    higher_is_better = (
        "pass_rate",
        "average_score",
        "routing_accuracy",
        "intent_accuracy",
        "evidence_accuracy",
        "grounding_accuracy",
    )
    metric_deltas = {
        name: round(float(current[name]) - float(baseline[name]), 4)
        for name in higher_is_better
    }
    metric_deltas["p95_latency_ms"] = round(
        float(current["p95_latency_ms"]) - float(baseline["p95_latency_ms"]),
        3,
    )
    baseline_results = {
        str(item["case_id"]): bool(item["passed"])
        for item in baseline.get("results", [])
    }
    current_results = {
        str(item["case_id"]): bool(item["passed"])
        for item in current.get("results", [])
    }
    shared_ids = baseline_results.keys() & current_results.keys()
    return {
        "metric_deltas": metric_deltas,
        "regressed_cases": sorted(
            case_id
            for case_id in shared_ids
            if baseline_results[case_id] and not current_results[case_id]
        ),
        "improved_cases": sorted(
            case_id
            for case_id in shared_ids
            if not baseline_results[case_id] and current_results[case_id]
        ),
        "interpretation": {
            "quality_metrics": "positive delta is better",
            "p95_latency_ms": "negative delta is better",
        },
    }


def _evaluate_expected(
    expected: ExpectedBehavior,
    response: TurnResponse,
) -> Iterator[CheckResult]:
    actual_agents = [report.role for report in response.reports]
    if expected.agents:
        route_ok = (
            actual_agents == expected.agents
            if expected.agent_match == "exact"
            else set(expected.agents).issubset(actual_agents)
        )
        yield CheckResult("agents", route_ok, expected.agents, actual_agents)

    understanding = response.trace.query_understanding
    if expected.intents:
        actual_intents = understanding.intents if understanding else []
        yield CheckResult(
            "intents",
            set(actual_intents) == set(expected.intents),
            expected.intents,
            actual_intents,
        )
    if expected.focus is not None:
        actual_focus = understanding.focus if understanding else None
        yield CheckResult("focus", actual_focus == expected.focus, expected.focus, actual_focus)

    evidence_refs = {
        item.reference for report in response.reports for item in report.evidence
    }
    if expected.required_evidence_refs:
        required = set(expected.required_evidence_refs)
        yield CheckResult(
            "required_evidence_refs",
            required.issubset(evidence_refs),
            sorted(required),
            sorted(evidence_refs),
        )
    if expected.forbidden_evidence_refs:
        forbidden = set(expected.forbidden_evidence_refs)
        yield CheckResult(
            "forbidden_evidence_refs",
            forbidden.isdisjoint(evidence_refs),
            sorted(forbidden),
            sorted(evidence_refs),
        )
    if expected.require_evidence is not None:
        yield CheckResult(
            "require_evidence",
            bool(evidence_refs) is expected.require_evidence,
            expected.require_evidence,
            bool(evidence_refs),
        )
    if expected.grounding_passed is not None:
        actual_grounding = response.trace.grounding.passed
        yield CheckResult(
            "grounding_passed",
            actual_grounding is expected.grounding_passed,
            expected.grounding_passed,
            actual_grounding,
        )
    if expected.planning_source is not None:
        yield CheckResult(
            "planning_source",
            response.trace.planning_source == expected.planning_source,
            expected.planning_source,
            response.trace.planning_source,
        )
    if expected.answer_source is not None:
        yield CheckResult(
            "answer_source",
            response.trace.answer_source == expected.answer_source,
            expected.answer_source,
            response.trace.answer_source,
        )

    failed_agents = {
        report.role for report in response.reports if report.status != "completed"
    }
    if expected.failed_agents:
        required_failed = set(expected.failed_agents)
        yield CheckResult(
            "failed_agents",
            required_failed.issubset(failed_agents),
            sorted(role.value for role in required_failed),
            sorted(role.value for role in failed_agents),
        )

    normalized_answer = response.answer.casefold()
    if expected.answer_contains_all:
        yield CheckResult(
            "answer_contains_all",
            all(term.casefold() in normalized_answer for term in expected.answer_contains_all),
            expected.answer_contains_all,
            response.answer,
        )
    if expected.answer_contains_any:
        yield CheckResult(
            "answer_contains_any",
            any(term.casefold() in normalized_answer for term in expected.answer_contains_any),
            expected.answer_contains_any,
            response.answer,
        )
    if expected.answer_excludes:
        yield CheckResult(
            "answer_excludes",
            all(term.casefold() not in normalized_answer for term in expected.answer_excludes),
            expected.answer_excludes,
            response.answer,
        )


@contextmanager
def _inject_fault(fault: FaultMode | None):
    if fault is None:
        yield
        return

    if fault in {FaultMode.PRODUCT_AGENT_TIMEOUT, FaultMode.ORDER_AGENT_TIMEOUT}:
        role = (
            AgentRole.PRODUCT_SCOUT
            if fault == FaultMode.PRODUCT_AGENT_TIMEOUT
            else AgentRole.ORDER_CARE
        )
        original_handler = agents.HANDLERS[role]

        async def timeout_handler(_):
            raise TimeoutError("injected evaluation timeout")

        agents.HANDLERS[role] = timeout_handler
        try:
            yield
        finally:
            agents.HANDLERS[role] = original_handler
        return

    invalid_transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={"choices": [{"message": {"content": "not-json"}}]},
        )
    )
    settings = replace(get_settings(), llm_api_key="evaluation-key")
    if fault == FaultMode.PLANNER_INVALID_JSON:
        original_planner = orchestrator.PLANNER
        orchestrator.PLANNER = DeepSeekPlanner(settings, invalid_transport)
        try:
            yield
        finally:
            orchestrator.PLANNER = original_planner
        return
    if fault == FaultMode.COMPOSER_INVALID_JSON:
        original_composer = orchestrator.ANSWER_COMPOSER
        orchestrator.ANSWER_COMPOSER = DeepSeekAnswerComposer(
            settings,
            invalid_transport,
        )
        try:
            yield
        finally:
            orchestrator.ANSWER_COMPOSER = original_composer
        return
    raise ValueError(f"unsupported fault mode: {fault}")


def _named_check_rate(results: Sequence[AgentCaseResult], name: str) -> float:
    checks = [check for result in results for check in result.checks if check.name == name]
    return _rate(check.passed for check in checks) if checks else 1.0


def _group_metrics(
    results: Sequence[AgentCaseResult],
    key,
) -> dict[str, GroupMetrics]:
    groups: dict[str, list[AgentCaseResult]] = defaultdict(list)
    for result in results:
        groups[key(result)].append(result)
    return {
        name: GroupMetrics(
            cases=len(items),
            passed=sum(item.passed for item in items),
            pass_rate=_rate(item.passed for item in items),
            average_score=round(mean(item.score for item in items), 4),
        )
        for name, items in sorted(groups.items())
    }


def _rate(values) -> float:
    items = list(values)
    return round(sum(bool(value) for value in items) / len(items), 4) if items else 1.0


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * quantile + 0.5)))
    return ordered[index]


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the complete Agent workflow.")
    parser.add_argument(
        "--dataset",
        default="evals/agent_cases.jsonl",
        help="JSONL evaluation dataset.",
    )
    parser.add_argument("--category", action="append", default=[])
    parser.add_argument("--difficulty", choices=[item.value for item in Difficulty])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--live-llm", action="store_true")
    parser.add_argument("--include-passed", action="store_true")
    parser.add_argument("--output", help="Optional JSON report path.")
    parser.add_argument("--baseline", help="Optional previous full JSON report.")
    parser.add_argument("--case-timeout-seconds", type=float, default=30.0)
    args = parser.parse_args()

    cases = load_cases(args.dataset)
    if args.category:
        allowed_categories = set(args.category)
        cases = [case for case in cases if case.category in allowed_categories]
    if args.difficulty:
        cases = [case for case in cases if case.difficulty.value == args.difficulty]
    if args.limit is not None:
        cases = cases[: args.limit]
    if not cases:
        raise SystemExit("no evaluation cases matched the selected filters")

    report = asyncio.run(
        evaluate_dataset(
            cases,
            dataset_name=str(args.dataset),
            local_only=not args.live_llm,
            case_timeout_seconds=args.case_timeout_seconds,
        )
    )
    full_payload = report_to_dict(report, include_passed=True)
    if args.baseline:
        baseline_payload = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        full_payload["comparison"] = compare_with_baseline(
            full_payload,
            baseline_payload,
        )
    display_payload = (
        full_payload
        if args.include_passed
        else {
            **full_payload,
            "results": [
                result for result in full_payload["results"] if not result["passed"]
            ],
        }
    )
    rendered = json.dumps(display_payload, ensure_ascii=False, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(full_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(rendered)


if __name__ == "__main__":
    main()
