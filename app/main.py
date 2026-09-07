"""FastAPI entry point. Wires broker + all engines into REST endpoints and
a WebSocket broadcast for the frontend dashboard."""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
from datetime import datetime, date, time as date_time
from typing import Literal
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_session, export_journal_csv
from app.brokers.fyers_client import FyersClient
from app.brokers.base import BrokerConnectionError
from app.models import IronCondorStructure
from pydantic import BaseModel, Field
from app.services import strategy_engine, position_sizing, daily_loss_drawdown
from app.services import risk_engine, stop_loss_engine, profit_booking_engine
from app.services import journal_service, notification_service, execution_engine
from app.services import market_data_service

app = FastAPI(title="Condor AI")

# Local frontend (opened as a file or served on any localhost port) needs
# CORS to call this API from a different origin during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

broker = FyersClient(
    app_id=settings.fyers_app_id,
    secret_id=settings.fyers_secret_id,
    redirect_uri=settings.fyers_redirect_uri,
    access_token=settings.fyers_access_token,
)
exec_engine = execution_engine.ExecutionEngine(broker, symbol_prefix="NSE:NIFTY")

# In-memory holder for the currently open structure (single active position
# at a time, per the spec's "one trade card -> live monitor" flow). Persist
# this to DB if you need multi-position support.
_active_structure: IronCondorStructure | None = None
_active_expiry: str | None = None
_active_quantity: int = 0  # set from PositionSize.quantity when a trade is actually filled


class ManualLeg(BaseModel):
    leg: Literal["CALL BUY", "CALL SELL", "PUT SELL", "PUT BUY"]
    symbol: str = Field(min_length=1)
    strike: float = Field(gt=0)
    quantity: int = Field(gt=0)
    entry_price: float = Field(ge=0)


class ManualMonitorSetup(BaseModel):
    expiry: date
    legs: list[ManualLeg] = Field(min_length=4, max_length=4)
    call_sl: float | None = Field(default=None, ge=0)
    put_sl: float | None = Field(default=None, ge=0)


_manual_monitor: ManualMonitorSetup | None = None
_manual_previous_spot: float | None = None
_manual_previous_iv: float | None = None

_ws_clients: list[WebSocket] = []
_main_loop: asyncio.AbstractEventLoop | None = None


@app.on_event("startup")
async def _capture_loop():
    global _main_loop
    _main_loop = asyncio.get_running_loop()


def broadcast_sync(payload: dict) -> None:
    """Safe to call from sync endpoint functions, which FastAPI runs in a
    worker thread with no running event loop of their own."""
    if _main_loop is not None:
        asyncio.run_coroutine_threadsafe(_broadcast(payload), _main_loop)


# ---------------- Auth ----------------
@app.get("/fyers/login-url")
def fyers_login_url():
    return {"url": broker.generate_login_url()}


@app.get("/fyers/callback")
def fyers_callback(auth_code: str):
    try:
        token = broker.exchange_auth_code(auth_code)
    except BrokerConnectionError:
        return RedirectResponse(url="/?connected=0")
    return RedirectResponse(url="/?connected=1" if token else "/?connected=0")


# ---------------- Status ----------------
@app.get("/status")
def status():
    return {
        "broker_status": broker.connection_status(),
        "operating_mode": settings.operating_mode,
        "execution_mode": settings.execution_mode,
    }


@app.post("/manual-monitor/setup")
def setup_manual_monitor(payload: ManualMonitorSetup):
    global _manual_monitor, _manual_previous_spot, _manual_previous_iv
    expected = {"CALL BUY", "CALL SELL", "PUT SELL", "PUT BUY"}
    supplied = {leg.leg for leg in payload.legs}
    if supplied != expected:
        raise HTTPException(status_code=422, detail="Enter exactly one CALL BUY, CALL SELL, PUT SELL, and PUT BUY leg")
    if len(payload.legs) != len(supplied):
        raise HTTPException(status_code=422, detail="Each leg must be entered once")
    _manual_monitor = payload
    _manual_previous_spot = None
    _manual_previous_iv = None
    return {"saved": True, "expiry": payload.expiry.isoformat(), "legs": [leg.model_dump() for leg in payload.legs]}


@app.delete("/manual-monitor/setup")
def clear_manual_monitor():
    global _manual_monitor, _manual_previous_spot, _manual_previous_iv
    _manual_monitor = None
    _manual_previous_spot = None
    _manual_previous_iv = None
    return {"saved": False}


def _quote_value(quote: dict, *keys: str):
    for key in keys:
        if quote.get(key) is not None:
            return quote[key]
    return None


