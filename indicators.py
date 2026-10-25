"""Technical indicators, implemented with the standard library only.

All functions accept plain sequences and return lists aligned to the input,
using ``None`` for the warm-up period. That makes it trivial to evaluate a
signal at any historical index (needed by the backtester) without look-ahead.
"""

from __future__ import annotations

import math
from statistics import fmean, pstdev


def sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0:
        return out
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period or period <= 0:
        return out
    seed = fmean(values[:period])
    out[period - 1] = seed
    k = 2.0 / (period + 1.0)
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def _ema_of_optional(values: list[float | None], period: int) -> list[float | None]:
    """EMA across a series that may begin with Nones (e.g. a MACD line)."""
    out: list[float | None] = [None] * len(values)
    idx = [i for i, v in enumerate(values) if v is not None]
    if len(idx) < period:
        return out
    compact = [values[i] for i in idx]  # type: ignore[list-item]
    e = ema(compact, period)
    for j, i in enumerate(idx):
        out[i] = e[j]
    return out


def rsi_wilder(closes: list[float], period: int = 14) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    if n <= period:
        return out
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))

    avg_gain = fmean(gains[:period])
    avg_loss = fmean(losses[:period])
    out[period] = _rsi_value(avg_gain, avg_loss)

    for i in range(period + 1, n):
        g = gains[i - 1]
        l = losses[i - 1]
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def atr_wilder(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    if n <= period:
        return out
    tr: list[float] = [highs[0] - lows[0]]
    for i in range(1, n):
        tr.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )
    avg = fmean(tr[1 : period + 1])
    out[period] = avg
    for i in range(period + 1, n):
        avg = (avg * (period - 1) + tr[i]) / period
        out[i] = avg
    return out


def macd(
    closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    ef = ema(closes, fast)
    es = ema(closes, slow)
    line: list[float | None] = [
        (a - b) if a is not None and b is not None else None for a, b in zip(ef, es)
    ]
    sig = _ema_of_optional(line, signal)
    hist: list[float | None] = [
        (a - b) if a is not None and b is not None else None for a, b in zip(line, sig)
    ]
    return line, sig, hist


def bollinger(
    closes: list[float], period: int = 20, mult: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    mid = sma(closes, period)
    upper: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        sd = pstdev(window)
        m = mid[i]
        if m is not None:
            upper[i] = m + mult * sd
            lower[i] = m - mult * sd
    return upper, mid, lower


def adx_wilder(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Return (ADX, +DI, -DI)."""
    n = len(closes)
    adx: list[float | None] = [None] * n
    pdi: list[float | None] = [None] * n
    mdi: list[float | None] = [None] * n
    if n <= 2 * period:
        return adx, pdi, mdi

    tr = [0.0] * n
    pdm = [0.0] * n
    mdm = [0.0] * n
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        pdm[i] = up if (up > down and up > 0) else 0.0
        mdm[i] = down if (down > up and down > 0) else 0.0
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    str_ = sum(tr[1 : period + 1])
    spdm = sum(pdm[1 : period + 1])
    smdm = sum(mdm[1 : period + 1])
    dxs: list[tuple[int, float]] = []

    for i in range(period + 1, n):
        str_ = str_ - str_ / period + tr[i]
        spdm = spdm - spdm / period + pdm[i]
        smdm = smdm - smdm / period + mdm[i]
        if str_ > 0:
            p = 100.0 * spdm / str_
            m = 100.0 * smdm / str_
            pdi[i] = p
            mdi[i] = m
            denom = p + m
            dx = 100.0 * abs(p - m) / denom if denom else 0.0
            dxs.append((i, dx))

    if len(dxs) < period:
        return adx, pdi, mdi
    first = fmean([dx for _, dx in dxs[:period]])
    adx[dxs[period - 1][0]] = first
    prev = first
    for i, dx in dxs[period:]:
        prev = (prev * (period - 1) + dx) / period
        adx[i] = prev
    return adx, pdi, mdi


def log_returns(closes: list[float]) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    for i in range(1, len(closes)):
        if closes[i] > 0 and closes[i - 1] > 0:
            out[i] = math.log(closes[i] / closes[i - 1])
    return out


def realized_vol(closes: list[float], period: int = 20) -> float | None:
    if len(closes) <= period:
        return None
    rets = [
        math.log(closes[i] / closes[i - 1])
        for i in range(len(closes) - period, len(closes))
        if closes[i] > 0 and closes[i - 1] > 0
    ]
    if len(rets) < period:
        return None
    return pstdev(rets) * math.sqrt(252.0)


def pct_return(closes: list[float], periods: int) -> float | None:
    if len(closes) <= periods or closes[-1 - periods] == 0:
        return None
    return closes[-1] / closes[-1 - periods] - 1.0


def percentile_rank(values: list[float], value: float) -> float:
    if not values:
        return 0.5
    below = sum(1 for v in values if v < value)
    equal = sum(1 for v in values if v == value)
    return (below + 0.5 * equal) / len(values)


def compute_indicators(bars) -> dict[str, list]:
    """Compute every indicator once, aligned to bar index."""
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    rsi = rsi_wilder(closes, 14)
    atr = atr_wilder(highs, lows, closes, 14)
    macd_line, macd_sig, macd_hist = macd(closes)
    bb_up, bb_mid, bb_lo = bollinger(closes, 20, 2.0)
    adx, pdi, mdi = adx_wilder(highs, lows, closes, 14)

    vol20: list[float | None] = [None] * len(closes)
    for i in range(20, len(closes)):
        window = closes[i - 20 : i + 1]
        rets = [
            math.log(window[j] / window[j - 1])
            for j in range(1, len(window))
            if window[j] > 0 and window[j - 1] > 0
        ]
        if len(rets) == 20:
            vol20[i] = pstdev(rets) * math.sqrt(252.0)

    return {
        "close": closes,
        "high": highs,
        "low": lows,
        "open": [b.open for b in bars],
        "volume": [b.volume for b in bars],
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "rsi": rsi,
        "atr": atr,
        "macd": macd_line,
        "macd_signal": macd_sig,
        "macd_hist": macd_hist,
        "bb_up": bb_up,
        "bb_mid": bb_mid,
        "bb_lo": bb_lo,
        "adx": adx,
        "pdi": pdi,
        "mdi": mdi,
        "vol20": vol20,
    }
