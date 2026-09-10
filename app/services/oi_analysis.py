"""OI analysis: concentration, shifts, and a bounded 0-100 contribution
score. OI is treated as ONE input, never a guaranteed support/resistance."""
from __future__ import annotations
from app.models import OptionChainSnapshot


def max_oi_strikes(chain: OptionChainSnapshot) -> tuple[float, float]:
    calls = [q for q in chain.quotes if q.option_type == "CE"]
    puts = [q for q in chain.quotes if q.option_type == "PE"]
    max_call = max(calls, key=lambda q: q.oi, default=None)
    max_put = max(puts, key=lambda q: q.oi, default=None)
    return (max_call.strike if max_call else 0.0, max_put.strike if max_put else 0.0)


def oi_score(chain: OptionChainSnapshot, short_ce_strike: float, short_pe_strike: float) -> tuple[float, str]:
    max_call_strike, max_put_strike = max_oi_strikes(chain)

    ce = next((q for q in chain.quotes if q.strike == short_ce_strike and q.option_type == "CE"), None)
    pe = next((q for q in chain.quotes if q.strike == short_pe_strike and q.option_type == "PE"), None)

    score = 50.0
    notes = []

    # Reward selling near/at strikes with heavy resting OI (writers already active there)
    if ce and max_call_strike and abs(ce.strike - max_call_strike) <= 100:
        score += 20
        notes.append(f"Short CE near max Call OI strike {max_call_strike}")
    if pe and max_put_strike and abs(pe.strike - max_put_strike) <= 100:
        score += 20
        notes.append(f"Short PE near max Put OI strike {max_put_strike}")

    # Penalize if change-in-OI shows aggressive fresh buildup against the short (short covering / breakout risk)
    if ce and ce.change_in_oi < 0 and ce.oi > 0 and abs(ce.change_in_oi) / max(ce.oi, 1) > 0.15:
        score -= 15
        notes.append("Sharp OI unwind at short Call strike — possible breakout risk")
    if pe and pe.change_in_oi < 0 and pe.oi > 0 and abs(pe.change_in_oi) / max(pe.oi, 1) > 0.15:
        score -= 15
        notes.append("Sharp OI unwind at short Put strike — possible breakdown risk")

    score = max(0.0, min(100.0, score))
    return score, "; ".join(notes) if notes else "OI distribution neutral"
