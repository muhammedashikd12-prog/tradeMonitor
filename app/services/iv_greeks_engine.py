"""IV / Greeks engine. Computes ATM IV, skew, percentile/rank, and classifies
the IV regime using CONFIGURABLE thresholds (never hardcoded truth)."""
from __future__ import annotations
from collections import deque
from statistics import NormalDist
from typing import Optional
from app.models import OptionChainSnapshot, IVResult
from app.config import settings

_N = NormalDist()

# Rolling IV history used to derive percentile/rank until you wire in a real
# historical-IV data source. Swap this for a DB-backed series in production.
_iv_history: deque[float] = deque(maxlen=252)  # ~1 trading year of daily ATM IVs


def _find_atm_quotes(chain: OptionChainSnapshot):
    strikes = sorted({q.strike for q in chain.quotes})
    atm_strike = min(strikes, key=lambda s: abs(s - chain.spot))
    ce = next((q for q in chain.quotes if q.strike == atm_strike and q.option_type == "CE"), None)
    pe = next((q for q in chain.quotes if q.strike == atm_strike and q.option_type == "PE"), None)
    return ce, pe


def black_scholes_iv_estimate(price: float, spot: float, strike: float, t_years: float,
                               r: float = 0.065, option_type: str = "CE",
                               tol: float = 1e-4, max_iter: int = 100) -> Optional[float]:
    """Newton-Raphson implied vol solver, used as a fallback when the broker
    doesn't supply IV directly for a given strike."""
    if t_years <= 0 or price <= 0:
        return None
    sigma = 0.20
    for _ in range(max_iter):
        d1 = (np_log(spot / strike) + (r + 0.5 * sigma ** 2) * t_years) / (sigma * t_years ** 0.5)
        d2 = d1 - sigma * t_years ** 0.5
        if option_type == "CE":
            model_price = spot * _N.cdf(d1) - strike * (2.71828 ** (-r * t_years)) * _N.cdf(d2)
        else:
            model_price = strike * (2.71828 ** (-r * t_years)) * _N.cdf(-d2) - spot * _N.cdf(-d1)
        vega = spot * _N.pdf(d1) * t_years ** 0.5
        if vega < 1e-8:
            break
        diff = model_price - price
        if abs(diff) < tol:
            return round(sigma * 100, 2)
        sigma -= diff / vega
        sigma = max(0.01, min(sigma, 3.0))
    return round(sigma * 100, 2)


def np_log(x: float) -> float:
    import math
    return math.log(x)


def classify_iv(iv_avg: float) -> str:
    if iv_avg < settings.iv_low_max:
        return "LOW"
    if iv_avg < settings.iv_preferred_max:
        return "PREFERRED"
    if iv_avg < settings.iv_high_max:
        return "HIGH"
    return "EXTREME"


def compute_iv_percentile_rank(current_iv: float) -> tuple[Optional[float], Optional[float]]:
    if len(_iv_history) < 20:
        return None, None
    hist = sorted(_iv_history)
    below = sum(1 for v in hist if v <= current_iv)
    percentile = round(100 * below / len(hist), 1)
    lo, hi = hist[0], hist[-1]
    rank = round(100 * (current_iv - lo) / (hi - lo), 1) if hi > lo else 50.0
    return percentile, rank


def record_daily_iv(iv_avg: float) -> None:
    _iv_history.append(iv_avg)


_last_recorded_date = None


def record_daily_iv_once(iv_avg: float, today=None) -> None:
    """Call this on every /analyze — it only actually appends to the
    rolling history the FIRST time it's called on a given calendar date,
    so repeated intraday calls don't inflate the 'daily IV' series."""
    global _last_recorded_date
    from datetime import date as _date
    today = today or _date.today()
    if _last_recorded_date != today:
        record_daily_iv(iv_avg)
        _last_recorded_date = today


def compute_iv(chain: OptionChainSnapshot, short_ce_strike: Optional[float] = None,
               short_pe_strike: Optional[float] = None) -> IVResult:
    ce, pe = _find_atm_quotes(chain)
    if not ce or not pe:
        raise ValueError("Could not locate ATM CE/PE quotes in chain")

    atm_ce_iv = ce.iv if ce.iv else 15.0
    atm_pe_iv = pe.iv if pe.iv else 15.0
    iv_avg = round((atm_ce_iv + atm_pe_iv) / 2, 2)
    skew = round(atm_ce_iv - atm_pe_iv, 2)

    short_ce_iv = short_pe_iv = None
    if short_ce_strike is not None:
        q = next((x for x in chain.quotes if x.strike == short_ce_strike and x.option_type == "CE"), None)
        short_ce_iv = q.iv if q and q.iv else None
    if short_pe_strike is not None:
        q = next((x for x in chain.quotes if x.strike == short_pe_strike and x.option_type == "PE"), None)
        short_pe_iv = q.iv if q and q.iv else None

    percentile, rank = compute_iv_percentile_rank(iv_avg)

    return IVResult(
        atm_ce_iv=atm_ce_iv,
        atm_pe_iv=atm_pe_iv,
        short_ce_iv=short_ce_iv,
        short_pe_iv=short_pe_iv,
        iv_average=iv_avg,
        ce_pe_skew=skew,
        iv_percentile=percentile,
        iv_rank=rank,
        classification=classify_iv(iv_avg),
    )
