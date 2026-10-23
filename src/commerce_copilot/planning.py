import json
import re
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from .budget import extract_budget_cents
from .config import Settings, get_settings
from .contracts import AgentRole, ConversationTurn, Evidence, Mission
from .llm_json import parse_json_object
from .query_understanding import (
    QueryConstraints,
    QueryIntent,
    QueryUnderstanding,
    ResponseFocus,
    detect_response_focus,
)


PRODUCT_TERMS = ("推荐", "商品", "价格", "预算", "耳机", "电脑", "手机", "键盘", "鼠标", "买")
CARE_TERMS = ("订单", "物流", "发货", "退款", "退货", "换货", "售后")
REFERENCE_TERMS = ("它", "这个", "那个", "这款", "那款", "第一款", "第二款", "第三款", "刚才", "上一个", "继续")
RELAXATION_ACCEPTANCE_TERMS = ("可以", "接受", "提高预算", "放宽预算")
PRODUCT_FOLLOW_UP_TERMS = (
    "便宜",
    "贵",
    "换一",
    "其他",
    "哪款",
    "怎么样",
    *RELAXATION_ACCEPTANCE_TERMS,
)


class Plan(BaseModel):
    missions: list[Mission] = Field(min_length=1)
    source: Literal["deepseek", "local", "local_after_error"]
    understanding: QueryUnderstanding
    error: str | None = None


class ModelTask(BaseModel):
    role: AgentRole
    instruction: str = Field(min_length=1, max_length=500)
    product_query: str | None = Field(default=None, max_length=200)
    max_price_yuan: float | None = Field(default=None, ge=0, le=1_000_000)
    preferences: list[str] = Field(default_factory=list, max_length=10)
    selected_product_references: list[str] = Field(default_factory=list, max_length=3)
    relax_budget: bool = False


class ModelPlan(BaseModel):
    understanding: QueryUnderstanding
    tasks: list[ModelTask] = Field(min_length=1, max_length=3)


SYSTEM_PROMPT = """你是电商助手的任务规划器。请把用户消息拆成可并行执行的任务。
只能使用以下 role：product_scout、order_care、conversation_guide。
product_scout 处理选购、商品、价格和比较；order_care 处理订单、物流、退款和退换货；
conversation_guide 只处理问候或能力咨询。同一个 role 最多出现一次。
预算统一输出为人民币元。不要虚构用户没有表达的约束。
输入可能包含 conversation_history 和 current_message。仅在当前消息存在指代或追问时使用历史，
例如“它”“那款”“再便宜一点”；不得从无关历史中继承条件或虚构上下文。
如果用户用“第一款、第二款”等指代历史商品，只能从历史 evidence 中复制对应的 reference 到
selected_product_references，不得编造商品编号。
如果历史 evidence 的 match_status 为 near_miss，且用户明确接受提高或放宽预算，应复制该 reference，
设置 relax_budget=true，并且不要继续输出旧的 max_price_yuan。
同时输出 understanding：intents、focus、constraints、referenced_products、confidence、needs_clarification
和 clarification_question。focus 只能是 summary、advantages、price、rating、stock、comparison。
即使用户使用同义表达、口语、省略或轻微错别字，也应按语义识别 focus，不要只匹配示例词。
confidence 表示当前消息结合历史后能否被可靠执行。缺少执行所必需的信息时设置 needs_clarification=true，
并给出一个简短、具体的问题；不要因为表达口语化就降低置信度。
必须只输出 JSON，格式示例：
{"understanding":{"intents":["product"],"focus":"summary","constraints":{"max_price_cents":50000,"preferences":["通勤","无线"],"excluded_terms":[]},"referenced_products":[],"confidence":0.95,"needs_clarification":false,"clarification_question":null},"tasks":[{"role":"product_scout","instruction":"寻找通勤耳机","product_query":"通勤 无线 耳机","max_price_yuan":500,"preferences":["通勤","无线"]}]}
"""


class DeepSeekPlanner:
    def __init__(self, settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings or get_settings()
        self.transport = transport

    async def create_plan(
        self,
        message: str,
        history: list[ConversationTurn] | None = None,
    ) -> Plan:
        recent_history = history or []
        local_decision = local_plan(message, recent_history)
        if not self.settings.llm_api_key:
            return local_decision
        if self.transport is None and _can_use_local_fast_path(local_decision):
            return local_decision
        try:
            model_plan = await self._request_model(message, recent_history)
            allowed_product_references = {
                item.reference
                for turn in recent_history
                for item in turn.evidence
                if item.source == "local_catalog"
            }
            if _requires_clarification(model_plan.understanding):
                question = (
                    model_plan.understanding.clarification_question
                    or "为了准确处理，请再补充一下你的具体需求。"
                )
                missions = [
                    Mission(
                        role=AgentRole.CONVERSATION_GUIDE,
                        instruction=question,
                        parameters={"clarification_question": question},
                    )
                ]
            else:
                missions = [
                    _task_to_mission(
                        task,
                        allowed_product_references,
                        model_plan.understanding,
                    )
                    for task in _deduplicate(model_plan.tasks)
                ]
            return Plan(
                missions=missions,
                source="deepseek",
                understanding=model_plan.understanding,
            )
        except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            return local_decision.model_copy(
                update={"source": "local_after_error", "error": type(exc).__name__}
            )

    async def _request_model(
        self,
        message: str,
        history: list[ConversationTurn],
    ) -> ModelPlan:
        endpoint = f"{self.settings.llm_base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "conversation_history": [
                                turn.model_dump(mode="json") for turn in history
                            ],
                            "current_message": message,
                        },
                        ensure_ascii=False,
                    ),
                },
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
        return ModelPlan.model_validate(parse_json_object(content))


