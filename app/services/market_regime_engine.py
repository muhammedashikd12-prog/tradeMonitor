"""Classifies market regime: RANGE / UNCERTAIN / TRENDING / HIGH_VOLATILITY /
EVENT_RISK, using price bars. A strong directional trend substantially
lowers the Iron Condor suitability score."""
from __future__ import annotations
from typing import List
from statistics import mean
from app.models import PriceBar, RegimeResult


def _atr(bars: List[PriceBar], period: int = 14) -> float:
    trs = []
    for i in range(1, len(bars)):
        h, l, prev_c = bars[i].high, bars[i].low, bars[i - 1].close
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
    if not trs:
        return 0.0
    return mean(trs[-period:])


def _vwap(bars: List[PriceBar]) -> float:
    num = sum(((b.high + b.low + b.close) / 3) * b.volume for b in bars)
    den = sum(b.volume for b in bars) or 1
    return num / den


def _trend_strength(bars: List[PriceBar]) -> float:
    """Simple normalized directional strength: net move / sum of absolute
    bar-to-bar moves. 0 = pure chop, 1 = one-directional trend."""
    if len(bars) < 2:
        return 0.0
    net = bars[-1].close - bars[0].close
    total_move = sum(abs(bars[i].close - bars[i - 1].close) for i in range(1, len(bars))) or 1
    return abs(net) / total_move


def classify_regime(bars: List[PriceBar], india_vix: float, prev_day_high: float,
                     prev_day_low: float, event_risk_flag: bool = False) -> RegimeResult:
    reasons = []
    if event_risk_flag:
        return RegimeResult(regime="EVENT_RISK", score_0_100=10,
                             reasons=["Known event/announcement risk flagged for this session"])

    if india_vix >= 22:
        reasons.append(f"India VIX elevated at {india_vix}")
        return RegimeResult(regime="HIGH_VOLATILITY", score_0_100=15, reasons=reasons)

    atr = _atr(bars)
    trend = _trend_strength(bars)
    last_close = bars[-1].close if bars else 0
    vwap = _vwap(bars) if bars else last_close
    range_span = prev_day_high - prev_day_low

    if trend >= 0.6:
        reasons.append(f"Strong directional trend strength ({trend:.2f})")
        return RegimeResult(regime="TRENDING", score_0_100=25, reasons=reasons)

    if trend >= 0.35:
        reasons.append(f"Moderate/ambiguous directional strength ({trend:.2f})")
        return RegimeResult(regime="UNCERTAIN", score_0_100=55, reasons=reasons)

    within_prev_range = prev_day_low <= last_close <= prev_day_high
    reasons.append("Price trading within prior day's range" if within_prev_range
                    else "Price outside prior day's range but low trend strength")
    reasons.append(f"ATR={atr:.1f}, trend_strength={trend:.2f}, close vs VWAP diff={last_close - vwap:.1f}")
    score = 85 if within_prev_range else 70
    return RegimeResult(regime="RANGE", score_0_100=score, reasons=reasons)
