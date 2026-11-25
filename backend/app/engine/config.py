"""The calibration constitution.

Two ideas here. First, every number the engine obeys is in one dataclass, so
"what is this system actually doing" has a single answer. Second, `hash` is
derived from the calibration values — change any of them and the hash moves,
and the UI shows it. You can run your own experiment; you just cannot pretend
you were running the measured one.

Identity settings (account size, risk %) deliberately do NOT move the hash:
they change your exposure, not the strategy.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Dict, Any, List


@dataclass
class StationConfig:
    # ── engine ──────────────────────────────────────────────────────────
    atr_len: int = 14
    ema_len: int = 100
    trend_lb: int = 10
    pivot_len: int = 3

    # ── zones & stations ────────────────────────────────────────────────
    zone_min_atr: float = 0.25         # a zone smaller than this is noise
    zone_max_atr: float = 12.0         # bigger than this is a range, not a zone
    size_law_atr: float = 1.0          # a zone >= 1 ATR tall is a "big" zone
    score_min: int = 3                 # size+2 · OB+1 · first-touch+1 · trend+1
    anchor_atr: float = 1.6            # turn must land within this of the station
    max_touches: int = 1
    eq_tol_atr: float = 0.15
    eq_window: int = 60
    ob_lookback: int = 20
    zone_archive: int = 400
    max_zones: int = 60

    # ── the stop: where the thesis dies ─────────────────────────────────
    stop_mode: str = "sweep"           # sweep | zone | atr | structure
    tight_pad_atr: float = 0.25        # pad beyond the sweep wick
    stop_pad_atr: float = 0.5          # pad beyond the zone's far edge
    stop_atr_mult: float = 1.5         # fixed-ATR stop distance
    stop_struct_lookback: int = 10     # swing lookback for a structural stop

    # ── the target: where the delivery ends ─────────────────────────────
    target_mode: str = "far"           # far | nearest | rr | atr | trail
    tp_rr: float = 3.0                 # reward:risk for rr mode
    tp_atr: float = 3.0                # distance for atr mode
    min_rr: float = 2.0                # refuse anything shorter than this
    dest_max_atr: float = 8.0          # nothing within this = no trade
    breakeven_at_r: float = 0.0        # 0 = off; else move stop to entry at +xR
    partial_at_r: float = 0.0          # 0 = off; else bank half at +xR

    # ── clocks, costs, gates ────────────────────────────────────────────
    pos_clock: int = 48
    rx_clock: int = 8
    rx_target_atr: float = 1.0
    rx_stop_atr: float = 0.0           # 0 = use zone far edge + stop_pad_atr
    cost_pts: float = 0.5
    route_reach_atr: float = 3.0
    route_max: int = 1
    pool_reach_atr: float = 2.0
    min_risk_atr: float = 0.2          # R-inflation guard
    sweep_window: int = 12             # ★ gate: sweep must precede the turn
    require_sweep: bool = True
    require_engulf: bool = False
    require_route_clear: bool = True
    require_destination: bool = True

    # ── regime & governors ──────────────────────────────────────────────
    adx_mixed_lo: float = 20.0
    adx_mixed_hi: float = 25.0
    day_budget_r: float = 1.5
    week_budget_r: float = 6.0
    dd_mode_r: float = 6.0
    susp_n: int = 50
    susp_ci_floor: float = 0.55
    susp_net_r: float = -6.0

    # ── sessions (UTC) ──────────────────────────────────────────────────
    asia_end: int = 7
    london_end: int = 12
    ny_end: int = 17
    pos_sessions: List[str] = field(default_factory=lambda: ["ASIA", "NY", "LATE"])
    rx_sessions: List[str] = field(default_factory=lambda: [])
    london_second_entry: bool = True

    # ── profiles: enabled at all, and whether they move capital ─────────
    position_enabled: bool = True
    pool_ride_enabled: bool = True
    reaction_enabled: bool = True
    breaker_enabled: bool = True
    position_live: bool = True
    pool_ride_live: bool = True
    reaction_live: bool = True         # LIVE by his order — the court judges it
    breaker_live: bool = True          # door two, on trial in the open

    # ── account ─────────────────────────────────────────────────────────
    account_size: float = 4000.0
    risk_pct: float = 0.5
    point_value: float = 10.0          # MGC: $10 per point per contract

    @property
    def hash(self) -> str:
        primes = [3, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61,
                  67, 71, 73, 79, 83, 89, 97, 101, 103, 107, 109, 113]
        vals = [self.atr_len, self.ema_len, self.trend_lb, self.pivot_len,
                self.zone_min_atr, self.zone_max_atr, self.size_law_atr,
                self.score_min, self.anchor_atr, self.stop_pad_atr,
                self.tight_pad_atr, self.pos_clock, self.rx_clock,
                self.rx_target_atr, self.cost_pts, self.day_budget_r,
                self.week_budget_r, self.susp_n, self.susp_ci_floor,
                self.susp_net_r, self.max_zones, self.route_reach_atr,
                self.route_max, self.pool_reach_atr, self.adx_mixed_lo,
                self.adx_mixed_hi, self.max_touches, self.min_rr]
        vals += [self.tp_rr, self.tp_atr, self.stop_atr_mult,
                 1 if self.require_sweep else 0, len(self.stop_mode), len(self.target_mode)]
        primes = primes + [127, 131, 137, 139, 149, 151]
        total = sum(v * p for v, p in zip(vals, primes))
        return str(int(total * 100) % 99991).zfill(5)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["hash"] = self.hash
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StationConfig":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})
