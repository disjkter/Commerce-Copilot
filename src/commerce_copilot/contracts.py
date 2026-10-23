from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .query_understanding import QueryUnderstanding


class AgentRole(StrEnum):
    PRODUCT_SCOUT = "product_scout"
    ORDER_CARE = "order_care"
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


class AgentReport(BaseModel):
    role: AgentRole
    message: str
    evidence: list[Evidence] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    elapsed_ms: float = Field(ge=0)
    status: str = "completed"


class ConversationTurn(BaseModel):
    user_message: str
    assistant_answer: str
    evidence: list[Evidence] = Field(default_factory=list)


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


class TurnTrace(BaseModel):
    trace_id: str = ""
    planning_source: Literal["deepseek", "local", "local_after_error"]
    answer_source: Literal["deepseek", "local", "local_after_error"]
    selected_agents: list[AgentRole]
    planner_error: str | None = None
    composer_error: str | None = None
    applied_preferences: dict[str, Any] = Field(default_factory=dict)
    query_understanding: QueryUnderstanding | None = None
    grounding: GroundingSummary
    stage_timings: list[StageTiming] = Field(default_factory=list)


class TurnResponse(BaseModel):
    conversation_id: str
    answer: str
    reports: list[AgentReport]
    elapsed_ms: float = Field(ge=0)
    trace: TurnTrace

