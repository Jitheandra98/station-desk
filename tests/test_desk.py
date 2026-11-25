"""The rules that must never regress.

If a future change makes the scoreboard flatter the strategy, one of these
fails. That is the entire point of the file.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest

from app.data.providers import SyntheticProvider, spec
from app.engine.config import StationConfig
from app.engine.station import StationEngine
from app.engine.types import Bar
from app.execution.base import size_for_risk
from app.execution.paper import PaperBroker


@pytest.fixture(scope="module")
def engine():
    bars = SyntheticProvider(seed=4116).fetch("XAUUSD", "30m", 2500)
    return StationEngine(StationConfig()).load(bars)


# ── the canon ────────────────────────────────────────────────────────
def test_no_same_bar_fills(engine):
    """A trade may never be filled and judged on the bar that signalled it."""
    for t in engine.court.closed:
        assert t.exit_bar > t.entry_bar, f"trade {t.id} exited on its entry bar"


def test_every_trade_scored(engine):
    for t in engine.court.closed:
        assert t.r is not None and t.reason, f"trade {t.id} left the book unscored"


def test_timeouts_are_recorded(engine):
    """Unresolved trades must land in the sample, not be deleted from it."""
    timeouts = [t for t in engine.court.closed if t.reason.startswith("TIME")]
    assert timeouts, "no timeouts recorded — they are being silently dropped"
    for t in timeouts:
        assert t.r is not None


def test_costs_are_charged():
    cfg = StationConfig(cost_pts=1.0)
    eng = StationEngine(cfg)
    eng.bars = [Bar(0, 100, 100, 100, 100)] * 3
    from app.engine.types import Trade
    t = Trade(id=1, dir=1, entry=100.0, stop=99.0, target=102.0, entry_bar=0,
              entry_ts=0, profile="POSITION", session="NY")
    eng.open_trades.append(t)
    eng._close(t, 2, 102.0, "DELIVERED")
    assert t.r == pytest.approx((2.0 - 1.0) / 1.0)   # 2R gross minus 1pt cost


def test_ambiguous_bar_is_a_loss():
    """A bar touching both stop and target resolves as the stop. Always."""
    cfg = StationConfig(cost_pts=0.0, pos_clock=100)
    eng = StationEngine(cfg)
    from app.engine.types import Trade
    eng.bars = [Bar(i, 100, 100, 100, 100) for i in range(3)]
    eng.bars.append(Bar(3, 100, 110, 90, 100))       # hits target AND stop
    eng.atr = [1.0] * 4
    eng._ind_offset = 0
    t = Trade(id=1, dir=1, entry=100.0, stop=95.0, target=105.0, entry_bar=0,
              entry_ts=0, profile="POSITION", session="NY")
    eng.open_trades.append(t)
    eng._manage_open(3)
    assert t.r is not None and t.r < 0, "ambiguous bar was scored as a win"


def test_min_rr_floor_blocks_short_trips():
    cfg = StationConfig(min_rr=3.0)
    eng = StationEngine(cfg)
    eng.bars = [Bar(i, 100, 101, 99, 100) for i in range(80)]
    eng.atr = [1.0] * 80
    eng._ind_offset = 0
    eng.pools = []
    eng.ph_levels = [(101.0, 10)]                     # only 1R away
    tgt, kind = eng.pick_target(1, 100.0, 79, risk=1.0)
    assert tgt is None, "a sub-3R destination passed a 3R floor"


def test_sweep_gate_actually_gates():
    """With the ★ gate on, a turn with no sweep behind it must not fire."""
    bars = SyntheticProvider(seed=99).fetch("XAUUSD", "30m", 1200)
    gated = StationEngine(StationConfig(require_sweep=True)).load(bars)
    ungated = StationEngine(StationConfig(require_sweep=False)).load(bars)
    g = gated.court.buckets["POSITION"].n
    u = ungated.court.buckets["POSITION"].n
    assert g <= u, "the sweep gate let through more trades than no gate at all"


def test_engine_is_deterministic():
    bars = SyntheticProvider(seed=7).fetch("XAUUSD", "30m", 900)
    a = StationEngine().load(bars).court.summary()
    b = StationEngine().load(bars).court.summary()
    assert a["net_r"] == b["net_r"] and a["n"] == b["n"]


def test_config_hash_tracks_calibration():
    assert StationConfig().hash != StationConfig(min_rr=4.0).hash
    assert StationConfig(risk_pct=1.0).hash == StationConfig().hash   # identity ≠ calibration


# ── execution ────────────────────────────────────────────────────────
def test_futures_cannot_be_sliced():
    sp = spec("MGC")
    qty = size_for_risk(1000, 0.5, 4400, 4396, sp["point_value"], sp["lot_step"], sp["min_qty"])
    assert qty == 0.0, "a $1k account was allowed a fraction of a futures contract"


def test_size_rounds_down_never_up():
    sp = spec("MGC")
    qty = size_for_risk(100_000, 0.5, 4400, 4396, sp["point_value"], sp["lot_step"], sp["min_qty"])
    assert qty == 12.0                                  # 500 risk / (4pts * $10) = 12.5 -> 12


def test_margin_is_enforced():
    b = PaperBroker(equity=1000.0, point_value=10.0)
    b.last_price["MGC"] = 4400.0
    o = b.place("MGC", "BUY", 1, stop_loss=4396.0, margin_per_unit=1500.0)
    assert o.status == "REJECTED" and "insufficient funds" in o.reject_reason


def test_broker_stop_beats_target_on_the_same_bar():
    b = PaperBroker(equity=100_000.0, point_value=10.0, spread_pts=0.0, commission=0.0)
    b.last_price["MGC"] = 4400.0
    b.place("MGC", "BUY", 1, stop_loss=4390.0, take_profit=4410.0)
    fills = b.mark("MGC", Bar(0, 4400, 4415, 4385, 4400))   # touches both
    assert fills and fills[0].reason == "stop-loss"


def test_spread_is_paid_on_entry():
    b = PaperBroker(equity=100_000.0, point_value=10.0, spread_pts=0.4, commission=0.0)
    b.last_price["MGC"] = 4400.0
    o = b.place("MGC", "BUY", 1)
    assert o.filled_price == pytest.approx(4400.2)