def _manual_snapshot() -> dict:
    if _manual_monitor is None:
        return {"configured": False, "connection": broker.connection_status()}
    if not broker.is_connected():
        return {"configured": True, "connection": "DISCONNECTED", "expiry": _manual_monitor.expiry.isoformat(),
                "legs": [leg.model_dump() | {"ltp": None} for leg in _manual_monitor.legs]}

    symbols = ["NSE:NIFTY50-INDEX"] + [leg.symbol for leg in _manual_monitor.legs]
    quotes = broker.get_quotes(symbols)
    spot_quote = quotes.get("NSE:NIFTY50-INDEX", {})
    spot = _quote_value(spot_quote, "lp")
    spot_change = _quote_value(spot_quote, "ch")
    spot_change_pct = _quote_value(spot_quote, "chp")
    live_legs = []
    by_name = {leg.leg: leg for leg in _manual_monitor.legs}
    for leg in _manual_monitor.legs:
        quote = quotes.get(leg.symbol, {})
        ltp = _quote_value(quote, "lp")
        pnl = None if ltp is None else (ltp - leg.entry_price if "BUY" in leg.leg else leg.entry_price - ltp) * leg.quantity
        live_legs.append(leg.model_dump() | {
            "ltp": ltp, "bid": _quote_value(quote, "bid"), "ask": _quote_value(quote, "ask"),
            "change": _quote_value(quote, "ch"), "volume": _quote_value(quote, "volume"),
            "oi": _quote_value(quote, "oi"), "iv": _quote_value(quote, "iv"), "pnl": pnl,
        })

    def leg_pnl(name: str):
        return next((leg["pnl"] for leg in live_legs if leg["leg"] == name and leg["pnl"] is not None), 0.0)

    call_buy, call_sell = by_name["CALL BUY"], by_name["CALL SELL"]
    put_sell, put_buy = by_name["PUT SELL"], by_name["PUT BUY"]
    call_credit = call_sell.entry_price - call_buy.entry_price
    put_credit = put_sell.entry_price - put_buy.entry_price
    net_credit = call_credit + put_credit
    total_quantity = min(leg.quantity for leg in _manual_monitor.legs)
    lower_short, upper_short = put_sell.strike, call_sell.strike
    lower_hedge, upper_hedge = put_buy.strike, call_buy.strike
    lower_be, upper_be = lower_short - net_credit, upper_short + net_credit
    call_iv = next((leg["iv"] for leg in live_legs if leg["leg"] == "CALL SELL"), None)
    put_iv = next((leg["iv"] for leg in live_legs if leg["leg"] == "PUT SELL"), None)
    short_ivs = [iv for iv in (call_iv, put_iv) if iv is not None]
    global _manual_previous_spot, _manual_previous_iv
    spot_move = None if spot is None or _manual_previous_spot is None else spot - _manual_previous_spot
    average_iv = sum(short_ivs) / len(short_ivs) if short_ivs else None
    iv_change = None if average_iv is None or _manual_previous_iv is None else average_iv - _manual_previous_iv
    _manual_previous_spot = spot
    _manual_previous_iv = average_iv

    distance_call = None if spot is None else upper_short - spot
    distance_put = None if spot is None else spot - lower_short
    distance_lower_be = None if spot is None else spot - lower_be
    distance_upper_be = None if spot is None else upper_be - spot
    proximity = min(abs(distance_call or 10**9), abs(distance_put or 10**9))
    risk = "DANGER" if proximity <= 50 else "WATCH" if proximity <= 100 else "SAFE"
    alerts = []
    if distance_call is not None and distance_call <= 100:
        alerts.append(f"NIFTY is {abs(distance_call):.0f} points from Call Short Strike")
    if distance_put is not None and distance_put <= 100:
        alerts.append(f"NIFTY is {abs(distance_put):.0f} points from Put Short Strike")
    if spot is not None and (spot <= lower_be or spot >= upper_be):
        alerts.append("NIFTY has crossed an Iron Condor breakeven")
    if spot_move is not None and abs(spot_move) >= 100:
        alerts.append(f"Large NIFTY movement: {spot_move:+.0f} points")
    if iv_change is not None and iv_change >= 2:
        alerts.append(f"Short IV increased by {iv_change:.1f} points")
    call_pnl = leg_pnl("CALL BUY") + leg_pnl("CALL SELL")
    put_pnl = leg_pnl("PUT SELL") + leg_pnl("PUT BUY")
    if _manual_monitor.call_sl is not None and call_pnl <= -abs(_manual_monitor.call_sl):
        alerts.append("Call-side manual SL reached")
    if _manual_monitor.put_sl is not None and put_pnl <= -abs(_manual_monitor.put_sl):
        alerts.append("Put-side manual SL reached")
    expiry_at = datetime.combine(_manual_monitor.expiry, date_time(15, 30))
    seconds_left = max(0, int((expiry_at - datetime.now()).total_seconds()))
    return {"configured": True, "connection": broker.connection_status(), "expiry": _manual_monitor.expiry.isoformat(),
            "spot": spot, "spot_change": spot_change, "spot_change_pct": spot_change_pct,
            "legs": live_legs, "total_pnl": sum(leg["pnl"] or 0 for leg in live_legs),
            "call_spread_pnl": call_pnl, "put_spread_pnl": put_pnl,
            "call_credit": call_credit, "put_credit": put_credit, "net_credit": net_credit,
            "max_initial_profit": net_credit * total_quantity, "lower_short": lower_short,
            "upper_short": upper_short, "lower_hedge": lower_hedge, "upper_hedge": upper_hedge,
            "lower_breakeven": lower_be, "upper_breakeven": upper_be,
            "distance_call_short": distance_call, "distance_put_short": distance_put,
            "distance_lower_breakeven": distance_lower_be, "distance_upper_breakeven": distance_upper_be,
            "risk": risk, "call_iv": call_iv, "put_iv": put_iv, "average_short_iv": average_iv,
            "iv_change": iv_change, "seconds_to_expiry": seconds_left, "call_sl": _manual_monitor.call_sl,
            "put_sl": _manual_monitor.put_sl, "alerts": alerts}


