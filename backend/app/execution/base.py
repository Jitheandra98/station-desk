"""Broker abstraction + risk-based sizing.

`Broker` is the seam a real venue plugs into: implement five methods and the
rest of the system is unchanged. Paper is the default by construction — a live
venue requires its own class, its own credentials, and a human who means it.

`size_for_risk` rounds DOWN to the instrument's lot step. Rounding up would
quietly risk more than the account allows, which is the kind of bug that only
shows up in the drawdown.
"""
from __future__ import annotations

import math
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict, field
from enum import Enum
from typing import Dict, List, Optional, Any


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass
class Order:
    id: str
    symbol: str
    side: str
    qty: float
    type: str = OrderType.MARKET
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    status: str = OrderStatus.PENDING
    filled_price: Optional[float] = None
    ts: int = field(default_factory=lambda: int(time.time()))
    note: str = ""
    reject_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Position:
    id: str
    symbol: str
    side: str
    qty: float
    entry: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    opened_ts: int = field(default_factory=lambda: int(time.time()))
    note: str = ""
    # live marks
    price: float = 0.0
    unrealised: float = 0.0
    r_multiple: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Fill:
    id: str
    symbol: str
    side: str
    qty: float
    price: float
    ts: int
    pnl: Optional[float] = None
    r: Optional[float] = None
    reason: str = ""
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class Broker(ABC):
    name = "base"
    is_paper = True

    @abstractmethod
    def account(self) -> Dict[str, Any]: ...

    @abstractmethod
    def place(self, symbol: str, side: str, qty: float, **kw) -> Order: ...

    @abstractmethod
    def close(self, position_id: str, price: Optional[float] = None) -> Optional[Fill]: ...

    @abstractmethod
    def positions(self) -> List[Position]: ...

    @abstractmethod
    def mark(self, symbol: str, bar: Any) -> List[Fill]: ...

    @staticmethod
    def new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:10]}"


def size_for_risk(account_equity: float, risk_pct: float, entry: float,
                  stop: float, point_value: float, lot_step: float = 0.01,
                  min_qty: float = 0.0, max_qty: float = 50.0) -> float:
    """Position size from risk, never from conviction.

    Rounded DOWN to the instrument's lot step: a futures contract cannot be
    sliced, and rounding up would quietly risk more than the account allows.
    """
    risk_cash = account_equity * (risk_pct / 100.0)
    stop_pts = abs(entry - stop)
    if stop_pts <= 0 or point_value <= 0 or lot_step <= 0:
        return 0.0
    raw = risk_cash / (stop_pts * point_value)
    qty = math.floor(raw / lot_step) * lot_step
    qty = min(max_qty, qty)
    return 0.0 if qty < max(min_qty, lot_step) - 1e-9 else round(qty, 4)
