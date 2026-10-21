import asyncio
import re
from collections.abc import Awaitable, Callable
from time import perf_counter

from .budget import extract_budget_cents
from .catalog import HybridSearchHit
from .comparison import compare_products
from .contracts import AgentReport, AgentRole, Claim, Evidence, Mission
from .database import get_catalog
from .personalization import needs_product_clarification
from .retrieval import get_retriever


AgentDraft = tuple[str, list[Evidence], list[Claim]]
AgentHandler = Callable[[Mission], Awaitable[AgentDraft]]
ORDER_ID_PATTERN = re.compile(r"ORD-\d{4}-\d{4}", re.IGNORECASE)
STATUS_LABELS = {
    "pending": "待处理",
    "paid": "已付款",
    "shipped": "已发货",
    "delivered": "已送达",
    "cancelled": "已取消",
}
RETURN_TERMS = ("退款", "退货", "换货", "能退", "可以退")
SHIPPING_TERMS = ("物流", "发货", "配送")
STATUS_QUERY_TERMS = ("状态", "到哪", "进度", "物流", "发货了吗", "送达了吗")
ITEM_QUERY_TERMS = ("商品", "买了什么", "买的什么", "什么东西", "包含什么")
PLACED_AT_QUERY_TERMS = ("下单时间", "什么时候下单", "哪天下单")
CURRENT_TURN_MARKER = "当前追问："


async def product_scout(mission: Mission) -> AgentDraft:
    selection_error = mission.parameters.get("selection_error")
    if isinstance(selection_error, str):
        return selection_error, [], []

    product_query = str(mission.parameters.get("product_query", mission.instruction))
    configured_budget = mission.parameters.get("max_price_cents")
    budget_cents = (
        int(configured_budget)
        if isinstance(configured_budget, (int, float))
        else extract_budget_cents(mission.instruction)
    )
    selected_references = mission.parameters.get("selected_references")
    if not selected_references and needs_product_clarification(product_query):
        return (
            "你想购买哪一类商品？还可以告诉我预算、使用场景，或者最关注的功能，我再为你筛选。",
            [],
            [],
        )
    if isinstance(selected_references, list) and selected_references:
        products = await asyncio.gather(
            *(
                asyncio.to_thread(get_catalog().get_product, str(reference))
                for reference in selected_references
            )
        )
        hits = [
            HybridSearchHit(
                product=product,
                fused_score=0,
                retrieval_sources=["conversation_memory"],
            )
            for product in products
            if product is not None
            and product.stock > 0
            and (budget_cents is None or product.price_cents <= budget_cents)
        ]
    else:
        hits = await asyncio.to_thread(
            get_retriever().search,
            product_query,
            max_price_cents=budget_cents,
            limit=3,
        )
    if mission.parameters.get("sort_by") == "price_asc":
        hits.sort(key=lambda hit: (hit.product.price_cents, -hit.product.rating))
    if not hits:
        budget_note = f"且价格不超过 {budget_cents / 100:.0f} 元" if budget_cents else ""
        return (
            f"商品发现任务已完成，但目录中没有找到符合关键词{budget_note}的在售商品。",
            [],
            [],
        )

    applied_preferences = mission.parameters.get("applied_preferences")
    if isinstance(applied_preferences, dict) and applied_preferences:
        applied_values = [
            f"预算不超过 {value / 100:.0f} 元"
            if key == "max_budget_cents" and isinstance(value, (int, float))
            else "、".join(str(item) for item in value)
            for key, value in applied_preferences.items()
            if value
        ]
        lines = [f"已结合你的账户偏好：{'；'.join(applied_values)}。"]
    else:
        lines = []

    if isinstance(selected_references, list) and len(selected_references) > 1:
        lines.append("以下是你指定商品的对比：")
    elif isinstance(selected_references, list):
        lines.append("你上一轮提到的商品详情如下：")
    else:
        lines.append("商品发现任务已完成，找到以下候选：")
    evidence: list[Evidence] = []
    claims: list[Claim] = []
    for index, hit in enumerate(hits, start=1):
        product = hit.product
        lines.append(
            f"{index}. {product.title}｜{product.price_cents / 100:.2f} 元｜"
            f"评分 {product.rating:.1f}｜库存 {product.stock}"
        )
        evidence.append(
            Evidence(
                source="local_catalog",
                reference=product.product_id,
                attributes={
                    "title": product.title,
                    "price_cents": product.price_cents,
                    "rating": product.rating,
                    "stock": product.stock,
                    "retrieval_sources": hit.retrieval_sources,
                },
            )
        )
        claim_values = {
            "title": product.title,
            "price_cents": product.price_cents,
            "rating": product.rating,
            "stock": product.stock,
        }
        claims.extend(
            Claim(
                statement=f"{product.product_id} 的 {attribute} 为 {value}",
                evidence_reference=product.product_id,
                attribute=attribute,
                expected_value=value,
            )
            for attribute, value in claim_values.items()
        )
    comparison_requested = (
        isinstance(selected_references, list) and len(selected_references) > 1
    ) or any(term in mission.instruction for term in ("对比", "比较"))
    if comparison_requested and len(hits) > 1:
        comparison = compare_products([hit.product for hit in hits])
        lines.extend(("", comparison.message))
        evidence.append(comparison.evidence)
        claims.extend(comparison.claims)
    return "\n".join(lines), evidence, claims


