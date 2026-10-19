import asyncio
import re
from collections.abc import Awaitable, Callable
from time import perf_counter

from .contracts import AgentReport, AgentRole, Claim, Evidence, Mission
from .database import get_catalog


AgentDraft = tuple[str, list[Evidence], list[Claim]]
AgentHandler = Callable[[Mission], Awaitable[AgentDraft]]


async def product_scout(mission: Mission) -> AgentDraft:
    product_query = str(mission.parameters.get("product_query", mission.instruction))
    configured_budget = mission.parameters.get("max_price_cents")
    budget_cents = (
        int(configured_budget)
        if isinstance(configured_budget, (int, float))
        else _extract_budget_cents(mission.instruction)
    )
    hits = await asyncio.to_thread(
        get_catalog().search_products,
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
    await asyncio.sleep(0)
    return (
        f"我已识别购买后服务任务：{mission.instruction}。需要订单号后才能查询具体状态。",
        [],
        [],
    )


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


def _extract_budget_cents(text: str) -> int | None:
    patterns = (
        r"预算\s*(\d+(?:\.\d+)?)",
        r"(\d+(?:\.\d+)?)\s*元(?:以内|以下|之内)",
        r"不超过\s*(\d+(?:\.\d+)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return round(float(match.group(1)) * 100)
    return None
