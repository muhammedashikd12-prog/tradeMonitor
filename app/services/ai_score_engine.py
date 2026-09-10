"""Composite 0-100 AI trade score from configurable weights (config.py)."""
from __future__ import annotations
from app.models import ScoreBreakdown
from app.config import settings


def compute_score(market_regime_0_100: float, iv_environment_0_100: float,
                   expected_move_0_100: float, strike_quality_0_100: float,
                   price_action_0_100: float, oi_0_100: float,
                   liquidity_0_100: float, time_0_100: float,
                   risk_reward_0_100: float) -> ScoreBreakdown:
    w = settings
    parts = {
        "market_regime": market_regime_0_100 * w.weight_market_regime / 100,
        "iv_environment": iv_environment_0_100 * w.weight_iv_environment / 100,
        "expected_move": expected_move_0_100 * w.weight_expected_move / 100,
        "strike_quality": strike_quality_0_100 * w.weight_strike_quality / 100,
        "price_action": price_action_0_100 * w.weight_price_action / 100,
        "oi": oi_0_100 * w.weight_oi / 100,
        "liquidity": liquidity_0_100 * w.weight_liquidity / 100,
        "time": time_0_100 * w.weight_time / 100,
        "risk_reward": risk_reward_0_100 * w.weight_risk_reward / 100,
    }
    total = round(sum(parts.values()), 1)

    if total >= w.score_high_quality:
        classification = "HIGH_QUALITY"
    elif total >= w.score_acceptable:
        classification = "ACCEPTABLE"
    elif total >= w.score_wait:
        classification = "WAIT"
    else:
        classification = "NO_TRADE"

    return ScoreBreakdown(total=total, classification=classification,
                           **{k: round(v, 1) for k, v in parts.items()})
