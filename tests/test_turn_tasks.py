import asyncio

from commerce_copilot.contracts import (
    GroundingSummary,
    TurnRequest,
    TurnResponse,
    TurnTaskStatus,
    TurnTrace,
)
from commerce_copilot.database import CatalogRepository
from commerce_copilot.turn_tasks import TurnTaskScheduler


def make_repository(tmp_path) -> CatalogRepository:
    repository = CatalogRepository(tmp_path / "turn-tasks.db")
    repository.seed_demo_identity()
    return repository


def make_response(conversation_id: str = "conversation-1") -> TurnResponse:
    return TurnResponse(
        conversation_id=conversation_id,
        answer="任务完成",
        reports=[],
        elapsed_ms=1,
        trace=TurnTrace(
            planning_source="local",
            answer_source="local",
            selected_agents=[],
            grounding=GroundingSummary(passed=True, checked_claims=0),
        ),
    )


def test_turn_task_state_machine_and_idempotency(tmp_path):
    repository = make_repository(tmp_path)
    request = TurnRequest(message="推荐耳机")

    created, is_new = repository.create_turn_task(
        "user-demo",
        request,
        idempotency_key="request-1",
    )
    duplicate, duplicate_is_new = repository.create_turn_task(
        "user-demo",
        request,
        idempotency_key="request-1",
    )

    assert is_new is True
    assert duplicate_is_new is False
    assert duplicate.task_id == created.task_id
    assert created.status is TurnTaskStatus.PENDING

    running = repository.claim_turn_task(created.task_id, worker_id="worker-a")
    assert running is not None
    assert running.status is TurnTaskStatus.RUNNING
    assert running.attempt_count == 1
    assert repository.claim_turn_task(created.task_id, worker_id="worker-b") is None

    assert (
        repository.complete_turn_task(
            created.task_id,
            make_response(),
            worker_id="worker-b",
        )
        is False
    )
    assert (
        repository.complete_turn_task(
            created.task_id,
            make_response(),
            worker_id="worker-a",
        )
        is True
    )
    completed = repository.get_turn_task(created.task_id, user_id="user-demo")
    assert completed is not None
    assert completed.status is TurnTaskStatus.SUCCEEDED
    assert completed.response is not None
    assert completed.response.answer == "任务完成"
    assert completed.completed_at is not None


def test_only_pending_owned_task_can_be_cancelled(tmp_path):
    repository = make_repository(tmp_path)
    task, _ = repository.create_turn_task(
        "user-demo",
        TurnRequest(message="查询订单"),
    )

    assert repository.cancel_turn_task(task.task_id, user_id="unknown") is False
    assert repository.cancel_turn_task(task.task_id, user_id="user-demo") is True
    assert repository.cancel_turn_task(task.task_id, user_id="user-demo") is False
    cancelled = repository.get_turn_task(task.task_id, user_id="user-demo")
    assert cancelled is not None
    assert cancelled.status is TurnTaskStatus.CANCELLED


def test_scheduler_persists_successful_result(tmp_path):
    repository = make_repository(tmp_path)
    task, _ = repository.create_turn_task(
        "user-demo",
        TurnRequest(message="你好", conversation_id="conversation-1"),
    )

    async def executor(message, conversation_id, *, user_id):
        assert message == "你好"
        assert conversation_id == "conversation-1"
        assert user_id == "user-demo"
        return make_response(conversation_id)

    async def run():
        scheduler = TurnTaskScheduler(repository, executor)
        scheduler.submit(task.task_id, user_id="user-demo")
        await scheduler.wait_idle()

    asyncio.run(run())

    completed = repository.get_turn_task(task.task_id, user_id="user-demo")
    assert completed is not None
    assert completed.status is TurnTaskStatus.SUCCEEDED
    assert completed.response is not None


def test_scheduler_persists_failure_type(tmp_path):
    repository = make_repository(tmp_path)
    task, _ = repository.create_turn_task(
        "user-demo",
        TurnRequest(message="失败任务"),
    )

    async def executor(*args, **kwargs):
        raise TimeoutError("simulated")

    async def run():
        scheduler = TurnTaskScheduler(repository, executor)
        scheduler.submit(task.task_id, user_id="user-demo")
        await scheduler.wait_idle()

    asyncio.run(run())

    failed = repository.get_turn_task(task.task_id, user_id="user-demo")
    assert failed is not None
    assert failed.status is TurnTaskStatus.FAILED
    assert failed.error_type == "TimeoutError"
