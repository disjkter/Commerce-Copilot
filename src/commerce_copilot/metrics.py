"""Low-cardinality, Prometheus-compatible aggregate workflow metrics.

Trace IDs stay in structured logs for single-request diagnostics. Metrics only
use bounded labels so production traffic cannot create an unbounded time series.
"""

from __future__ import annotations

from collections import defaultdict
from threading import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .contracts import LLMCallTrace, TurnResponse


LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)


def _escape_label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_labels(names: tuple[str, ...], values: tuple[str, ...]) -> str:
    if not names:
        return ""
    content = ",".join(
        f'{name}="{_escape_label(value)}"' for name, value in zip(names, values)
    )
    return "{" + content + "}"


def _format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return format(value, ".12g")


class Counter:
    def __init__(self, name: str, help_text: str, labels: tuple[str, ...] = ()) -> None:
        self.name = name
        self.help_text = help_text
        self.labels = labels
        self._values: defaultdict[tuple[str, ...], float] = defaultdict(float)
        self._lock = RLock()

    def inc(self, amount: float = 1, **labels: str) -> None:
        if amount < 0:
            raise ValueError("counter cannot decrease")
        key = self._label_key(labels)
        with self._lock:
            self._values[key] += amount

    def render(self) -> list[str]:
        with self._lock:
            values = list(self._values.items())
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} counter"]
        lines.extend(
            f"{self.name}{_format_labels(self.labels, key)} {_format_number(value)}"
            for key, value in sorted(values)
        )
        return lines

    def reset(self) -> None:
        with self._lock:
            self._values.clear()

    def _label_key(self, values: dict[str, str]) -> tuple[str, ...]:
        if set(values) != set(self.labels):
            raise ValueError(f"labels for {self.name} must be {self.labels}")
        return tuple(str(values[name]) for name in self.labels)


class Histogram:
    def __init__(
        self,
        name: str,
        help_text: str,
        labels: tuple[str, ...] = (),
        buckets: tuple[float, ...] = LATENCY_BUCKETS,
    ) -> None:
        self.name = name
        self.help_text = help_text
        self.labels = labels
        self.buckets = tuple(sorted(buckets))
        self._counts: defaultdict[tuple[str, ...], int] = defaultdict(int)
        self._sums: defaultdict[tuple[str, ...], float] = defaultdict(float)
        self._bucket_counts: defaultdict[tuple[str, ...], list[int]] = defaultdict(
            lambda: [0] * len(self.buckets)
        )
        self._lock = RLock()

    def observe(self, value: float, **labels: str) -> None:
        if value < 0:
            raise ValueError("histogram observation cannot be negative")
        key = self._label_key(labels)
        with self._lock:
            self._counts[key] += 1
            self._sums[key] += value
            counts = self._bucket_counts[key]
            for index, upper_bound in enumerate(self.buckets):
                if value <= upper_bound:
                    counts[index] += 1

    def render(self) -> list[str]:
        with self._lock:
            counts = dict(self._counts)
            sums = dict(self._sums)
            buckets = {key: list(value) for key, value in self._bucket_counts.items()}
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} histogram"]
        for key in sorted(counts):
            base_values = dict(zip(self.labels, key))
            for upper_bound, count in zip(self.buckets, buckets[key]):
                bucket_labels = {**base_values, "le": _format_number(float(upper_bound))}
                bucket_names = (*self.labels, "le")
                bucket_key = tuple(bucket_labels[name] for name in bucket_names)
                lines.append(
                    f"{self.name}_bucket{_format_labels(bucket_names, bucket_key)} {count}"
                )
            inf_labels = {**base_values, "le": "+Inf"}
            inf_names = (*self.labels, "le")
            inf_key = tuple(inf_labels[name] for name in inf_names)
            lines.append(
                f"{self.name}_bucket{_format_labels(inf_names, inf_key)} {counts[key]}"
            )
            lines.append(
                f"{self.name}_sum{_format_labels(self.labels, key)} "
                f"{_format_number(sums[key])}"
            )
            lines.append(
                f"{self.name}_count{_format_labels(self.labels, key)} {counts[key]}"
            )
        return lines

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()
            self._sums.clear()
            self._bucket_counts.clear()

    def _label_key(self, values: dict[str, str]) -> tuple[str, ...]:
        if set(values) != set(self.labels):
            raise ValueError(f"labels for {self.name} must be {self.labels}")
        return tuple(str(values[name]) for name in self.labels)


