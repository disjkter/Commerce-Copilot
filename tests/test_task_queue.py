import asyncio

from commerce_copilot.task_queue import RedisTurnTaskQueue, TurnTaskMessage
from commerce_copilot.task_worker import RedisTurnTaskWorker


class FakeRedis:
    def __init__(self):
        self.added = []
        self.acked = []

    async def xadd(self, stream, fields, **options):
        self.added.append((stream, fields, options))
        return "1-0"

    async def xreadgroup(self, group, consumer, streams, **options):
        return [
            (
                "commerce:turn_tasks",
                [("2-0", {"task_id": "task-2", "user_id": "user-2"})],
            )
        ]

    async def xautoclaim(self, *args, **kwargs):
        return ["0-0", [], []]

    async def xack(self, stream, group, message_id):
        self.acked.append((stream, group, message_id))
        return 1

    async def aclose(self):
        return None


def make_queue() -> tuple[RedisTurnTaskQueue, FakeRedis]:
    queue = RedisTurnTaskQueue(
        "redis://unused",
        stream_key="commerce:turn_tasks",
        consumer_group="commerce-workers",
    )
    fake = FakeRedis()
    queue.client = fake
    return queue, fake


def test_redis_stream_queue_encodes_and_decodes_task_messages():
    async def run():
        queue, fake = make_queue()
        message_id = await queue.enqueue("task-1", user_id="user-1")
        messages = await queue.read_new(consumer_name="worker-1", block_ms=1)
        acknowledged = await queue.acknowledge(messages[0].message_id)

        assert message_id == "1-0"
        assert fake.added[0][1] == {"task_id": "task-1", "user_id": "user-1"}
        assert messages == [TurnTaskMessage("2-0", "task-2", "user-2")]
        assert acknowledged is True
        assert fake.acked[0][2] == "2-0"

    asyncio.run(run())


def test_worker_acknowledges_only_after_persistent_execution_succeeds():
    class Scheduler:
        def __init__(self):
            self.calls = []

        async def execute(self, task_id, *, user_id):
            self.calls.append((task_id, user_id))
            return True

    async def run():
        queue, fake = make_queue()
        scheduler = Scheduler()
        worker = RedisTurnTaskWorker(
            queue,
            scheduler,
            consumer_name="worker-1",
            claim_idle_ms=1000,
        )

        processed = await worker.process_once(block_ms=1)

        assert processed is True
        assert scheduler.calls == [("task-2", "user-2")]
        assert fake.acked[0][2] == "2-0"

    asyncio.run(run())


def test_worker_leaves_message_pending_when_database_persistence_loses_lease():
    class Scheduler:
        async def execute(self, task_id, *, user_id):
            return False

    async def run():
        queue, fake = make_queue()
        worker = RedisTurnTaskWorker(
            queue,
            Scheduler(),
            consumer_name="worker-1",
            claim_idle_ms=1000,
        )

        processed = await worker.process_once(block_ms=1)

        assert processed is True
        assert fake.acked == []

    asyncio.run(run())
