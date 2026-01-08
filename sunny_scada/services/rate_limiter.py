from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class Limit:
    allowed: bool
    remaining: int
    reset_after_s: float


class RateLimiter:
    """Simple in-memory sliding window rate limiter.

    Note: This limiter is process-local. In HA deployments, use a shared store
    (Redis, etc.) to enforce limits across instances.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # key -> (window_start_epoch_s, count)
        self._state: Dict[str, Tuple[float, int]] = {}

    def allow(self, key: str, *, limit: int, window_s: int) -> Limit:
        now = time.time()
        window_s = max(1, int(window_s))
        limit = max(1, int(limit))
        with self._lock:
            start, count = self._state.get(key, (now, 0))
            if now - start >= window_s:
                start, count = now, 0
            if count >= limit:
                reset_after = max(0.0, (start + window_s) - now)
                return Limit(False, 0, reset_after)
            count += 1
            self._state[key] = (start, count)
            remaining = max(0, limit - count)
            reset_after = max(0.0, (start + window_s) - now)
            return Limit(True, remaining, reset_after)
