"""Persists every trade (entry through exit) to SQLite, with CSV export."""
from __future__ import annotations
import json
from datetime import datetime
from sqlalchemy.orm import Session
from app.database import TradeJournalEntry
from app.models import IronCondorStructure, TradeDecision


def log_entry(db: Session, decision: TradeDecision, spot: float, expiry: str) -> TradeJournalEntry:
    structure = decision.structure
    strikes = {leg.role: leg.strike for leg in structure.legs} if structure else {}
    entry = TradeJournalEntry(
        date=str(datetime.now().date()),
        entry_time=datetime.now().strftime("%H:%M:%S"),
        spot_at_entry=spot,
        expiry=expiry,
        strikes_json=json.dumps(strikes),
        entry_premium=structure.net_credit if structure else 0.0,
        iv=decision.iv.iv_average,
        iv_percentile=decision.iv.iv_percentile,
        market_regime=decision.regime.regime,
        ai_score=decision.score.total,
        reason_for_entry="; ".join(decision.reasoning),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def log_exit(db: Session, entry_id: int, exit_premium: float, gross_pnl: float, charges: float,
             reason_for_exit: str, mae: float | None = None, mfe: float | None = None) -> TradeJournalEntry:
    entry = db.query(TradeJournalEntry).get(entry_id)
    if not entry:
        raise ValueError(f"Journal entry {entry_id} not found")
    entry.exit_premium = exit_premium
    entry.gross_pnl = gross_pnl
    entry.charges = charges
    entry.net_pnl = gross_pnl - charges
    entry.reason_for_exit = reason_for_exit
    entry.max_adverse_excursion = mae
    entry.max_favorable_excursion = mfe
    db.commit()
    db.refresh(entry)
    return entry
