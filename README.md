# NIFTY Iron Condor Live Monitor

FastAPI backend and lightweight dashboard for manually entered NIFTY Iron
Condor positions. It connects to **Fyers** for live quotes and calculates
position P&L, breakevens, distances, IV, countdowns, and alert-only manual
stop losses. It does not select strikes, place orders, or backtest.

⚠️ This is a decision-support and (optionally) execution system. It never
guarantees profit, never assumes a fill, and never auto-executes unless you
explicitly flip `EXECUTION_MODE=on` in settings.

## Architecture

```
app/
  config.py              Settings (env-driven), all thresholds configurable
  models.py               Pydantic schemas shared across services
  database.py              SQLite journal + settings persistence
  brokers/
    base.py                Abstract BrokerClient interface (broker-agnostic)
    fyers_client.py         Fyers implementation of BrokerClient
  services/
    iv_greeks_engine.py     IV calc/classification, skew, percentile
    expected_move.py        Expected move from IV + spot + time to expiry
    market_regime_engine.py RANGE / UNCERTAIN / TRENDING / HIGH-VOL / EVENT
    time_of_day_engine.py   Entry-window scoring
    oi_analysis.py           OI concentration / shift analysis
    strike_selection_engine.py  Multi-factor short-strike scoring
    wing_width_engine.py    Chooses 100/150/200/250 wing width
    ai_score_engine.py      Weighted 0–100 composite score + classification
    no_trade_engine.py       Hard-reject checklist (liquidity, spread, etc.)
    strategy_engine.py       Orchestrator: builds the 4-leg Iron Condor
    risk_engine.py           Live greeks, distances, drawdown classification
    stop_loss_engine.py      Per-vertical SL monitor + ordered unwind
    profit_booking_engine.py Premium-captured tracking + exit-worth calc
    position_sizing.py       Capital → lots, respecting max risk %
    daily_loss_drawdown.py   Daily loss limit + account drawdown gating
    execution_engine.py      Pre-trade validation + order placement + fill verification
    journal_service.py       Persists every trade to SQLite, CSV export
    notification_service.py  Alert throttling + dispatch hooks
    market_data_service.py   Caches candles/VIX/prev-day-range, manual event-risk flag
  main.py                    FastAPI app: REST endpoints + WebSocket broadcast
frontend/
  index.html / styles.css / app.js   Dark trading-terminal dashboard (no build step)
```

## Setup — backend

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your Fyers app_id / secret / redirect_uri, and NIFTY_LOT_SIZE
uvicorn app.main:app --reload --port 8000
```

You still need to complete the Fyers OAuth login flow once (see
`brokers/fyers_client.py::generate_login_url` /
`brokers/fyers_client.py::exchange_auth_code`) to obtain an access token —
this cannot be done headlessly since Fyers requires an interactive browser
login redirect. Hit `GET /fyers/login-url`, open it, log in, then hit
`GET /fyers/callback?auth_code=...` with the code from the redirect URL.

## Setup — frontend

No npm/build step is required. The FastAPI backend serves the frontend, so
start only the backend and open `http://127.0.0.1:8000` in a browser. Enter the expiry and
the four broker symbols, strikes, quantities, and entry prices. The monitor
refreshes live quote data automatically and never sends exit orders.

Manual monitor endpoints are `POST /manual-monitor/setup`,
`GET /manual-monitor`, and `DELETE /manual-monitor/setup`.

Screens: **Dashboard** (AI score gauge, decision, 4-leg structure, reasoning/
risks/invalidation — click Analyze, or enable 30s auto-refresh), **Option
Chain** (full chain with ATM and AI-selected strikes highlighted), **Live
Position** (P&L, per-leg table, risk panel, SL/profit-booking status — only
populates once `/analyze` has produced a `TRADE` decision), **Trade
Journal** (history + CSV export), **Settings** (read-only view of current
config — edit via `.env`/`config.py`, there's no write-back endpoint yet).

## What is real vs. what you must verify

- The **scoring, regime, IV-classification, wing-width, risk, stop-loss,
  position-sizing, and no-trade logic** is fully implemented and was
  verified end-to-end (20-point test suite covering every engine and
  endpoint, plus a real headless-browser pass over the frontend).
- The **Fyers client** follows the real `fyers-apiv3` SDK shape (auth flow,
  `optionchain()`, `place_order()`, `positions()`, `history()`, WebSocket
  ticker) and now captures the broker's own `symbol` string per strike
  rather than reconstructing one — but Fyers' exact field names/endpoints
  do change, so verify against https://myapi.fyers.in/docsv3 before going
  live, and paper-trade first.
- **Lot size** is confirmed at runtime from Fyers' public Symbol Master CSV
  where possible, and falls back to `NIFTY_LOT_SIZE` in `.env` on any
  failure — the CSV column layout used for this was inferred from
  community-documented samples, not an official schema, so it's worth a
  one-time visual check against
  https://public.fyers.in/sym_details/NSE_FO.csv for your instrument.
- No infra here runs 24/7 by itself — deploy this FastAPI app on a server/VM
  you control (or a small cloud box) for it to actually watch the live
  market during trading hours.