def local_plan(
    message: str,
    history: list[ConversationTurn] | None = None,
) -> Plan:
    normalized = message.strip()
    recent_history = history or []
    previous = recent_history[-1].user_message if recent_history else ""
    positions = _ordinal_positions(normalized)
    required_product_count = max(positions, default=1)
    previous_products = _previous_product_evidence(
        recent_history,
        minimum_count=required_product_count,
    )
    selected_references = [
        previous_products[position - 1].reference
        for position in positions
        if position <= len(previous_products)
    ]
    invalid_positions = [
        position for position in positions if position > len(previous_products)
    ]
    accepts_relaxation = bool(previous_products) and any(
        term in normalized for term in RELAXATION_ACCEPTANCE_TERMS
    )
    if accepts_relaxation and not selected_references:
        selected_references = [previous_products[0].reference]
    has_reference = bool(previous) and any(
        term in normalized for term in REFERENCE_TERMS + PRODUCT_FOLLOW_UP_TERMS
    )
    contextual_text = f"{previous}\n当前追问：{normalized}" if has_reference else normalized
    product_intent = bool(positions and previous_products) or any(
        term in normalized for term in PRODUCT_TERMS
    )
    product_intent = product_intent or (
        has_reference
        and any(term in previous for term in PRODUCT_TERMS)
        and any(term in normalized for term in REFERENCE_TERMS + PRODUCT_FOLLOW_UP_TERMS)
    )
    care_intent = any(term in normalized for term in CARE_TERMS)
    care_intent = care_intent or (
        has_reference
        and any(term in previous for term in CARE_TERMS)
        and any(term in normalized for term in REFERENCE_TERMS)
    )
    focus = detect_response_focus(normalized)
    intents: list[QueryIntent] = []
    if product_intent:
        intents.append(QueryIntent.PRODUCT)
    if care_intent:
        intents.append(QueryIntent.ORDER_CARE)
    if not intents:
        intents.append(QueryIntent.CONVERSATION)
    local_budget = extract_budget_cents(normalized)
    understanding = QueryUnderstanding(
        intents=intents,
        focus=focus,
        constraints=QueryConstraints(max_price_cents=local_budget),
        referenced_products=selected_references,
        confidence=0.9 if product_intent or care_intent else 0.75,
    )
    missions: list[Mission] = []
    if product_intent:
        budget = extract_budget_cents(normalized)
        if budget is None and has_reference and not accepts_relaxation:
            budget = extract_budget_cents(previous)
        parameters = {"product_query": contextual_text}
        parameters["response_focus"] = focus.value
        if selected_references:
            parameters["selected_references"] = selected_references
        if accepts_relaxation:
            parameters["relax_budget"] = True
        if invalid_positions:
            missing = "、".join(str(position) for position in invalid_positions)
            parameters["selection_error"] = f"上一轮结果中没有第 {missing} 款商品。"
        if budget is not None:
            parameters["max_price_cents"] = budget
        if "便宜" in normalized:
            parameters["sort_by"] = "price_asc"
        missions.append(
            Mission(
                role=AgentRole.PRODUCT_SCOUT,
                instruction=contextual_text,
                parameters=parameters,
            )
        )
    if care_intent:
        missions.append(Mission(role=AgentRole.ORDER_CARE, instruction=contextual_text))
    if not missions:
        missions.append(Mission(role=AgentRole.CONVERSATION_GUIDE, instruction=normalized))
    return Plan(missions=missions, source="local", understanding=understanding)


def _task_to_mission(
    task: ModelTask,
    allowed_product_references: set[str] | None = None,
    understanding: QueryUnderstanding | None = None,
) -> Mission:
    parameters: dict[str, object] = {}
    if task.role == AgentRole.PRODUCT_SCOUT:
        parameters["product_query"] = task.product_query or task.instruction
        parameters["response_focus"] = (
            understanding.focus.value if understanding else ResponseFocus.SUMMARY.value
        )
        if task.max_price_yuan is not None:
            parameters["max_price_cents"] = round(task.max_price_yuan * 100)
        if task.preferences:
            parameters["preferences"] = task.preferences
        selected_references = [
            reference
            for reference in task.selected_product_references
            if reference in (allowed_product_references or set())
        ]
        if selected_references:
            parameters["selected_references"] = selected_references
        if task.relax_budget:
            parameters["relax_budget"] = True
    return Mission(role=task.role, instruction=task.instruction, parameters=parameters)


def _deduplicate(tasks: list[ModelTask]) -> list[ModelTask]:
    by_role: dict[AgentRole, ModelTask] = {}
    for task in tasks:
        by_role.setdefault(task.role, task)
    return list(by_role.values())


def _requires_clarification(understanding: QueryUnderstanding) -> bool:
    if understanding.needs_clarification:
        return True
    return (
        understanding.confidence < 0.55
        and QueryIntent.CONVERSATION not in understanding.intents
    )


def _can_use_local_fast_path(plan: Plan) -> bool:
    """Avoid a remote planning call when deterministic routing is already reliable."""
    return (
        plan.understanding.confidence >= 0.85
        and all(
            mission.role != AgentRole.CONVERSATION_GUIDE
            for mission in plan.missions
        )
    )


def _ordinal_positions(text: str) -> list[int]:
    number_map = {"一": 1, "二": 2, "三": 3, "1": 1, "2": 2, "3": 3}
    return [number_map[value] for value in re.findall(r"第\s*([一二三123])\s*款", text)]


def _previous_product_evidence(
    history: list[ConversationTurn],
    *,
    minimum_count: int = 1,
) -> list[Evidence]:
    for turn in reversed(history):
        products = [item for item in turn.evidence if item.source == "local_catalog"]
        if len(products) >= minimum_count:
            return products
    return []
