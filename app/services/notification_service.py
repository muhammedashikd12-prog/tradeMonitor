"""Throttled alert dispatch — avoids the 'excessive alerts' failure mode by
rate-limiting identical alert keys."""
from __future__ import annotations
import time
from typing import Callable

_last_sent: dict[str, float] = {}
_subscribers: list[Callable[[str, str], None]] = []  # (level, message)


def subscribe(callback: Callable[[str, str], None]) -> None:
    _subscribers.append(callback)


def notify(key: str, level: str, message: str, min_interval_s: float = 60.0) -> bool:
    """Returns True if the alert was actually dispatched (not throttled)."""
    now = time.time()
    last = _last_sent.get(key, 0)
    if now - last < min_interval_s:
        return False
    _last_sent[key] = now
    for cb in _subscribers:
        cb(level, message)
    return True
