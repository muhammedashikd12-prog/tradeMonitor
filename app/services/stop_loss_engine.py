"""Monitors each vertical (call spread / put spread) separately, based on
ACTUAL live spread P&L — never theoretical premium alone. Always closes the
short leg before (or together with) the hedge, never leaving a naked short."""
from __future__ import annotations
from app.models import IronCondorStructure, StopLossEvent
from app.config import settings


def _spread_pnl(entry_short: float, entry_hedge: float, live_short: float, live_hedge: float) -> float:
    """Positive = profit on this vertical (credit spread)."""
    return (entry_short - live_short) - (entry_hedge - live_hedge)


def check_call_side_sl(structure: IronCondorStructure, live_ltp: dict[str, float]) -> StopLossEvent:
    short = next(l for l in structure.legs if l.role == "call_short")
    hedge = next(l for l in structure.legs if l.role == "call_hedge")
    credit_received = short.ltp - hedge.ltp
    live_pnl_per_share = _spread_pnl(short.ltp, hedge.ltp, live_ltp.get("call_short", short.ltp),
                                      live_ltp.get("call_hedge", hedge.ltp))
    sl_threshold = -credit_received * settings.sl_multiple_of_credit
    triggered = live_pnl_per_share <= sl_threshold
    reason = (f"Call spread P&L {live_pnl_per_share:.2f}/share breached SL threshold "
              f"{sl_threshold:.2f} ({settings.sl_multiple_of_credit}x credit)") if triggered else "Within SL"
    return StopLossEvent(
        side="CALL", triggered=triggered, reason=reason,
        pnl_per_share=round(live_pnl_per_share, 2), sl_threshold_per_share=round(sl_threshold, 2),
        close_order=["call_short", "call_hedge"] if triggered else [],
    )


def check_put_side_sl(structure: IronCondorStructure, live_ltp: dict[str, float]) -> StopLossEvent:
    short = next(l for l in structure.legs if l.role == "put_short")
    hedge = next(l for l in structure.legs if l.role == "put_hedge")
    credit_received = short.ltp - hedge.ltp
    live_pnl_per_share = _spread_pnl(short.ltp, hedge.ltp, live_ltp.get("put_short", short.ltp),
                                      live_ltp.get("put_hedge", hedge.ltp))
    sl_threshold = -credit_received * settings.sl_multiple_of_credit
    triggered = live_pnl_per_share <= sl_threshold
    reason = (f"Put spread P&L {live_pnl_per_share:.2f}/share breached SL threshold "
              f"{sl_threshold:.2f} ({settings.sl_multiple_of_credit}x credit)") if triggered else "Within SL"
    return StopLossEvent(
        side="PUT", triggered=triggered, reason=reason,
        pnl_per_share=round(live_pnl_per_share, 2), sl_threshold_per_share=round(sl_threshold, 2),
        close_order=["put_short", "put_hedge"] if triggered else [],
    )