class MetricsRegistry:
    def __init__(self) -> None:
        self._metrics: list[Counter | Histogram] = []

    def counter(
        self, name: str, help_text: str, labels: tuple[str, ...] = ()
    ) -> Counter:
        metric = Counter(name, help_text, labels)
        self._metrics.append(metric)
        return metric

    def histogram(
        self, name: str, help_text: str, labels: tuple[str, ...] = ()
    ) -> Histogram:
        metric = Histogram(name, help_text, labels)
        self._metrics.append(metric)
        return metric

    def render(self) -> str:
        lines: list[str] = []
        for metric in self._metrics:
            lines.extend(metric.render())
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        for metric in self._metrics:
            metric.reset()


REGISTRY = MetricsRegistry()
TURN_TOTAL = REGISTRY.counter(
    "commerce_turns_total",
    "Total number of completed commerce assistant turns.",
    ("outcome",),
)
TURN_DURATION = REGISTRY.histogram(
    "commerce_turn_duration_seconds",
    "End-to-end turn latency in seconds.",
    ("outcome",),
)
AGENT_TOTAL = REGISTRY.counter(
    "commerce_agent_executions_total",
    "Total number of Agent executions.",
    ("role", "status"),
)
AGENT_DURATION = REGISTRY.histogram(
    "commerce_agent_duration_seconds",
    "Agent execution latency in seconds.",
    ("role",),
)
TOOL_TOTAL = REGISTRY.counter(
    "commerce_tool_calls_total",
    "Total number of domain tool calls.",
    ("tool", "risk", "outcome"),
)
TOOL_DURATION = REGISTRY.histogram(
    "commerce_tool_duration_seconds",
    "Domain tool call latency in seconds.",
    ("tool",),
)
TOOL_RETRIES = REGISTRY.counter(
    "commerce_tool_retries_total",
    "Total number of domain tool retries.",
    ("tool",),
)
LLM_TOTAL = REGISTRY.counter(
    "commerce_llm_calls_total",
    "Total number of successful LLM calls represented in workflow traces.",
    ("stage", "provider", "model"),
)
LLM_DURATION = REGISTRY.histogram(
    "commerce_llm_duration_seconds",
    "LLM call latency in seconds.",
    ("stage", "provider", "model"),
)
LLM_TOKENS = REGISTRY.counter(
    "commerce_llm_tokens_total",
    "Total reported LLM tokens.",
    ("stage", "provider", "model", "direction"),
)
LLM_FALLBACKS = REGISTRY.counter(
    "commerce_llm_fallbacks_total",
    "Total number of LLM stages that used deterministic fallback.",
    ("stage", "reason"),
)
GROUNDING_TOTAL = REGISTRY.counter(
    "commerce_grounding_validations_total",
    "Total number of grounding validations.",
    ("outcome",),
)
GROUNDING_CLAIMS = REGISTRY.counter(
    "commerce_grounding_claims_checked_total",
    "Total number of claims checked by grounding.",
)
GROUNDING_ISSUES = REGISTRY.counter(
    "commerce_grounding_issues_total",
    "Total grounding issues grouped by Agent role.",
    ("role",),
)
BUSINESS_OUTCOMES = REGISTRY.counter(
    "commerce_business_outcomes_total",
    "Total workflow-level business outcomes.",
    ("outcome",),
)
RATE_LIMIT_CHECKS = REGISTRY.counter(
    "commerce_rate_limit_checks_total",
    "Total rate-limit decisions grouped by protected API scope.",
    ("scope", "outcome"),
)
MEMORY_COMPACTIONS = REGISTRY.counter(
    "commerce_memory_compactions_total",
    "Total conversation-memory compaction attempts.",
    ("outcome",),
)
MEMORY_TURNS_COMPACTED = REGISTRY.counter(
    "commerce_memory_turns_compacted_total",
    "Total raw conversation turns merged into structured memory.",
)


