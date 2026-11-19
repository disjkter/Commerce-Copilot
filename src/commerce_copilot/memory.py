"""Deterministic, bounded conversation-memory compaction."""

from __future__ import annotations

import re

from .budget import extract_budget_cents
from .contracts import ConversationMemory, ConversationTurn, Evidence


ORDER_PATTERN = re.compile(r"\bORD-\d{4}-\d{4}\b", re.IGNORECASE)
RETURN_PATTERN = re.compile(r"\bRET-[A-F0-9]{12}\b", re.IGNORECASE)
TRACKING_PATTERN = re.compile(r"\b[A-Z]{2,4}\d{8,20}\b", re.IGNORECASE)
TOPIC_TERMS = {
    "product": ("商品", "推荐", "耳机", "键盘", "价格", "预算", "第一款", "第二款"),
    "order": ("订单", "下单", "退款"),
    "logistics": ("物流", "快递", "运单", "发货", "到哪"),
    "after_sales": ("退货", "换货", "售后", "工单"),
}


def merge_conversation_memory(
    previous: ConversationMemory | None,
    turns: list[tuple[int, ConversationTurn]],
) -> ConversationMemory:
    """Merge newly compactable turns into a bounded structured memory."""

    entities = {
        key: list(values)
        for key, values in (previous.entities.items() if previous else [])
    }
    constraints = dict(previous.constraints) if previous else {}
    topics = list(entities.pop("topics", []))
    product_evidence = list(previous.evidence) if previous else []
    source_turn_count = previous.source_turn_count if previous else 0
    compacted_through = previous.compacted_through_turn_id if previous else 0

    for turn_id, turn in turns:
        if turn_id <= compacted_through:
            continue
        combined_text = f"{turn.user_message}\n{turn.assistant_answer}"
        _extend_unique(entities, "order_ids", ORDER_PATTERN.findall(combined_text))
        _extend_unique(entities, "return_request_ids", RETURN_PATTERN.findall(combined_text))
        _extend_unique(entities, "tracking_numbers", TRACKING_PATTERN.findall(combined_text))
        for topic, terms in TOPIC_TERMS.items():
            if any(term in combined_text for term in terms) and topic not in topics:
                topics.append(topic)
        budget = extract_budget_cents(turn.user_message)
        if budget is not None:
            constraints["max_price_cents"] = budget
        current_products = [
            evidence for evidence in turn.evidence if evidence.source == "local_catalog"
        ]
        if current_products:
            product_evidence = current_products[:5]
            entities["product_ids"] = [item.reference for item in product_evidence]
        compacted_through = turn_id
        source_turn_count += 1

    entities["topics"] = topics
    summary = _build_summary(entities, constraints)
    return ConversationMemory(
        summary=summary,
        entities=entities,
        constraints=constraints,
        evidence=product_evidence,
        compacted_through_turn_id=compacted_through,
        source_turn_count=source_turn_count,
    )


def _extend_unique(
    entities: dict[str, list[str]],
    key: str,
    values: list[str],
) -> None:
    existing = entities.setdefault(key, [])
    for value in values:
        normalized = value.upper()
        if normalized not in existing:
            existing.append(normalized)
    if len(existing) > 10:
        del existing[:-10]


def _build_summary(
    entities: dict[str, list[str]],
    constraints: dict[str, object],
) -> str:
    parts: list[str] = []
    labels = {
        "topics": "涉及主题",
        "product_ids": "最近商品",
        "order_ids": "相关订单",
        "tracking_numbers": "相关运单",
        "return_request_ids": "售后工单",
    }
    for key in ("topics", "product_ids", "order_ids", "tracking_numbers", "return_request_ids"):
        values = entities.get(key, [])
        if values:
            parts.append(f"{labels[key]}：{'、'.join(values)}")
    budget = constraints.get("max_price_cents")
    if isinstance(budget, int):
        parts.append(f"最近预算上限：{budget / 100:.2f} 元")
    return "；".join(parts)[:1000] or "较早会话未提取到可复用的业务事实"
