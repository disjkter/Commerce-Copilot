from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


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


class AgentReport(BaseModel):
    role: AgentRole
    message: str
    evidence: list[Evidence] = Field(default_factory=list)
    elapsed_ms: float = Field(ge=0)
    status: str = "completed"


class TurnRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None


class TurnTrace(BaseModel):
    planning_source: Literal["deepseek", "local", "local_after_error"]
    selected_agents: list[AgentRole]
    planner_error: str | None = None


class TurnResponse(BaseModel):
    answer: str
    reports: list[AgentReport]
    elapsed_ms: float = Field(ge=0)
    trace: TurnTrace

