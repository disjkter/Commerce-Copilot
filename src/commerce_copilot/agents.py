import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from .budget import extract_budget_cents
from .after_sales import ReturnEligibilityStatus, evaluate_return_eligibility
from .after_sales_rules import (
    AfterSalesRuleDecision,
    EVIDENCE_TYPE_LABELS,
    EvidenceSnapshot,
    ReturnPrecheckInput,
    evaluate_return_precheck,
)
from .agent_harness import AgentRuntimeHarness
from .catalog import HybridSearchHit, Product
from .comparison import compare_products
from .contracts import AgentReport, AgentRole, Claim, Evidence, Mission
from .database import get_catalog
from .domain_tools import get_tool_runtime
from .logistics import detect_shipment_exception
from .personalization import needs_product_clarification
from .query_understanding import ResponseFocus, detect_response_focus
from .retrieval import get_retriever


AgentDraft = tuple[str, list[Evidence], list[Claim]]
AgentHandler = Callable[[Mission], Awaitable[AgentDraft]]
ORDER_ID_PATTERN = re.compile(r"ORD-\d{4}-\d{4}", re.IGNORECASE)
TRACKING_NUMBER_PATTERN = re.compile(r"\b[A-Z]{2,4}\d{8,20}\b", re.IGNORECASE)
RETURN_REQUEST_ID_PATTERN = re.compile(r"\bRET-[A-F0-9]{12}\b", re.IGNORECASE)
STATUS_LABELS = {
    "pending": "待处理",
    "paid": "已付款",
    "shipped": "已发货",
    "delivered": "已送达",
    "cancelled": "已取消",
}
LOGISTICS_STATUS_LABELS = {
    "pending": "待揽收",
    "picked_up": "已揽收",
    "in_transit": "运输中",
    "out_for_delivery": "派送中",
    "delivered": "已签收",
    "delivery_failed": "派送失败",
    "exception": "物流异常",
    "returning": "退回中",
    "returned": "已退回",
}
RETURN_TERMS = ("退款", "退货", "换货", "能退", "可以退")
SHIPPING_TERMS = ("物流", "发货", "配送")
STATUS_QUERY_TERMS = ("状态", "到哪", "进度", "物流", "发货了吗", "送达了吗")
ITEM_QUERY_TERMS = ("商品", "买了什么", "买的什么", "什么东西", "包含什么")
PLACED_AT_QUERY_TERMS = ("下单时间", "什么时候下单", "哪天下单")
CURRENT_TURN_MARKER = "当前追问："
MY_ORDER_TERMS = (
    "我的订单",
    "我的包裹",
    "我买的",
    "购买记录",
    "最近订单",
    "全部订单",
    "订单列表",
)
RETURN_REQUEST_TERMS = ("申请退货", "我要退货", "办理退货", "提交退货")
RETURN_CONFIRMATION_TERMS = ("确认申请退货", "确认退货", "确认提交退货")
RETURN_STATUS_TERMS = ("退货进度", "售后进度", "退货申请状态", "工单状态")
RETURN_CANCEL_TERMS = ("取消退货申请", "撤销退货申请", "取消售后")
RETURN_CANCEL_CONFIRMATION_TERMS = (
    "确认取消退货申请",
    "确认撤销退货申请",
    "确认取消售后",
)
RETURN_REQUEST_STATUS_LABELS = {
    "pending_review": "待审核",
    "approved": "审核通过",
    "rejected": "已驳回",
    "cancelled": "已取消",
}
ROLE_TOOL_PERMISSIONS: dict[AgentRole, set[str]] = {
    AgentRole.PRODUCT_SCOUT: {"catalog:read"},
    AgentRole.ORDER_CARE: {"orders:read", "policies:read"},
    AgentRole.LOGISTICS_TRACKER: {"logistics:read"},
    AgentRole.AFTER_SALES_ADVISOR: {
        "orders:read",
        "logistics:read",
        "returns:read",
        "returns:write",
    },
    AgentRole.CONVERSATION_GUIDE: set(),
}