@app.get("/manual-monitor")
def manual_monitor():
    try:
        return _manual_snapshot()
    except BrokerConnectionError as exc:
        return {"configured": _manual_monitor is not None, "connection": "RECONNECTING", "error": str(exc), "alerts": ["Broker connection lost"]}


# ---------------- Raw option chain (for the Option Chain screen) ----------------
@app.get("/chain")
def chain_endpoint(symbol: str = "NSE:NIFTY50-INDEX"):
    try:
        expiry = broker.get_nearest_expiry(symbol)
        chain = broker.get_option_chain(symbol, expiry)
    except BrokerConnectionError as e:
        raise HTTPException(status_code=503, detail=f"Broker/data unavailable: {e}")
    return chain.model_dump(mode="json")


# ---------------- Analysis ----------------
@app.get("/analyze")
def analyze(symbol: str = "NSE:NIFTY50-INDEX", db: Session = Depends(get_session)):
    allowed, msg = daily_loss_drawdown.is_trading_allowed_today(db)
    if not allowed:
        raise HTTPException(status_code=423, detail=msg)

    try:
        expiry = broker.get_nearest_expiry(symbol)
        expiry_verified = True
    except BrokerConnectionError as e:
        expiry = str(date.today())
        expiry_verified = False

    try:
        chain = broker.get_option_chain(symbol, expiry)
        margin = broker.get_margin_available()
        broker_status = broker.connection_status()
    except BrokerConnectionError as e:
        raise HTTPException(status_code=503, detail=f"Broker/data unavailable: {e}")

    index_symbol = "NSE:NIFTY50-INDEX"
    try:
        context = market_data_service.get_market_context(broker, symbol, index_symbol)
    except BrokerConnectionError as e:
        # Missing/unreliable market data is itself a no-trade condition —
        # don't silently fall back to stubbed values, surface it.
        raise HTTPException(status_code=503, detail=f"Market data unavailable: {e}")

    decision = strategy_engine.analyze(
        chain=chain, price_bars=context["price_bars"], india_vix=context["india_vix"],
        prev_day_high=context["prev_day_high"], prev_day_low=context["prev_day_low"],
        available_margin=margin, broker_status=broker_status,
        expiry_verified=expiry_verified, event_risk_flag=context["event_risk_flag"],
    )

    global _active_structure, _active_expiry
    if decision.decision == "TRADE" and decision.structure:
        _active_structure = decision.structure
        _active_expiry = expiry
        journal_service.log_entry(db, decision, chain.spot, expiry)

    broadcast_sync({"type": "decision", "data": decision.model_dump(mode="json")})
    return decision.model_dump(mode="json")


# ---------------- Position sizing ----------------
@app.post("/size")
def size(structure: IronCondorStructure, margin_per_lot: float):
    available_margin = broker.get_margin_available()
    return position_sizing.size_position(structure, margin_per_lot, available_margin,
                                          lot_size=settings.nifty_lot_size).model_dump()


# ---------------- Execution ----------------
@app.post("/execute")
def execute(db: Session = Depends(get_session)):
    if _active_structure is None:
        raise HTTPException(status_code=400, detail="No active structure to execute")
    allowed, msg = daily_loss_drawdown.is_trading_allowed_today(db)
    if not allowed:
        raise HTTPException(status_code=423, detail=msg)

    margin = broker.get_margin_available()
    size_result = position_sizing.size_position(_active_structure, margin_per_lot=margin, available_margin=margin,
                                                 lot_size=settings.nifty_lot_size)
    result = exec_engine.execute_iron_condor(_active_structure, size_result, _active_expiry, margin)
    if not result.success:
        raise HTTPException(status_code=409, detail=result.aborted_reason)

    global _active_quantity
    _active_quantity = size_result.quantity
    return {"success": True, "orders": {k: v.model_dump() for k, v in result.orders.items()}}


