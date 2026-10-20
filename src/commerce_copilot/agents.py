import asyncio
import re
from collections.abc import Awaitable, Callable
from time import perf_counter

from .budget import extract_budget_cents
from .contracts import AgentReport, AgentRole, Claim, Evidence, Mission
from .database import get_catalog
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


async def product_scout(mission: Mission) -> AgentDraft:
    product_query = str(mission.parameters.get("product_query", mission.instruction))
    configured_budget = mission.parameters.get("max_price_cents")
    budget_cents = (
        int(configured_budget)
        if isinstance(configured_budget, (int, float))
        else extract_budget_cents(mission.instruction)
    )
    hits = await asyncio.to_thread(
        get_retriever().search,
        product_query,
        max_price_cents=budget_cents,
        limit=3,
    )
    if not hits:
        budget_note = f"且价格不超过 {budget_cents / 100:.0f} 元" if budget_cents else ""
        return (
            f"商品发现任务已完成，但目录中没有找到符合关键词{budget_note}的在售商品。",
            [],
            [],
        )

    lines = ["商品发现任务已完成，找到以下候选："]
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
    return "\n".join(lines), evidence, claims


async def order_care(mission: Mission) -> AgentDraft:
    text = mission.instruction
    order_match = ORDER_ID_PATTERN.search(text)
    messages: list[str] = []
    evidence: list[Evidence] = []
    claims: list[Claim] = []

    if order_match:
        order_id = order_match.group(0).upper()
        order = await asyncio.to_thread(get_catalog().get_order, order_id)
        if order is None:
            messages.append(f"没有查询到订单 {order_id}，请检查订单号是否正确。")
        else:
            status_label = STATUS_LABELS.get(order.status, order.status)
            item_summary = "、".join(
                f"{item.title}×{item.quantity}" for item in order.items
            ) or "无商品明细"
            messages.append(
                f"订单 {order.order_id} 当前状态：{status_label}；"
                f"下单时间：{order.placed_at}；商品：{item_summary}。"
            )
            attributes = {
                "status_label": status_label,
                "placed_at": order.placed_at,
                "item_summary": item_summary,
            }
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

    policy_topic = _policy_topic(text)
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
def _policy_topic(text: str) -> str | None:
    if any(term in text for term in RETURN_TERMS):
        return "returns"
    if any(term in text for term in SHIPPING_TERMS):
        return "shipping"
    return None
