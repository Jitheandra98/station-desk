"""DeskService — the only stateful object in the system.

Owns one StationEngine per (symbol, timeframe), one broker, and one background
poll loop. Everything the API and the UI read comes from here, which means
there is exactly one place where "what is true right now" is decided.

The live loop deliberately steps the engine only on CLOSED bars (`new_bars[:-1]`)
— the forming bar is shown on the chart but never fed to the engine, because a
signal computed from a bar that is still moving is a signal that can un-happen.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional, Set

from .data.providers import MarketData, SYMBOLS, TF_SECONDS, spec
from .engine.config import StationConfig
from .engine.station import StationEngine
from .engine.types import Bar
from .execution.base import OrderSide, size_for_risk
from .execution.paper import PaperBroker

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data")


class DeskService:
    def __init__(self) -> None:
        self.md = MarketData(prefer_live=True)
        self.cfg = StationConfig()
        self.broker = PaperBroker(equity=100_000.0, point_value=10.0,
                                  state_path=os.path.join(STATE_DIR, "paper_state.json"))
        self.engines: Dict[str, StationEngine] = {}
        self.subscribers: Set[asyncio.Queue] = set()
        self.auto_trade = False
        self.auto_profiles = {"POSITION", "POOL-RIDE"}
        self.symbol = "XAUUSD"
        self.timeframe = "30m"
        self._last_bar_ts: Dict[str, int] = {}
        self._task: Optional[asyncio.Task] = None
        self.events: List[Dict[str, Any]] = []

    # ── engine access ──────────────────────────────────────────────────
    def key(self, symbol: str, timeframe: str) -> str:
        return f"{symbol}:{timeframe}"

    def engine(self, symbol: Optional[str] = None, timeframe: Optional[str] = None,
               limit: int = 600, rebuild: bool = False) -> StationEngine:
        symbol = symbol or self.symbol
        timeframe = timeframe or self.timeframe
        k = self.key(symbol, timeframe)
        bars = self.md.bars(symbol, timeframe, limit)
        eng = self.engines.get(k)
        if eng is None or rebuild:
            eng = StationEngine(self.cfg)
            eng.load(bars)
            self.engines[k] = eng
            self._last_bar_ts[k] = bars[-1].ts if bars else 0
        return eng

    def bars(self, symbol: Optional[str] = None, timeframe: Optional[str] = None,
             limit: int = 600) -> List[Bar]:
        return self.md.bars(symbol or self.symbol, timeframe or self.timeframe, limit)

    def rebuild_all(self) -> None:
        for k in list(self.engines):
            sym, tf = k.split(":")
            self.engine(sym, tf, rebuild=True)

    # ── the read: one paragraph a human can act on ─────────────────────
    def read(self, symbol: Optional[str] = None, timeframe: Optional[str] = None) -> Dict[str, Any]:
        eng = self.engine(symbol, timeframe)
        s = eng.snapshot()
        if not s.get("ready"):
            return {"headline": "warming up", "detail": "", "state": "WAIT"}
        route, radar = s["route"], s["radar"]
        up = route["up"][0] if route["up"] else None
        dn = route["down"][0] if route["down"] else None
        open_t = s["open_trades"]
        if open_t:
            t = open_t[0]
            state = "IN-TRADE"
            headline = f"{t['profile']} {'long' if t['dir'] == 1 else 'short'} running"
            tgt_txt = "trail" if t["target"] is None else format(t["target"], ".2f")
            detail = (f"entry {t['entry']:.2f} · stop {t['stop']:.2f} · "
                      f"target {tgt_txt} — manage, do not add")
        elif s["governors"]["suspended"]:
            state, headline = "HALT", "Engine suspended"
            detail = "live performance broke its own floor — no new risk until reviewed"
        elif radar:
            state = "RADAR"
            headline = f"Price at a {'demand' if radar['dir'] == 1 else 'supply'} station"
            detail = (f"{radar['kind']} score {radar['score']}, touch {radar['touches']} — "
                      f"{'route clear' if radar['route_clear'] else 'route dirty'}; "
                      f"waiting for the sweep, then the turn")
        elif s["session"] == "LONDON" and not s["licensed"]:
            state, headline = "WATCH", "London — it breaks and runs"
            detail = "fresh turns are journalled, not traded; second entries only"
        else:
            state, headline = "MONITOR", "No appointment"
            detail = s["last_pass"] if s["last_pass"] != "—" else "waiting for a station to be approached"
        return {
            "state": state, "headline": headline, "detail": detail,
            "session": s["session"], "behaviour": s["session_behaviour"],
            "regime": s["regime"], "price": s["price"],
            "next_up": up, "next_down": dn,
            "pools_above": route["pools_above"], "pools_below": route["pools_below"],
            "last_pass": s["last_pass"],
        }

    # ── order helpers ──────────────────────────────────────────────────
    def margin_per_unit(self, symbol: str) -> float:
        return spec(symbol)["margin_per_unit"]

    def size(self, symbol: str, entry: float, stop: float, risk_pct: float) -> float:
        sp = spec(symbol)
        return size_for_risk(self.broker.account()["equity"], risk_pct, entry, stop,
                             sp["point_value"], sp["lot_step"], sp["min_qty"])

    def suggest_order(self, symbol: Optional[str] = None,
                      timeframe: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """What the engine would do right now, sized to the account's risk."""
        eng = self.engine(symbol, timeframe)
        s = eng.snapshot()
        if not s.get("ready") or not s["open_trades"]:
            if not eng.pending:
                return None
            p = eng.pending[-1]
            entry = s["price"]
            stop, target = p["stop"], p["target"]
        else:
            t = s["open_trades"][0]
            entry, stop, target = t["entry"], t["stop"], t["target"]
        if stop is None:
            return None
        acct = self.broker.account()
        qty = self.size(symbol or self.symbol, entry, stop, self.cfg.risk_pct)
        return {"symbol": symbol or self.symbol,
                "side": OrderSide.BUY if entry > stop else OrderSide.SELL,
                "entry": round(entry, 2), "stop": round(stop, 2),
                "target": round(target, 2) if target else None,
                "qty": qty,
                "risk_cash": round(abs(entry - stop) * qty * self.broker.point_value, 2),
                "risk_pct": self.cfg.risk_pct}

    def log(self, kind: str, message: str, data: Optional[Dict] = None) -> Dict[str, Any]:
        ev = {"ts": int(time.time()), "kind": kind, "message": message, "data": data or {}}
        self.events.append(ev)
        self.events = self.events[-300:]
        return ev

    # ── live loop ──────────────────────────────────────────────────────
    async def broadcast(self, payload: Dict[str, Any]) -> None:
        dead = []
        for q in self.subscribers:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.subscribers.discard(q)

    async def run_live(self, interval: float = 20.0) -> None:
        """Poll for new closed bars, step the engine, act, broadcast."""
        while True:
            try:
                await self._tick()
            except Exception as exc:                       # never kill the desk
                self.log("ERROR", f"live loop: {exc}")
            await asyncio.sleep(interval)

    async def _tick(self) -> None:
        symbol, tf = self.symbol, self.timeframe
        k = self.key(symbol, tf)
        bars = self.md.bars(symbol, tf, 600, ttl=10)
        if not bars:
            return
        eng = self.engines.get(k)
        if eng is None:
            eng = self.engine(symbol, tf)
        last_ts = self._last_bar_ts.get(k, 0)
        new_bars = [b for b in bars if b.ts > last_ts]
        signals = []
        for b in new_bars[:-1] if len(new_bars) > 1 else []:
            signals.extend(eng.step(b))
            self._last_bar_ts[k] = b.ts
        # mark the broker on the freshest bar
        self.broker.mark(symbol, bars[-1])
        for s in signals:
            self.log(s.kind, s.title, {"detail": s.detail, "price": s.price})
            if s.kind == "ENTRY" and self.auto_trade and s.profile in self.auto_profiles:
                self._auto_execute(eng, symbol, s)
        await self.broadcast({
            "type": "tick", "ts": int(time.time()),
            "quote": self.md.quote(symbol),
            "read": self.read(symbol, tf),
            "signals": [s.to_dict() for s in signals],
            "account": self.broker.account(),
            "positions": [p.to_dict() for p in self.broker.positions()],
        })

    def _auto_execute(self, eng: StationEngine, symbol: str, signal) -> None:
        trade = next((t for t in eng.open_trades if t.profile == signal.profile), None)
        if not trade:
            return
        acct = self.broker.account()
        qty = self.size(symbol, trade.entry, trade.stop, self.cfg.risk_pct)
        if qty <= 0:
            self.log("REJECT", "auto-trade skipped: size resolved to zero")
            return
        order = self.broker.place(
            symbol, OrderSide.BUY if trade.dir == 1 else OrderSide.SELL, qty,
            price=trade.entry, stop_loss=trade.stop, take_profit=trade.target,
            margin_per_unit=self.margin_per_unit(symbol),
            note=f"auto · {trade.profile} · {trade.zone_label}")
        self.log("ORDER", f"auto {order.side} {qty} {symbol} @ {order.filled_price or '-'}",
                 {"status": order.status, "reason": order.reject_reason})


desk = DeskService()
