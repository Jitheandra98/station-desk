# STATION DESK — walkthrough

A gold/crypto trading desk: a market-structure engine ported from Pine Script
into Python, served over FastAPI + WebSocket, with a zero-dependency front end
and a paper broker that refuses to flatter itself.

**Run it:** `./run.sh` → http://localhost:8787 · **Test it:** `python3 -m pytest tests/ -q`

---

## 1. The problem

Retail trading tools lie in a specific, boring way: their backtests fill at
prices that were never available, resolve ambiguous bars as wins, and delete
trades that never resolved. The result looks profitable and isn't.

This system was built the other way round — the scoring rules were fixed first,
and the strategy had to survive them.

## 2. The four rules everything else is built on

| Rule | Where it lives | Why |
|---|---|---|
| Fills at the **next bar's open** | `station.py::_fill_pending` refuses `bar >= i` | A signal computed on a close cannot be filled at that close |
| **Stop checked before target** | `_manage_open`, and `paper.py::mark` | One bar containing both levels is unknowable; assume the worse |
| **Timeouts recorded**, never deleted | `_manage_open` clock branch | Dropping unresolved trades inflates the win rate |
| **Costs charged** every trade | `_close`, and the broker's spread + commission | 0.5pt on a 4pt stop is 12% of R |

`tests/test_desk.py` exists to make these unbreakable — 14 tests, each one a
rule that must never silently regress.

## 3. Architecture

```
Browser (vanilla JS, custom canvas)
        │  REST + WebSocket
FastAPI (main.py) ── DeskService (service.py) ── live poll loop
        │                     │
   StationEngine        PaperBroker ── Broker ABC ← real venue plugs in here
        │
  MarketData ── yfinance → CSV → synthetic (never goes dark)
```

Design choices worth defending:

- **The engine is a pure state machine.** `step(bar)` in, signals out. No I/O,
  no clock, no network. That is why the same code path serves the live desk,
  the backtester and the parameter sweep — and why it is trivially testable.
- **Adapters at both edges.** `DataProvider` and `Broker` are ABCs. Adding a
  real broker is one class; nothing else moves. The paper broker is the default
  by construction, not by configuration.
- **Instrument specs, not hardcoded gold.** Lot step, point value and margin per
  symbol. Sizing rounds *down* to the lot step, which is why a $1,000 account
  asking for one MGC contract gets refused with arithmetic instead of a crash.
- **Zone geometry in ATR, not points.** The same rulebook runs gold at $4,390
  and bitcoin at $64,000 without a single instrument-specific constant.

## 4. The engine, in one pass per bar

1. **Sensors** — fair value gaps, order blocks, pivots, equal-high/low pools,
   structure breaks (CHoCH/BOS), and sweeps of pools / swing extremes / the
   previous day's range.
2. **Zone lifecycle** — ACTIVE → TOUCHED → CONSUMED → INVERTED → ARCHIVED.
3. **Scoring** — size ≥1 ATR (+2), order block (+1), first touch (+1),
   with trend (+1); ≥3 makes it a tradeable station.
4. **Appointments** — POSITION (turn at a station with a sweep behind it),
   POOL-RIDE (first touch with liquidity just beyond), REACTION (the reflex),
   BREAKER (the inverted zone retested).
5. **Fills → management → governors** in that order, per the table above.

## 5. What the tool found on its own

Everything below came out of `/api/engine/sweep`, which re-runs the entire court
on the same bars changing exactly one variable. This is the feature I'd demo.

**Stop placement dominates everything else** (live gold 30m, 1,200 bars):

| stop mode | n | avg R | net R | PF |
|---|---|---|---|---|
| **sweep wick** | 52 | **+1.004** | +52.2 | 2.85 |
| zone edge | 66 | +0.195 | +12.9 | 1.31 |
| fixed ATR | 67 | +0.046 | +3.1 | 1.07 |
| structural | 58 | −0.114 | −6.6 | 0.80 |

Same entries, same targets — only the stop moved. Meanwhile the target mode
barely mattered (+0.73 to +0.81R across five modes) and the min-RR floor was
nearly flat from 0 to 4. **The edge is in the stop, not the take-profit** — the
opposite of where most retail effort goes.

**It also contradicted its own author.** The ★ sweep gate, the doctrine's
centrepiece, measured almost nothing once sweep detection was widened
(gate on 14 trades +0.214R vs gate off 16 trades +0.222R). I shipped that result
in the README rather than tuning it away.

**And it does not transfer.** The same calibration on BTCUSD: −0.376R per trade
versus +0.285R on gold. A strategy is a claim about one market's mechanics, not
a universal law.

## 6. Honest limits (stated in the product, not just here)

- One instrument, one history, small samples. Every number is a measurement of
  this engine on this tape, never a forecast.
- Free intraday history is capped (yfinance: 60 days sub-hourly), so "two years
  of 5-minute data" isn't available from this feed at any price.
- The synthetic tape exists so the product always runs. It is never evidence.
- Auto-execution routes to paper only.

## 7. Numbers

~3,600 lines. Backend 2,000 (engine 900). Frontend 1,000, zero dependencies —
the chart is hand-written canvas. 14 tests. Engine runs 600 bars in ~390ms
(647µs/bar), so a 12-value parameter sweep is ~4 seconds.
