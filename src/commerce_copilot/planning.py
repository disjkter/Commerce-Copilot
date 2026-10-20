import json
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from .budget import extract_budget_cents
from .config import Settings, get_settings
from .contracts import AgentRole, Mission


PRODUCT_TERMS = ("推荐", "商品", "价格", "预算", "耳机", "电脑", "手机", "键盘", "鼠标", "买")
CARE_TERMS = ("订单", "物流", "发货", "退款", "退货", "换货", "售后")


class Plan(BaseModel):
    missions: list[Mission] = Field(min_length=1)
    source: Literal["deepseek", "local", "local_after_error"]
    error: str | None = None


class ModelTask(BaseModel):
    role: AgentRole
    instruction: str = Field(min_length=1, max_length=500)
    product_query: str | None = Field(default=None, max_length=200)
    max_price_yuan: float | None = Field(default=None, ge=0, le=1_000_000)
    preferences: list[str] = Field(default_factory=list, max_length=10)


class ModelPlan(BaseModel):
    tasks: list[ModelTask] = Field(min_length=1, max_length=3)


SYSTEM_PROMPT = """你是电商助手的任务规划器。请把用户消息拆成可并行执行的任务。
只能使用以下 role：product_scout、order_care、conversation_guide。
product_scout 处理选购、商品、价格和比较；order_care 处理订单、物流、退款和退换货；
conversation_guide 只处理问候或能力咨询。同一个 role 最多出现一次。
预算统一输出为人民币元。不要虚构用户没有表达的约束。
必须只输出 JSON，格式示例：
{"tasks":[{"role":"product_scout","instruction":"寻找通勤耳机","product_query":"通勤 无线 耳机","max_price_yuan":500,"preferences":["通勤","无线"]}]}
"""


class DeepSeekPlanner:
    def __init__(self, settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings or get_settings()
        self.transport = transport

    async def create_plan(self, message: str) -> Plan:
        if not self.settings.llm_api_key:
            return local_plan(message)
        try:
            model_plan = await self._request_model(message)
            return Plan(
                missions=[_task_to_mission(task) for task in _deduplicate(model_plan.tasks)],
                source="deepseek",
            )
        except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            fallback = local_plan(message)
            return fallback.model_copy(
                update={"source": "local_after_error", "error": type(exc).__name__}
            )

    async def _request_model(self, message: str) -> ModelPlan:
        endpoint = f"{self.settings.llm_base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": message},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": 600,
        }
        timeout = httpx.Timeout(12.0, connect=5.0)
        async with httpx.AsyncClient(transport=self.transport, timeout=timeout) as client:
            response = await client.post(
                endpoint,
                headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                json=payload,
            )
            response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return ModelPlan.model_validate(json.loads(content))


def local_plan(message: str) -> Plan:
    normalized = message.strip()
    missions: list[Mission] = []
    if any(term in normalized for term in PRODUCT_TERMS):
        budget = extract_budget_cents(normalized)
        parameters = {"product_query": normalized}
        if budget is not None:
            parameters["max_price_cents"] = budget
        missions.append(
            Mission(
                role=AgentRole.PRODUCT_SCOUT,
                instruction=normalized,
                parameters=parameters,
            )
        )
    if any(term in normalized for term in CARE_TERMS):
        missions.append(Mission(role=AgentRole.ORDER_CARE, instruction=normalized))
    if not missions:
        missions.append(Mission(role=AgentRole.CONVERSATION_GUIDE, instruction=normalized))
    return Plan(missions=missions, source="local")


def _task_to_mission(task: ModelTask) -> Mission:
    parameters: dict[str, object] = {}
    if task.role == AgentRole.PRODUCT_SCOUT:
        parameters["product_query"] = task.product_query or task.instruction
        if task.max_price_yuan is not None:
            parameters["max_price_cents"] = round(task.max_price_yuan * 100)
        if task.preferences:
            parameters["preferences"] = task.preferences
    return Mission(role=task.role, instruction=task.instruction, parameters=parameters)


def _deduplicate(tasks: list[ModelTask]) -> list[ModelTask]:
    by_role: dict[AgentRole, ModelTask] = {}
    for task in tasks:
        by_role.setdefault(task.role, task)
    return list(by_role.values())
