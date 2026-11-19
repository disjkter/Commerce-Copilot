import json
import re
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from .budget import extract_budget_cents
from .catalog import Product
from .config import Settings, get_settings
from .contracts import AgentRole, ConversationTurn, Evidence, LLMCallTrace, Mission
from .entity_resolution import resolve_product_mentions
from .llm_gateway import LLMClient, OpenAICompatibleClient
from .query_understanding import (
    QueryConstraints,
    QueryIntent,
    QueryUnderstanding,
    ResponseFocus,
    detect_response_focus,
)


PRODUCT_TERMS = ("推荐", "商品", "价格", "预算", "耳机", "电脑", "手机", "键盘", "鼠标", "买")
CARE_TERMS = (
    "退款",
    "退货",
    "换货",
    "售后",
    "取消订单",
    "订单状态",
    "下单时间",
    "买了什么",
    "订单明细",
    "发货规则",
    "发货政策",
)
LOGISTICS_TERMS = (
    "物流",
    "快递",
    "包裹",
    "运单",
    "到哪",
    "送到",
    "送达",
    "配送",
    "派送",
    "揽收",
    "签收",
    "物流异常",
    "物流不动",
    "没更新",
)
LOGISTICS_FOLLOW_UP_TERMS = ("什么时候到", "到哪了", "有更新吗", "多久送到")
AFTER_SALES_ELIGIBILITY_TERMS = (
    "能退",
    "可以退",
    "还能退",
    "能不能退",
    "是否能退",
    "退货资格",
    "申请退货",
    "我要退货",
    "办理退货",
    "提交退货",
    "确认退货",
    "退货进度",
    "售后进度",
    "退货申请状态",
    "工单状态",
    "取消退货申请",
    "撤销退货申请",
    "取消售后",
    "过了退货期",
    "退货期限",
)
TRACKING_NUMBER_PATTERN = re.compile(r"\b[A-Z]{2,4}\d{8,20}\b", re.IGNORECASE)
ORDER_ID_PATTERN = re.compile(r"\bORD-\d{4}-\d{4}\b", re.IGNORECASE)
RETURN_REQUEST_PATTERN = re.compile(r"\bRET-[A-F0-9]{12}\b", re.IGNORECASE)
ACCOUNT_ORDER_TERMS = (
    "我的订单",
    "我的包裹",
    "我买的",
    "购买记录",
    "最近订单",
    "全部订单",
)
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
    source: Literal["llm", "local", "local_after_error"]
    understanding: QueryUnderstanding
    error: str | None = None
    llm_trace: LLMCallTrace | None = None


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
只能使用以下 role：product_scout、order_care、logistics_tracker、after_sales_advisor、conversation_guide。
product_scout 处理选购、商品、价格和比较；order_care 处理订单详情、退款、退换货和售后政策；
logistics_tracker 处理包裹轨迹、当前位置、预计送达和物流异常；
after_sales_advisor 根据订单、签收时间和商品规则判断退货资格；
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


