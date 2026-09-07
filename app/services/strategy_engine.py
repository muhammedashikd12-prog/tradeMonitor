"""Orchestrates all engines into one TradeDecision. This is the single
entry point the API/dashboard calls."""
from __future__ import annotations
from datetime import datetime
from typing import List

from app.models import (OptionChainSnapshot, PriceBar, IronCondorStructure,
                         LegAction, TradeDecision)
from app.services import iv_greeks_engine as iv_engine
from app.services import expected_move as em_engine
from app.services import market_regime_engine as regime_engine
from app.services import time_of_day_engine as tod_engine
from app.services import oi_analysis
from app.services import strike_selection_engine as strike_engine
from app.services import wing_width_engine as wing_engine
from app.services import ai_score_engine as score_engine
from app.services import no_trade_engine


def _find_quote(chain: OptionChainSnapshot, strike: float, opt_type: str):
    return next((q for q in chain.quotes if q.strike == strike and q.option_type == opt_type), None)


def build_structure(chain: OptionChainSnapshot, short_ce, short_pe, wing_choice, lot_size: int = None) -> IronCondorStructure:
    from app.config import settings
    if lot_size is None:
        lot_size = chain.lot_size or settings.nifty_lot_size

    ce_hedge_strike = short_ce.quote.strike + wing_choice.width
    pe_hedge_strike = short_pe.quote.strike - wing_choice.width
    ce_hedge = _find_quote(chain, ce_hedge_strike, "CE")
    pe_hedge = _find_quote(chain, pe_hedge_strike, "PE")

    legs: List[LegAction] = [
        LegAction(role="call_hedge", action="BUY", option_type="CE", strike=ce_hedge_strike,
                  ltp=ce_hedge.ltp, iv=ce_hedge.iv, delta=ce_hedge.delta, oi=ce_hedge.oi, symbol=ce_hedge.symbol),
        LegAction(role="call_short", action="SELL", option_type="CE", strike=short_ce.quote.strike,
                  ltp=short_ce.quote.ltp, iv=short_ce.quote.iv, delta=short_ce.quote.delta, oi=short_ce.quote.oi,
                  symbol=short_ce.quote.symbol),
        LegAction(role="put_short", action="SELL", option_type="PE", strike=short_pe.quote.strike,
                  ltp=short_pe.quote.ltp, iv=short_pe.quote.iv, delta=short_pe.quote.delta, oi=short_pe.quote.oi,
                  symbol=short_pe.quote.symbol),
        LegAction(role="put_hedge", action="BUY", option_type="PE", strike=pe_hedge_strike,
                  ltp=pe_hedge.ltp, iv=pe_hedge.iv, delta=pe_hedge.delta, oi=pe_hedge.oi, symbol=pe_hedge.symbol),
    ]

    gross_credit = short_ce.quote.ltp + short_pe.quote.ltp
    net_credit = gross_credit - ce_hedge.ltp - pe_hedge.ltp
    max_profit = net_credit * lot_size
    max_loss = wing_choice.estimated_max_loss
    breakeven_upper = short_ce.quote.strike + net_credit
    breakeven_lower = short_pe.quote.strike - net_credit

    return IronCondorStructure(
        legs=legs, wing_width=wing_choice.width,
        gross_credit=round(gross_credit, 2), net_credit=round(net_credit, 2),
        max_profit=round(max_profit, 2), max_loss=round(max_loss, 2),
        breakeven_upper=round(breakeven_upper, 1), breakeven_lower=round(breakeven_lower, 1),
        risk_reward=wing_choice.risk_reward,
        wing_width_justification=wing_choice.justification,
    )


