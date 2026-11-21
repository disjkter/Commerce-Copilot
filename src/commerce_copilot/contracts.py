from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .query_understanding import QueryUnderstanding


class AgentRole(StrEnum):
    PRODUCT_SCOUT = "product_scout"
    ORDER_CARE = "order_care"
    LOGISTICS_TRACKER = "logistics_tracker"
    AFTER_SALES_ADVISOR = "after_sales_advisor"
    CONVERSATION_GUIDE = "conversation_guide"


class Mission(BaseModel):
    role: AgentRole
    instruction: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    source: str
    reference: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class Claim(BaseModel):
    statement: str
    evidence_reference: str
    attribute: str
    expected_value: Any


class ToolCallTrace(BaseModel):
    call_id: str
    tool_name: str
    display_name: str = ""
    risk_level: str
    arguments_hash: str
    elapsed_ms: float = Field(ge=0)
    success: bool
    retry_count: int = Field(ge=0)
    error_type: str | None = None
    policy_decision: Literal["allowed", "denied"] = "allowed"


class AgentReport(BaseModel):
    role: AgentRole
    message: str
    evidence: list[Evidence] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)
    elapsed_ms: float = Field(ge=0)
    status: str = "completed"


class ConversationTurn(BaseModel):
    user_message: str
    assistant_answer: str
    evidence: list[Evidence] = Field(default_factory=list)


class ConversationMemory(BaseModel):
    summary: str = ""
    entities: dict[str, list[str]] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    compacted_through_turn_id: int = Field(default=0, ge=0)
    source_turn_count: int = Field(default=0, ge=0)

    def as_context_turn(self) -> ConversationTurn:
        return ConversationTurn(
            user_message=f"历史会话摘要：{self.summary}",
            assistant_answer="以上是较早会话的结构化压缩记忆。",
            evidence=self.evidence,
        )


class MemoryType(StrEnum):
    SEMANTIC = "semantic"
    EPISODIC = "episodic"
    PROCEDURAL = "procedural"


class MemoryScope(StrEnum):
    CONVERSATION = "conversation"
    USER = "user"


class MemorySourceType(StrEnum):
    RULE_EXTRACTED = "rule_extracted"
    USER_EXPLICIT = "user_explicit"
    TOOL_VERIFIED = "tool_verified"
    LLM_EXTRACTED = "llm_extracted"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    INVALIDATED = "invalidated"


class MemoryCandidate(BaseModel):
    """Validated memory proposal before persistence and conflict resolution."""

    memory_type: MemoryType
    scope: MemoryScope
    key: str = Field(min_length=1, max_length=128)
    value: dict[str, Any]
    source_type: MemorySourceType
    confidence: float = Field(ge=0, le=1)
    evidence_reference: str | None = Field(default=None, max_length=256)


class MemoryItem(MemoryCandidate):
    """Versioned, provenance-aware long-term memory record."""

    memory_id: str
    user_id: str
    conversation_id: str | None = None
    source_turn_id: int | None = None
    status: MemoryStatus = MemoryStatus.ACTIVE
    supersedes_memory_id: str | None = None
    valid_from: datetime
    valid_to: datetime | None = None
    created_at: datetime


class UserRecord(BaseModel):
    user_id: str
    username: str
    password_hash: str


class UserPreferences(BaseModel):
    max_budget_cents: int | None = Field(default=None, ge=0)
    preferred_categories: list[str] = Field(default_factory=list, max_length=10)
    preferred_brands: list[str] = Field(default_factory=list, max_length=10)
    scenarios: list[str] = Field(default_factory=list, max_length=10)
    priorities: list[str] = Field(default_factory=list, max_length=10)

    @field_validator(
        "preferred_categories",
        "preferred_brands",
        "scenarios",
        "priorities",
        mode="before",
    )
    @classmethod
    def normalize_terms(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("preference terms must be a list")
        normalized: list[str] = []
        for item in value:
            term = str(item).strip()
            if term and term not in normalized:
                normalized.append(term)
        return normalized


class TurnRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class GroundingIssue(BaseModel):
    role: AgentRole
    statement: str
    reason: str


class GroundingSummary(BaseModel):
    passed: bool
    checked_claims: int = Field(ge=0)
    issues: list[GroundingIssue] = Field(default_factory=list)


class StageTiming(BaseModel):
    stage: str
    elapsed_ms: float = Field(ge=0)
    agent_role: AgentRole | None = None


class LLMCallTrace(BaseModel):
    provider: str
    model: str
    elapsed_ms: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class MemoryTrace(BaseModel):
    summary_loaded: bool = False
    recent_turns: int = Field(default=0, ge=0)
    compacted_turns: int = Field(default=0, ge=0)
    source_turns: int = Field(default=0, ge=0)


class TurnTrace(BaseModel):
    trace_id: str = ""
    planning_source: Literal["llm", "local", "local_after_error"]
    answer_source: Literal["llm", "local", "local_after_error"]
    planner_llm: LLMCallTrace | None = None
    answer_llm: LLMCallTrace | None = None
    selected_agents: list[AgentRole]
    planner_error: str | None = None
    composer_error: str | None = None
    applied_preferences: dict[str, Any] = Field(default_factory=dict)
    query_understanding: QueryUnderstanding | None = None
    grounding: GroundingSummary
    memory: MemoryTrace = Field(default_factory=MemoryTrace)
    stage_timings: list[StageTiming] = Field(default_factory=list)


class TurnResponse(BaseModel):
    conversation_id: str
    answer: str
    reports: list[AgentReport]
    elapsed_ms: float = Field(ge=0)
    trace: TurnTrace


class TurnTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TurnTask(BaseModel):
    task_id: str
    status: TurnTaskStatus
    request: TurnRequest
    response: TurnResponse | None = None
    error_type: str | None = None
    attempt_count: int = Field(default=0, ge=0)
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

