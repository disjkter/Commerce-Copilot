"""Redis Streams transport for asynchronous Agent turn tasks."""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import ResponseError


@dataclass(frozen=True, slots=True)
class TurnTaskMessage:
    message_id: str
    task_id: str
    user_id: str


class RedisTurnTaskQueue:
    def __init__(
        self,
        redis_url: str,
        *,
        stream_key: str,
        consumer_group: str,
        max_length: int = 10_000,
    ) -> None:
        self.stream_key = stream_key
        self.consumer_group = consumer_group
        self.max_length = max_length
        self.client = Redis.from_url(redis_url, decode_responses=True)

    async def ensure_group(self) -> None:
        try:
            await self.client.xgroup_create(
                self.stream_key,
                self.consumer_group,
                id="0-0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def enqueue(self, task_id: str, *, user_id: str) -> str:
        return await self.client.xadd(
            self.stream_key,
            {"task_id": task_id, "user_id": user_id},
            maxlen=self.max_length,
            approximate=True,
        )

    async def read_new(
        self,
        *,
        consumer_name: str,
        block_ms: int = 5_000,
    ) -> list[TurnTaskMessage]:
        response = await self.client.xreadgroup(
            self.consumer_group,
            consumer_name,
            {self.stream_key: ">"},
            count=1,
            block=block_ms,
        )
        return _parse_stream_response(response)

    async def claim_stale(
        self,
        *,
        consumer_name: str,
        min_idle_ms: int,
    ) -> list[TurnTaskMessage]:
        response = await self.client.xautoclaim(
            self.stream_key,
            self.consumer_group,
            consumer_name,
            min_idle_ms,
            "0-0",
            count=1,
        )
        entries = response[1] if len(response) > 1 else []
        return [_message_from_entry(entry) for entry in entries]

    async def acknowledge(self, message_id: str) -> bool:
        return bool(
            await self.client.xack(
                self.stream_key,
                self.consumer_group,
                message_id,
            )
        )

    async def ping(self) -> bool:
        return bool(await self.client.ping())

    async def close(self) -> None:
        await self.client.aclose()


def _parse_stream_response(response: list[object]) -> list[TurnTaskMessage]:
    messages: list[TurnTaskMessage] = []
    for _, entries in response:
        messages.extend(_message_from_entry(entry) for entry in entries)
    return messages


def _message_from_entry(entry: tuple[str, dict[str, str]]) -> TurnTaskMessage:
    message_id, fields = entry
    return TurnTaskMessage(
        message_id=message_id,
        task_id=fields["task_id"],
        user_id=fields["user_id"],
    )
