"""A small in-process TTL + LRU response cache (no Redis needed for one process)."""
import time
from collections import OrderedDict
from typing import Any


class TTLCache:
    def __init__(self, maxsize: int = 512, ttl_s: float = 600.0) -> None:
        self.maxsize, self.ttl_s = maxsize, ttl_s
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self.hits = self.misses = 0

    def get(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item is None or time.monotonic() - item[0] > self.ttl_s:
            self._data.pop(key, None)
            self.misses += 1
            return None
        self._data.move_to_end(key)
        self.hits += 1
        return item[1]

    def set(self, key: str, value: Any) -> None:
        self._data[key] = (time.monotonic(), value)
        self._data.move_to_end(key)
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)

    def stats(self) -> dict:
        return {"size": len(self._data), "hits": self.hits, "misses": self.misses, "ttl_s": self.ttl_s}