class LLMPlanner:
    def __init__(
        self,
        settings: Settings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        client: LLMClient | None = None,
    ):
        self.settings = settings or get_settings()
        self.transport = transport
        self.client = client or OpenAICompatibleClient(self.settings, transport)

    async def create_plan(
        self,
        message: str,
        history: list[ConversationTurn] | None = None,
        catalog_products: list[Product] | None = None,
    ) -> Plan:
        recent_history = history or []
        local_decision = local_plan(
            message,
            recent_history,
            catalog_products=catalog_products,
        )
        if not self.settings.llm_api_key:
            return local_decision
        if self.transport is None and _can_use_local_fast_path(local_decision):
            return local_decision
        try:
            model_plan, call_trace = await self._request_model(message, recent_history)
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
                source="llm",
                understanding=model_plan.understanding,
                llm_trace=call_trace,
            )
        except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            return local_decision.model_copy(
                update={"source": "local_after_error", "error": type(exc).__name__}
            )

    async def _request_model(
        self,
        message: str,
        history: list[ConversationTurn],
    ) -> tuple[ModelPlan, LLMCallTrace]:
        model = self.settings.planner_model or self.settings.llm_model
        response = await self.client.generate_json(
            [
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
            model=model,
            temperature=0,
            max_tokens=600,
            timeout_seconds=12,
        )
        return (
            ModelPlan.model_validate(response.data),
            LLMCallTrace(
                provider=response.provider,
                model=response.model,
                elapsed_ms=response.elapsed_ms,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            ),
        )


# Backwards-compatible import for existing integrations; new code should use LLMPlanner.
DeepSeekPlanner = LLMPlanner


def local_plan(
    message: str,
    history: list[ConversationTurn] | None = None,
    *,
    catalog_products: list[Product] | None = None,
) -> Plan:
    normalized = message.strip()
    recent_history = history or []
    previous = _select_context_message(normalized, recent_history)
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
    mentioned_products = resolve_product_mentions(normalized, catalog_products or [])
    selected_references = _unique(
        [*selected_references, *(product.product_id for product in mentioned_products)]
    )
    invalid_positions = [
        position for position in positions if position > len(previous_products)
    ]
    accepts_relaxation = bool(previous_products) and any(
        term in normalized for term in RELAXATION_ACCEPTANCE_TERMS
    )
    if accepts_relaxation and not selected_references:
        selected_references = [previous_products[0].reference]
    has_reference = bool(previous) and any(
        term in normalized
        for term in (
            REFERENCE_TERMS
            + PRODUCT_FOLLOW_UP_TERMS
            + LOGISTICS_FOLLOW_UP_TERMS
        )
    )
    contextual_text = f"{previous}\n当前追问：{normalized}" if has_reference else normalized
    product_intent = bool(mentioned_products) or bool(positions and previous_products) or any(
        term in normalized for term in PRODUCT_TERMS
    )
    if any(term in normalized for term in ACCOUNT_ORDER_TERMS) and not any(
        term in normalized for term in ("推荐", "再买", "相似商品")
    ):
        product_intent = False
    product_intent = product_intent or (
        has_reference
        and any(term in previous for term in PRODUCT_TERMS)
        and any(term in normalized for term in REFERENCE_TERMS + PRODUCT_FOLLOW_UP_TERMS)
    )
    logistics_intent = any(term in normalized for term in LOGISTICS_TERMS) or bool(
        TRACKING_NUMBER_PATTERN.search(normalized)
    )
    logistics_intent = logistics_intent or (
        has_reference
        and any(term in previous for term in LOGISTICS_TERMS)
        and any(
            term in normalized
            for term in REFERENCE_TERMS + LOGISTICS_FOLLOW_UP_TERMS
        )
    )
    after_sales_intent = any(
        term in normalized for term in AFTER_SALES_ELIGIBILITY_TERMS
    ) or bool(RETURN_REQUEST_PATTERN.search(normalized))
    care_intent = any(term in normalized for term in CARE_TERMS)
    care_intent = care_intent or (
        ("订单" in normalized or any(term in normalized for term in ACCOUNT_ORDER_TERMS))
        and not logistics_intent
    )
    # Eligibility is a deterministic business decision handled by the dedicated
    # after-sales agent. Avoid also dispatching the policy/order agent merely
    # because the same sentence contains the word "退货".
    if after_sales_intent:
        care_intent = False
    care_intent = care_intent or (
        has_reference
        and ("订单" in previous or any(term in previous for term in CARE_TERMS))
        and any(term in normalized for term in REFERENCE_TERMS)
        and not logistics_intent
    )
    focus = detect_response_focus(normalized)
    intents: list[QueryIntent] = []
    if product_intent:
        intents.append(QueryIntent.PRODUCT)
    if care_intent:
        intents.append(QueryIntent.ORDER_CARE)
    if logistics_intent:
        intents.append(QueryIntent.LOGISTICS)
    if after_sales_intent:
        intents.append(QueryIntent.AFTER_SALES)
    if not intents:
        intents.append(QueryIntent.CONVERSATION)
    local_budget = extract_budget_cents(normalized)
    understanding = QueryUnderstanding(
        intents=intents,
        focus=focus,
        constraints=QueryConstraints(max_price_cents=local_budget),
        referenced_products=selected_references,
        confidence=(
            0.9
            if product_intent or care_intent or logistics_intent or after_sales_intent
            else 0.75
        ),
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
    if logistics_intent:
        missions.append(
            Mission(role=AgentRole.LOGISTICS_TRACKER, instruction=contextual_text)
        )
    if after_sales_intent:
        missions.append(
            Mission(role=AgentRole.AFTER_SALES_ADVISOR, instruction=contextual_text)
        )
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


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


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


def _select_context_message(
    message: str,
    history: list[ConversationTurn],
) -> str:
    if not history:
        return ""
    needs_context = bool(_ordinal_positions(message)) or any(
        term in message
        for term in REFERENCE_TERMS + PRODUCT_FOLLOW_UP_TERMS + LOGISTICS_FOLLOW_UP_TERMS
    )
    if not needs_context:
        return history[-1].user_message
    for turn in reversed(history):
        if turn.evidence:
            return turn.user_message
        if turn.user_message.startswith("历史会话摘要："):
            return turn.user_message
        if ORDER_ID_PATTERN.search(turn.user_message) or TRACKING_NUMBER_PATTERN.search(
            turn.user_message
        ):
            return turn.user_message
        if any(
            term in turn.user_message
            for term in PRODUCT_TERMS + CARE_TERMS + LOGISTICS_TERMS
        ):
            return turn.user_message
    return history[-1].user_message


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
