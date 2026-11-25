"""Market data with a fallback chain: yfinance → CSV → synthetic.

The desk must never go dark. If the vendor is down, rate-limited, or refuses a
range (Yahoo caps sub-hourly history at 60 days), the synthetic tape keeps the
product demonstrable — and `last_source` always says which one you are looking
at, so a demo can never be mistaken for evidence.

SYMBOLS carries real contract specs. They are what let one engine trade gold at
$4,390 and bitcoin at $64,000 without a single instrument-specific constant in
the strategy code.
"""
from __future__ import annotations

import csv
import math
import os
import random
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..engine.types import Bar

TF_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
              "1h": 3600, "4h": 14400, "1d": 86400}

# Instrument specs. These are what make the difference between a demo and a
# desk: you cannot buy a third of a futures contract, and margin is real money.
SYMBOLS: Dict[str, Dict[str, Any]] = {
    "XAUUSD": {"label": "Gold Spot", "yf": "GC=F", "unit": "$/oz",
               "point_value": 10.0, "lot_step": 0.01, "min_qty": 0.01,
               "margin_per_unit": 200.0, "spread_pts": 0.25},
    "MGC": {"label": "Micro Gold Futures", "yf": "MGC=F", "unit": "pts",
            "point_value": 10.0, "lot_step": 1.0, "min_qty": 1.0,
            "margin_per_unit": 1500.0, "spread_pts": 0.2},
    "GC": {"label": "Gold Futures", "yf": "GC=F", "unit": "pts",
           "point_value": 100.0, "lot_step": 1.0, "min_qty": 1.0,
           "margin_per_unit": 15000.0, "spread_pts": 0.2},
    "BTCUSD": {"label": "Bitcoin", "yf": "BTC-USD", "unit": "$",
               "point_value": 1.0, "lot_step": 0.001, "min_qty": 0.001,
               "margin_per_unit": 2000.0, "spread_pts": 6.0},
    "ETHUSD": {"label": "Ethereum", "yf": "ETH-USD", "unit": "$",
               "point_value": 1.0, "lot_step": 0.01, "min_qty": 0.01,
               "margin_per_unit": 200.0, "spread_pts": 1.2},
}


def spec(symbol: str) -> Dict[str, Any]:
    return SYMBOLS.get((symbol or "").upper(), SYMBOLS["XAUUSD"])


class DataProvider(ABC):
    name = "base"

    @abstractmethod
    def fetch(self, symbol: str, timeframe: str, limit: int = 600) -> List[Bar]: ...

    def quote(self, symbol: str) -> Optional[Dict]:
        bars = self.fetch(symbol, "5m", 3)
        if not bars:
            return None
        last = bars[-1]
        prev = bars[-2] if len(bars) > 1 else last
        return {"symbol": symbol, "price": last.close, "ts": last.ts,
                "change": last.close - prev.close,
                "change_pct": (last.close - prev.close) / prev.close * 100 if prev.close else 0.0,
                "high": last.high, "low": last.low, "source": self.name}


class YFinanceProvider(DataProvider):
    name = "yfinance"
    _INTERVAL = {"1m": "1m", "3m": "5m", "5m": "5m", "15m": "15m",
                 "30m": "30m", "1h": "60m", "4h": "1h", "1d": "1d"}
    # Yahoo's intraday ceilings: 1m -> 7d, sub-hour -> 60d, hourly -> 730d
    _PERIOD = {"1m": "7d", "5m": "60d", "15m": "60d", "30m": "60d",
               "60m": "730d", "1h": "730d", "1d": "5y"}

    def fetch(self, symbol: str, timeframe: str, limit: int = 600) -> List[Bar]:
        import yfinance as yf
        meta = SYMBOLS.get(symbol.upper(), SYMBOLS["XAUUSD"])
        self._is_crypto = meta["yf"].endswith("-USD")
        interval = self._INTERVAL.get(timeframe, "30m")
        period = self._PERIOD.get(interval, "1mo")
        df = yf.download(meta["yf"], interval=interval, period=period,
                         progress=False, auto_adjust=False, threads=False)
        if df is None or df.empty:
            raise RuntimeError(f"no data for {meta['yf']} {interval}")
        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)
        bars: List[Bar] = []
        for idx, row in df.iterrows():
            ts = int(idx.timestamp()) if hasattr(idx, "timestamp") else int(time.time())
            try:
                bars.append(Bar(ts=ts, open=float(row["Open"]), high=float(row["High"]),
                                low=float(row["Low"]), close=float(row["Close"]),
                                volume=float(row.get("Volume", 0) or 0)))
            except (TypeError, ValueError):
                continue
        if timeframe in ("3m", "4h"):
            bars = self._resample(bars, TF_SECONDS[timeframe])
        return bars[-limit:]

    @staticmethod
    def _resample(bars: List[Bar], seconds: int) -> List[Bar]:
        out: List[Bar] = []
        bucket: List[Bar] = []
        cur = None
        for b in bars:
            key = b.ts - (b.ts % seconds)
            if cur is None:
                cur = key
            if key != cur and bucket:
                out.append(Bar(ts=cur, open=bucket[0].open,
                               high=max(x.high for x in bucket),
                               low=min(x.low for x in bucket),
                               close=bucket[-1].close,
                               volume=sum(x.volume for x in bucket)))
                bucket, cur = [], key
            bucket.append(b)
        if bucket and cur is not None:
            out.append(Bar(ts=cur, open=bucket[0].open, high=max(x.high for x in bucket),
                           low=min(x.low for x in bucket), close=bucket[-1].close,
                           volume=sum(x.volume for x in bucket)))
        return out


