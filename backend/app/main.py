"""HTTP + WebSocket surface.

Deliberately few endpoints. The rule followed throughout: anything that could
misrepresent performance is computed in exactly one place (the court) and
served from there, so no route can accidentally invent a friendlier number.

/api/engine/sweep is the interesting one — it re-runs the entire court over the
same bars, changing a single parameter, and returns the comparison. It is how a
setting earns its place instead of being asserted.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .data.providers import SYMBOLS, TF_SECONDS, spec
from .engine.config import StationConfig
from .engine.station import StationEngine
from .execution.base import OrderSide
from .service import desk

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FRONTEND = os.path.join(ROOT, "frontend")

app = FastAPI(title="STATION DESK", version="1.0.0",
              description="Gold delivery engine — zones, sweeps, appointments, honest court.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ══════════════════════════════════════════════════════════════════════
# schemas
# ══════════════════════════════════════════════════════════════════════
class OrderRequest(BaseModel):
    symbol: str = "XAUUSD"
    side: str = Field(..., pattern="^(BUY|SELL)$")
    qty: Optional[float] = None
    risk_pct: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    note: str = ""


class ConfigPatch(BaseModel):
    values: Dict[str, Any]


class AutoTrade(BaseModel):
    enabled: bool
    profiles: Optional[List[str]] = None


# ══════════════════════════════════════════════════════════════════════
# market
# ══════════════════════════════════════════════════════════════════════
@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "source": desk.md.last_source,
            "symbols": {k: v for k, v in SYMBOLS.items()},
            "timeframes": list(TF_SECONDS), "config_hash": desk.cfg.hash,
            "auto_trade": desk.auto_trade}


@app.get("/api/market/candles")
def candles(symbol: str = "XAUUSD", timeframe: str = "30m",
            limit: int = Query(600, ge=50, le=2000)) -> Dict[str, Any]:
    bars = desk.bars(symbol, timeframe, limit)
    return {"symbol": symbol, "timeframe": timeframe, "source": desk.md.last_source,
            "bars": [b.to_dict() for b in bars]}


@app.get("/api/market/quote")
def quote(symbol: str = "XAUUSD") -> Dict[str, Any]:
    return desk.md.quote(symbol)


# ══════════════════════════════════════════════════════════════════════
# engine
# ══════════════════════════════════════════════════════════════════════
@app.get("/api/engine/snapshot")
def snapshot(symbol: str = "XAUUSD", timeframe: str = "30m",
             rebuild: bool = False) -> Dict[str, Any]:
    eng = desk.engine(symbol, timeframe, rebuild=rebuild)
    snap = eng.snapshot()
    snap["read"] = desk.read(symbol, timeframe)
    snap["symbol"], snap["timeframe"] = symbol, timeframe
    snap["source"] = desk.md.last_source
    return snap


@app.get("/api/engine/read")
def read(symbol: str = "XAUUSD", timeframe: str = "30m") -> Dict[str, Any]:
    return desk.read(symbol, timeframe)


@app.get("/api/engine/court")
def court(symbol: str = "XAUUSD", timeframe: str = "30m") -> Dict[str, Any]:
    return desk.engine(symbol, timeframe).court.summary()


@app.get("/api/engine/scan")
def scan(timeframe: str = "30m") -> Dict[str, Any]:
    """Every timeframe, one verdict each — the top-of-desk sweep."""
    out = []
    for tf in ("15m", "30m", "1h", "4h"):
        try:
            r = desk.read("XAUUSD", tf)
            out.append({"timeframe": tf, **r})
        except Exception as exc:
            out.append({"timeframe": tf, "state": "ERROR", "headline": str(exc)})
    return {"scan": out}


@app.get("/api/engine/config")
def get_config() -> Dict[str, Any]:
    return desk.cfg.to_dict()


@app.post("/api/engine/config")
def set_config(patch: ConfigPatch) -> Dict[str, Any]:
    data = desk.cfg.to_dict()
    data.update(patch.values or {})
    desk.cfg = StationConfig.from_dict(data)
    desk.rebuild_all()
    desk.log("CONFIG", f"calibration changed — hash {desk.cfg.hash}")
    return desk.cfg.to_dict()


class SweepRequest(BaseModel):
    symbol: str = "XAUUSD"
    timeframe: str = "30m"
    limit: int = 1200
    param: str
    values: List[Any]
    base: Optional[Dict[str, Any]] = None


@app.post("/api/engine/sweep")
def sweep(req: SweepRequest) -> Dict[str, Any]:
    """What would this knob have been worth?

    Re-runs the whole court once per value, on the same bars, changing one
    thing. This is how a setting earns its place instead of being asserted.
    """
    bars = desk.bars(req.symbol, req.timeframe, req.limit)
    base = desk.cfg.to_dict()
    base.update(req.base or {})
    rows = []
    for v in req.values[:12]:
        cfg_d = dict(base)
        cfg_d[req.param] = v
        cfg = StationConfig.from_dict(cfg_d)
        c = StationEngine(cfg).load(bars).court.summary()
        rows.append({
            "value": v, "hash": cfg.hash, "n": c["n"],
            "win_rate": c["win_rate"], "avg_r": c["avg_r"], "net_r": c["net_r"],
            "profit_factor": c["profit_factor"], "max_dd_r": c["max_dd_r"],
        })
    scored = [r for r in rows if r["n"] >= 5]
    best = max(scored, key=lambda r: r["avg_r"] or -99) if scored else None
    return {"param": req.param, "bars": len(bars), "symbol": req.symbol,
            "timeframe": req.timeframe, "rows": rows, "best": best,
            "caveat": ("One history, one instrument. A knob that looks best here "
                       "is a hypothesis, not a setting. Samples under 5 are ignored.")}


@app.get("/api/engine/presets")
def presets() -> Dict[str, Any]:
    """Named calibrations — the arguments we have actually had, made runnable."""
    return {"presets": [
        {"id": "measured", "name": "The measured calibration",
         "note": "sweep-wick stops, far destinations, 2R floor — the doctrine as counted",
         "values": {"stop_mode": "sweep", "target_mode": "far", "min_rr": 2.0,
                    "require_sweep": True, "tight_pad_atr": 0.25}},
        {"id": "tight3r", "name": "Tight stop · fixed 3R",
         "note": "the geometry that flipped the 154-trade book positive",
         "values": {"stop_mode": "sweep", "target_mode": "rr", "tp_rr": 3.0,
                    "min_rr": 2.0, "require_sweep": True}},
        {"id": "runner", "name": "Swing trail",
         "note": "max asymmetry: exit only on structure loss",
         "values": {"stop_mode": "sweep", "target_mode": "trail", "require_sweep": True}},
        {"id": "zonewide", "name": "Classic zone stop",
         "note": "stop at the zone's far edge — the old geometry, for comparison",
         "values": {"stop_mode": "zone", "stop_pad_atr": 0.5, "target_mode": "nearest",
                    "min_rr": 0.0, "require_sweep": False}},
        {"id": "everything", "name": "Everything live",
         "note": "all four contracts moving capital — his order, the court judging",
         "values": {"position_live": True, "pool_ride_live": True,
                    "reaction_live": True, "breaker_live": True}},
    ]}


@app.post("/api/engine/backtest")
def backtest(symbol: str = "XAUUSD", timeframe: str = "30m",
             limit: int = Query(1500, ge=200, le=5000)) -> Dict[str, Any]:
    bars = desk.bars(symbol, timeframe, limit)
    eng = StationEngine(desk.cfg).load(bars)
    c = eng.court.summary()
    return {"symbol": symbol, "timeframe": timeframe, "bars": len(bars),
            "from_ts": bars[0].ts if bars else None, "to_ts": bars[-1].ts if bars else None,
            "source": desk.md.last_source, "court": c,
            "trades": [t.to_dict() for t in eng.court.closed[-200:]],
            "caveat": ("Single instrument, single history, small samples. "
                       "This is a measurement, not a promise.")}


# ══════════════════════════════════════════════════════════════════════
# execution
# ══════════════════════════════════════════════════════════════════════
@app.get("/api/broker/account")
def account() -> Dict[str, Any]:
    return desk.broker.account()


@app.get("/api/broker/positions")
def positions() -> Dict[str, Any]:
    return {"positions": [p.to_dict() for p in desk.broker.positions()],
            "fills": [f.to_dict() for f in desk.broker.fills(40)],
            "orders": [o.to_dict() for o in desk.broker.orders(20)]}


@app.post("/api/broker/order")
def place_order(req: OrderRequest) -> Dict[str, Any]:
    px = desk.md.quote(req.symbol)["price"]
    desk.broker.last_price[req.symbol] = px
    qty = req.qty
    if qty is None:
        if req.stop_loss is None:
            raise HTTPException(400, "provide qty, or a stop so size can be derived from risk")
        qty = desk.size(req.symbol, px, req.stop_loss,
                        req.risk_pct or desk.cfg.risk_pct)
        if qty <= 0:
            raise HTTPException(400,
                f"account too small: 0.5% of ${desk.broker.account()['equity']:,.0f} "
                f"cannot cover one lot of {req.symbol} at that stop distance")
    order = desk.broker.place(req.symbol, req.side, qty, price=px,
                              stop_loss=req.stop_loss, take_profit=req.take_profit,
                              margin_per_unit=desk.margin_per_unit(req.symbol),
                              note=req.note or "manual")
    desk.log("ORDER", f"{req.side} {qty} {req.symbol}",
             {"status": order.status, "reason": order.reject_reason})
    return order.to_dict()


@app.post("/api/broker/close/{position_id}")
def close_position(position_id: str) -> Dict[str, Any]:
    fill = desk.broker.close(position_id)
    if not fill:
        raise HTTPException(404, "position not found")
    desk.log("CLOSE", f"closed {fill.symbol} for {fill.pnl:+.2f}")
    return fill.to_dict()


@app.post("/api/broker/reset")
def reset_account(equity: float = Query(100_000.0, ge=100.0)) -> Dict[str, Any]:
    desk.broker.reset(equity)
    desk.log("RESET", f"paper account reset to ${equity:,.0f}")
    return desk.broker.account()


@app.get("/api/broker/suggest")
def suggest(symbol: str = "XAUUSD", timeframe: str = "30m") -> Dict[str, Any]:
    s = desk.suggest_order(symbol, timeframe)
    return s or {"available": False,
                 "reason": desk.read(symbol, timeframe)["detail"]}


@app.post("/api/broker/auto")
def set_auto(cfg: AutoTrade) -> Dict[str, Any]:
    desk.auto_trade = cfg.enabled
    if cfg.profiles:
        desk.auto_profiles = set(cfg.profiles)
    desk.log("AUTO", f"auto-execution {'ARMED' if cfg.enabled else 'disarmed'}",
             {"profiles": sorted(desk.auto_profiles)})
    return {"auto_trade": desk.auto_trade, "profiles": sorted(desk.auto_profiles)}


@app.get("/api/events")
def events(limit: int = 80) -> Dict[str, Any]:
    return {"events": desk.events[-limit:]}


# ══════════════════════════════════════════════════════════════════════
# stream
# ══════════════════════════════════════════════════════════════════════
@app.websocket("/ws")
async def ws(sock: WebSocket) -> None:
    await sock.accept()
    q: asyncio.Queue = asyncio.Queue(maxsize=32)
    desk.subscribers.add(q)
    try:
        await sock.send_json({"type": "hello", "config_hash": desk.cfg.hash,
                              "quote": desk.md.quote(desk.symbol),
                              "read": desk.read()})
        while True:
            payload = await q.get()
            await sock.send_json(payload)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        desk.subscribers.discard(q)


@app.on_event("startup")
async def startup() -> None:
    desk.engine(desk.symbol, desk.timeframe)
    desk._task = asyncio.create_task(desk.run_live(interval=20.0))
    desk.log("BOOT", "STATION DESK online")


# ══════════════════════════════════════════════════════════════════════
# static site
# ══════════════════════════════════════════════════════════════════════
if os.path.isdir(FRONTEND):
    app.mount("/css", StaticFiles(directory=os.path.join(FRONTEND, "css")), name="css")
    app.mount("/js", StaticFiles(directory=os.path.join(FRONTEND, "js")), name="js")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(os.path.join(FRONTEND, "index.html"))
