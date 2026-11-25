#!/usr/bin/env python3
"""STATION DESK — command-line court.

    python3 backtest.py --tf 30m --bars 2000
    python3 backtest.py --csv ../recorder_5m_week.csv --tf 5m
    python3 backtest.py --sweep-gate off        # measure what the gate is worth
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))

from app.data.providers import CSVProvider, MarketData, SyntheticProvider
from app.engine.config import StationConfig
from app.engine.station import StationEngine

BAR = "─" * 78


def run(bars, cfg, title):
    eng = StationEngine(cfg).load(bars)
    c = eng.court.summary()
    print(f"\n{BAR}\n {title}   ·   calibration {cfg.hash}   ·   {len(bars)} bars\n{BAR}")
    if not c["n"]:
        print("  no live trades — the engine refused everything it saw.")
    else:
        print(f"  live trades {c['n']:>4}    win {c['win_rate']:.1f}%    "
              f"avg {c['avg_r']:+.3f}R    net {c['net_r']:+.1f}R    "
              f"PF {c['profit_factor'] or 0:.2f}    maxDD {c['max_dd_r']:.1f}R")
        if c["wilson_lo"] is not None:
            print(f"  honest win-rate interval: {c['wilson_lo']*100:.0f}% – {c['wilson_hi']*100:.0f}% (95%)")
    print(f"\n  {'PATTERN':<14}{'N':>5}{'WIN':>8}{'AVG R':>9}{'NET R':>9}   VERDICT")
    for k, b in c["buckets"].items():
        if not b["n"]:
            continue
        print(f"  {k:<14}{b['n']:>5}{(b['win_rate'] or 0):>7.0f}%"
              f"{b['avg_r']:>+9.3f}{b['net_r']:>+9.1f}   {b['verdict']}"
              f"{'' if b['live'] else '  (paper)'}")
    if c["sessions"]:
        print("\n  by session:", "  ".join(
            f"{k} n{v['n']} {v['avg_r']:+.2f}R" for k, v in c["sessions"].items()))
    if c["targets"]:
        print("  by target :", "  ".join(
            f"{k} n{v['n']} {v['avg_r']:+.2f}R" for k, v in c["targets"].items()))
    return c


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the STATION court over history")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--tf", default="30m")
    ap.add_argument("--bars", type=int, default=2000)
    ap.add_argument("--csv", help="score a CSV instead of live data")
    ap.add_argument("--synthetic", action="store_true", help="use the offline tape")
    ap.add_argument("--sweep-gate", choices=["on", "off", "both"], default="on")
    ap.add_argument("--min-rr", type=float, default=None)
    args = ap.parse_args()

    if args.csv:
        bars, src = CSVProvider(args.csv).fetch(args.symbol, args.tf, args.bars), args.csv
    elif args.synthetic:
        bars, src = SyntheticProvider().fetch(args.symbol, args.tf, args.bars), "synthetic"
    else:
        md = MarketData()
        bars = md.bars(args.symbol, args.tf, args.bars)
        src = md.last_source
    print(f"\n◈ STATION DESK — {args.symbol} {args.tf} via {src}")

    def cfg_for(gate: bool) -> StationConfig:
        c = StationConfig(require_sweep=gate)
        if args.min_rr is not None:
            c.min_rr = args.min_rr
        return c

    if args.sweep_gate in ("on", "both"):
        run(bars, cfg_for(True), "★ SWEEP GATE ARMED")
    if args.sweep_gate in ("off", "both"):
        run(bars, cfg_for(False), "GATE OFF — every turn taken")
    print(f"\n{BAR}\n  Single instrument, one history, small samples. A measurement,\n"
          f"  not a promise. Fills next bar open · stop before target · costs charged.\n{BAR}\n")


if __name__ == "__main__":
    main()
