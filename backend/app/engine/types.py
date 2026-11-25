"""Domain types.

Each one mirrors something a trader marks by hand: a Zone is a shelf where size
once moved the market, a Pool is resting money, a Trade is an appointment kept,
a Bucket is a pattern on trial. Keeping the vocabulary identical to the way the
strategy is actually spoken about is what stops the code and the doctrine from
drifting apart.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import IntEnum
from typing import Optional, List, Dict, Any


class ZoneState(IntEnum):
    ACTIVE = 0      # drawn, untouched
    TOUCHED = 1     # price has visited, still alive
    CONSUMED = 2    # fully traversed — the shelf broke
    INVERTED = 3    # retested from the other side — now a breaker
    ARCHIVED = 4    # aged out


class Direction(IntEnum):
    DEMAND = 1      # supports price / long bias
    SUPPLY = -1     # caps price / short bias


@dataclass
class Zone:
    """A station: a location where the market previously showed its hand."""
    id: int
    top: float
    bot: float
    dir: int
    kind: str                    # "OB" | "FVG" | "4H"
    born: int                    # bar index of birth
    state: int = ZoneState.ACTIVE
    first_touched: bool = False
    touches: int = 0
    born_ts: int = 0

    @property
    def height(self) -> float:
        return self.top - self.bot

    @property
    def mid(self) -> float:
        return (self.top + self.bot) / 2.0

    @property
    def near_edge(self) -> float:
        """The edge price meets first when approaching from the live side."""
        return self.top if self.dir == Direction.DEMAND else self.bot

    @property
    def far_edge(self) -> float:
        """The edge that, once closed through, kills the station."""
        return self.bot if self.dir == Direction.DEMAND else self.top

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["height"] = self.height
        return d


@dataclass
class Pool:
    """Resting liquidity — equal highs or equal lows. A destination, never a wall."""
    id: int
    level: float
    bar: int
    is_high: bool
    swept: bool = False
    swept_bar: Optional[int] = None
    ts: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Trade:
    """An appointment kept. Scored honestly or not at all."""
    id: int
    dir: int
    entry: float
    stop: float
    target: Optional[float]
    entry_bar: int
    entry_ts: int
    profile: str                 # POSITION | POOL-RIDE | REACTION | BREAKER | LDN-JOURNAL
    session: str
    zone_label: str = ""
    target_kind: str = ""
    tags: str = ""
    live: bool = True            # counts toward capital, vs paper bucket
    open: bool = True
    # lifecycle
    retested: bool = False
    held: bool = False
    trail_stop: Optional[float] = None
    zone_top: Optional[float] = None
    zone_bot: Optional[float] = None
    # outcome
    exit: Optional[float] = None
    exit_bar: Optional[int] = None
    exit_ts: Optional[int] = None
    reason: str = ""
    r: Optional[float] = None
    mfe_r: float = 0.0
    mae_r: float = 0.0
    partial_taken: bool = False
    banked_r: float = 0.0

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def rr_planned(self) -> Optional[float]:
        if self.target is None or self.risk <= 0:
            return None
        return abs(self.target - self.entry) / self.risk

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["risk"] = self.risk
        d["rr_planned"] = self.rr_planned
        return d


@dataclass
class Bar:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Signal:
    """Something the engine wants the trader to see, ranked by loudness."""
    bar: int
    ts: int
    kind: str                    # ENTRY | RADAR | STRUCTURE | SWEEP | EXIT | PASS
    dir: int
    price: float
    title: str
    detail: str = ""
    profile: str = ""
    r: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Bucket:
    """One column of the court: a pattern on trial."""
    name: str
    live: bool = True
    n: int = 0
    wins: int = 0
    net_r: float = 0.0
    long_n: int = 0
    long_r: float = 0.0
    short_n: int = 0
    short_r: float = 0.0
    gross_win_r: float = 0.0
    gross_loss_r: float = 0.0

    def add(self, direction: int, r: float) -> None:
        self.n += 1
        self.net_r += r
        if r > 0:
            self.wins += 1
            self.gross_win_r += r
        else:
            self.gross_loss_r += abs(r)
        if direction == 1:
            self.long_n += 1
            self.long_r += r
        else:
            self.short_n += 1
            self.short_r += r

    @property
    def win_rate(self) -> Optional[float]:
        return (self.wins / self.n * 100.0) if self.n else None

    @property
    def avg_r(self) -> Optional[float]:
        return (self.net_r / self.n) if self.n else None

    @property
    def profit_factor(self) -> Optional[float]:
        if self.gross_loss_r <= 0:
            return None if self.gross_win_r <= 0 else float("inf")
        return self.gross_win_r / self.gross_loss_r

    @property
    def verdict(self) -> str:
        """Honest labelling: nothing is 'proven' before it has been counted."""
        if self.n < 20:
            return "NEED-DATA"
        tier = "PROVISIONAL" if self.n < 50 else "STABLE"
        two_sided = self.long_r > 0 and self.short_r > 0
        return f"{tier} · {'STRUCTURE' if two_sided else 'BETA'}"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.update(
            win_rate=self.win_rate,
            avg_r=self.avg_r,
            profit_factor=(None if self.profit_factor in (None, float("inf")) else self.profit_factor),
            verdict=self.verdict,
        )
        return d
