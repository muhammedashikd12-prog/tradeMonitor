"""Market Data Service — the one place that decides how fresh candle/VIX/
prev-day data needs to be, so /analyze doesn't hammer the broker API on
every call. Swap the event-risk source (an economic calendar feed) in here
when you have one; it's a manual flag for now.
"""
from __future__ import annotations
import time
from app.brokers.base import BrokerClient, BrokerConnectionError
from app.models import PriceBar

_CACHE_TTL_SECONDS = 15  # candles/VIX refresh at most every 15s

_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, fetch_fn, ttl: float = _CACHE_TTL_SECONDS):
    now = time.time()
    if key in _cache and now - _cache[key][0] < ttl:
        return _cache[key][1]
    value = fetch_fn()
    _cache[key] = (now, value)
    return value


def get_intraday_candles(broker: BrokerClient, symbol: str, resolution_minutes: int = 5) -> list[PriceBar]:
    return _cached(f"candles:{symbol}:{resolution_minutes}",
                   lambda: broker.get_intraday_candles(symbol, resolution_minutes))


def get_india_vix(broker: BrokerClient) -> float:
    return _cached("india_vix", broker.get_india_vix, ttl=10)


def get_prev_day_range(broker: BrokerClient, symbol: str) -> tuple[float, float]:
    # This only changes once a day — cache generously.
    return _cached(f"prev_day_range:{symbol}", lambda: broker.get_prev_day_high_low(symbol), ttl=3600)


# Manual event-risk flag — set True ahead of RBI policy days, budget day, major
# results, US Fed events, etc. until a real economic-calendar feed is wired in.
_event_risk_flag = False


def set_event_risk(flag: bool) -> None:
    global _event_risk_flag
    _event_risk_flag = flag


def get_event_risk() -> bool:
    return _event_risk_flag


def get_market_context(broker: BrokerClient, symbol: str, index_symbol: str) -> dict:
    """One call that /analyze uses to get everything the regime engine needs.
    Raises BrokerConnectionError (uncaught) if any piece is unreliable —
    the caller must treat that as a NO-TRADE condition, not silently fall
    back to stale/stubbed values."""
    candles = get_intraday_candles(broker, index_symbol)
    vix = get_india_vix(broker)
    prev_high, prev_low = get_prev_day_range(broker, index_symbol)
    return {
        "price_bars": candles,
        "india_vix": vix,
        "prev_day_high": prev_high,
        "prev_day_low": prev_low,
        "event_risk_flag": get_event_risk(),
    }
