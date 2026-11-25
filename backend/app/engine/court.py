"""The court: honest bookkeeping.

Every pattern is tried in its own bucket, and buckets are labelled by how much
evidence they actually have — NEED-DATA under 20 trades, PROVISIONAL under 50,
STABLE beyond. A bucket is only called STRUCTURE if it made money in BOTH
directions; one-sided profit on a trending tape is beta wearing a strategy's
clothes, and it gets called BETA.

Splits by session and by destination type answer the question that actually
compounds: which trips are worth taking?
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .indicators import wilson_bounds
from .types import Bucket, Trade


class Court:
    def __init__(self, live_map: Optional[Dict[str, bool]] = None) -> None:
        live_map = live_map or {}
        self.buckets: Dict[str, Bucket] = {
            "POSITION": Bucket("POSITION", live=live_map.get("POSITION", True)),
            "POOL-RIDE": Bucket("POOL-RIDE", live=live_map.get("POOL-RIDE", True)),
            "REACTION": Bucket("REACTION", live=live_map.get("REACTION", False)),
            "BREAKER": Bucket("BREAKER", live=live_map.get("BREAKER", False)),
            "LDN-JOURNAL": Bucket("LDN-JOURNAL", live=False),
        }
        self.by_session: Dict[str, Bucket] = {
            s: Bucket(s) for s in ("ASIA", "LONDON", "NY", "LATE")
        }
        self.by_target: Dict[str, Bucket] = {}
        self.closed: List[Trade] = []
        self.roll_win: List[float] = []
        self.roll_r: List[float] = []
        self.equity_r: float = 0.0
        self.peak_r: float = 0.0
        self.max_dd_r: float = 0.0
        self.equity_curve: List[Dict[str, float]] = []

    def record(self, t: Trade, counts_capital: bool) -> None:
        r = t.r or 0.0
        self.closed.append(t)
        self.buckets.setdefault(t.profile, Bucket(t.profile)).add(t.dir, r)
        self.by_session.setdefault(t.session, Bucket(t.session)).add(t.dir, r)
        if t.target_kind:
            self.by_target.setdefault(t.target_kind, Bucket(t.target_kind)).add(t.dir, r)
        if counts_capital:
            self.roll_win.append(1.0 if r > 0 else 0.0)
            self.roll_r.append(r)
            self.roll_win = self.roll_win[-200:]
            self.roll_r = self.roll_r[-200:]
            self.equity_r += r
            self.peak_r = max(self.peak_r, self.equity_r)
            self.max_dd_r = max(self.max_dd_r, self.peak_r - self.equity_r)
            self.equity_curve.append({"ts": t.exit_ts or 0, "equity_r": round(self.equity_r, 4), "r": round(r, 4)})

    # ── health of the live book ────────────────────────────────────────
    def rolling_net_r(self, n: int = 30) -> float:
        return sum(self.roll_r[-n:])

    def wilson(self) -> Optional[tuple]:
        if len(self.roll_win) < 10:
            return None
        return wilson_bounds(sum(self.roll_win), len(self.roll_win))

    def summary(self) -> Dict:
        live = [b for b in self.buckets.values() if b.live]
        n = sum(b.n for b in live)
        wins = sum(b.wins for b in live)
        net = sum(b.net_r for b in live)
        gw = sum(b.gross_win_r for b in live)
        gl = sum(b.gross_loss_r for b in live)
        w = self.wilson()
        return {
            "n": n,
            "wins": wins,
            "win_rate": (wins / n * 100.0) if n else None,
            "net_r": round(net, 3),
            "avg_r": round(net / n, 4) if n else None,
            "profit_factor": round(gw / gl, 3) if gl > 0 else None,
            "equity_r": round(self.equity_r, 3),
            "peak_r": round(self.peak_r, 3),
            "max_dd_r": round(self.max_dd_r, 3),
            "wilson_lo": round(w[0], 4) if w else None,
            "wilson_hi": round(w[1], 4) if w else None,
            "rolling30_r": round(self.rolling_net_r(30), 3),
            "buckets": {k: b.to_dict() for k, b in self.buckets.items()},
            "sessions": {k: b.to_dict() for k, b in self.by_session.items() if b.n},
            "targets": {k: b.to_dict() for k, b in self.by_target.items() if b.n},
            "equity_curve": self.equity_curve[-500:],
        }
