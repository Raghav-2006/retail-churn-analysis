"""Hard sliding-window rate limit for live LLM calls (shared by the API and the dashboard)."""
import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self, max_calls: int, per_seconds: float) -> None:
        self.max_calls, self.per_seconds = max_calls, per_seconds
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        """Record a call and return True if it is within the limit, else False (and record nothing)."""
        now = time.monotonic()
        with self._lock:
            while self._calls and now - self._calls[0] > self.per_seconds:
                self._calls.popleft()
            if len(self._calls) >= self.max_calls:
                return False
            self._calls.append(now)
            return True

    def remaining(self) -> int:
        now = time.monotonic()
        with self._lock:
            return self.max_calls - sum(1 for t in self._calls if now - t <= self.per_seconds)
