# ◈ STATION DESK

**A gold trading desk in a browser.** The STATION doctrine — zones, sweeps,
appointments, honest scoring — ported out of Pine Script into a Python engine,
wrapped in a real API, and given a face a trader can live in front of.

```bash
./run.sh            # → http://localhost:8787
```

---

## What it is

Gold does not wander. It **delivers** — from one location to the next, all day.
A *station* is a location where size once moved the market. A *pool* is resting
money. A structure turn **at** a station, fuelled by a **sweep** that trapped the
other side, is an appointment worth keeping. Everything else is scenery, and this
desk says so out loud instead of drawing an arrow.

The whole product is built around one uncomfortable idea: **the scoreboard is not
allowed to flatter the strategy.**

---

## Architecture

```
frontend/                    zero-dependency SPA, custom canvas tape
  index.html · css/desk.css · js/chart.js · js/app.js
backend/app/
  main.py                    FastAPI: REST + WebSocket + static host
  service.py                 the desk: engines, broker, live loop, broadcast
  engine/
    types.py                 Zone · Pool · Trade · Signal · Bucket
    config.py                the calibration constitution (+ change hash)
    indicators.py            ATR/EMA/RSI/DMI/pivots/Wilson — streaming safe
    station.py               THE ENGINE: sensors → lifecycle → appointments
    court.py                 honest bookkeeping and verdicts
  data/providers.py          yfinance → CSV → synthetic (never goes dark)
  execution/
    base.py                  Broker interface + risk-based sizing
    paper.py                 paper broker with spread, commission, margin
backtest.py                  command-line court, with A/B on any gate
tests/test_desk.py           14 tests that lock the canon
```

**Extending it** is deliberate: implement `Broker` for a real venue, or
`DataProvider` for a real feed, and nothing else changes. Live venues are opt-in
by construction — the paper broker is the default and always will be.

---

## The engine, in the order it thinks

1. **Sensors** — fair value gaps, order blocks, pivots, equal-high/low pools,
   structure breaks (CHoCH/BOS), and sweeps of pools, swing extremes and the
   previous day's range. Sensors see; they never vote.
2. **Zone lifecycle** — ACTIVE → TOUCHED → CONSUMED → INVERTED (breaker) → ARCHIVED.
3. **Scoring** — size ≥1 ATR (+2) · order block (+1) · first touch (+1) ·
   with trend (+1). Three or better is a station.
4. **Appointments**
   - **POSITION** — turn at a station, sweep behind it, route clear, session
     licensed, a named destination ≥ MIN_RR away.
   - **POOL-RIDE** — first touch with an unswept pool just beyond: a ferry ticket.
   - **REACTION** — the reflex trade. Paper by default; it measured uncollectable.
   - **BREAKER** — door two from the notebook. On trial, tracked either way.
5. **Fills** at the **next bar's open** — same-bar fills are structurally impossible.
6. **Management** — stop checked **before** target, trail optional, clock enforced,
   timeouts recorded at their real R.
7. **Governors** — daily and weekly loss budgets, drawdown mode, and a Wilson-interval
   kill switch that suspends the engine when live results break their own floor.

---

## The court

Every pattern is tried separately and labelled honestly:

- `NEED-DATA` under 20 trades · `PROVISIONAL` under 50 · `STABLE` beyond
- `STRUCTURE` only when **both** directions are profitable; otherwise `BETA`
- Live buckets move capital. Paper buckets are tracked and never counted.
- Splits by **session** and by **destination type** answer the only question that
  matters over time: *which trips actually pay?*

Costs are charged, spreads are crossed, and a bar that touches both stop and
target is resolved as the **stop**.

---

## API

| | |
|---|---|
| `GET /api/health` | source, symbols, calibration hash |
| `GET /api/market/candles` | OHLCV |
| `GET /api/market/quote` | live price |
| `GET /api/engine/snapshot` | zones, pools, route, signals, court, the read |
| `GET /api/engine/read` | one paragraph a human can act on |
| `GET /api/engine/scan` | every timeframe, one verdict each |
| `POST /api/engine/backtest` | run the court over history |
| `GET/POST /api/engine/config` | read or change the calibration (hash moves) |
| `GET /api/broker/account` · `/positions` · `/suggest` | the book |
| `POST /api/broker/order` · `/close/{id}` · `/reset` · `/auto` | execution |
| `WS /ws` | live ticks, reads, signals, fills |

---

## Instruments

Specs are real, which is why the desk can tell you the truth about a small account:

| symbol | point value | lot step | margin/lot |
|---|---|---|---|
| XAUUSD | $10 | 0.01 | $200 |
| MGC | $10 | 1 | $1,500 |
| GC | $100 | 1 | $15,000 |

Size is derived from risk and **rounded down** to the lot step. A $1,000 account
asking for MGC is refused with the arithmetic, not a shrug.

---

## Honest limits

- One instrument, one history, small samples. Every number is a **measurement of
  this engine on this tape**, never a promise about the next one.
- `yfinance` intraday history is capped by the vendor (60 days sub-hourly), so
  "two years of 5-minute data" is not something this or any free feed can give you.
- The synthetic tape exists so the product always runs. It is **not** evidence.
- Auto-execution routes to the paper broker only. Wiring a live venue is a
  deliberate act requiring credentials and a human who means it.

---

## Test

```bash
python3 -m pytest tests/ -q          # 14 tests: the canon
python3 backtest.py --tf 30m --bars 2000 --sweep-gate both
```

*Fills next bar open · stop checked first · ambiguous bar is a loss.*

## About

Built by Jitheandra Subramaniam with AI coding agents (Claude Code), directed and reviewed by me. Part of my portfolio: [github.com/Jitheandra98](https://github.com/Jitheandra98).

© 2026 Jitheandra Subramaniam. All rights reserved. This code is shared for viewing; no license to copy, modify or redistribute it is granted.
