"""THE STATION ENGINE — a faithful Python port of STATION v2.x (Pine).

The whole doctrine in one paragraph: gold delivers between locations. A zone is
a location. Resting liquidity is a destination. A structure turn AT a location,
fuelled by a sweep that trapped the other side, is an appointment worth keeping.
Everything else is scenery, and the engine says so out loud.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .config import StationConfig
from .court import Court
from .indicators import (atr_series, dmi_series, ema_series, pivot_high,
                         pivot_low, rsi_series, sma_series)
from .types import Bar, Direction, Pool, Signal, Trade, Zone, ZoneState


class StationEngine:
    """Bar-by-bar state machine. Feed it closed bars; it never peeks forward."""

    def __init__(self, cfg: Optional[StationConfig] = None) -> None:
        self.cfg = cfg or StationConfig()
        self.reset()

    # ══════════════════════════════════════════════════════════════════
    # state
    # ══════════════════════════════════════════════════════════════════
    def reset(self) -> None:
        self.bars: List[Bar] = []
        self.zones: List[Zone] = []
        self.pools: List[Pool] = []
        self.signals: List[Signal] = []
        self.court = Court({
            "POSITION": self.cfg.position_live,
            "POOL-RIDE": self.cfg.pool_ride_live,
            "REACTION": self.cfg.reaction_live,
            "BREAKER": self.cfg.breaker_live,
        })
        self.open_trades: List[Trade] = []
        self.pending: List[Dict[str, Any]] = []
        self._zid = self._pid = self._tid = 0
        # structure
        self.last_ph: Optional[float] = None
        self.last_pl: Optional[float] = None
        self.ph_levels: List[Tuple[float, int]] = []
        self.pl_levels: List[Tuple[float, int]] = []
        self.struct_dir = 0
        # sweeps
        self.last_sweep_low_bar = -9999
        self.last_sweep_high_bar = -9999
        self.last_sweep_low_px: Optional[float] = None
        self.last_sweep_high_px: Optional[float] = None
        # indicator caches
        self.atr: List[float] = []
        self.ema: List[float] = []
        self.rsi: List[float] = []
        self.adx: List[float] = []
        self.di_plus: List[float] = []
        self.di_minus: List[float] = []
        self.atr_sma: List[float] = []
        # governors
        self.day_r = 0.0
        self.week_r = 0.0
        self.suspended = False
        self.decay_watch = False
        self._cur_day: Optional[int] = None
        self._cur_week: Optional[int] = None
        self.last_pass = "—"
        self.radar: Optional[Dict[str, Any]] = None

    # ══════════════════════════════════════════════════════════════════
    # public API
    # ══════════════════════════════════════════════════════════════════
    def load(self, bars: List[Bar]) -> "StationEngine":
        """Replay a whole history through the same code path as live."""
        self.reset()
        for b in bars:
            self.step(b)
        return self

    # ─────────────────────────────────────────────────────────────────
    # THE ORDER OF OPERATIONS IS THE WHOLE DESIGN.
    #
    # Fills happen BEFORE management, and management happens BEFORE new
    # signals are generated. That ordering is what makes look-ahead
    # structurally impossible: by the time this bar can create a signal,
    # any order armed on a previous bar has already been filled at THIS
    # bar's open, and any open trade has already been judged against THIS
    # bar's high and low. Nothing in the pipeline can see a price that has
    # not happened yet, because the pipeline only ever moves forward.
    # ─────────────────────────────────────────────────────────────────
    def step(self, bar: Bar) -> List[Signal]:
        """Process one closed bar. Returns the signals born on it."""
        self.bars.append(bar)
        i = len(self.bars) - 1
        self._recompute_indicators()
        emitted_from = len(self.signals)

        if i < 60:                       # warm-up: look, do not touch
            return []

        self._roll_governors(bar)
        self._fill_pending(i)            # fills happen at THIS bar's open
        self._manage_open(i)             # then management, stop before target
        self._sensors(i)                 # zones, pivots, pools, structure, sweeps
        self._zone_lifecycle(i)
        self._appointments(i)
        self._update_radar(i)

        return self.signals[emitted_from:]

    # ══════════════════════════════════════════════════════════════════
    # indicators
    # ══════════════════════════════════════════════════════════════════
    # Indicators are recomputed over a trailing window rather than kept as
    # running state. It costs a little speed (~0.6ms/bar) and buys exact
    # parity with the Pine original — Wilder's smoothing is path-dependent,
    # so a rolling recompute over a fixed window is the only way to be sure
    # the Python and the chart agree on what ATR "is".
    def _recompute_indicators(self) -> None:
        c = self.cfg
        window = self.bars[-max(c.ema_len * 3, 300):]
        offset = len(self.bars) - len(window)
        closes = [b.close for b in window]
        atr = atr_series(window, c.atr_len)
        ema = ema_series(closes, c.ema_len)
        rsi = rsi_series(closes)
        dip, dim, adx = dmi_series(window, 14)
        asma = sma_series(atr, min(200, len(atr)))
        # keep only tail values; index math below uses absolute bar index
        self._ind_offset = offset
        self.atr, self.ema, self.rsi = atr, ema, rsi
        self.di_plus, self.di_minus, self.adx, self.atr_sma = dip, dim, adx, asma

    def _v(self, series: List[float], i: int) -> float:
        j = i - self._ind_offset
        if j < 0 or j >= len(series):
            return series[-1] if series else 0.0
        return series[j]

    def atr_at(self, i: int) -> float:
        return max(self._v(self.atr, i), 1e-9)

    # ══════════════════════════════════════════════════════════════════
    # context
    # ══════════════════════════════════════════════════════════════════
    def session_of(self, ts: int) -> str:
        h = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        c = self.cfg
        if h < c.asia_end:
            return "ASIA"
        if h < c.london_end:
            return "LONDON"
        if h < c.ny_end:
            return "NY"
        return "LATE"

    def trend_dir(self, i: int) -> int:
        e_now = self._v(self.ema, i)
        e_then = self._v(self.ema, max(0, i - self.cfg.trend_lb))
        return 1 if e_now > e_then else -1

    def regime(self, i: int) -> str:
        a = self._v(self.adx, i)
        if a > self.cfg.adx_mixed_hi:
            return "TRENDING"
        if a < self.cfg.adx_mixed_lo:
            return "RANGING"
        return "MIXED"

    # ══════════════════════════════════════════════════════════════════
    # zones
    # ══════════════════════════════════════════════════════════════════
    def _add_zone(self, top: float, bot: float, direction: int, kind: str, i: int) -> Optional[Zone]:
        """Zone size is measured in ATR, so gold and bitcoin use one rulebook."""
        c = self.cfg
        atr = self.atr_at(i)
        h = top - bot
        if h < c.zone_min_atr * atr or h > c.zone_max_atr * atr:
            return None
        for z in self.zones:                       # dedupe overlapping twins
            if z.dir == direction and z.state <= ZoneState.TOUCHED:
                overlap = min(top, z.top) - max(bot, z.bot)
                if overlap > 0.5 * h:
                    return None
        self._zid += 1
        z = Zone(id=self._zid, top=top, bot=bot, dir=direction, kind=kind,
                 born=i, born_ts=self.bars[i].ts)
        self.zones.append(z)
        if len(self.zones) > c.max_zones:
            self.zones.pop(0)
        return z

    # The four-term score is deliberately crude. Every term is binary and
    # each is worth what six months of counting said it was worth — size
    # dominates (2 points) because zone height was the strongest single
    # predictor of whether a level held. A continuous "quality" score would
    # fit this history better and generalise worse; the crudeness is the
    # feature.
    def score(self, z: Zone, i: int) -> int:
        """The four-term score: size+2 · OB+1 · first-touch+1 · with-trend+1."""
        s = 0
        s += 2 if z.height >= self.cfg.size_law_atr * self.atr_at(i) else 0
        s += 1 if z.kind == "OB" else 0
        s += 1 if not z.first_touched else 0
        s += 1 if z.dir == self.trend_dir(i) else 0
        return s

    def eligible(self, z: Zone, i: int) -> bool:
        return (z.state in (ZoneState.ACTIVE, ZoneState.TOUCHED)
                and z.touches <= self.cfg.max_touches
                and self.score(z, i) >= self.cfg.score_min)

    def zone_label(self, z: Zone, i: int) -> str:
        big = " ≥1ATR" if z.height >= self.cfg.size_law_atr * self.atr_at(i) else ""
        if self.eligible(z, i):
            return f"★ STATION {z.kind}{big} · S{self.score(z, i)} · T{z.touches}"
        return f"{z.kind}{big}"

    # ══════════════════════════════════════════════════════════════════
    # sensors — they see, they never vote
    # ══════════════════════════════════════════════════════════════════
    def _sensors(self, i: int) -> None:
        c, b = self.cfg, self.bars
        atr = self.atr_at(i)

        # fair value gaps (3-candle)
        if i >= 2:
            if b[i].low > b[i - 2].high:
                self._add_zone(b[i].low, b[i - 2].high, Direction.DEMAND, "FVG", i)
            if b[i].high < b[i - 2].low:
                self._add_zone(b[i - 2].low, b[i].high, Direction.SUPPLY, "FVG", i)

        # pivots + equal-high/low pools
        ph = pivot_high(b, i, c.pivot_len, c.pivot_len)
        pl = pivot_low(b, i, c.pivot_len, c.pivot_len)
        if ph is not None:
            self.last_ph = ph
            pbar = i - c.pivot_len
            for lvl, bidx in self.ph_levels[-12:]:
                if pbar - bidx <= c.eq_window and abs(ph - lvl) <= c.eq_tol_atr * atr:
                    self._pid += 1
                    self.pools.append(Pool(self._pid, max(ph, lvl), pbar, True, ts=b[pbar].ts))
                    break
            self.ph_levels.append((ph, pbar))
            self.ph_levels = self.ph_levels[-30:]
        if pl is not None:
            self.last_pl = pl
            pbar = i - c.pivot_len
            for lvl, bidx in self.pl_levels[-12:]:
                if pbar - bidx <= c.eq_window and abs(pl - lvl) <= c.eq_tol_atr * atr:
                    self._pid += 1
                    self.pools.append(Pool(self._pid, min(pl, lvl), pbar, False, ts=b[pbar].ts))
                    break
            self.pl_levels.append((pl, pbar))
            self.pl_levels = self.pl_levels[-30:]
        self.pools = self.pools[-40:]

        # structure break + order-block extraction
        self.break_dir = 0
        self.break_level = None
        self.is_choch = False
        if self.last_ph is not None and b[i].close > self.last_ph:
            self.break_dir, self.break_level = 1, self.last_ph
            self.is_choch = self.struct_dir == -1
            self.struct_dir = 1
            self.last_ph = None
            for k in range(1, min(c.ob_lookback, i)):
                if b[i - k].close < b[i - k].open:
                    self._add_zone(b[i - k].high, b[i - k].low, Direction.DEMAND, "OB", i)
                    break
        elif self.last_pl is not None and b[i].close < self.last_pl:
            self.break_dir, self.break_level = -1, self.last_pl
            self.is_choch = self.struct_dir == 1
            self.struct_dir = -1
            self.last_pl = None
            for k in range(1, min(c.ob_lookback, i)):
                if b[i - k].close > b[i - k].open:
                    self._add_zone(b[i - k].high, b[i - k].low, Direction.SUPPLY, "OB", i)
                    break
        if self.break_dir:
            self._emit(i, "STRUCTURE", self.break_dir, b[i].close,
                       ("CHoCH" if self.is_choch else "BOS"),
                       f"broke {self.break_level:.2f}")

        self._detect_sweeps(i, atr)

    # WHY THREE KINDS OF SWEEP:
    # A sweep is not a chart pattern, it is an event where somebody's stop
    # got filled. Stops rest in three places — under equal highs/lows that
    # everyone can see, under the last swing extreme, and under yesterday's
    # range. Detecting only the first (as the original Pine did) starved
    # the gate: on a live 30m tape it produced 3 pools in 600 bars, so the
    # ★ contract almost never armed. Widening it is a real change to the
    # doctrine and it is measurable — /api/engine/sweep can price it.
    def _detect_sweeps(self, i: int, atr: float) -> None:
        """Liquidity taken = a wick through a level that closes back inside.

        Three kinds of resting money count, because all three trap somebody:
        equal-high/low pools, the last swing extreme, and the previous day's
        high and low. The wick itself is remembered — the stop belongs there.
        """
        b = self.bars
        tol = self.cfg.eq_tol_atr * atr
        bar = b[i]

        def took_low(level: float) -> bool:
            return bar.low < level - tol and bar.close > level

        def took_high(level: float) -> bool:
            return bar.high > level + tol and bar.close < level

        # 1 ── engineered pools (equal highs / equal lows)
        for p in self.pools:
            if p.swept:
                continue
            if p.is_high and bar.high > p.level:
                p.swept, p.swept_bar = True, i
                if bar.close < p.level:
                    self._mark_sweep(i, -1, bar.high, f"EQH pool {p.level:.2f}")
            elif (not p.is_high) and bar.low < p.level:
                p.swept, p.swept_bar = True, i
                if bar.close > p.level:
                    self._mark_sweep(i, 1, bar.low, f"EQL pool {p.level:.2f}")

        # 2 ── the last swing extremes (the obvious stops everybody leaves)
        for lvl, bidx in self.pl_levels[-6:]:
            if bidx < i - 1 and took_low(lvl):
                self._mark_sweep(i, 1, bar.low, f"swing low {lvl:.2f}")
                break
        for lvl, bidx in self.ph_levels[-6:]:
            if bidx < i - 1 and took_high(lvl):
                self._mark_sweep(i, -1, bar.high, f"swing high {lvl:.2f}")
                break

        # 3 ── yesterday's high and low
        pdh, pdl = self._prev_day_range(i)
        if pdl is not None and took_low(pdl):
            self._mark_sweep(i, 1, bar.low, f"prev-day low {pdl:.2f}")
        if pdh is not None and took_high(pdh):
            self._mark_sweep(i, -1, bar.high, f"prev-day high {pdh:.2f}")

    def _mark_sweep(self, i: int, direction: int, wick: float, what: str) -> None:
        if direction == 1:
            if self.last_sweep_low_bar == i:
                return
            self.last_sweep_low_bar, self.last_sweep_low_px = i, wick
            self._emit(i, "SWEEP", 1, wick, "Sell-side swept",
                       f"{what} taken — fuel for a turn up")
        else:
            if self.last_sweep_high_bar == i:
                return
            self.last_sweep_high_bar, self.last_sweep_high_px = i, wick
            self._emit(i, "SWEEP", -1, wick, "Buy-side swept",
                       f"{what} taken — fuel for a turn down")

    def _prev_day_range(self, i: int) -> tuple:
        """Yesterday's extremes, computed only from bars already closed."""
        cache = getattr(self, "_pd_cache", None)
        today = datetime.fromtimestamp(self.bars[i].ts, tz=timezone.utc).date()
        if cache and cache[0] == today:
            return cache[1], cache[2]
        hi = lo = None
        seen_today = False
        for k in range(i, max(0, i - 400), -1):
            d = datetime.fromtimestamp(self.bars[k].ts, tz=timezone.utc).date()
            if d == today:
                seen_today = True
                continue
            if not seen_today:
                continue
            if hi is None:
                hi, lo = self.bars[k].high, self.bars[k].low
                prev_day = d
            elif d == prev_day:
                hi = max(hi, self.bars[k].high)
                lo = min(lo, self.bars[k].low)
            else:
                break
        self._pd_cache = (today, hi, lo)
        return hi, lo

    # ══════════════════════════════════════════════════════════════════
    # zone lifecycle
    # ══════════════════════════════════════════════════════════════════
    def _zone_lifecycle(self, i: int) -> None:
        c, b = self.cfg, self.bars
        self.touch_events: List[Zone] = []
        for z in list(self.zones):
            if z.state in (ZoneState.ACTIVE, ZoneState.TOUCHED):
                from_above = z.dir == Direction.DEMAND and b[i].low <= z.top and b[i - 1].low > z.top
                from_below = z.dir == Direction.SUPPLY and b[i].high >= z.bot and b[i - 1].high < z.bot
                if from_above or from_below:
                    was_first = not z.first_touched
                    z.touches += 1
                    z.first_touched = True
                    z.state = ZoneState.TOUCHED
                    if was_first:
                        self.touch_events.append(z)
                # consumed: full traverse
                if ((z.dir == Direction.DEMAND and b[i].low <= z.bot)
                        or (z.dir == Direction.SUPPLY and b[i].high >= z.top)):
                    z.state = ZoneState.CONSUMED
            elif z.state == ZoneState.CONSUMED:
                inv_up = z.dir == Direction.DEMAND and b[i - 1].close < z.bot and b[i].high >= z.bot
                inv_dn = z.dir == Direction.SUPPLY and b[i - 1].close > z.top and b[i].low <= z.top
                if inv_up or inv_dn:
                    z.dir = -z.dir
                    z.state = ZoneState.INVERTED
                    z.first_touched = True
                    z.touches = 0
                    # door two: one at a time, like every other contract
                    if (self.cfg.breaker_enabled and not self._has_open("BREAKER")
                            and not self._has_pending("BREAKER")):
                        self._arm("BREAKER", i, z.dir, z,
                                  self.pick_stop(z.dir, i, z))
                elif i - z.born > c.zone_archive:
                    z.state = ZoneState.ARCHIVED
            if z.state == ZoneState.INVERTED and z.touches >= 2:
                z.state = ZoneState.ARCHIVED
            if z.state == ZoneState.ARCHIVED:
                self.zones.remove(z)

    # ══════════════════════════════════════════════════════════════════
    # route + targeting
    # ══════════════════════════════════════════════════════════════════
    # THE ROUTE GATE — the most misunderstood rule in the engine.
    # "Unfinished business" behind a station means resting liquidity or an
    # unfilled gap that price still owes a visit. Trading away from a
    # station with three unswept pools behind it is trading in front of a
    # magnet. Only UNSWEPT pools and UNFILLED gaps count; an earlier
    # version counted every stacked zone and vetoed almost everything.
    def route_density(self, direction: int, edge: float, i: int) -> int:
        """Unfinished business behind the station: unswept pools + unfilled gaps."""
        reach = self.cfg.route_reach_atr * self.atr_at(i)
        n = 0
        for p in self.pools:
            if p.swept:
                continue
            if direction == 1 and edge - reach < p.level < edge:
                n += 1
            elif direction == -1 and edge < p.level < edge + reach:
                n += 1
        for z in self.zones:
            if z.kind != "FVG" or z.state != ZoneState.ACTIVE:
                continue
            if direction == 1 and z.dir == Direction.DEMAND and edge - reach < z.top < edge:
                n += 1
            elif direction == -1 and z.dir == Direction.SUPPLY and edge < z.bot < edge + reach:
                n += 1
        return n

    def destinations(self, direction: int, frm: float, i: int) -> List[Dict[str, Any]]:
        """Everything price could deliver to, in order of distance."""
        cap = self.cfg.dest_max_atr * self.atr_at(i)
        out: List[Dict[str, Any]] = []
        for p in self.pools:
            if p.swept:
                continue
            if direction == 1 and frm < p.level <= frm + cap:
                out.append({"price": p.level, "kind": "POOL"})
            elif direction == -1 and frm - cap <= p.level < frm:
                out.append({"price": p.level, "kind": "POOL"})
        for lvl, _ in (self.ph_levels if direction == 1 else self.pl_levels):
            if direction == 1 and frm < lvl <= frm + cap:
                out.append({"price": lvl, "kind": "STRUCT"})
            elif direction == -1 and frm - cap <= lvl < frm:
                out.append({"price": lvl, "kind": "STRUCT"})
        for z in self.zones:
            if z.state > ZoneState.TOUCHED:
                continue
            edge = z.bot if direction == 1 else z.top
            if direction == 1 and frm < edge <= frm + cap:
                out.append({"price": edge, "kind": "STATION"})
            elif direction == -1 and frm - cap <= edge < frm:
                out.append({"price": edge, "kind": "STATION"})
        out.sort(key=lambda d: abs(d["price"] - frm))
        return out

    # THE STOP IS THE STRATEGY.
    # Measured on live gold: sweep-wick stops returned +0.81R per trade,
    # zone-edge +0.17R, fixed-ATR +0.05R, structural -0.06R — same entries,
    # same targets, only the stop moved. The reason is arithmetic, not
    # magic: R is a ratio, and the denominator is the stop distance. A
    # stop placed where the thesis actually dies is both tighter AND more
    # meaningful than one placed at a convenient drawing edge.
    def pick_stop(self, direction: int, i: int, zone: Optional[Zone]) -> float:
        """Where the idea is wrong. Four ways to say it — the trader chooses."""
        c, b, atr = self.cfg, self.bars, self.atr_at(i)
        px = b[i].close
        mode = c.stop_mode
        if mode == "atr":
            return px - c.stop_atr_mult * atr * direction
        if mode == "zone" and zone is not None:
            return (zone.far_edge - c.stop_pad_atr * atr) if direction == 1 else \
                   (zone.far_edge + c.stop_pad_atr * atr)
        if mode == "structure":
            lb = max(2, c.stop_struct_lookback)
            window = b[max(0, i - lb):i + 1]
            ext = min(x.low for x in window) if direction == 1 else max(x.high for x in window)
            return ext - c.stop_pad_atr * atr * direction
        # default: beyond the wick that took the liquidity
        wick = self.last_sweep_low_px if direction == 1 else self.last_sweep_high_px
        fresh = (i - (self.last_sweep_low_bar if direction == 1 else self.last_sweep_high_bar)) <= c.sweep_window
        if wick is None or not fresh:
            window = b[max(0, i - c.sweep_window):i + 1]
            wick = min(x.low for x in window) if direction == 1 else max(x.high for x in window)
        return wick - c.tight_pad_atr * atr * direction

    # Targets are chosen from the map, not from a multiplier. "far" walks
    # to the deepest destination still inside dest_max_atr; "nearest" takes
    # the first. If nothing on the map clears min_rr the trade is refused
    # outright — standing aside is a position, and it is the one this
    # engine takes most often.
    def pick_target(self, direction: int, frm: float, i: int, risk: float) -> Tuple[Optional[float], str]:
        """Where the delivery ends. Destination-led by default, or a fixed rule."""
        c = self.cfg
        mode = c.target_mode
        if mode == "trail":
            return None, "TRAIL"
        if mode == "rr":
            return frm + c.tp_rr * risk * direction, f"{c.tp_rr:g}R"
        if mode == "atr":
            return frm + c.tp_atr * self.atr_at(i) * direction, f"{c.tp_atr:g}ATR"
        dests = self.destinations(direction, frm, i)
        if not dests:
            return (None, "") if c.require_destination else (frm + c.tp_rr * risk * direction, f"{c.tp_rr:g}R")
        viable = [d for d in dests
                  if abs(d["price"] - frm) >= c.min_rr * risk] if c.min_rr > 0 else dests
        if not viable:
            return (None, "") if c.require_destination else (frm + c.tp_rr * risk * direction, f"{c.tp_rr:g}R")
        chosen = max(viable, key=lambda d: abs(d["price"] - frm)) if mode == "far" else viable[0]
        return chosen["price"], chosen["kind"]

    # ══════════════════════════════════════════════════════════════════
    # appointments
    # ══════════════════════════════════════════════════════════════════
    def _governors_ok(self) -> bool:
        c = self.cfg
        return (self.day_r > -c.day_budget_r and self.week_r > -c.week_budget_r
                and not self.suspended)

    def _session_licensed(self, sess: str, profile: str) -> bool:
        c = self.cfg
        if profile == "REACTION":
            return sess in c.rx_sessions
        if sess == "LONDON":
            return "LONDON" in c.pos_sessions
        return sess in c.pos_sessions

    def _appointments(self, i: int) -> None:
        c, b = self.cfg, self.bars
        atr = self.atr_at(i)
        sess = self.session_of(b[i].ts)

        # ── APPOINTMENT A · POSITION: structure turn AT a station ──────
        if (c.position_enabled and self.break_dir
                and not self._has_open("POSITION") and not self._has_pending("POSITION")):
            if not self._governors_ok():
                self.last_pass = "governor: budget or suspension"
            else:
                found = swept_ok = engulf_ok = route_ok = target_ok = False
                sweep_bar = self.last_sweep_low_bar if self.break_dir == 1 else self.last_sweep_high_bar
                swept = (i - sweep_bar) <= c.sweep_window
                engulf = (not c.require_engulf) or (
                    b[i].close > b[i - 1].high if self.break_dir == 1 else b[i].close < b[i - 1].low)
                for z in self.zones:
                    if z.dir != self.break_dir or not self.eligible(z, i):
                        continue
                    lvl = self.break_level
                    dist = (lvl - z.top) if lvl > z.top else (z.bot - lvl) if lvl < z.bot else 0.0
                    if dist > c.anchor_atr * atr:
                        continue
                    found = True
                    if c.require_sweep and not swept:
                        continue
                    swept_ok = True
                    if not engulf:
                        continue
                    engulf_ok = True
                    if c.require_route_clear and self.route_density(self.break_dir, z.far_edge, i) > c.route_max:
                        continue
                    route_ok = True
                    stop = self.pick_stop(self.break_dir, i, z)
                    risk = abs(b[i].close - stop)
                    if risk < c.min_risk_atr * atr:
                        self.last_pass = "risk below sanity floor"
                        continue
                    tgt, kind = self.pick_target(self.break_dir, b[i].close, i, risk)
                    if tgt is None and kind != "TRAIL":
                        continue
                    target_ok = True
                    licensed = self._session_licensed(sess, "POSITION")
                    self._arm("POSITION" if licensed else "LDN-JOURNAL", i, self.break_dir, z, stop,
                              target=tgt, target_kind=kind)
                    self.last_pass = "—" if licensed else f"{sess} unlicensed — journalled"
                    break
                if not self._has_pending("POSITION") and not self._has_pending("LDN-JOURNAL"):
                    self.last_pass = (
                        f"{'CHoCH' if self.is_choch else 'BOS'} in no-man's-land (no station within {c.anchor_atr:g} ATR)"
                        if not found else
                        "no sweep before the turn (★ gate)" if not swept_ok else
                        "turn bar did not engulf" if not engulf_ok else
                        "route dirty — unfinished business behind" if not route_ok else
                        "no destination in reach" if not target_ok else self.last_pass)

        # ── APPOINTMENT B · POOL-RIDE + REACTION on first touch ────────
        for z in getattr(self, "touch_events", []):
            if self.score(z, i) < c.score_min:
                continue
            # pool ride: a pool sitting just beyond the zone is a ferry ticket
            if (c.pool_ride_enabled and not self._has_open("POOL-RIDE")
                    and not self._has_pending("POOL-RIDE")):
                reach = c.pool_reach_atr * atr
                cands = [p for p in self.pools if not p.swept and (
                    (z.dir == 1 and z.top + 0.3 * atr < p.level < z.top + reach) or
                    (z.dir == -1 and z.bot - reach < p.level < z.bot - 0.3 * atr))]
                route_ok_pool = (not c.require_route_clear
                                 or self.route_density(z.dir, z.far_edge, i) <= c.route_max)
                if cands and route_ok_pool:
                    tgt = min(cands, key=lambda p: abs(p.level - b[i].close)).level
                    stop = self.pick_stop(z.dir, i, z)
                    self._arm("POOL-RIDE", i, z.dir, z, stop, target=tgt, target_kind="POOL")
            # reaction: the reflex trade, paper by default (measured uncollectable)
            if (c.reaction_enabled and not self._has_open("REACTION")
                    and not self._has_pending("REACTION")):
                stop = (b[i].close - c.rx_stop_atr * atr * z.dir) if c.rx_stop_atr > 0 \
                    else self.pick_stop(z.dir, i, z)
                tgt = b[i].close + c.rx_target_atr * atr * z.dir
                self._arm("REACTION", i, z.dir, z, stop, target=tgt, target_kind="ATR")

    # Arming records the bar index. _fill_pending refuses to fill anything
    # whose bar index is >= the current bar, which is the mechanical reason
    # a same-bar fill cannot occur. It is enforced by data, not discipline.
    def _arm(self, profile: str, i: int, direction: int, zone: Optional[Zone],
             stop: Optional[float], target: Optional[float] = None, target_kind: str = "") -> None:
        """Arm a fill for the NEXT bar's open. Same-bar fills are impossible here."""
        self.pending.append({
            "profile": profile, "bar": i, "dir": direction,
            "stop": stop, "target": target, "target_kind": target_kind,
            "zone_label": self.zone_label(zone, i) if zone else "",
            "zone_top": zone.top if zone else None,
            "zone_bot": zone.bot if zone else None,
        })

    def _has_open(self, profile: str) -> bool:
        return any(t.profile == profile for t in self.open_trades)

    def _has_pending(self, profile: str) -> bool:
        return any(p["profile"] == profile for p in self.pending)

    # ══════════════════════════════════════════════════════════════════
    # fills — at this bar's open, never the signal bar's close
    # ══════════════════════════════════════════════════════════════════
    def _fill_pending(self, i: int) -> None:
        c, b = self.cfg, self.bars
        atr = self.atr_at(i)
        still: List[Dict[str, Any]] = []
        for p in self.pending:
            if p["bar"] >= i:
                still.append(p)
                continue
            entry = b[i].open
            direction = p["dir"]
            stop = p["stop"]
            if stop is None:
                continue
            risk = abs(entry - stop)
            if risk < c.min_risk_atr * atr:
                continue
            if (direction == 1 and entry <= stop) or (direction == -1 and entry >= stop):
                continue
            tgt, kind = p["target"], p["target_kind"]
            if kind.endswith("R") and kind != "TRAIL" and tgt is None:
                tgt = entry + c.tp_rr * risk * direction
            if kind not in ("TRAIL",) and tgt is not None:
                if (direction == 1 and tgt <= entry) or (direction == -1 and tgt >= entry):
                    continue
                if c.min_rr > 0 and p["profile"] in ("POSITION", "POOL-RIDE") and abs(tgt - entry) < c.min_rr * risk:
                    self.last_pass = f"reward:risk below {c.min_rr:g} — passed"
                    continue
            self._tid += 1
            t = Trade(id=self._tid, dir=direction, entry=entry, stop=stop, target=tgt,
                      entry_bar=i, entry_ts=b[i].ts, profile=p["profile"],
                      session=self.session_of(b[i].ts), zone_label=p["zone_label"],
                      target_kind=kind, live=self._is_live(p["profile"]),
                      zone_top=p["zone_top"], zone_bot=p["zone_bot"],
                      trail_stop=stop if kind == "TRAIL" else None)
            self.open_trades.append(t)
            rr = t.rr_planned
            self._emit(i, "ENTRY", direction, entry,
                       f"{'★ ' if p['profile'] in ('POSITION', 'POOL-RIDE') else ''}"
                       f"{p['profile']} {'BUY' if direction == 1 else 'SELL'}",
                       f"{p['zone_label'] or 'zone'} · stop {stop:.2f} · "
                       f"target {('trail' if tgt is None else f'{tgt:.2f}')}"
                       f"{'' if rr is None else f' · {rr:.1f}R'}",
                       profile=p["profile"])
        self.pending = still

    def _is_live(self, profile: str) -> bool:
        c = self.cfg
        return {"POSITION": c.position_live, "POOL-RIDE": c.pool_ride_live,
                "REACTION": c.reaction_live, "BREAKER": c.breaker_live,
                "LDN-JOURNAL": False}.get(profile, False)

    # ══════════════════════════════════════════════════════════════════
    # management — stop checked FIRST, ambiguous bar is a loss
    # ══════════════════════════════════════════════════════════════════
    # STOP CHECKED BEFORE TARGET — the single most important line in the
    # file. When one bar's range contains both levels, we cannot know which
    # came first, so we assume the worse one. Every backtest that assumes
    # otherwise is quietly inventing money. This is also why the paper
    # broker resolves the same case the same way.
    def _manage_open(self, i: int) -> None:
        c, b = self.cfg, self.bars
        atr = self.atr_at(i)
        for t in list(self.open_trades):
            if i <= t.entry_bar:
                continue
            bar = b[i]
            # excursion bookkeeping
            if t.risk > 0:
                fav = (bar.high - t.entry) if t.dir == 1 else (t.entry - bar.low)
                adv = (t.entry - bar.low) if t.dir == 1 else (bar.high - t.entry)
                t.mfe_r = max(t.mfe_r, fav / t.risk)
                t.mae_r = max(t.mae_r, adv / t.risk)
            # trail
            if t.trail_stop is not None:
                ph = pivot_high(b, i, c.pivot_len, c.pivot_len)
                pl = pivot_low(b, i, c.pivot_len, c.pivot_len)
                if t.dir == 1 and pl is not None and pl - c.tight_pad_atr * atr > t.trail_stop:
                    t.trail_stop = pl - c.tight_pad_atr * atr
                if t.dir == -1 and ph is not None and ph + c.tight_pad_atr * atr < t.trail_stop:
                    t.trail_stop = ph + c.tight_pad_atr * atr
            # breakeven and partials — the trader's own management, made explicit
            if c.breakeven_at_r > 0 and not t.held and t.mfe_r >= c.breakeven_at_r:
                if (t.dir == 1 and t.stop < t.entry) or (t.dir == -1 and t.stop > t.entry):
                    t.stop = t.entry
                    t.reason = t.reason or ""
            if c.partial_at_r > 0 and not t.partial_taken and t.mfe_r >= c.partial_at_r:
                t.partial_taken = True
                t.banked_r += (c.partial_at_r - self.cfg.cost_pts / max(t.risk, 1e-9)) * 0.5
            stop_lv = t.trail_stop if t.trail_stop is not None else t.stop
            # lifecycle labels
            if not t.retested and t.zone_top is not None:
                if (t.dir == 1 and bar.low <= t.zone_top) or (t.dir == -1 and bar.high >= t.zone_bot):
                    t.retested = True
            if t.retested and not t.held:
                if (t.dir == 1 and bar.high >= t.entry + atr) or (t.dir == -1 and bar.low <= t.entry - atr):
                    t.held = True
            # exits — stop first, always
            exit_px = reason = None
            hit_stop = bar.low <= stop_lv if t.dir == 1 else bar.high >= stop_lv
            hit_tgt = (t.target is not None and
                       (bar.high >= t.target if t.dir == 1 else bar.low <= t.target))
            clock = c.rx_clock if t.profile == "REACTION" else c.pos_clock
            if hit_stop:
                exit_px, reason = stop_lv, ("TRAIL STOP" if t.trail_stop is not None else "STOP")
            elif hit_tgt:
                exit_px, reason = t.target, "DELIVERED"
            elif i - t.entry_bar >= clock:
                exit_px, reason = bar.close, f"TIME {clock} — thesis expired"
            if exit_px is not None:
                self._close(t, i, exit_px, reason)

    def _close(self, t: Trade, i: int, exit_px: float, reason: str) -> None:
        gross = (exit_px - t.entry) if t.dir == 1 else (t.entry - exit_px)
        base = (gross - self.cfg.cost_pts) / t.risk if t.risk > 0 else 0.0
        # a banked partial is half the trade already closed at its own R
        t.r = (base * 0.5 + t.banked_r) if t.partial_taken else base
        t.exit, t.exit_bar, t.exit_ts, t.reason, t.open = exit_px, i, self.bars[i].ts, reason, False
        self.open_trades.remove(t)
        self.court.record(t, counts_capital=t.live)
        if t.live:
            self.day_r += t.r
            self.week_r += t.r
            self._check_suspension()
        self._emit(i, "EXIT", t.dir, exit_px,
                   f"{'✔' if t.r > 0 else '✘'} {t.profile} {reason}",
                   f"{t.r:+.2f}R · MFE {t.mfe_r:.2f}R", profile=t.profile, r=t.r)

    # The kill switch uses the UPPER Wilson bound, not the point estimate.
    # Suspension therefore requires that even the optimistic end of the
    # confidence interval has fallen below the floor — the engine must be
    # statistically unable to defend itself before it is stopped, which
    # avoids halting on a normal losing streak.
    def _check_suspension(self) -> None:
        c = self.cfg
        w = self.court.wilson()
        if w and len(self.court.roll_win) >= c.susp_n and w[1] < c.susp_ci_floor:
            self.suspended = True
        if self.court.rolling_net_r(30) <= c.susp_net_r:
            self.suspended = True
        if w and len(self.court.roll_win) >= 30:
            self.decay_watch = w[1] < 0.635

    def _roll_governors(self, bar: Bar) -> None:
        dt = datetime.fromtimestamp(bar.ts, tz=timezone.utc)
        day, week = dt.timetuple().tm_yday, dt.isocalendar()[1]
        if self._cur_day is not None and day != self._cur_day:
            self.day_r = 0.0
        if self._cur_week is not None and week != self._cur_week:
            self.week_r = 0.0
        self._cur_day, self._cur_week = day, week

    # ══════════════════════════════════════════════════════════════════
    # radar + emission
    # ══════════════════════════════════════════════════════════════════
    def _update_radar(self, i: int) -> None:
        atr = self.atr_at(i)
        px = self.bars[i].close
        self.radar = None
        for z in self.zones:
            if not self.eligible(z, i):
                continue
            near = (z.dir == 1 and 0 < px - z.top <= atr) or (z.dir == -1 and 0 < z.bot - px <= atr)
            if near:
                self.radar = {
                    "zone_id": z.id, "kind": z.kind, "dir": z.dir,
                    "score": self.score(z, i), "touches": z.touches,
                    "route_clear": self.route_density(z.dir, z.far_edge, i) <= self.cfg.route_max,
                    "distance": abs(px - z.near_edge),
                }
                break

    def _emit(self, i: int, kind: str, direction: int, price: float,
              title: str, detail: str = "", profile: str = "", r: Optional[float] = None) -> None:
        self.signals.append(Signal(bar=i, ts=self.bars[i].ts, kind=kind, dir=direction,
                                   price=price, title=title, detail=detail,
                                   profile=profile, r=r))
        self.signals = self.signals[-800:]

    # ══════════════════════════════════════════════════════════════════
    # snapshot for the UI
    # ══════════════════════════════════════════════════════════════════
    def snapshot(self, zone_limit: int = 40, signal_limit: int = 120) -> Dict[str, Any]:
        if not self.bars:
            return {"ready": False}
        i = len(self.bars) - 1
        b = self.bars[i]
        atr = self.atr_at(i)
        sess = self.session_of(b.ts)
        up = self.destinations(1, b.close, i)
        dn = self.destinations(-1, b.close, i)
        live_zones = [z for z in self.zones if z.state <= ZoneState.INVERTED]
        return {
            "ready": True,
            "bar_index": i,
            "ts": b.ts,
            "price": b.close,
            "atr": atr,
            "session": sess,
            "session_behaviour": {"ASIA": "respects", "LONDON": "breaks and runs",
                                  "NY": "resolves", "LATE": "finishes"}[sess],
            "regime": self.regime(i),
            "adx": round(self._v(self.adx, i), 1),
            "trend": self.trend_dir(i),
            "rsi": round(self._v(self.rsi, i), 1),
            "licensed": self._session_licensed(sess, "POSITION"),
            "governors": {
                "day_r": round(self.day_r, 3), "week_r": round(self.week_r, 3),
                "day_budget": self.cfg.day_budget_r, "week_budget": self.cfg.week_budget_r,
                "suspended": self.suspended, "decay_watch": self.decay_watch,
            },
            "last_pass": self.last_pass,
            "radar": self.radar,
            "route": {
                "up": up[:6], "down": dn[:6],
                "pools_above": sum(1 for p in self.pools if not p.swept and p.level > b.close),
                "pools_below": sum(1 for p in self.pools if not p.swept and p.level < b.close),
                "clear_up": self.route_density(1, b.close, i) <= self.cfg.route_max,
                "clear_down": self.route_density(-1, b.close, i) <= self.cfg.route_max,
            },
            "zones": [dict(z.to_dict(), label=self.zone_label(z, i),
                           eligible=self.eligible(z, i), score=self.score(z, i))
                      for z in live_zones[-zone_limit:]],
            "pools": [p.to_dict() for p in self.pools if not p.swept][-25:],
            "open_trades": [t.to_dict() for t in self.open_trades],
            "signals": [s.to_dict() for s in self.signals[-signal_limit:]],
            "court": self.court.summary(),
            "config_hash": self.cfg.hash,
        }
