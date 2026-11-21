"""In-process adapter for the persistent asynchronous turn-task contract.

The scheduler intentionally depends on the database state machine. A future Redis
worker can claim and finish the same task records without changing the HTTP API.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from .contracts import TurnResponse, TurnTask, TurnTaskStatus
from .database import CatalogRepository, get_catalog
from .observability import log_event
from .orchestrator import handle_turn


TurnExecutor = Callable[..., Awaitable[TurnResponse]]
LOGGER = logging.getLogger("commerce_copilot.turn_tasks")


class TurnTaskScheduler:
    """Execute persisted tasks without tying their lifecycle to an HTTP request."""

    def __init__(
        self,
        repository: CatalogRepository | None = None,
        executor: TurnExecutor = handle_turn,
    ) -> None:
        self._repository = repository
        self._executor = executor
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def repository(self) -> CatalogRepository:
        if self._repository is None:
            self._repository = get_catalog()
        return self._repository

    def submit(self, task_id: str, *, user_id: str) -> None:
        background = asyncio.create_task(
            self._run(task_id, user_id=user_id),
            name=f"turn-task:{task_id}",
        )
        self._tasks.add(background)
        background.add_done_callback(self._tasks.discard)

    async def _run(self, task_id: str, *, user_id: str) -> None:
        task = await asyncio.to_thread(self.repository.claim_turn_task, task_id)
        if task is None:
            return
        log_event(LOGGER, "turn_task_started", task_id=task_id)
        try:
            response = await self._executor(
                task.request.message,
                task.request.conversation_id,
                user_id=user_id,
            )
        except asyncio.CancelledError:
            await asyncio.to_thread(
                self.repository.fail_turn_task,
                task_id,
                error_type="WorkerShutdown",
            )
            raise
        except Exception as exc:
            await asyncio.to_thread(
                self.repository.fail_turn_task,
                task_id,
                error_type=type(exc).__name__,
            )
            log_event(
                LOGGER,
                "turn_task_failed",
                level=logging.WARNING,
                task_id=task_id,
                error_type=type(exc).__name__,
            )
            return
        await asyncio.to_thread(
            self.repository.complete_turn_task,
            task_id,
            response,
        )
        log_event(LOGGER, "turn_task_completed", task_id=task_id)

    async def close(self) -> None:
        running = list(self._tasks)
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)

    async def wait_idle(self) -> None:
        """Wait for currently submitted work; intended for controlled shutdown/tests."""

        running = list(self._tasks)
        if running:
            await asyncio.gather(*running)


def task_can_be_scheduled(task: TurnTask) -> bool:
    return task.status is TurnTaskStatus.PENDING
