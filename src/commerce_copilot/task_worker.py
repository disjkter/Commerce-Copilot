"""Standalone Redis Streams consumer for asynchronous Agent turns."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from uuid import uuid4

from .config import get_settings
from .database import get_catalog
from .observability import configure_logging, log_event
from .retrieval import get_retriever
from .task_queue import RedisTurnTaskQueue, TurnTaskMessage
from .turn_tasks import TurnTaskScheduler


LOGGER = logging.getLogger("commerce_copilot.task_worker")


class RedisTurnTaskWorker:
    def __init__(
        self,
        queue: RedisTurnTaskQueue,
        scheduler: TurnTaskScheduler,
        *,
        consumer_name: str,
        claim_idle_ms: int,
    ) -> None:
        self.queue = queue
        self.scheduler = scheduler
        self.consumer_name = consumer_name
        self.claim_idle_ms = claim_idle_ms

    async def recover_pending_tasks(self) -> int:
        pending = await asyncio.to_thread(
            self.scheduler.repository.list_pending_turn_tasks,
            limit=1000,
        )
        for task_id, user_id in pending:
            await self.queue.enqueue(task_id, user_id=user_id)
        return len(pending)

    async def process_once(self, *, block_ms: int = 5_000) -> bool:
        messages = await self.queue.claim_stale(
            consumer_name=self.consumer_name,
            min_idle_ms=self.claim_idle_ms,
        )
        if not messages:
            messages = await self.queue.read_new(
                consumer_name=self.consumer_name,
                block_ms=block_ms,
            )
        if not messages:
            return False
        await self._process(messages[0])
        return True

    async def _process(self, message: TurnTaskMessage) -> None:
        can_ack = await self.scheduler.execute(
            message.task_id,
            user_id=message.user_id,
        )
        if can_ack:
            await self.queue.acknowledge(message.message_id)

    async def run_forever(self) -> None:
        await self.queue.ensure_group()
        recovered = await self.recover_pending_tasks()
        log_event(
            LOGGER,
            "turn_task_worker_started",
            consumer_name=self.consumer_name,
            recovered_tasks=recovered,
        )
        while True:
            await self.process_once()


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.task_queue_backend != "redis":
        raise RuntimeError("TASK_QUEUE_BACKEND must be redis for the standalone worker")
    if (
        settings.embedding_warmup_enabled
        and settings.embedding_provider.casefold() == "bge"
    ):
        await asyncio.to_thread(get_retriever().warmup)
    consumer_name = (
        f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"
    )
    queue = RedisTurnTaskQueue(
        settings.redis_url,
        stream_key=settings.task_stream_key,
        consumer_group=settings.task_consumer_group,
    )
    scheduler = TurnTaskScheduler(
        get_catalog(),
        worker_id=consumer_name,
        lease_seconds=settings.task_lease_seconds,
    )
    worker = RedisTurnTaskWorker(
        queue,
        scheduler,
        consumer_name=consumer_name,
        claim_idle_ms=settings.task_lease_seconds * 1000,
    )
    try:
        await worker.run_forever()
    finally:
        await scheduler.close()
        await queue.close()


def main() -> None:
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
