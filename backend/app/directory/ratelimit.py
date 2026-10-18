"""Per-IP sliding-window rate limit for store lookups (M2 contract 4.2).

Two windows (per minute and per hour). Every lookup counts - 400s and 404s
included - so codes can't be enumerated quickly. A refused request is not
counted, so ``Retry-After`` is exact. In memory: one directory process.
"""

import math
import threading
import time
from collections import OrderedDict, deque
from typing import Callable, Deque, Optional

MINUTE = 60.0
HOUR = 3600.0


class SlidingWindowLimiter:
    def __init__(
        self,
        per_minute: int,
        per_hour: int,
        clock: Callable[[], float] = time.monotonic,
        max_clients: int = 50_000,
    ):
        self.per_minute = per_minute
        self.per_hour = per_hour
        self._clock = clock
        self._max_clients = max_clients
        self._hits: "OrderedDict[str, Deque[float]]" = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _wait(hits: Deque[float], limit: int, window: float, now: float) -> float:
        """Seconds until one more request fits in ``window`` (0 = fits now)."""
        if limit <= 0:
            return 0.0
        recent = [t for t in hits if t > now - window]
        if len(recent) < limit:
            return 0.0
        # The request fits once the oldest hit that keeps the count at the
        # limit leaves the window.
        return recent[len(recent) - limit] + window - now

    def check(self, key: str) -> Optional[int]:
        """None = allowed (and counted); otherwise the whole seconds to wait."""
        if self.per_minute <= 0 and self.per_hour <= 0:
            return None
        now = self._clock()
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                hits = deque()
                self._hits[key] = hits
                while len(self._hits) > self._max_clients:
                    self._hits.popitem(last=False)
            else:
                self._hits.move_to_end(key)
            while hits and hits[0] <= now - HOUR:
                hits.popleft()
            wait = max(
                self._wait(hits, self.per_minute, MINUTE, now),
                self._wait(hits, self.per_hour, HOUR, now),
            )
            if wait > 0:
                return max(1, int(math.ceil(wait)))
            hits.append(now)
            return None
