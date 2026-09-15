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


def calculate_fyers_leg_charges(price: float, quantity: int, side: str) -> float:
    """Estimate Fyers-equivalent option charges using real option-execution logic."""
    qty = max(0, int(quantity))
    turnover = max(0.0, float(price) * qty)
    brokerage = 0.0
    exchange_txn = turnover * 0.0005
    clearing = turnover * 0.0001
    sebi = turnover * 0.00002
    stamp = turnover * 0.00003 if side == "BUY" else 0.0
    stt = turnover * 0.0001 if side == "SELL" else 0.0
    gst = (brokerage + exchange_txn + clearing + sebi + stamp + stt) * 0.18
    return brokerage + exchange_txn + clearing + sebi + stamp + stt + gst


def calculate_paper_execution_price(leg: Any, quote: dict | None, slippage_points: float = 0.05) -> float:
    """Use a live executable side of the quote plus configured slippage.

    Fyers does not guarantee bid/ask in every quote response.  In that case
    the live LTP is the only market-derived fallback; a manually supplied
    entry is used only when no quote at all is available.
    """
    if quote is None:
        return float(leg.entry_price)
    if "BUY" in leg.leg:
        raw = (float(quote.get("ask") or quote.get("lp") or leg.entry_price) + abs(float(slippage_points)))
    else:
        raw = (float(quote.get("bid") or quote.get("lp") or leg.entry_price) - abs(float(slippage_points)))
    return max(0.0, raw)


def calculate_position_charges(legs: dict[str, Any], exit_ltps: dict[str, float | None] | None = None) -> float:
    """Return opening charges and, when supplied, closing charges per leg.

    A long option is sold to close and a short option is bought to close.
    This deliberately keeps the transaction-side tax treatment in one place.
    """
    total = 0.0
    for name, leg in legs.items():
        entry_side = "BUY" if "BUY" in name else "SELL"
        total += calculate_fyers_leg_charges(leg.entry_price, leg.quantity, entry_side)
        if exit_ltps and exit_ltps.get(name) is not None:
            exit_side = "SELL" if entry_side == "BUY" else "BUY"
            total += calculate_fyers_leg_charges(float(exit_ltps[name]), leg.quantity, exit_side)
    return total


def calculate_fund_requirement(legs: dict[str, Any], mode: str = "MARKET", live_ltps: dict[str, float | None] | None = None,
                              quotes: dict[str, dict] | None = None, slippage_points: float = 0.05) -> dict[str, float]:
    """Core fund requirement model: hedge BUY first, shorts SECOND, then net out proceeds and charges."""
    call_buy = legs["CALL BUY"]
    call_sell = legs["CALL SELL"]
    put_sell = legs["PUT SELL"]
    put_buy = legs["PUT BUY"]
    quote_map = quotes or {}
    if mode == "PAPER":
        call_buy_price = calculate_paper_execution_price(call_buy, quote_map.get(call_buy.symbol), slippage_points)
        call_sell_price = calculate_paper_execution_price(call_sell, quote_map.get(call_sell.symbol), slippage_points)
        put_sell_price = calculate_paper_execution_price(put_sell, quote_map.get(put_sell.symbol), slippage_points)
        put_buy_price = calculate_paper_execution_price(put_buy, quote_map.get(put_buy.symbol), slippage_points)
    else:
        call_buy_price = float(call_buy.entry_price)
        call_sell_price = float(call_sell.entry_price)
        put_sell_price = float(put_sell.entry_price)
        put_buy_price = float(put_buy.entry_price)
    gross_hedge_requirement = (call_buy_price * call_buy.quantity) + (put_buy_price * put_buy.quantity)
    short_leg_proceeds = (call_sell_price * call_sell.quantity) + (put_sell_price * put_sell.quantity)
    net_initial_cash_requirement = max(0.0, gross_hedge_requirement - short_leg_proceeds)
    charges = (
        calculate_fyers_leg_charges(call_buy_price, call_buy.quantity, "BUY")
        + calculate_fyers_leg_charges(call_sell_price, call_sell.quantity, "SELL")
        + calculate_fyers_leg_charges(put_sell_price, put_sell.quantity, "SELL")
        + calculate_fyers_leg_charges(put_buy_price, put_buy.quantity, "BUY")
    )
    final_requirement = net_initial_cash_requirement + charges
    return {
        "gross_hedge_requirement": gross_hedge_requirement,
        "short_leg_proceeds": short_leg_proceeds,
        "net_initial_cash_requirement": net_initial_cash_requirement,
        "charges": charges,
        "final_estimated_fund_requirement": final_requirement,
    }


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


# Public calculation-engine operations.  Keeping these thin wrappers here
# gives routes/components a stable, single source of truth without copying
# any financial formula into a caller.
def calculateLegPnL(leg: Any, ltp: float | None) -> float | None:
    return leg_pnl(leg, ltp)


def calculateCallCredit(legs: dict[str, Any]) -> dict[str, float]:
    return side_credit(legs["CALL SELL"], legs["CALL BUY"])


def calculatePutCredit(legs: dict[str, Any]) -> dict[str, float]:
    return side_credit(legs["PUT SELL"], legs["PUT BUY"])


def calculateTotalNetCredit(legs: dict[str, Any]) -> float:
    return calculateCallCredit(legs)["total"] + calculatePutCredit(legs)["total"]


def calculateMaximumInitialProfit(legs: dict[str, Any]) -> float:
    return calculateTotalNetCredit(legs)


def calculateCallSpreadPnL(legs: dict[str, Any], ltps: dict[str, float | None]) -> float:
    result = calculate_position(legs, ltps)
    return result["call_spread_pnl"]


def calculatePutSpreadPnL(legs: dict[str, Any], ltps: dict[str, float | None]) -> float:
    result = calculate_position(legs, ltps)
    return result["put_spread_pnl"]


def calculateTotalPositionPnL(legs: dict[str, Any], ltps: dict[str, float | None]) -> float:
    return calculate_position(legs, ltps)["total_pnl"]


def calculateBreakEven(legs: dict[str, Any]) -> tuple[float, float]:
    result = calculate_position(legs, {})
    return result["lower_breakeven"], result["upper_breakeven"]


def calculateSL(legs: dict[str, Any]) -> tuple[float, float]:
    result = calculate_position(legs, {})
    return result["call_sl_default"], result["put_sl_default"]


def calculateCharges(legs: dict[str, Any], exit_ltps: dict[str, float | None] | None = None) -> float:
    return calculate_position_charges(legs, exit_ltps)


def calculateFundRequirement(legs: dict[str, Any], **kwargs: Any) -> dict[str, float]:
    return calculate_fund_requirement(legs, **kwargs)


def calculateRiskStatus(spot: float | None, lower_be: float, upper_be: float,
                        warning_distance: float = 100.0, danger_distance: float = 50.0) -> str:
    if spot is None or spot <= lower_be or spot >= upper_be:
        return "DANGER"
    distance = min(spot - lower_be, upper_be - spot)
    return "DANGER" if distance <= danger_distance else "WARNING" if distance <= warning_distance else "SAFE"


def calculateAlerts(legs: dict[str, Any], ltps: dict[str, float | None], call_sl: float,
                    put_sl: float) -> list[str]:
    result = calculate_position(legs, ltps)
    alerts: list[str] = []
    if result["call_spread_pnl"] <= -abs(call_sl):
        alerts.append("CALL SL HIT")
    if result["put_spread_pnl"] <= -abs(put_sl):
        alerts.append("PUT SL HIT")
    return alerts