def analyze(chain: OptionChainSnapshot, price_bars: List[PriceBar], india_vix: float,
            prev_day_high: float, prev_day_low: float, available_margin: float,
            broker_status: str, expiry_verified: bool, event_risk_flag: bool = False,
            lot_size: int = None) -> TradeDecision:
    from app.config import settings
    if lot_size is None:
        lot_size = chain.lot_size or settings.nifty_lot_size
    now = chain.timestamp or datetime.now()

    # 1. Regime
    regime = regime_engine.classify_regime(price_bars, india_vix, prev_day_high, prev_day_low, event_risk_flag)

    # 2. Preliminary IV (ATM only, before strikes chosen)
    prelim_iv = iv_engine.compute_iv(chain)
    iv_engine.record_daily_iv_once(prelim_iv.iv_average, today=now.date())

    # 3. Expected move
    em = em_engine.compute_expected_move(chain.spot, prelim_iv.iv_average, now, chain.expiry)

    # 4. Time of day
    time_score, time_note = tod_engine.time_of_day_score(now)

    reasoning: List[str] = [regime.reasons[-1] if regime.reasons else "",
                            f"IV regime: {prelim_iv.classification} ({prelim_iv.iv_average}%)",
                            time_note]
    risks: List[str] = []
    invalidation: List[str] = []

    try:
        # 5. Strike selection
        short_ce, short_pe = strike_engine.select_short_strikes(chain, em)

        # 6. Re-compute IV using actual short strikes
        iv_result = iv_engine.compute_iv(chain, short_ce.quote.strike, short_pe.quote.strike)

        # 7. Wing width
        wing_choice = wing_engine.evaluate_wing_widths(
            chain, short_ce.quote.strike, short_pe.quote.strike, em, available_margin, lot_size
        )
        structure = build_structure(chain, short_ce, short_pe, wing_choice, lot_size)

        # 8. OI score
        oi_score_val, oi_note = oi_analysis.oi_score(chain, short_ce.quote.strike, short_pe.quote.strike)
        reasoning.append(oi_note)

        # 9. No-trade checklist
        no_trade = no_trade_engine.check_no_trade_conditions(
            chain, regime, em, short_ce.quote, short_pe.quote,
            available_margin, wing_choice.estimated_max_loss,
            broker_status, expiry_verified,
        )

        # 10. Expected-move / strike-quality sub-scores (avg of both sides)
        em_score = (
            em_engine.strike_buffer_score(short_ce.quote.strike, em, "CE") +
            em_engine.strike_buffer_score(short_pe.quote.strike, em, "PE")
        ) / 2
        strike_quality_score = (short_ce.score + short_pe.score) / 2
        liquidity_score = min(
            strike_selection_liquidity(short_ce), strike_selection_liquidity(short_pe)
        )
        iv_env_score = {"LOW": 55, "PREFERRED": 100, "HIGH": 75, "EXTREME": 20}[iv_result.classification]

        score = score_engine.compute_score(
            market_regime_0_100=regime.score_0_100,
            iv_environment_0_100=iv_env_score,
            expected_move_0_100=em_score,
            strike_quality_0_100=strike_quality_score,
            price_action_0_100=regime.score_0_100,  # reuse regime price-action signal
            oi_0_100=oi_score_val,
            liquidity_0_100=liquidity_score,
            time_0_100=time_score,
            risk_reward_0_100=min(100.0, wing_choice.risk_reward * 100),
        )

        reasoning.append(f"Short strikes: {short_ce.quote.strike}CE / {short_pe.quote.strike}PE, "
                          f"wing width {wing_choice.width}")
        reasoning.append(wing_choice.justification)
        risks.append(f"Max loss ₹{structure.max_loss:.0f} if NIFTY closes beyond "
                      f"{structure.breakeven_lower}/{structure.breakeven_upper}")
        if iv_result.classification in ("HIGH", "EXTREME"):
            risks.append("IV elevated — richer premium but implies market pricing bigger moves")
        invalidation = [
            f"NIFTY closing beyond breakeven {structure.breakeven_lower} or {structure.breakeven_upper}",
            "Regime shifting to TRENDING or HIGH_VOLATILITY intraday",
            "Broker/data feed becoming unreliable before entry is confirmed",
        ]

        if not no_trade.passed:
            decision = "NO_TRADE"
            risks.extend(no_trade.failed_reasons)
        elif time_score == 0:
            decision = "NO_TRADE"
            risks.append(time_note)
        elif score.classification in ("HIGH_QUALITY", "ACCEPTABLE"):
            decision = "TRADE"
        elif score.classification == "WAIT":
            decision = "WAIT"
        else:
            decision = "NO_TRADE"

        return TradeDecision(
            decision=decision, score=score, structure=structure, regime=regime,
            iv=iv_result, expected_move=em, no_trade_check=no_trade,
            reasoning=reasoning, risks=risks, invalidation=invalidation,
        )

    except ValueError as e:
        # Any hard failure (no valid strikes, no wing width fits margin, etc.) => NO TRADE
        zero_score = score_engine.compute_score(0, 0, 0, 0, 0, 0, 0, 0, 0)
        return TradeDecision(
            decision="NO_TRADE", score=zero_score, structure=None, regime=regime,
            iv=prelim_iv, expected_move=em,
            no_trade_check=no_trade_engine.NoTradeCheck if False else _fallback_no_trade(str(e)),
            reasoning=reasoning, risks=[str(e)], invalidation=[],
        )


def _fallback_no_trade(reason: str):
    from app.models import NoTradeCheck
    return NoTradeCheck(passed=False, failed_reasons=[reason])


def strike_selection_liquidity(candidate) -> float:
    # candidate.reasons[1] holds "Liquidity score X" from strike_selection_engine
    for r in candidate.reasons:
        if r.startswith("Liquidity score"):
            return float(r.split(" ")[-1])
    return 50.0
