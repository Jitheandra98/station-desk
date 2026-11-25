"""Paper broker with a conscience.

Three things make this different from a toy simulator:
  1. fills cross the spread and pay commission — the price you see is not the
     price you get;
  2. a bar that touches stop and target resolves as the STOP, matching the
     engine's own canon, so the two scoreboards can never disagree;
  3. margin is enforced per instrument, which is why a small account gets told
     the arithmetic instead of silently trading a fraction of a contract.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from .base import (Broker, Fill, Order, OrderSide, OrderStatus, OrderType,
                   Position, size_for_risk)


class PaperBroker(Broker):
    name = "paper"
    is_paper = True

    def __init__(self, equity: float = 100_000.0, spread_pts: float = 0.2,
                 commission: float = 1.0, point_value: float = 10.0,
                 state_path: Optional[str] = None) -> None:
        self.start_equity = equity
        self.equity = equity
        self.spread = spread_pts
        self.commission = commission
        self.point_value = point_value
        self.state_path = state_path
        self._positions: Dict[str, Position] = {}
        self._orders: List[Order] = []
        self._fills: List[Fill] = []
        self.last_price: Dict[str, float] = {}
        if state_path and os.path.exists(state_path):
            self._load()

    # ── account ────────────────────────────────────────────────────────
    def account(self) -> Dict[str, Any]:
        unreal = sum(p.unrealised for p in self._positions.values())
        wins = [f for f in self._fills if (f.pnl or 0) > 0]
        losses = [f for f in self._fills if (f.pnl or 0) < 0]
        gross_w = sum(f.pnl for f in wins) if wins else 0.0
        gross_l = abs(sum(f.pnl for f in losses)) if losses else 0.0
        closed = len(wins) + len(losses)
        return {
            "broker": self.name, "paper": True,
            "equity": round(self.equity, 2),
            "balance": round(self.equity, 2),
            "unrealised": round(unreal, 2),
            "net_liq": round(self.equity + unreal, 2),
            "start_equity": self.start_equity,
            "return_pct": round((self.equity + unreal - self.start_equity) / self.start_equity * 100, 3),
            "open_positions": len(self._positions),
            "closed_trades": closed,
            "win_rate": round(len(wins) / closed * 100, 1) if closed else None,
            "profit_factor": round(gross_w / gross_l, 2) if gross_l > 0 else None,
            "point_value": self.point_value,
            "spread_pts": self.spread,
            "commission": self.commission,
        }

    # ── orders ─────────────────────────────────────────────────────────
    def place(self, symbol: str, side: str, qty: float, **kw) -> Order:
        o = Order(id=self.new_id("ord"), symbol=symbol, side=side, qty=qty,
                  type=kw.get("type", OrderType.MARKET),
                  limit_price=kw.get("limit_price"), stop_price=kw.get("stop_price"),
                  take_profit=kw.get("take_profit"), stop_loss=kw.get("stop_loss"),
                  note=kw.get("note", ""))
        ref = kw.get("price") or self.last_price.get(symbol)
        if ref is None:
            o.status, o.reject_reason = OrderStatus.REJECTED, "no market price"
            self._orders.append(o)
            return o
        if qty <= 0:
            o.status, o.reject_reason = OrderStatus.REJECTED, "quantity must be positive"
            self._orders.append(o)
            return o
        # margin sanity: this is where a $1k account meets a $1.5k ticket
        notional_margin = kw.get("margin_per_unit", 0.0) * qty
        if notional_margin > self.equity:
            o.status = OrderStatus.REJECTED
            o.reject_reason = (f"insufficient funds: needs ${notional_margin:,.0f} "
                               f"margin, account holds ${self.equity:,.0f}")
            self._orders.append(o)
            return o
        fill_px = ref + (self.spread / 2 if side == OrderSide.BUY else -self.spread / 2)
        o.status, o.filled_price = OrderStatus.FILLED, round(fill_px, 4)
        self.equity -= self.commission
        pos = Position(id=self.new_id("pos"), symbol=symbol, side=side, qty=qty,
                       entry=o.filled_price, stop_loss=o.stop_loss,
                       take_profit=o.take_profit, note=o.note, price=ref)
        self._positions[pos.id] = pos
        self._orders.append(o)
        self._save()
        return o

    def close(self, position_id: str, price: Optional[float] = None,
              reason: str = "manual") -> Optional[Fill]:
        pos = self._positions.get(position_id)
        if not pos:
            return None
        ref = price or self.last_price.get(pos.symbol) or pos.entry
        exit_px = ref - (self.spread / 2 if pos.side == OrderSide.BUY else -self.spread / 2)
        return self._settle(pos, exit_px, reason)

    def _settle(self, pos: Position, exit_px: float, reason: str) -> Fill:
        direction = 1 if pos.side == OrderSide.BUY else -1
        pnl = (exit_px - pos.entry) * direction * pos.qty * self.point_value - self.commission
        r = None
        if pos.stop_loss:
            risk_cash = abs(pos.entry - pos.stop_loss) * pos.qty * self.point_value
            if risk_cash > 0:
                r = round(pnl / risk_cash, 3)
        self.equity += pnl
        f = Fill(id=self.new_id("fill"), symbol=pos.symbol,
                 side="SELL" if pos.side == OrderSide.BUY else "BUY",
                 qty=pos.qty, price=round(exit_px, 4), ts=int(time.time()),
                 pnl=round(pnl, 2), r=r, reason=reason, note=pos.note)
        self._fills.append(f)
        self._positions.pop(pos.id, None)
        self._save()
        return f

    # ── marking to market ──────────────────────────────────────────────
    def mark(self, symbol: str, bar: Any) -> List[Fill]:
        """Advance one bar. Stop is checked BEFORE target — always."""
        fills: List[Fill] = []
        self.last_price[symbol] = bar.close
        for pos in list(self._positions.values()):
            if pos.symbol != symbol:
                continue
            direction = 1 if pos.side == OrderSide.BUY else -1
            hit_stop = pos.stop_loss is not None and (
                bar.low <= pos.stop_loss if direction == 1 else bar.high >= pos.stop_loss)
            hit_tp = pos.take_profit is not None and (
                bar.high >= pos.take_profit if direction == 1 else bar.low <= pos.take_profit)
            if hit_stop:
                fills.append(self._settle(pos, pos.stop_loss, "stop-loss"))
                continue
            if hit_tp:
                fills.append(self._settle(pos, pos.take_profit, "take-profit"))
                continue
            pos.price = bar.close
            pos.unrealised = round((bar.close - pos.entry) * direction * pos.qty * self.point_value, 2)
            if pos.stop_loss:
                risk_cash = abs(pos.entry - pos.stop_loss) * pos.qty * self.point_value
                pos.r_multiple = round(pos.unrealised / risk_cash, 2) if risk_cash else None
        return fills

    def positions(self) -> List[Position]:
        return list(self._positions.values())

    def orders(self, limit: int = 50) -> List[Order]:
        return self._orders[-limit:]

    def fills(self, limit: int = 100) -> List[Fill]:
        return self._fills[-limit:]

    def reset(self, equity: Optional[float] = None) -> None:
        self.equity = equity if equity is not None else self.start_equity
        self.start_equity = self.equity
        self._positions.clear()
        self._orders.clear()
        self._fills.clear()
        self._save()

    def size_for(self, entry: float, stop: float, risk_pct: float) -> float:
        return size_for_risk(self.equity, risk_pct, entry, stop, self.point_value)

    # ── persistence ────────────────────────────────────────────────────
    def _save(self) -> None:
        if not self.state_path:
            return
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            with open(self.state_path, "w") as fh:
                json.dump({"equity": self.equity, "start_equity": self.start_equity,
                           "positions": [p.to_dict() for p in self._positions.values()],
                           "orders": [o.to_dict() for o in self._orders[-200:]],
                           "fills": [f.to_dict() for f in self._fills[-500:]]}, fh)
        except OSError:
            pass

    def _load(self) -> None:
        try:
            with open(self.state_path) as fh:
                d = json.load(fh)
            self.equity = d.get("equity", self.equity)
            self.start_equity = d.get("start_equity", self.start_equity)
            self._positions = {p["id"]: Position(**p) for p in d.get("positions", [])}
            self._orders = [Order(**o) for o in d.get("orders", [])]
            self._fills = [Fill(**f) for f in d.get("fills", [])]
        except (OSError, ValueError, TypeError):
            pass
