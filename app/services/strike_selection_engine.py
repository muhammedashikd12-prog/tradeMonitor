"""Multi-factor short-strike scoring. The ₹8-10 premium band is a PREFERRED
target/filter, not an absolute requirement — strikes are ranked on a
composite score across delta, liquidity, OI, spread, and distance."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from app.models import OptionChainSnapshot, OptionQuote, ExpectedMoveResult
from app.services.expected_move import strike_buffer_score
from app.config import settings


@dataclass
class StrikeCandidate:
    quote: OptionQuote
    score: float
    reasons: list[str]


def _liquidity_score(q: OptionQuote) -> float:
    if q.ltp <= 0:
        return 0.0
    spread_pct = (q.ask - q.bid) / q.ltp if q.ltp else 1.0
    spread_score = max(0.0, 100 - spread_pct * 500)  # 2% spread -> ~90, 10% -> ~50
    volume_score = min(100.0, (q.volume / 5000) * 100) if q.volume else 30.0
    oi_score_ = min(100.0, (q.oi / 200_000) * 100) if q.oi else 20.0
    return round((spread_score * 0.5 + volume_score * 0.25 + oi_score_ * 0.25), 1)


def _premium_fit_score(ltp: float) -> float:
    lo, hi = settings.preferred_premium_min, settings.preferred_premium_max
    if lo <= ltp <= hi:
        return 100.0
    dist = min(abs(ltp - lo), abs(ltp - hi))
    return max(0.0, 100 - dist * 8)  # gentle falloff outside the preferred band


def _delta_fit_score(delta: Optional[float]) -> float:
    """Prefer short strikes around 0.12-0.18 delta (roughly matches
    ~85% probability OTM), configurable target could be added to settings."""
    if delta is None:
        return 50.0
    target = 0.15
    return round(max(0.0, 100 - abs(abs(delta) - target) * 400), 1)


def score_candidate(q: OptionQuote, em: ExpectedMoveResult, side: str) -> StrikeCandidate:
    reasons = []
    buf = strike_buffer_score(q.strike, em, side)
    reasons.append(f"Expected-move buffer score {buf}")
    liq = _liquidity_score(q)
    reasons.append(f"Liquidity score {liq}")
    prem = _premium_fit_score(q.ltp)
    reasons.append(f"Premium-fit score {prem} (LTP {q.ltp})")
    delta_s = _delta_fit_score(q.delta)
    reasons.append(f"Delta-fit score {delta_s}")

    composite = round(buf * 0.35 + liq * 0.25 + prem * 0.25 + delta_s * 0.15, 1)
    return StrikeCandidate(quote=q, score=composite, reasons=reasons)


def select_short_strikes(chain: OptionChainSnapshot, em: ExpectedMoveResult,
                          min_distance_from_spot: float = 100) -> tuple[StrikeCandidate, StrikeCandidate]:
    """Returns (best_short_ce, best_short_pe)."""
    ce_candidates = [
        score_candidate(q, em, "CE") for q in chain.quotes
        if q.option_type == "CE" and q.strike - chain.spot >= min_distance_from_spot
    ]
    pe_candidates = [
        score_candidate(q, em, "PE") for q in chain.quotes
        if q.option_type == "PE" and chain.spot - q.strike >= min_distance_from_spot
    ]
    if not ce_candidates or not pe_candidates:
        raise ValueError("Insufficient strikes at required distance from spot")

    best_ce = max(ce_candidates, key=lambda c: c.score)
    best_pe = max(pe_candidates, key=lambda c: c.score)
    return best_ce, best_pe
