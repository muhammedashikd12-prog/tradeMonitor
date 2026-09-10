"""Daily loss limit + account drawdown gating. When breached, trading is
disabled until the next permitted session — never silently bypassed."""
from __future__ import annotations
from datetime import date
from sqlalchemy.orm import Session
from app.database import DailyPnL, AccountState
from app.config import settings


def get_or_create_today(db: Session) -> DailyPnL:
    today = str(date.today())
    row = db.query(DailyPnL).filter_by(date=today).first()
    if not row:
        row = DailyPnL(date=today, realized_pnl=0.0, trading_disabled=0)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def record_realized_pnl(db: Session, pnl_delta: float) -> DailyPnL:
    row = get_or_create_today(db)
    row.realized_pnl += pnl_delta
    max_daily_loss = -settings.total_capital * settings.max_daily_loss_pct / 100
    if row.realized_pnl <= max_daily_loss:
        row.trading_disabled = 1
    db.commit()
    db.refresh(row)
    return row


def is_trading_allowed_today(db: Session) -> tuple[bool, str]:
    row = get_or_create_today(db)
    if row.trading_disabled:
        return False, (f"Daily loss limit hit: realized P&L ₹{row.realized_pnl:.0f} "
                        f"(limit {settings.max_daily_loss_pct}% of capital)")
    return True, "Within daily loss limit"


def update_equity_and_check_drawdown(db: Session, current_equity: float) -> tuple[bool, float, str]:
    """Returns (allowed_to_trade, drawdown_pct, message)."""
    state = db.query(AccountState).first()
    if not state:
        state = AccountState(peak_equity=current_equity, current_equity=current_equity)
        db.add(state)
        db.commit()
        db.refresh(state)

    state.peak_equity = max(state.peak_equity, current_equity)
    state.current_equity = current_equity
    db.commit()

    if state.peak_equity <= 0:
        return True, 0.0, "No equity history yet"

    drawdown_pct = round((state.peak_equity - state.current_equity) / state.peak_equity * 100, 2)

    if drawdown_pct >= settings.drawdown_stop_pct:
        return False, drawdown_pct, f"STOP TRADING: drawdown {drawdown_pct}% >= {settings.drawdown_stop_pct}% limit"
    if drawdown_pct >= 10:
        return True, drawdown_pct, f"HIGH RISK / REVIEW: drawdown {drawdown_pct}%"
    if drawdown_pct >= 5:
        return True, drawdown_pct, f"REDUCE SIZE: drawdown {drawdown_pct}%"
    return True, drawdown_pct, "NORMAL"
