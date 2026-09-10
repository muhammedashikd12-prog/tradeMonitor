"""SQLite-backed persistence: trade journal, runtime settings overrides,
and daily P&L (needed by the daily-loss-limit / drawdown engines)."""
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
import csv
import io

engine = create_engine("sqlite:///condor_ai.db", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class TradeJournalEntry(Base):
    __tablename__ = "trade_journal"
    id = Column(Integer, primary_key=True)
    date = Column(String)
    entry_time = Column(String)
    spot_at_entry = Column(Float)
    expiry = Column(String)
    strikes_json = Column(Text)           # the four strikes, JSON-encoded
    entry_premium = Column(Float)
    exit_premium = Column(Float, nullable=True)
    iv = Column(Float)
    iv_percentile = Column(Float, nullable=True)
    oi_snapshot_json = Column(Text, nullable=True)
    market_regime = Column(String)
    ai_score = Column(Float)
    reason_for_entry = Column(Text)
    reason_for_exit = Column(Text, nullable=True)
    gross_pnl = Column(Float, nullable=True)
    charges = Column(Float, nullable=True)
    net_pnl = Column(Float, nullable=True)
    max_adverse_excursion = Column(Float, nullable=True)
    max_favorable_excursion = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class DailyPnL(Base):
    __tablename__ = "daily_pnl"
    id = Column(Integer, primary_key=True)
    date = Column(String, unique=True)
    realized_pnl = Column(Float, default=0.0)
    trading_disabled = Column(Integer, default=0)  # 0/1


class AccountState(Base):
    __tablename__ = "account_state"
    id = Column(Integer, primary_key=True)
    peak_equity = Column(Float, default=0.0)
    current_equity = Column(Float, default=0.0)
    updated_at = Column(DateTime, default=datetime.utcnow)


class SettingOverride(Base):
    __tablename__ = "setting_override"
    key = Column(String, primary_key=True)
    value = Column(String)


Base.metadata.create_all(engine)


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def export_journal_csv() -> str:
    db = SessionLocal()
    rows = db.query(TradeJournalEntry).all()
    buf = io.StringIO()
    if not rows:
        db.close()
        return ""
    writer = csv.writer(buf)
    cols = [c.name for c in TradeJournalEntry.__table__.columns]
    writer.writerow(cols)
    for r in rows:
        writer.writerow([getattr(r, c) for c in cols])
    db.close()
    return buf.getvalue()
