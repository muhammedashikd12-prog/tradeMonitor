"""Shared data models. Kept broker-agnostic and framework-light so any
service can be unit tested with plain synthetic data."""
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime, date


class OptionQuote(BaseModel):
    strike: float
    option_type: Literal["CE", "PE"]
    ltp: float
    bid: float
    ask: float
    iv: Optional[float] = None          # % implied vol
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    oi: int = 0
    change_in_oi: int = 0
    volume: int = 0
    symbol: Optional[str] = None        # broker's own tradable symbol string for this strike —
                                         # ALWAYS prefer this over reconstructing a symbol yourself


class OptionChainSnapshot(BaseModel):
    underlying: str = "NIFTY50"
    spot: float
    timestamp: datetime
    expiry: date
    quotes: List[OptionQuote]
    india_vix: Optional[float] = None
    lot_size: Optional[int] = None      # None if the broker didn't supply it for this fetch —
                                         # caller must fall back to settings.nifty_lot_size


class PriceBar(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


class RegimeResult(BaseModel):
    regime: Literal["RANGE", "UNCERTAIN", "TRENDING", "HIGH_VOLATILITY", "EVENT_RISK"]
    score_0_100: float
    reasons: List[str]


class IVResult(BaseModel):
    atm_ce_iv: float
    atm_pe_iv: float
    short_ce_iv: Optional[float] = None
    short_pe_iv: Optional[float] = None
    iv_average: float
    ce_pe_skew: float
    iv_percentile: Optional[float] = None
    iv_rank: Optional[float] = None
    classification: Literal["LOW", "NORMAL", "PREFERRED", "HIGH", "EXTREME"]


class ExpectedMoveResult(BaseModel):
    spot: float
    expected_move_points: float
    upper_range: float
    lower_range: float
    days_to_expiry: float


class LegAction(BaseModel):
    role: Literal["call_hedge", "call_short", "put_short", "put_hedge"]
    action: Literal["BUY", "SELL"]
    option_type: Literal["CE", "PE"]
    strike: float
    ltp: float
    iv: Optional[float] = None
    delta: Optional[float] = None
    oi: int = 0
    symbol: Optional[str] = None  # broker's tradable symbol string, carried through from OptionQuote


class IronCondorStructure(BaseModel):
    legs: List[LegAction]
    wing_width: int
    gross_credit: float
    net_credit: float
    max_profit: float
    max_loss: float
    breakeven_upper: float
    breakeven_lower: float
    margin_required: Optional[float] = None
    risk_reward: Optional[float] = None
    wing_width_justification: str


class ScoreBreakdown(BaseModel):
    market_regime: float
    iv_environment: float
    expected_move: float
    strike_quality: float
    price_action: float
    oi: float
    liquidity: float
    time: float
    risk_reward: float
    total: float
    classification: Literal["HIGH_QUALITY", "ACCEPTABLE", "WAIT", "NO_TRADE"]


class NoTradeCheck(BaseModel):
    passed: bool
    failed_reasons: List[str]


class TradeDecision(BaseModel):
    decision: Literal["TRADE", "WAIT", "NO_TRADE", "EXIT"]
    score: ScoreBreakdown
    structure: Optional[IronCondorStructure] = None
    regime: RegimeResult
    iv: IVResult
    expected_move: ExpectedMoveResult
    no_trade_check: NoTradeCheck
    reasoning: List[str]
    risks: List[str]
    invalidation: List[str]


class PositionSize(BaseModel):
    lots: int
    quantity: int
    margin_required: float
    max_loss_rupees: float
    risk_pct_of_capital: float


class RiskSnapshot(BaseModel):
    position_delta: float
    position_gamma: float
    position_theta: float
    position_vega: float
    distance_to_short_call: float
    distance_to_short_put: float
    distance_to_call_sl: float
    distance_to_put_sl: float
    current_pnl: float
    max_loss: float
    drawdown_pct_of_max_loss: float
    risk_level: Literal["SAFE", "WATCH", "ELEVATED_RISK", "EXIT_RISK"]


class StopLossEvent(BaseModel):
    side: Literal["CALL", "PUT"]
    triggered: bool
    reason: str
    pnl_per_share: float = 0.0
    sl_threshold_per_share: float = 0.0
    close_order: List[str] = Field(default_factory=list)  # e.g. ["short_call","call_hedge"]


class OrderResult(BaseModel):
    order_id: Optional[str]
    status: Literal["PENDING", "FILLED", "PARTIAL", "REJECTED", "UNKNOWN"]
    filled_price: Optional[float] = None
    raw: Optional[dict] = None
