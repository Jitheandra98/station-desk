"""Streaming-safe indicators.

Every function reads only indices <= the current bar, which is what makes
look-ahead impossible at the arithmetic level rather than by convention.

`pivot_high`/`pivot_low` confirm `right` bars late and say so — an honest lag is
better than a pivot that repaints. `wilson_bounds` is used for the kill switch:
it answers "how sure can we honestly be about this win rate", which a raw
percentage cannot.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from .types import Bar


def true_range(bars: Sequence[Bar], i: int) -> float:
    if i == 0:
        return bars[0].high - bars[0].low
    prev_close = bars[i - 1].close
    b = bars[i]
    return max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))


def atr_series(bars: Sequence[Bar], length: int) -> List[float]:
    """Wilder's ATR, matching TradingView's ta.atr()."""
    out: List[float] = []
    prev: Optional[float] = None
    for i in range(len(bars)):
        tr = true_range(bars, i)
        if prev is None:
            prev = tr
        else:
            prev = (prev * (length - 1) + tr) / length
        out.append(prev)
    return out


def ema_series(values: Sequence[float], length: int) -> List[float]:
    out: List[float] = []
    k = 2.0 / (length + 1.0)
    prev: Optional[float] = None
    for v in values:
        prev = v if prev is None else (v - prev) * k + prev
        out.append(prev)
    return out


def sma_series(values: Sequence[float], length: int) -> List[float]:
    out: List[float] = []
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= length:
            run -= values[i - length]
        out.append(run / min(i + 1, length))
    return out


def rsi_series(values: Sequence[float], length: int = 14) -> List[float]:
    out: List[float] = []
    gain = loss = 0.0
    for i, v in enumerate(values):
        if i == 0:
            out.append(50.0)
            continue
        ch = v - values[i - 1]
        up, dn = max(ch, 0.0), max(-ch, 0.0)
        if i <= length:
            gain = (gain * (i - 1) + up) / i
            loss = (loss * (i - 1) + dn) / i
        else:
            gain = (gain * (length - 1) + up) / length
            loss = (loss * (length - 1) + dn) / length
        out.append(100.0 if loss == 0 else 100.0 - 100.0 / (1.0 + gain / loss))
    return out


def dmi_series(bars: Sequence[Bar], length: int = 14) -> Tuple[List[float], List[float], List[float]]:
    """Returns (+DI, -DI, ADX) — the regime instrument."""
    n = len(bars)
    plus_di: List[float] = [0.0] * n
    minus_di: List[float] = [0.0] * n
    adx: List[float] = [0.0] * n
    s_tr = s_pdm = s_mdm = 0.0
    adx_prev: Optional[float] = None
    for i in range(n):
        if i == 0:
            continue
        up = bars[i].high - bars[i - 1].high
        dn = bars[i - 1].low - bars[i].low
        pdm = up if (up > dn and up > 0) else 0.0
        mdm = dn if (dn > up and dn > 0) else 0.0
        tr = true_range(bars, i)
        if i <= length:
            s_tr += tr
            s_pdm += pdm
            s_mdm += mdm
        else:
            s_tr = s_tr - s_tr / length + tr
            s_pdm = s_pdm - s_pdm / length + pdm
            s_mdm = s_mdm - s_mdm / length + mdm
        if s_tr > 0:
            plus_di[i] = 100.0 * s_pdm / s_tr
            minus_di[i] = 100.0 * s_mdm / s_tr
        denom = plus_di[i] + minus_di[i]
        dx = 100.0 * abs(plus_di[i] - minus_di[i]) / denom if denom > 0 else 0.0
        if i <= length * 2:
            adx_prev = dx if adx_prev is None else (adx_prev * (i - 1) + dx) / i
        else:
            adx_prev = (adx_prev * (length - 1) + dx) / length
        adx[i] = adx_prev or 0.0
    return plus_di, minus_di, adx


def pivot_high(bars: Sequence[Bar], i: int, left: int, right: int) -> Optional[float]:
    """Confirmed pivot high at bar (i - right). Honest lag: needs `right` bars to confirm."""
    c = i - right
    if c - left < 0 or c < 0 or i >= len(bars):
        return None
    pivot = bars[c].high
    for j in range(c - left, c + right + 1):
        if j == c:
            continue
        if bars[j].high >= pivot:
            return None
    return pivot


def pivot_low(bars: Sequence[Bar], i: int, left: int, right: int) -> Optional[float]:
    c = i - right
    if c - left < 0 or c < 0 or i >= len(bars):
        return None
    pivot = bars[c].low
    for j in range(c - left, c + right + 1):
        if j == c:
            continue
        if bars[j].low <= pivot:
            return None
    return pivot


def wilson_bounds(wins: float, n: float, z: float = 1.96) -> Tuple[float, float]:
    """Wilson interval — how sure can we honestly be about a win rate."""
    if n <= 0:
        return 0.0, 1.0
    p = wins / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (centre - spread) / d, (centre + spread) / d
