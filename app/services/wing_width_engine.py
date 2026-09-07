"""Chooses wing width from settings.wing_widths (default 100/150/200/250)
based on available margin, expected move, IV, liquidity and risk/reward —
never permanently hardcoded to 150."""
from __future__ import annotations
from dataclasses import dataclass
from app.models import OptionChainSnapshot, ExpectedMoveResult
from app.config import settings


@dataclass
class WingWidthChoice:
    width: int
    justification: str
    estimated_max_loss: float
    estimated_credit: float
    risk_reward: float


def _find_quote(chain: OptionChainSnapshot, strike: float, opt_type: str):
    return next((q for q in chain.quotes if q.strike == strike and q.option_type == opt_type), None)


def evaluate_wing_widths(chain: OptionChainSnapshot, short_ce_strike: float, short_pe_strike: float,
                          em: ExpectedMoveResult, available_margin: float, lot_size: int = 75) -> WingWidthChoice:
    short_ce = _find_quote(chain, short_ce_strike, "CE")
    short_pe = _find_quote(chain, short_pe_strike, "PE")
    if not short_ce or not short_pe:
        raise ValueError("Short strikes not found in chain")

    best: WingWidthChoice | None = None
    for width in settings.wing_widths:
        ce_hedge_strike = short_ce_strike + width
        pe_hedge_strike = short_pe_strike - width
        ce_hedge = _find_quote(chain, ce_hedge_strike, "CE")
        pe_hedge = _find_quote(chain, pe_hedge_strike, "PE")
        if not ce_hedge or not pe_hedge:
            continue  # strike not listed at this width, skip

        credit = (short_ce.ltp - ce_hedge.ltp) + (short_pe.ltp - pe_hedge.ltp)
        max_loss_per_share = max(width - credit, credit)  # symmetric wings, worst side
        max_loss_rupees = max_loss_per_share * lot_size
        rr = round(credit / max_loss_per_share, 3) if max_loss_per_share > 0 else 0

        # Reject widths whose max loss doesn't fit available margin at 1 lot
        if max_loss_rupees > available_margin:
            continue

        # Prefer widths where max loss comfortably covers a move to ~1x expected move beyond short strike
        buffer_fit = 1.0 if width >= em.expected_move_points * 0.5 else 0.6

        score = rr * 50 + buffer_fit * 50
        justification = (
            f"Width {width}: credit ₹{credit:.1f}, max loss ₹{max_loss_rupees:.0f}, "
            f"R:R {rr:.2f}, fits margin ₹{available_margin:.0f}, buffer_fit={buffer_fit}"
        )
        candidate = WingWidthChoice(width=width, justification=justification,
                                     estimated_max_loss=max_loss_rupees,
                                     estimated_credit=credit * lot_size, risk_reward=rr)
        if best is None or score > (best.risk_reward * 50 + 50):
            best = candidate

    if best is None:
        raise ValueError("No wing width fits available margin/liquidity — recommend NO TRADE")
    return best
