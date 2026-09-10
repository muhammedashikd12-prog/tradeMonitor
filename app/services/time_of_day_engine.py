"""Time-of-day scoring for entries, using configurable window boundaries."""
from __future__ import annotations
from datetime import datetime, time as dtime
from app.config import settings


def _parse(hhmm: str) -> dtime:
    h, m = hhmm.split(":")
    return dtime(int(h), int(m))


def time_of_day_score(now: datetime) -> tuple[float, str]:
    t = now.time()
    no_entry_before = _parse(settings.no_entry_before)
    preferred_end = _parse(settings.preferred_entry_end)
    good_end = _parse(settings.good_entry_end)
    selective_end = _parse(settings.selective_entry_end)
    very_selective_end = _parse(settings.very_selective_entry_end)

    if t < dtime(9, 15):
        return 0.0, "Before market open"
    if t < no_entry_before:
        return 0.0, "09:15-09:30 data-collection window: NO ENTRY"
    if t < preferred_end:
        return 100.0, "Preferred entry window (highest score)"
    if t < good_end:
        return 80.0, "Good entry window if conditions remain favorable"
    if t < selective_end:
        return 60.0, "Selective window — require stronger confirmation"
    if t < very_selective_end:
        return 40.0, "Very selective window — near expiry cutoff"
    return 0.0, "After 14:00 — NO NEW TRADE by default"
