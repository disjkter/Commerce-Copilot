"""Rate limiting contracts and a bounded in-memory sliding-window backend."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import ceil
from threading import RLock
from time import monotonic
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int


class RateLimiter(Protocol):
    def check(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
        now: float | None = None,
    ) -> RateLimitDecision: ...


@dataclass(slots=True)
class _Window:
    timestamps: deque[float]
    window_seconds: int
    last_seen: float


class InMemorySlidingWindowRateLimiter:
    """Thread-safe limiter for one-process deployments and local development.

    The key space is bounded and stale windows are removed periodically. The
    interface is intentionally storage-neutral so Redis can replace this class
    without changing API handlers.
    """

    def __init__(self, *, max_keys: int = 10_000, cleanup_interval: int = 128):
        if max_keys <= 0 or cleanup_interval <= 0:
            raise ValueError("max_keys and cleanup_interval must be positive")
        self.max_keys = max_keys
        self.cleanup_interval = cleanup_interval
        self._windows: dict[str, _Window] = {}
        self._operations = 0
        self._lock = RLock()

    def check(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
        now: float | None = None,
    ) -> RateLimitDecision:
        if not key:
            raise ValueError("rate-limit key cannot be empty")
        if limit <= 0 or window_seconds <= 0:
            raise ValueError("limit and window_seconds must be positive")
        current = monotonic() if now is None else now
        cutoff = current - window_seconds
        with self._lock:
            self._operations += 1
            if self._operations % self.cleanup_interval == 0:
                self._cleanup(current)
            window = self._windows.get(key)
            if window is None:
                if len(self._windows) >= self.max_keys:
                    self._cleanup(current)
                if len(self._windows) >= self.max_keys:
                    oldest_key = min(
                        self._windows,
                        key=lambda candidate: self._windows[candidate].last_seen,
                    )
                    self._windows.pop(oldest_key, None)
                window = _Window(deque(), window_seconds, current)
                self._windows[key] = window
            else:
                window.window_seconds = window_seconds
                window.last_seen = current

            while window.timestamps and window.timestamps[0] <= cutoff:
                window.timestamps.popleft()
            if len(window.timestamps) >= limit:
                retry_after = max(
                    1,
                    ceil(window.timestamps[0] + window_seconds - current),
                )
                return RateLimitDecision(
                    allowed=False,
                    limit=limit,
                    remaining=0,
                    retry_after_seconds=retry_after,
                )

            window.timestamps.append(current)
            return RateLimitDecision(
                allowed=True,
                limit=limit,
                remaining=max(0, limit - len(window.timestamps)),
                retry_after_seconds=0,
            )

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()
            self._operations = 0

    def _cleanup(self, current: float) -> None:
        stale_keys = [
            key
            for key, window in self._windows.items()
            if current - window.last_seen > window.window_seconds
        ]
        for key in stale_keys:
            self._windows.pop(key, None)