class CSVProvider(DataProvider):
    name = "csv"

    def __init__(self, path: str) -> None:
        self.path = path

    def fetch(self, symbol: str, timeframe: str, limit: int = 600) -> List[Bar]:
        bars: List[Bar] = []
        with open(self.path, newline="") as fh:
            for row in csv.DictReader(fh):
                low = {k.lower().strip(): v for k, v in row.items() if k}
                try:
                    raw_ts = low.get("time") or low.get("timestamp") or low.get("date") or "0"
                    ts = int(float(raw_ts)) if str(raw_ts).replace(".", "").isdigit() else int(
                        datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00")).timestamp())
                    bars.append(Bar(ts=ts, open=float(low["open"]), high=float(low["high"]),
                                    low=float(low["low"]), close=float(low["close"]),
                                    volume=float(low.get("volume", 0) or 0)))
                except (KeyError, ValueError, TypeError):
                    continue
        return bars[-limit:]


class SyntheticProvider(DataProvider):
    """A believable gold tape: sessions, deliveries, sweeps. Deterministic."""
    name = "synthetic"

    def __init__(self, seed: int = 4116, start: float = 4462.0) -> None:
        self.seed, self.start = seed, start

    def fetch(self, symbol: str, timeframe: str, limit: int = 600) -> List[Bar]:
        rng = random.Random(self.seed)
        base = {"BTCUSD": 96000.0, "ETHUSD": 3400.0}.get((symbol or "").upper(), self.start)
        scale = base / 4462.0
        step = TF_SECONDS.get(timeframe, 1800)
        now = int(time.time())
        t0 = now - (now % step) - step * limit
        px = base
        bars: List[Bar] = []
        drift = 0.0
        for k in range(limit):
            ts = t0 + k * step
            hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
            # sessions breathe differently — Asia coils, London runs, NY resolves
            vol = 0.55 if hour < 7 else 1.35 if hour < 12 else 1.5 if hour < 17 else 0.8
            if k % 47 == 0:
                drift = rng.uniform(-1.0, 1.0)
            drift = drift * 0.94 + rng.uniform(-0.35, 0.35)
            body = drift * vol * (step / 1800) ** 0.5 * 2.2 * scale
            o = px
            c = o + body + rng.gauss(0, 0.9 * vol * scale)
            wick = abs(rng.gauss(0, 1.2 * vol * scale)) + 0.4 * scale
            h = max(o, c) + wick * rng.uniform(0.3, 1.4)
            l = min(o, c) - wick * rng.uniform(0.3, 1.4)
            if k % 63 == 0:                      # the raid: a wick that takes stops
                if rng.random() < 0.5:
                    l -= rng.uniform(2.5, 6.0) * vol * scale
                else:
                    h += rng.uniform(2.5, 6.0) * vol * scale
            bars.append(Bar(ts=ts, open=round(o, 2), high=round(h, 2),
                            low=round(l, 2), close=round(c, 2),
                            volume=round(rng.uniform(400, 2600) * vol, 0)))
            px = c
        return bars


class MarketData:
    """Provider chain with a memory: try live, degrade gracefully, never fail."""

    def __init__(self, prefer_live: bool = True) -> None:
        self.prefer_live = prefer_live
        self.synthetic = SyntheticProvider()
        self._cache: Dict[str, tuple] = {}
        self.last_source = "synthetic"

    def bars(self, symbol: str = "XAUUSD", timeframe: str = "30m",
             limit: int = 600, ttl: int = 45) -> List[Bar]:
        key = f"{symbol}:{timeframe}:{limit}"
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            self.last_source = hit[2]
            return hit[1]
        bars: List[Bar] = []
        source = "synthetic"
        if self.prefer_live:
            try:
                bars = YFinanceProvider().fetch(symbol, timeframe, limit)
                source = "yfinance"
            except Exception:
                bars = []
        if len(bars) < 120:
            bars = self.synthetic.fetch(symbol, timeframe, limit)
            source = "synthetic"
        self._cache[key] = (time.time(), bars, source)
        self.last_source = source
        return bars

    def quote(self, symbol: str = "XAUUSD") -> Dict:
        bars = self.bars(symbol, "5m", 300)
        last, prev = bars[-1], bars[-2] if len(bars) > 1 else bars[-1]
        day = [b for b in bars if b.ts > last.ts - 86400]
        return {
            "symbol": symbol, "label": SYMBOLS.get(symbol, {}).get("label", symbol),
            "price": last.close, "ts": last.ts,
            "change": round(last.close - prev.close, 2),
            "change_pct": round((last.close - prev.close) / prev.close * 100, 3) if prev.close else 0.0,
            "day_high": max(b.high for b in day) if day else last.high,
            "day_low": min(b.low for b in day) if day else last.low,
            "source": self.last_source,
        }
