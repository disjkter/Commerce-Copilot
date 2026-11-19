"""Run and clean up a real-database context-memory smoke scenario."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from commerce_copilot.contracts import AgentRole
from commerce_copilot.database import get_catalog
from commerce_copilot.orchestrator import handle_turn


async def main() -> int:
    conversation_id = f"smoke-memory-{uuid4().hex}"
    try:
        await handle_turn(
            "查询订单 ORD-2026-1001",
            conversation_id,
            local_only=True,
        )
        for index in range(5):
            await handle_turn(
                f"你好 {index}",
                conversation_id,
                local_only=True,
            )
        result = await handle_turn(
            "它现在是什么状态？",
            conversation_id,
            local_only=True,
        )
        roles = [report.role for report in result.reports]
        passed = (
            roles == [AgentRole.ORDER_CARE]
            and "ORD-2026-1001" in result.answer
            and result.trace.memory.summary_loaded
        )
        print(result.answer)
        print(result.trace.memory.model_dump_json())
        print(f"context_memory_smoke={'passed' if passed else 'failed'}")
        return 0 if passed else 1
    finally:
        get_catalog().delete_conversation(
            conversation_id,
            user_id="user-demo",
        )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
