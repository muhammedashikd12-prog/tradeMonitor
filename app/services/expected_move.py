"""Expected move from ATM IV and time to expiry. Never presented as a
guaranteed range — used only to compare against proposed short strikes."""
from __future__ import annotations
from datetime import datetime, date
import math
from app.models import ExpectedMoveResult


def days_to_expiry(now: datetime, expiry: date) -> float:
    expiry_dt = datetime.combine(expiry, datetime.min.time()).replace(hour=15, minute=30)
    delta = (expiry_dt - now).total_seconds() / 86400
    return max(delta, 0.0001)


def compute_expected_move(spot: float, iv_avg_pct: float, now: datetime, expiry: date) -> ExpectedMoveResult:
    dte = days_to_expiry(now, expiry)
    t_years = dte / 365.0
    sigma = iv_avg_pct / 100.0
    move = spot * sigma * math.sqrt(t_years)  # 1 std-dev expected move
    return ExpectedMoveResult(
        spot=spot,
        expected_move_points=round(move, 1),
        upper_range=round(spot + move, 1),
        lower_range=round(spot - move, 1),
        days_to_expiry=round(dte, 3),
    )


def strike_buffer_score(short_strike: float, expected_move: ExpectedMoveResult, side: str) -> float:
    """0-100: how much statistical buffer the short strike has beyond the
    expected move. side = 'CE' or 'PE'."""
    if side == "CE":
        buffer_points = short_strike - expected_move.upper_range
    else:
        buffer_points = expected_move.lower_range - short_strike
    if expected_move.expected_move_points <= 0:
        return 50.0
    ratio = buffer_points / expected_move.expected_move_points
    # 0 buffer -> 40 pts, 0.5x EM buffer -> 75 pts, 1x+ EM buffer -> 100 pts
    score = 40 + min(max(ratio, 0), 1.0) * 60
    return round(max(0.0, min(100.0, score)), 1)
