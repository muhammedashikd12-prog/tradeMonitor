"""Hard reject checklist. Any failure here overrides the AI score — this is
a floor, not a suggestion."""
from __future__ import annotations
from app.models import (OptionChainSnapshot, RegimeResult, ExpectedMoveResult,
                         NoTradeCheck, OptionQuote)
from app.config import settings


def check_no_trade_conditions(
    chain: OptionChainSnapshot,
    regime: RegimeResult,
    em: ExpectedMoveResult,
    short_ce: OptionQuote,
    short_pe: OptionQuote,
    available_margin: float,
    estimated_margin_required: float,
    broker_status: str,
    expiry_verified: bool,
    max_bid_ask_spread_pct: float = 8.0,
    min_short_strike_distance_from_spot: float = 100.0,
) -> NoTradeCheck:
    reasons = []

    if regime.regime == "TRENDING":
        reasons.append("Strong directional trend detected")
    if regime.regime == "HIGH_VOLATILITY":
        reasons.append("Extreme/high volatility regime")
    if regime.regime == "EVENT_RISK":
        reasons.append("Major event/announcement risk flagged")

    for q, label in ((short_ce, "short Call"), (short_pe, "short Put")):
        if q.ltp > 0:
            spread_pct = (q.ask - q.bid) / q.ltp * 100
            if spread_pct > max_bid_ask_spread_pct:
                reasons.append(f"Excessive bid/ask spread on {label} ({spread_pct:.1f}%)")
        if q.volume < 100 and q.oi < 1000:
            reasons.append(f"Poor liquidity on {label} (low volume/OI)")

    if short_ce.strike - chain.spot < min_short_strike_distance_from_spot:
        reasons.append("Short Call strike too close to spot")
    if chain.spot - short_pe.strike < min_short_strike_distance_from_spot:
        reasons.append("Short Put strike too close to spot")

    if available_margin < estimated_margin_required:
        reasons.append(
            f"Insufficient margin: available ₹{available_margin:.0f} < required ₹{estimated_margin_required:.0f}"
        )

    if em.expected_move_points > (chain.spot * 0.02):  # >2% of spot as 1-DTE move is unusually large
        reasons.append(f"Expected move unusually large ({em.expected_move_points} pts)")

    if broker_status != "LIVE":
        reasons.append(f"Broker/data feed not LIVE (status={broker_status})")

    if not expiry_verified:
        reasons.append("Expiry could not be verified against live exchange data")

    return NoTradeCheck(passed=len(reasons) == 0, failed_reasons=reasons)
