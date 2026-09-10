"""Real-time risk engine. Continuously recomputed from live position quotes,
not theoretical premium alone."""
from __future__ import annotations
from app.models import IronCondorStructure, RiskSnapshot


def compute_position_greeks(structure: IronCondorStructure, live_deltas: dict[str, float],
                             live_gammas: dict[str, float], live_thetas: dict[str, float],
                             live_vegas: dict[str, float]) -> tuple[float, float, float, float]:
    """live_* dicts keyed by leg role ('call_hedge','call_short','put_short','put_hedge')."""
    sign = {"call_hedge": +1, "call_short": -1, "put_short": -1, "put_hedge": +1}
    delta = sum(sign[leg.role] * live_deltas.get(leg.role, 0.0) for leg in structure.legs)
    gamma = sum(sign[leg.role] * live_gammas.get(leg.role, 0.0) for leg in structure.legs)
    theta = sum(sign[leg.role] * live_thetas.get(leg.role, 0.0) for leg in structure.legs)
    vega = sum(sign[leg.role] * live_vegas.get(leg.role, 0.0) for leg in structure.legs)
    return round(delta, 3), round(gamma, 4), round(theta, 2), round(vega, 2)


def compute_live_pnl(structure: IronCondorStructure, live_ltp: dict[str, float], lot_size: int = 75) -> float:
    """Positive = profit. Short legs profit as price falls; long legs profit as price rises."""
    sign = {"call_hedge": -1, "call_short": +1, "put_short": +1, "put_hedge": -1}
    entry_price = {leg.role: leg.ltp for leg in structure.legs}
    pnl_per_share = 0.0
    for leg in structure.legs:
        current = live_ltp.get(leg.role, entry_price[leg.role])
        pnl_per_share += sign[leg.role] * (entry_price[leg.role] - current)
    return round(pnl_per_share * lot_size, 2)


def classify_risk(current_pnl: float, max_loss: float, call_sl_distance: float,
                   put_sl_distance: float, days_to_expiry: float) -> str:
    """call_sl_distance / put_sl_distance: rupees-per-share of remaining
    buffer before that vertical's SL triggers (see build_risk_snapshot),
    NOT a price-point distance — do not compare against spot-price deltas."""
    if max_loss <= 0:
        return "SAFE"
    drawdown_pct = max(0.0, -current_pnl) / max_loss * 100
    worst_side_distance = min(call_sl_distance, put_sl_distance)

    at_or_past_sl = worst_side_distance <= 0
    close_to_sl = 0 < worst_side_distance <= 1.0  # within ₹1/share of triggering

    if at_or_past_sl or drawdown_pct >= 90:
        return "EXIT_RISK"
    if close_to_sl or drawdown_pct >= 60:
        return "ELEVATED_RISK"
    if drawdown_pct >= 30:
        return "WATCH"
    return "SAFE"


def build_risk_snapshot(structure: IronCondorStructure, live_ltp: dict[str, float],
                         live_spot: float, call_sl_distance: float, put_sl_distance: float,
                         days_to_expiry: float, lot_size: int = 75,
                         live_deltas: dict | None = None, live_gammas: dict | None = None,
                         live_thetas: dict | None = None, live_vegas: dict | None = None) -> RiskSnapshot:
    """call_sl_distance / put_sl_distance: (pnl_per_share - sl_threshold_per_share)
    from stop_loss_engine — positive means still-safe room before that side's
    SL triggers, zero/negative means at-or-past the SL threshold."""
    live_deltas = live_deltas or {}
    live_gammas = live_gammas or {}
    live_thetas = live_thetas or {}
    live_vegas = live_vegas or {}

    delta, gamma, theta, vega = compute_position_greeks(structure, live_deltas, live_gammas, live_thetas, live_vegas)
    pnl = compute_live_pnl(structure, live_ltp, lot_size)

    short_call_strike = next(l.strike for l in structure.legs if l.role == "call_short")
    short_put_strike = next(l.strike for l in structure.legs if l.role == "put_short")

    dist_call = short_call_strike - live_spot
    dist_put = live_spot - short_put_strike

    risk_level = classify_risk(pnl, structure.max_loss, call_sl_distance, put_sl_distance, days_to_expiry)

    return RiskSnapshot(
        position_delta=delta, position_gamma=gamma, position_theta=theta, position_vega=vega,
        distance_to_short_call=round(dist_call, 1), distance_to_short_put=round(dist_put, 1),
        distance_to_call_sl=round(call_sl_distance, 2), distance_to_put_sl=round(put_sl_distance, 2),
        current_pnl=pnl, max_loss=structure.max_loss,
        drawdown_pct_of_max_loss=round(max(0.0, -pnl) / structure.max_loss * 100, 1) if structure.max_loss else 0.0,
        risk_level=risk_level,
    )
