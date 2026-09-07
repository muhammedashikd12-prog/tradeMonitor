"""Tracks % of available premium captured and whether remaining reward
justifies remaining expiry/gamma risk. Never forces holding to expiry."""
from __future__ import annotations
from app.models import IronCondorStructure
from app.config import settings


def pct_premium_captured(structure: IronCondorStructure, live_ltp: dict[str, float]) -> float:
    entry_net_credit = structure.net_credit
    if entry_net_credit <= 0:
        return 0.0
    live_ce_short = live_ltp.get("call_short", 0)
    live_ce_hedge = live_ltp.get("call_hedge", 0)
    live_pe_short = live_ltp.get("put_short", 0)
    live_pe_hedge = live_ltp.get("put_hedge", 0)
    current_spread_value = (live_ce_short - live_ce_hedge) + (live_pe_short - live_pe_hedge)
    captured = entry_net_credit - current_spread_value
    return round(max(0.0, min(100.0, captured / entry_net_credit * 100)), 1)


def should_evaluate_exit(structure: IronCondorStructure, live_ltp: dict[str, float],
                          days_to_expiry: float) -> tuple[bool, str]:
    pct = pct_premium_captured(structure, live_ltp)
    if pct >= settings.profit_booking_pct:
        return True, f"{pct}% of premium captured (>= {settings.profit_booking_pct}% threshold)"

    # Remaining reward vs remaining gamma risk: if very little time and little reward left, exit anyway
    remaining_reward = structure.max_profit * (1 - pct / 100)
    if days_to_expiry < 0.15 and remaining_reward < structure.max_profit * 0.15:
        return True, "Minimal reward remains with high gamma risk into expiry"

    return False, f"{pct}% captured — below booking threshold, remaining reward justifies holding"