async def order_care(mission: Mission) -> AgentDraft:
    text = mission.instruction
    current_text = _current_turn_text(text)
    order_match = ORDER_ID_PATTERN.search(text)
    messages: list[str] = []
    evidence: list[Evidence] = []
    claims: list[Claim] = []

    if order_match:
        order_id = order_match.group(0).upper()
        configured_user_id = mission.parameters.get("user_id")
        user_id = str(configured_user_id) if configured_user_id else None
        order = await asyncio.to_thread(
            get_catalog().get_order,
            order_id,
            user_id=user_id,
        )
        if order is None:
            messages.append(f"没有查询到订单 {order_id}，请检查订单号是否正确。")
        else:
            status_label = STATUS_LABELS.get(order.status, order.status)
            item_summary = "、".join(
                f"{item.title}×{item.quantity}" for item in order.items
            ) or "无商品明细"
            all_attributes = {
                "status_label": status_label,
                "placed_at": order.placed_at,
                "item_summary": item_summary,
            }
            attributes = _select_order_attributes(current_text, all_attributes)
            messages.append(_format_order_message(order.order_id, attributes))
            evidence.append(
                Evidence(
                    source="local_orders",
                    reference=order.order_id,
                    attributes=attributes,
                )
            )
            claims.extend(
                Claim(
                    statement=f"订单 {order.order_id} 的 {attribute} 为 {value}",
                    evidence_reference=order.order_id,
                    attribute=attribute,
                    expected_value=value,
                )
                for attribute, value in attributes.items()
            )

    policy_topic = _policy_topic(current_text)
    if policy_topic:
        policy = await asyncio.to_thread(get_catalog().get_policy, policy_topic)
        if policy:
            reference = f"policy:{policy.topic}"
            messages.append(f"{policy.title}：{policy.body}")
            evidence.append(
                Evidence(
                    source="service_policy",
                    reference=reference,
                    attributes={
                        "title": policy.title,
                        "body": policy.body,
                        "effective_from": policy.effective_from,
                    },
                )
            )
            claims.extend(
                (
                    Claim(
                        statement=policy.title,
                        evidence_reference=reference,
                        attribute="title",
                        expected_value=policy.title,
                    ),
                    Claim(
                        statement=policy.body,
                        evidence_reference=reference,
                        attribute="body",
                        expected_value=policy.body,
                    ),
                )
            )

    if not order_match:
        messages.append("如需查询具体订单状态，请提供格式为 ORD-2026-1001 的订单号。")
    if not messages:
        messages.append("请提供订单号，或说明需要查询物流、退货还是退款规则。")
    return "\n".join(messages), evidence, claims


async def conversation_guide(mission: Mission) -> AgentDraft:
    await asyncio.sleep(0)
    return "你好，我可以协助挑选商品，也可以解释订单、物流和退换货问题。", [], []


HANDLERS: dict[AgentRole, AgentHandler] = {
    AgentRole.PRODUCT_SCOUT: product_scout,
    AgentRole.ORDER_CARE: order_care,
    AgentRole.CONVERSATION_GUIDE: conversation_guide,
}


async def execute_mission(mission: Mission) -> AgentReport:
    started = perf_counter()
    try:
        message, evidence, claims = await HANDLERS[mission.role](mission)
        status = "completed"
    except Exception as exc:  # One failed branch must not erase sibling results.
        message = f"{mission.role.value} 暂时不可用：{type(exc).__name__}"
        evidence = []
        claims = []
        status = "failed"
    return AgentReport(
        role=mission.role,
        message=message,
        evidence=evidence,
        claims=claims,
        elapsed_ms=round((perf_counter() - started) * 1000, 2),
        status=status,
    )


def _current_turn_text(text: str) -> str:
    if CURRENT_TURN_MARKER not in text:
        return text
    return text.rsplit(CURRENT_TURN_MARKER, maxsplit=1)[-1].strip()


def _select_order_attributes(
    text: str,
    attributes: dict[str, str],
) -> dict[str, str]:
    selected: dict[str, str] = {}
    if any(term in text for term in STATUS_QUERY_TERMS):
        selected["status_label"] = attributes["status_label"]
    if any(term in text for term in PLACED_AT_QUERY_TERMS):
        selected["placed_at"] = attributes["placed_at"]
    if any(term in text for term in ITEM_QUERY_TERMS):
        selected["item_summary"] = attributes["item_summary"]
    return selected or attributes


def _format_order_message(order_id: str, attributes: dict[str, str]) -> str:
    labels = {
        "status_label": "当前状态",
        "placed_at": "下单时间",
        "item_summary": "商品",
    }
    details = "；".join(
        f"{labels[attribute]}：{value}"
        for attribute, value in attributes.items()
    )
    return f"订单 {order_id} {details}。"


def _policy_topic(text: str) -> str | None:
    if any(term in text for term in RETURN_TERMS):
        return "returns"
    if any(term in text for term in SHIPPING_TERMS):
        return "shipping"
    return None