async def product_scout(mission: Mission) -> AgentDraft:
    selection_error = mission.parameters.get("selection_error")
    if isinstance(selection_error, str):
        return selection_error, [], []

    product_query = str(mission.parameters.get("product_query", mission.instruction))
    configured_budget = mission.parameters.get("max_price_cents")
    if mission.parameters.get("relax_budget") is True:
        budget_cents = None
    else:
        budget_cents = (
            int(configured_budget)
            if isinstance(configured_budget, (int, float))
            else extract_budget_cents(mission.instruction)
        )
    selected_references = mission.parameters.get("selected_references")
    response_focus = str(
        mission.parameters.get(
            "response_focus",
            detect_response_focus(_current_turn_text(mission.instruction)).value,
        )
    )
    configured_categories = mission.parameters.get("categories")
    categories = (
        [str(item) for item in configured_categories]
        if isinstance(configured_categories, list)
        else None
    )
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
            categories=categories,
            limit=3,
        )
    if mission.parameters.get("sort_by") == "price_asc":
        hits.sort(key=lambda hit: (hit.product.price_cents, -hit.product.rating))
    if not hits:
        near_miss = await _find_budget_near_miss(budget_cents, categories)
        if near_miss is not None and budget_cents is not None:
            return _budget_near_miss_draft(near_miss, budget_cents)
        constraints = []
        if categories:
            constraints.append(f"品类为 {'/'.join(categories)}")
        if budget_cents:
            constraints.append(f"价格不超过 {budget_cents / 100:.0f} 元")
        constraint_note = "、".join(constraints)
        if constraint_note:
            constraint_note = f"{constraint_note}且符合其他关键词"
        else:
            constraint_note = "符合关键词"
        return (
            f"商品发现任务已完成，但目录中没有找到{constraint_note}的在售商品。",
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
        lines.append(_format_product_line(index, product, response_focus))
        evidence.append(
            Evidence(
                source="local_catalog",
                reference=product.product_id,
                attributes={
                    "title": product.title,
                    "category": product.category,
                    "brand": product.brand,
                    "description": product.description,
                    "tags": product.tags,
                    "price_cents": product.price_cents,
                    "rating": product.rating,
                    "review_count": product.review_count,
                    "stock": product.stock,
                    "retrieval_sources": hit.retrieval_sources,
                },
            )
        )
        claim_values = {
            "title": product.title,
            **_focused_claim_values(product, response_focus),
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


def _format_product_line(index: int, product: Product, focus: str) -> str:
    if focus == ResponseFocus.ADVANTAGES.value:
        return f"{index}. {product.title} 的主要特点：{product.description}"
    if focus == ResponseFocus.PRICE.value:
        return f"{index}. {product.title} 当前售价 {product.price_cents / 100:.2f} 元。"
    if focus == ResponseFocus.RATING.value:
        return (
            f"{index}. {product.title} 评分 {product.rating:.1f}，"
            f"共有 {product.review_count} 条评价。"
        )
    if focus == ResponseFocus.STOCK.value:
        return f"{index}. {product.title} 当前库存 {product.stock} 件。"
    return (
        f"{index}. {product.title}｜{product.price_cents / 100:.2f} 元｜"
        f"评分 {product.rating:.1f}｜库存 {product.stock}"
    )


def _focused_claim_values(product: Product, focus: str) -> dict[str, object]:
    if focus == ResponseFocus.ADVANTAGES.value:
        return {"description": product.description}
    if focus == ResponseFocus.PRICE.value:
        return {"price_cents": product.price_cents}
    if focus == ResponseFocus.RATING.value:
        return {
            "rating": product.rating,
            "review_count": product.review_count,
        }
    if focus == ResponseFocus.STOCK.value:
        return {"stock": product.stock}
    return {
        "price_cents": product.price_cents,
        "rating": product.rating,
        "stock": product.stock,
    }


async def _find_budget_near_miss(
    budget_cents: int | None,
    categories: list[str] | None,
) -> Product | None:
    if budget_cents is None or not categories:
        return None
    products = await asyncio.to_thread(
        get_catalog().list_products,
        categories=categories,
        in_stock_only=True,
    )
    over_budget = [
        product for product in products if product.price_cents > budget_cents
    ]
    if len(over_budget) != len(products) or not over_budget:
        return None
    return min(
        over_budget,
        key=lambda product: (
            product.price_cents - budget_cents,
            -product.rating,
            product.product_id,
        ),
    )


def _budget_near_miss_draft(product: Product, budget_cents: int) -> AgentDraft:
    gap_cents = product.price_cents - budget_cents
    evidence = Evidence(
        source="local_catalog",
        reference=product.product_id,
        attributes={
            "title": product.title,
            "price_cents": product.price_cents,
            "rating": product.rating,
            "stock": product.stock,
            "requested_budget_cents": budget_cents,
            "budget_gap_cents": gap_cents,
            "match_status": "near_miss",
            "retrieval_sources": ["constraint_relaxation"],
        },
    )
    claim_values = {
        "title": product.title,
        "price_cents": product.price_cents,
        "budget_gap_cents": gap_cents,
    }
    claims = [
        Claim(
            statement=f"{product.product_id} 的 {attribute} 为 {value}",
            evidence_reference=product.product_id,
            attribute=attribute,
            expected_value=value,
        )
        for attribute, value in claim_values.items()
    ]
    message = (
        f"{budget_cents / 100:.0f} 元预算内暂无符合品类要求的在售商品。"
        f"最接近预算的候补是 {product.title}，价格 {product.price_cents / 100:.2f} 元，"
        f"超出预算 {gap_cents / 100:.2f} 元。该商品没有被当作符合预算的结果；"
        "如果你能接受提高预算，我可以继续介绍这款商品。"
    )
    return message, [evidence], claims


async def order_care(mission: Mission) -> AgentDraft:
    text = mission.instruction
    current_text = _current_turn_text(text)
    order_match = ORDER_ID_PATTERN.search(text)
    messages: list[str] = []
    evidence: list[Evidence] = []
    claims: list[Claim] = []
    configured_user_id = mission.parameters.get("user_id")
    user_id = str(configured_user_id) if configured_user_id else None
    requests_own_orders = any(term in current_text for term in MY_ORDER_TERMS)
    runtime = get_tool_runtime()

    if order_match:
        order_id = order_match.group(0).upper()
        order = (await runtime.execute("order.get", {"order_id": order_id})).data
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
    elif requests_own_orders and user_id:
        orders = (await runtime.execute("order.list", {"limit": 10})).data
        if not orders:
            messages.append("当前账号暂无订单。")
        for order in orders:
            status_label = STATUS_LABELS.get(order.status, order.status)
            item_summary = "、".join(
                f"{item.title}×{item.quantity}" for item in order.items
            ) or "无商品明细"
            attributes = {
                "status_label": status_label,
                "placed_at": order.placed_at,
                "item_summary": item_summary,
            }
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

    if not order_match and not requests_own_orders:
        messages.append("如需查询具体订单状态，请提供格式为 ORD-2026-1001 的订单号。")
    if not messages:
        messages.append("请提供订单号，或说明需要查询物流、退货还是退款规则。")
    return "\n".join(messages), evidence, claims


async def logistics_tracker(mission: Mission) -> AgentDraft:
    text = mission.instruction
    current_text = _current_turn_text(text)
    order_match = ORDER_ID_PATTERN.search(text)
    tracking_match = TRACKING_NUMBER_PATTERN.search(text)
    configured_user_id = mission.parameters.get("user_id")
    user_id = str(configured_user_id) if configured_user_id else ""
    if not user_id:
        return "无法确认当前用户身份，请重新登录后再查询物流。", [], []
    requests_own_shipments = any(term in current_text for term in MY_ORDER_TERMS)
    if order_match is None and tracking_match is None and not requests_own_shipments:
        return (
            "请提供订单号或运单号，例如 ORD-2026-1001，我才能查询具体物流轨迹。",
            [],
            [],
        )

    runtime = get_tool_runtime()
    if order_match is not None:
        order_id = order_match.group(0).upper()
        shipments = (
            await runtime.execute(
                "logistics.get_by_order",
                {"order_id": order_id},
            )
        ).data
        missing_message = f"没有查询到订单 {order_id} 的物流信息，请确认订单归属或订单号是否正确。"
    elif tracking_match is not None:
        tracking_number = tracking_match.group(0).upper()  # type: ignore[union-attr]
        shipment = (
            await runtime.execute(
                "logistics.get_by_tracking",
                {"tracking_number": tracking_number},
            )
        ).data
        shipments = [shipment] if shipment is not None else []
        missing_message = "没有查询到该运单的物流信息，请确认运单号及账号归属。"
    else:
        shipments = (await runtime.execute("logistics.list", {"limit": 20})).data
        missing_message = "当前账号暂无可查询的物流信息。"
    if not shipments:
        return missing_message, [], []

    messages: list[str] = []
    evidence: list[Evidence] = []
    claims: list[Claim] = []
    for shipment in shipments:
        latest_event = shipment.events[-1] if shipment.events else None
        shipment_exception = detect_shipment_exception(shipment)
        status_label = LOGISTICS_STATUS_LABELS.get(shipment.status, shipment.status)
        attributes: dict[str, object] = {
            "order_id": shipment.order_id,
            "tracking_number": shipment.tracking_number,
            "carrier_code": shipment.carrier_code,
            "status": shipment.status,
            "status_label": status_label,
            "estimated_delivery_at": shipment.estimated_delivery_at,
            "latest_event_description": (
                latest_event.description if latest_event is not None else None
            ),
            "latest_event_location": (
                latest_event.location if latest_event is not None else None
            ),
            "latest_event_at": (
                latest_event.occurred_at if latest_event is not None else None
            ),
            "exception_code": (
                shipment_exception.code if shipment_exception is not None else None
            ),
            "exception_reason": (
                shipment_exception.reason if shipment_exception is not None else None
            ),
        }
        reference = f"shipment:{shipment.shipment_id}"
        selected_attributes = _select_logistics_attributes(current_text, attributes)
        messages.append(_format_logistics_message(selected_attributes))
        evidence.append(
            Evidence(
                source="logistics_repository",
                reference=reference,
                attributes=attributes,
            )
        )
        claims.extend(
            Claim(
                statement=f"物流字段 {attribute} 为 {value}",
                evidence_reference=reference,
                attribute=attribute,
                expected_value=value,
            )
            for attribute, value in selected_attributes.items()
            if value is not None
        )
    return "\n".join(messages), evidence, claims


async def after_sales_advisor(mission: Mission) -> AgentDraft:
    """Make a deterministic return-eligibility decision for an owned order."""
    order_match = ORDER_ID_PATTERN.search(mission.instruction)
    request_match = RETURN_REQUEST_ID_PATTERN.search(mission.instruction)
    configured_user_id = mission.parameters.get("user_id")
    user_id = str(configured_user_id) if configured_user_id else ""
    if not user_id:
        return "无法确认当前用户身份，请重新登录后再查询退货资格。", [], []
    repository = get_catalog()
    runtime = get_tool_runtime()
    confirmed_actions = _confirmed_tool_actions(mission)
    if request_match is not None:
        request_id = request_match.group(0).upper()
        return_request = await asyncio.to_thread(
            repository.get_return_request_by_id,
            request_id,
            user_id=user_id,
        )
        if return_request is None:
            return (
                f"没有查询到售后工单 {request_id}，请确认工单号及账号归属。",
                [],
                [],
            )
        cancels_request = any(
            term in mission.instruction for term in RETURN_CANCEL_TERMS
        )
        confirms_cancellation = "returns.cancel_request" in confirmed_actions
        changed = False
        if cancels_request and confirms_cancellation:
            cancellation = (
                await runtime.execute(
                    "returns.cancel_request",
                    {"request_id": request_id},
                )
            ).data
            if cancellation is None:
                return "工单归属校验失败，未执行取消操作。", [], []
            return_request, changed = cancellation

        status_label = RETURN_REQUEST_STATUS_LABELS.get(
            return_request.status,
            return_request.status,
        )
        request_attributes: dict[str, object] = {
            "request_id": return_request.request_id,
            "order_id": return_request.order_id,
            "status": return_request.status,
            "status_label": status_label,
            "updated_at": return_request.updated_at,
        }
        if cancels_request and not confirms_cancellation:
            request_attributes["action_state"] = "awaiting_cancellation_confirmation"
            message = (
                f"售后工单 {request_id} 当前状态：{status_label}。"
                "取消后将停止审核；如确认取消，请回复："
                f'“确认取消退货申请 {request_id}”。'
            )
        elif cancels_request and changed:
            message = f"售后工单 {request_id} 已取消。"
        elif cancels_request and return_request.status == "cancelled":
            message = f"售后工单 {request_id} 已经取消，无需重复操作。"
        elif cancels_request:
            message = (
                f"售后工单 {request_id} 当前状态为{status_label}，"
                "只有待审核工单可以由用户取消。"
            )
        else:
            message = (
                f"售后工单 {request_id} 当前状态：{status_label}；"
                f"关联订单：{return_request.order_id}。"
            )
        reference = f"return-request:{request_id}"
        evidence = [
            Evidence(
                source="return_request_repository",
                reference=reference,
                attributes=request_attributes,
            )
        ]
        claims = [
            Claim(
                statement=f"售后工单字段 {attribute} 为 {value}",
                evidence_reference=reference,
                attribute=attribute,
                expected_value=value,
            )
            for attribute, value in request_attributes.items()
        ]
        return message, evidence, claims

    requests_request_status = any(
        term in mission.instruction for term in RETURN_STATUS_TERMS
    )
    cancels_request = any(term in mission.instruction for term in RETURN_CANCEL_TERMS)
    if order_match is None and (requests_request_status or cancels_request):
        return "请提供 RET- 开头的售后工单号，我才能查询或取消工单。", [], []
    if order_match is None:
        return (
            "请提供需要判断退货资格的订单号，例如 ORD-2026-1003。",
            [],
            [],
        )

    order_id = order_match.group(0).upper()
    order = await asyncio.to_thread(repository.get_order, order_id, user_id=user_id)
    if order is None:
        return (
            f"没有查询到订单 {order_id}，请确认订单号及账号归属。",
            [],
            [],
        )
    if requests_request_status:
        return_request = await asyncio.to_thread(
            repository.get_return_request,
            order_id,
            user_id=user_id,
        )
        if return_request is None:
            return f"订单 {order_id} 当前没有退货申请。", [], []
        status_label = RETURN_REQUEST_STATUS_LABELS.get(
            return_request.status,
            return_request.status,
        )
        reference = f"return-request:{return_request.request_id}"
        attributes = {
            "request_id": return_request.request_id,
            "order_id": return_request.order_id,
            "status": return_request.status,
            "status_label": status_label,
        }
        return (
            f"售后工单 {return_request.request_id} 当前状态：{status_label}。",
            [
                Evidence(
                    source="return_request_repository",
                    reference=reference,
                    attributes=attributes,
                )
            ],
            [
                Claim(
                    statement=f"售后工单状态为 {status_label}",
                    evidence_reference=reference,
                    attribute="status_label",
                    expected_value=status_label,
                )
            ],
        )
    shipments = await asyncio.to_thread(
        repository.get_order_shipments,
        order_id,
        user_id=user_id,
    )
    decision = evaluate_return_eligibility(order, shipments)
    status_labels = {
        ReturnEligibilityStatus.ELIGIBLE: "符合退货资格",
        ReturnEligibilityStatus.INELIGIBLE: "暂不符合退货资格",
        ReturnEligibilityStatus.NEEDS_REVIEW: "需要人工复核",
    }
    status_label = status_labels[decision.status]
    attributes: dict[str, object] = {
        "order_id": order_id,
        "eligibility_status": decision.status.value,
        "status_label": status_label,
        "reason": decision.reason,
        "evaluated_at": decision.evaluated_at,
        "delivered_at": decision.delivered_at,
        "deadline_at": decision.deadline_at,
        "return_window_days": decision.return_window_days,
    }
    reference = f"return-eligibility:{order_id}"
    parts = [f"订单 {order_id} 当前{status_label}", f"判断依据：{decision.reason}"]
    if decision.deadline_at is not None:
        deadline_text = decision.deadline_at.astimezone(
            ZoneInfo("Asia/Shanghai")
        ).strftime("%Y-%m-%d %H:%M")
        parts.append(f"退货截止时间：{deadline_text}（北京时间）")
    message = "；".join(parts) + "。"
    claim_attributes = ("status_label", "reason")
    if decision.deadline_at is not None:
        claim_attributes += ("deadline_at",)
    evidence = [
        Evidence(
            source="derived_return_eligibility",
            reference=reference,
            attributes=attributes,
        )
    ]
    claims = [
        Claim(
            statement=f"订单 {order_id} 的退货判断 {attribute} 为 {attributes[attribute]}",
            evidence_reference=reference,
            attribute=attribute,
            expected_value=attributes[attribute],
        )
        for attribute in claim_attributes
    ]

    requests_return = any(term in mission.instruction for term in RETURN_REQUEST_TERMS)
    confirms_return = "returns.create_request" in confirmed_actions
    precheck = None
    if requests_return or confirms_return:
        products = [
            product
            for item in order.items
            if (product := await asyncio.to_thread(repository.get_product, item.product_id))
            is not None
        ]
        submitted_types = mission.parameters.get("submitted_evidence_types", [])
        precheck = evaluate_return_precheck(
            ReturnPrecheckInput(
                eligibility=decision,
                refund_amount_cents=sum(
                    item.quantity * item.unit_price_cents for item in order.items
                ),
                product_categories=[product.category for product in products],
                product_tags=[tag for product in products for tag in product.tags],
                evidence=EvidenceSnapshot(
                    submitted_types=(
                        [str(item) for item in submitted_types]
                        if isinstance(submitted_types, list)
                        else []
                    )
                ),
            )
        )
        attributes.update(
            {
                "precheck_decision": precheck.decision.value,
                "risk_score": precheck.risk_score,
                "risk_level": precheck.risk_level,
                "matched_rules": precheck.matched_rules,
                "missing_evidence": precheck.missing_evidence,
                "rule_version": precheck.rule_version,
            }
        )
        evidence[0].attributes.update(
            {
                "precheck_decision": precheck.decision.value,
                "risk_score": precheck.risk_score,
                "risk_level": precheck.risk_level,
                "matched_rules": precheck.matched_rules,
                "missing_evidence": precheck.missing_evidence,
                "rule_version": precheck.rule_version,
            }
        )
        for attribute in ("precheck_decision", "rule_version"):
            claims.append(
                Claim(
                    statement=f"退货预审字段 {attribute} 为 {attributes[attribute]}",
                    evidence_reference=reference,
                    attribute=attribute,
                    expected_value=attributes[attribute],
                )
            )

    can_create_request = precheck is not None and precheck.decision != AfterSalesRuleDecision.REJECT
    if confirms_return and can_create_request:
        result = (
            await runtime.execute(
                "returns.create_request",
                {
                    "order_id": order_id,
                    "reason": decision.reason,
                    "precheck_decision": precheck.decision.value,
                    "risk_score": precheck.risk_score,
                    "risk_level": precheck.risk_level,
                    "decision_reasons": precheck.reasons,
                    "matched_rules": precheck.matched_rules,
                    "missing_evidence": precheck.missing_evidence,
                    "rule_version": precheck.rule_version,
                },
            )
        ).data
        if result is None:
            return (
                f"订单 {order_id} 的归属校验失败，未创建退货申请。",
                evidence,
                claims,
            )
        return_request, created = result
        request_reference = f"return-request:{return_request.request_id}"
        request_attributes: dict[str, object] = {
            "request_id": return_request.request_id,
            "order_id": return_request.order_id,
            "status": return_request.status,
            "created": created,
        }
        evidence.append(
            Evidence(
                source="return_request_repository",
                reference=request_reference,
                attributes=request_attributes,
            )
        )
        claims.extend(
            Claim(
                statement=f"退货申请字段 {attribute} 为 {value}",
                evidence_reference=request_reference,
                attribute=attribute,
                expected_value=value,
            )
            for attribute, value in request_attributes.items()
        )
        if created:
            message += f" 已创建退货申请 {return_request.request_id}，当前状态为待审核。"
            if precheck.decision == AfterSalesRuleDecision.REQUEST_MORE_EVIDENCE:
                missing = "、".join(
                    EVIDENCE_TYPE_LABELS.get(item, item)
                    for item in precheck.missing_evidence
                )
                message += f" 预审仍缺少材料：{missing}，需要补充后才能自动授权寄回。"
            elif precheck.decision == AfterSalesRuleDecision.MANUAL_REVIEW:
                message += f" 命中人工审核规则：{'；'.join(precheck.reasons)}。"
        else:
            message += (
                f" 该订单已有退货申请 {return_request.request_id}，"
                "本次重复确认未创建新工单。"
            )
    elif requests_return and can_create_request:
        attributes["action_state"] = "awaiting_confirmation"
        evidence[0].attributes["action_state"] = "awaiting_confirmation"
        claims.append(
            Claim(
                statement="退货申请等待用户确认",
                evidence_reference=reference,
                attribute="action_state",
                expected_value="awaiting_confirmation",
            )
        )
        message += (
            " 创建售后工单会改变订单状态；"
        )
        if precheck.decision == AfterSalesRuleDecision.REQUEST_MORE_EVIDENCE:
            missing = "、".join(
                EVIDENCE_TYPE_LABELS.get(item, item)
                for item in precheck.missing_evidence
            )
            message += f"当前缺少预审材料：{missing}；"
        elif precheck.decision == AfterSalesRuleDecision.MANUAL_REVIEW:
            message += f"该申请需要人工审核：{'；'.join(precheck.reasons)}；"
        message += (
            "如确认提交，请回复："
            f'“确认申请退货 {order_id}”。'
        )
    return message, evidence, claims


async def conversation_guide(mission: Mission) -> AgentDraft:
    await asyncio.sleep(0)
    clarification = mission.parameters.get("clarification_question")
    if isinstance(clarification, str) and clarification.strip():
        return clarification.strip(), [], []
    return "你好，我可以协助挑选商品，也可以解释订单、物流和退换货问题。", [], []


HANDLERS: dict[AgentRole, AgentHandler] = {
    AgentRole.PRODUCT_SCOUT: product_scout,
    AgentRole.ORDER_CARE: order_care,
    AgentRole.LOGISTICS_TRACKER: logistics_tracker,
    AgentRole.AFTER_SALES_ADVISOR: after_sales_advisor,
    AgentRole.CONVERSATION_GUIDE: conversation_guide,
}


async def execute_mission(mission: Mission, *, trace_id: str = "") -> AgentReport:
    return await get_agent_harness().execute(mission, trace_id=trace_id)


@lru_cache(maxsize=1)
def get_agent_harness() -> AgentRuntimeHarness:
    return AgentRuntimeHarness(
        handlers=HANDLERS,
        role_permissions=ROLE_TOOL_PERMISSIONS,
        confirmation_resolver=_confirmed_tool_actions,
    )


def _confirmed_tool_actions(mission: Mission) -> set[str]:
    """Convert exact current-turn confirmations into non-LLM execution grants."""
    current_text = _current_turn_text(mission.instruction).strip()
    actions: set[str] = set()
    if re.fullmatch(
        r"确认(?:申请|提交)?退货\s*ORD-\d{4}-\d{4}[。.!！]?",
        current_text,
        re.IGNORECASE,
    ):
        actions.add("returns.create_request")
    if re.fullmatch(
        r"确认(?:取消|撤销)(?:退货申请|售后)\s*RET-[A-F0-9]{12}[。.!！]?",
        current_text,
        re.IGNORECASE,
    ):
        actions.add("returns.cancel_request")
    return actions


def _current_turn_text(text: str) -> str:
    if CURRENT_TURN_MARKER not in text:
        return text
    return text.rsplit(CURRENT_TURN_MARKER, maxsplit=1)[-1].strip()


def _select_order_attributes(
    text: str,
    attributes: dict[str, object],
) -> dict[str, object]:
    selected: dict[str, object] = {}
    if any(term in text for term in STATUS_QUERY_TERMS):
        selected["status_label"] = attributes["status_label"]
    if any(term in text for term in PLACED_AT_QUERY_TERMS):
        selected["placed_at"] = attributes["placed_at"]
    if any(term in text for term in ITEM_QUERY_TERMS):
        selected["item_summary"] = attributes["item_summary"]
    return selected or attributes


def _format_order_message(order_id: str, attributes: dict[str, object]) -> str:
    labels = {
        "status_label": "当前状态",
        "placed_at": "下单时间",
        "item_summary": "商品",
    }
    details = "；".join(
        f"{labels[attribute]}：{_format_business_value(attribute, value)}"
        for attribute, value in attributes.items()
    )
    return f"订单 {order_id} {details}。"


def _policy_topic(text: str) -> str | None:
    if any(term in text for term in RETURN_TERMS):
        return "returns"
    if any(term in text for term in SHIPPING_TERMS):
        return "shipping"
    return None


def _select_logistics_attributes(
    text: str,
    attributes: dict[str, object],
) -> dict[str, object]:
    selected: dict[str, object] = {
        "order_id": attributes["order_id"],
        "status_label": attributes["status_label"],
    }
    if any(term in text for term in ("运单", "单号", "快递")):
        selected["carrier_code"] = attributes["carrier_code"]
        selected["tracking_number"] = attributes["tracking_number"]
    if any(term in text for term in ("到哪", "位置", "轨迹", "进度", "物流", "更新")):
        selected["latest_event_description"] = attributes["latest_event_description"]
        selected["latest_event_location"] = attributes["latest_event_location"]
        selected["latest_event_at"] = attributes["latest_event_at"]
    if any(term in text for term in ("什么时候", "多久", "预计", "送到", "送达")):
        selected["estimated_delivery_at"] = attributes["estimated_delivery_at"]
    if any(term in text for term in ("异常", "不动", "没更新", "停滞")):
        selected["exception_code"] = attributes["exception_code"]
        selected["exception_reason"] = attributes["exception_reason"]
    return selected


def _format_logistics_message(attributes: dict[str, object]) -> str:
    parts = [
        f"订单 {attributes['order_id']} 当前物流状态：{attributes['status_label']}"
    ]
    if attributes.get("tracking_number"):
        parts.append(
            f"承运商：{attributes['carrier_code']}，运单号：{attributes['tracking_number']}"
        )
    if attributes.get("latest_event_description"):
        event = f"最新进展：{attributes['latest_event_description']}"
        if attributes.get("latest_event_location"):
            event += f"，位置：{attributes['latest_event_location']}"
        if attributes.get("latest_event_at"):
            event += f"，更新时间：{_format_china_datetime(attributes['latest_event_at'])}"
        parts.append(event)
    if attributes.get("estimated_delivery_at"):
        parts.append(
            f"预计送达：{_format_china_datetime(attributes['estimated_delivery_at'])}"
        )
    if attributes.get("exception_reason"):
        parts.append(f"异常提醒：{attributes['exception_reason']}")
    return "；".join(parts) + "。"


def _format_business_value(attribute: str, value: object) -> str:
    if attribute in {"placed_at", "updated_at", "created_at", "delivered_at"}:
        return _format_china_datetime(value)
    return str(value)


def _format_china_datetime(value: object) -> str:
    parsed: datetime | None = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if parsed is None:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    localized = parsed.astimezone(ZoneInfo("Asia/Shanghai"))
    return localized.strftime("%Y-%m-%d %H:%M（北京时间）")
