import asyncio
from collections.abc import Awaitable, Callable
from time import perf_counter

from .contracts import AgentReport, AgentRole, Mission


AgentHandler = Callable[[Mission], Awaitable[str]]


async def product_scout(mission: Mission) -> str:
    await asyncio.sleep(0)
    return f"我已识别商品发现任务：{mission.instruction}。商品数据层将在下一里程碑接入。"


async def order_care(mission: Mission) -> str:
    await asyncio.sleep(0)
    return f"我已识别购买后服务任务：{mission.instruction}。需要订单号后才能查询具体状态。"


async def conversation_guide(mission: Mission) -> str:
    await asyncio.sleep(0)
    return "你好，我可以协助挑选商品，也可以解释订单、物流和退换货问题。"


HANDLERS: dict[AgentRole, AgentHandler] = {
    AgentRole.PRODUCT_SCOUT: product_scout,
    AgentRole.ORDER_CARE: order_care,
    AgentRole.CONVERSATION_GUIDE: conversation_guide,
}


async def execute_mission(mission: Mission) -> AgentReport:
    started = perf_counter()
    try:
        message = await HANDLERS[mission.role](mission)
        status = "completed"
    except Exception as exc:  # Boundary: one agent must not break sibling branches.
        message = f"{mission.role.value} 暂时不可用：{type(exc).__name__}"
        status = "failed"
    return AgentReport(
        role=mission.role,
        message=message,
        elapsed_ms=round((perf_counter() - started) * 1000, 2),
        status=status,
    )