# ---------------- Live monitoring ----------------
@app.get("/monitor")
def monitor():
    if _active_structure is None:
        raise HTTPException(status_code=404, detail="No active position")
    live_ltp = {leg.role: broker.get_ltp(leg.symbol or f"NSE:NIFTY{int(leg.strike)}{leg.option_type}")
                for leg in _active_structure.legs}
    spot = broker.get_ltp("NSE:NIFTY50-INDEX")

    from app.services.expected_move import days_to_expiry as _dte
    expiry_date = date.fromisoformat(_active_expiry) if _active_expiry and "-" in _active_expiry else date.today()
    dte_days = _dte(datetime.now(), expiry_date)

    call_sl = stop_loss_engine.check_call_side_sl(_active_structure, live_ltp)
    put_sl = stop_loss_engine.check_put_side_sl(_active_structure, live_ltp)

    if call_sl.triggered:
        exec_engine.unwind_vertical(_active_structure, call_sl.close_order, _active_expiry,
                                     qty=_active_quantity or settings.nifty_lot_size)
        notification_service.notify("call_sl", "🔴", call_sl.reason)
    if put_sl.triggered:
        exec_engine.unwind_vertical(_active_structure, put_sl.close_order, _active_expiry,
                                     qty=_active_quantity or settings.nifty_lot_size)
        notification_service.notify("put_sl", "🔴", put_sl.reason)

    snapshot = risk_engine.build_risk_snapshot(
        _active_structure, live_ltp, spot,
        call_sl_distance=call_sl.pnl_per_share - call_sl.sl_threshold_per_share,
        put_sl_distance=put_sl.pnl_per_share - put_sl.sl_threshold_per_share,
        days_to_expiry=dte_days, lot_size=settings.nifty_lot_size,
    )
    should_book, book_reason = profit_booking_engine.should_evaluate_exit(_active_structure, live_ltp, dte_days)

    payload = {
        "risk": snapshot.model_dump(),
        "call_sl": call_sl.model_dump(),
        "put_sl": put_sl.model_dump(),
        "profit_booking": {"should_evaluate_exit": should_book, "reason": book_reason},
        "structure": _active_structure.model_dump(mode="json"),
        "live_ltp": live_ltp,
        "spot": spot,
    }
    broadcast_sync({"type": "monitor", "data": payload})
    return payload


# ---------------- Journal ----------------
@app.get("/journal")
def journal_list(db: Session = Depends(get_session), limit: int = 50):
    from app.database import TradeJournalEntry
    rows = db.query(TradeJournalEntry).order_by(TradeJournalEntry.id.desc()).limit(limit).all()
    return [
        {
            "id": r.id, "date": r.date, "entry_time": r.entry_time, "spot_at_entry": r.spot_at_entry,
            "expiry": r.expiry, "entry_premium": r.entry_premium, "exit_premium": r.exit_premium,
            "market_regime": r.market_regime, "ai_score": r.ai_score,
            "net_pnl": r.net_pnl, "reason_for_exit": r.reason_for_exit,
        }
        for r in rows
    ]


@app.get("/journal/export")
def journal_export():
    return {"csv": export_journal_csv()}


# ---------------- Settings ----------------
@app.get("/settings")
def get_settings():
    return settings.model_dump()


# ---------------- Event risk (manual flag until a calendar feed is wired in) ----------------
@app.post("/event-risk")
def set_event_risk(flag: bool):
    market_data_service.set_event_risk(flag)
    return {"event_risk_flag": market_data_service.get_event_risk()}


@app.get("/event-risk")
def get_event_risk():
    return {"event_risk_flag": market_data_service.get_event_risk()}


# ---------------- WebSocket broadcast ----------------
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.append(ws)
    try:
        while True:
            await ws.receive_text()  # keepalive / ignored
    except WebSocketDisconnect:
        _ws_clients.remove(ws)


async def _broadcast(payload: dict):
    dead = []
    for client in _ws_clients:
        try:
            await client.send_text(json.dumps(payload))
        except Exception:
            dead.append(client)
    for d in dead:
        _ws_clients.remove(d)


# Serve the monitor from the same origin as the API, so local use needs only
# one URL and the FYERS callback can stay on port 8000.
app.mount("/", StaticFiles(directory=Path(__file__).resolve().parent.parent / "frontend", html=True), name="frontend")
