"""Broker-agnostic interface. Any broker (Fyers, Zerodha, Upstox, Angel One)
implements this so the rest of the app never touches broker-specific code."""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional, Callable
from app.models import OptionChainSnapshot, OrderResult, PriceBar


class BrokerConnectionError(Exception):
    pass


class BrokerClient(ABC):
    """All methods must raise BrokerConnectionError (not silently return
    stale/empty data) if the connection is unreliable — the no-trade engine
    depends on this to reject trades on bad data."""

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def connection_status(self) -> str:
        """Returns one of: LIVE, DELAYED, DISCONNECTED."""

    @abstractmethod
    def get_nearest_expiry(self, symbol: str) -> str:
        """MUST come from live exchange/broker data. Never hardcode."""

    @abstractmethod
    def get_option_chain(self, symbol: str, expiry: str) -> OptionChainSnapshot: ...

    @abstractmethod
    def get_ltp(self, symbol: str) -> float: ...

    @abstractmethod
    def get_margin_available(self) -> float: ...

    @abstractmethod
    def get_intraday_candles(self, symbol: str, resolution_minutes: int = 5) -> list[PriceBar]:
        """Today's intraday candles so far, oldest first. Used by the market
        regime engine (ATR, VWAP, trend strength)."""

    @abstractmethod
    def get_india_vix(self) -> float: ...

    @abstractmethod
    def get_prev_day_high_low(self, symbol: str) -> tuple[float, float]:
        """Returns (prev_day_high, prev_day_low) from the last completed
        trading day's daily candle."""

    @abstractmethod
    def get_positions(self) -> list[dict]: ...

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: str,           # BUY / SELL
        quantity: int,
        order_type: str = "MARKET",
        limit_price: Optional[float] = None,
        tag: Optional[str] = None,
    ) -> OrderResult: ...

    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderResult:
        """Never assume a fill — always poll/confirm via this method."""

    @abstractmethod
    def subscribe_ticks(self, symbols: list[str], on_tick: Callable[[dict], None]) -> None:
        """Must auto-reconnect internally on WebSocket drop."""

    @abstractmethod
    def unsubscribe_ticks(self, symbols: list[str]) -> None: ...