def _record_llm(stage: str, trace: LLMCallTrace) -> None:
    labels = {"stage": stage, "provider": trace.provider, "model": trace.model}
    LLM_TOTAL.inc(**labels)
    LLM_DURATION.observe(trace.elapsed_ms / 1000, **labels)
    if trace.input_tokens is not None:
        LLM_TOKENS.inc(trace.input_tokens, **labels, direction="input")
    if trace.output_tokens is not None:
        LLM_TOKENS.inc(trace.output_tokens, **labels, direction="output")


def record_turn_response(response: TurnResponse) -> None:
    """Aggregate a successful workflow without retaining user or trace identifiers."""

    TURN_TOTAL.inc(outcome="success")
    TURN_DURATION.observe(response.elapsed_ms / 1000, outcome="success")

    for report in response.reports:
        role = report.role.value
        AGENT_TOTAL.inc(role=role, status=report.status)
        AGENT_DURATION.observe(report.elapsed_ms / 1000, role=role)
        for tool in report.tool_calls:
            outcome = "success" if tool.success else "failure"
            TOOL_TOTAL.inc(
                tool=tool.tool_name,
                risk=tool.risk_level,
                outcome=outcome,
            )
            TOOL_DURATION.observe(tool.elapsed_ms / 1000, tool=tool.tool_name)
            if tool.retry_count:
                TOOL_RETRIES.inc(tool.retry_count, tool=tool.tool_name)

    grounding = response.trace.grounding
    GROUNDING_TOTAL.inc(outcome="passed" if grounding.passed else "blocked")
    GROUNDING_CLAIMS.inc(grounding.checked_claims)
    for issue in grounding.issues:
        GROUNDING_ISSUES.inc(role=issue.role.value)

    if response.trace.planner_llm is not None:
        _record_llm("planner", response.trace.planner_llm)
    elif response.trace.planning_source == "local_after_error":
        LLM_FALLBACKS.inc(
            stage="planner",
            reason=response.trace.planner_error or "unknown",
        )
    if response.trace.answer_llm is not None:
        _record_llm("composer", response.trace.answer_llm)
    elif response.trace.answer_source == "local_after_error":
        LLM_FALLBACKS.inc(
            stage="composer",
            reason=response.trace.composer_error or "unknown",
        )

    messages = "\n".join(report.message for report in response.reports)
    if any(term in messages for term in ("没有找到", "没有查询到", "暂无")):
        BUSINESS_OUTCOMES.inc(outcome="no_result")
    if any(term in messages for term in ("人工审核", "人工复核")):
        BUSINESS_OUTCOMES.inc(outcome="human_review")


def record_turn_failure(error_type: str, elapsed_ms: float) -> None:
    TURN_TOTAL.inc(outcome="failure")
    TURN_DURATION.observe(elapsed_ms / 1000, outcome="failure")
    BUSINESS_OUTCOMES.inc(outcome=f"error_{error_type}")


def record_rate_limit(scope: str, allowed: bool) -> None:
    RATE_LIMIT_CHECKS.inc(
        scope=scope,
        outcome="allowed" if allowed else "rejected",
    )


def record_memory_compaction(compacted_turns: int, *, failed: bool = False) -> None:
    outcome = "failed" if failed else ("updated" if compacted_turns else "noop")
    MEMORY_COMPACTIONS.inc(outcome=outcome)
    if compacted_turns:
        MEMORY_TURNS_COMPACTED.inc(compacted_turns)


def render_metrics() -> str:
    return REGISTRY.render()


def reset_metrics() -> None:
    """Clear process-local metrics; intended for isolated tests."""

    REGISTRY.reset()

