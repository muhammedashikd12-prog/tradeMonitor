"""Pure calculations for the manually monitored Iron Condor position."""
from __future__ import annotations

from typing import Any


def leg_pnl(leg: Any, ltp: float | None) -> float | None:
    if ltp is None:
        return None
    direction = 1 if "BUY" in leg.leg else -1
    return (ltp - leg.entry_price) * leg.quantity * direction


def side_credit(short_leg: Any, hedge_leg: Any) -> dict[str, float]:
    """Return the side credit using the actual quantity on each leg."""
    total = (short_leg.entry_price * short_leg.quantity) - (hedge_leg.entry_price * hedge_leg.quantity)
    per_unit = total / short_leg.quantity if short_leg.quantity else 0.0
    return {"per_unit": per_unit, "total": total}


def calculate_position(legs: dict[str, Any], live_ltps: dict[str, float | None]) -> dict[str, Any]:
    call_buy = legs["CALL BUY"]
    call_sell = legs["CALL SELL"]
    put_sell = legs["PUT SELL"]
    put_buy = legs["PUT BUY"]
    call_credit = side_credit(call_sell, call_buy)
    put_credit = side_credit(put_sell, put_buy)
    net_credit = call_credit["total"] + put_credit["total"]
    short_quantity = min(call_sell.quantity, put_sell.quantity)
    net_credit_per_unit = net_credit / short_quantity if short_quantity else 0.0
    pnl = {name: leg_pnl(leg, live_ltps.get(name)) for name, leg in legs.items()}

    call_spread = (pnl["CALL BUY"] or 0.0) + (pnl["CALL SELL"] or 0.0)
    put_spread = (pnl["PUT SELL"] or 0.0) + (pnl["PUT BUY"] or 0.0)
    return {
        "pnl": pnl,
        "call_credit": call_credit,
        "put_credit": put_credit,
        "net_credit": net_credit,
        "net_credit_per_unit": net_credit_per_unit,
        "max_initial_profit": net_credit,
        "call_spread_pnl": call_spread,
        "put_spread_pnl": put_spread,
        "total_pnl": call_spread + put_spread,
        "lower_breakeven": put_sell.strike - net_credit_per_unit,
        "upper_breakeven": call_sell.strike + net_credit_per_unit,
        "call_sl_default": call_credit["total"] * 3,
        "put_sl_default": put_credit["total"] * 3,
    }