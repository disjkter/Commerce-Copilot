import asyncio
from functools import lru_cache

from pydantic import BaseModel, Field

from .database import get_catalog
from .tool_runtime import ToolContext, ToolRiskLevel, ToolRuntime, ToolSpec


class OrderGetInput(BaseModel):
    order_id: str = Field(pattern=r"^ORD-\d{4}-\d{4}$")


class ListInput(BaseModel):
    limit: int = Field(default=20, ge=1, le=50)


class TrackingInput(BaseModel):
    tracking_number: str = Field(min_length=8, max_length=128)


class ReturnRequestCreateInput(BaseModel):
    order_id: str = Field(pattern=r"^ORD-\d{4}-\d{4}$")
    reason: str = Field(min_length=1, max_length=1000)
    precheck_decision: str = Field(max_length=64)
    risk_score: int = Field(ge=0, le=100)
    risk_level: str = Field(pattern=r"^(low|medium|high)$")
    decision_reasons: list[str] = Field(default_factory=list, max_length=20)
    matched_rules: list[str] = Field(default_factory=list, max_length=20)
    missing_evidence: list[str] = Field(default_factory=list, max_length=20)
    rule_version: str = Field(min_length=1, max_length=64)


class ReturnRequestCancelInput(BaseModel):
    request_id: str = Field(pattern=r"^RET-[A-F0-9]{12}$")


async def _get_order(arguments: BaseModel, context: ToolContext):
    payload = OrderGetInput.model_validate(arguments)
    return await asyncio.to_thread(
        get_catalog().get_order,
        payload.order_id,
        user_id=context.user_id,
    )


async def _list_orders(arguments: BaseModel, context: ToolContext):
    payload = ListInput.model_validate(arguments)
    return await asyncio.to_thread(
        get_catalog().list_user_orders,
        context.user_id,
        limit=payload.limit,
    )


async def _get_order_shipments(arguments: BaseModel, context: ToolContext):
    payload = OrderGetInput.model_validate(arguments)
    return await asyncio.to_thread(
        get_catalog().get_order_shipments,
        payload.order_id,
        user_id=context.user_id,
    )


async def _list_shipments(arguments: BaseModel, context: ToolContext):
    payload = ListInput.model_validate(arguments)
    return await asyncio.to_thread(
        get_catalog().list_user_shipments,
        context.user_id,
        limit=payload.limit,
    )


async def _get_shipment(arguments: BaseModel, context: ToolContext):
    payload = TrackingInput.model_validate(arguments)
    return await asyncio.to_thread(
        get_catalog().get_shipment,
        payload.tracking_number,
        user_id=context.user_id,
    )


async def _create_return_request(arguments: BaseModel, context: ToolContext):
    payload = ReturnRequestCreateInput.model_validate(arguments)
    return await asyncio.to_thread(
        get_catalog().create_return_request,
        payload.order_id,
        context.user_id,
        reason=payload.reason,
        precheck_decision=payload.precheck_decision,
        risk_score=payload.risk_score,
        risk_level=payload.risk_level,
        decision_reasons=payload.decision_reasons,
        matched_rules=payload.matched_rules,
        missing_evidence=payload.missing_evidence,
        rule_version=payload.rule_version,
    )


async def _cancel_return_request(arguments: BaseModel, context: ToolContext):
    payload = ReturnRequestCancelInput.model_validate(arguments)
    return await asyncio.to_thread(
        get_catalog().cancel_return_request,
        payload.request_id,
        user_id=context.user_id,
    )


@lru_cache(maxsize=1)
def get_tool_runtime() -> ToolRuntime:
    runtime = ToolRuntime()
    for spec in (
        ToolSpec(
            name="order.get",
            display_name="查询订单详情",
            description="Get one order owned by the authenticated user",
            input_model=OrderGetInput,
            risk_level=ToolRiskLevel.READ,
            timeout_seconds=3,
            max_retries=1,
            handler=_get_order,
        ),
        ToolSpec(
            name="order.list",
            display_name="查询我的订单",
            description="List recent orders owned by the authenticated user",
            input_model=ListInput,
            risk_level=ToolRiskLevel.READ,
            timeout_seconds=3,
            max_retries=1,
            handler=_list_orders,
        ),
        ToolSpec(
            name="logistics.get_by_order",
            display_name="查询订单物流",
            description="Get shipments for an owned order",
            input_model=OrderGetInput,
            risk_level=ToolRiskLevel.READ,
            timeout_seconds=3,
            max_retries=1,
            handler=_get_order_shipments,
        ),
        ToolSpec(
            name="logistics.list",
            display_name="查询我的物流",
            description="List shipments for the authenticated user",
            input_model=ListInput,
            risk_level=ToolRiskLevel.READ,
            timeout_seconds=3,
            max_retries=1,
            handler=_list_shipments,
        ),
        ToolSpec(
            name="logistics.get_by_tracking",
            display_name="查询运单物流",
            description="Get an owned shipment by tracking number",
            input_model=TrackingInput,
            risk_level=ToolRiskLevel.READ,
            timeout_seconds=3,
            max_retries=1,
            handler=_get_shipment,
        ),
        ToolSpec(
            name="returns.create_request",
            display_name="提交退货申请",
            description="Create an idempotent return request for an owned order",
            input_model=ReturnRequestCreateInput,
            risk_level=ToolRiskLevel.HIGH_WRITE,
            timeout_seconds=3,
            allowed_roles={"after_sales_advisor"},
            required_permission="returns:write",
            requires_confirmation=True,
            handler=_create_return_request,
        ),
        ToolSpec(
            name="returns.cancel_request",
            display_name="取消退货申请",
            description="Cancel an owned pending return request",
            input_model=ReturnRequestCancelInput,
            risk_level=ToolRiskLevel.HIGH_WRITE,
            timeout_seconds=3,
            allowed_roles={"after_sales_advisor"},
            required_permission="returns:write",
            requires_confirmation=True,
            handler=_cancel_return_request,
        ),
    ):
        runtime.register(spec)
    return runtime
