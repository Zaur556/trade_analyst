"""Shared fixtures for the test suite (no network, no cache)."""

from __future__ import annotations

from datetime import date, timedelta
from commodity_agent.market import Bar, Series


def make_series(
    prices: list[float],
    *,
    symbol: str = "TEST",
    name: str = "Test Instrument",
    start: date = date(2024, 1, 1),
    volume: float = 10_000,
) -> Series:
    bars: list[Bar] = []
    for i, price in enumerate(prices):
        bars.append(
            Bar(
                date=start + timedelta(days=i),
                open=price,
                high=price * 1.01,
                low=price * 0.99,
                close=price,
                volume=volume,
            )
        )
    return Series(symbol=symbol, name=name, bars=bars)


def trend(n: int = 320, start: float = 100.0, step: float = 0.30) -> list[float]:
    return [start + i * step for i in range(n)]


def downtrend(n: int = 320, start: float = 200.0, step: float = 0.30) -> list[float]:
    return [start - i * step for i in range(n)]


def flat(n: int = 320, level: float = 100.0) -> list[float]:
    """A perfectly flat tape: all factors should cancel to neutral."""
    return [level] * n
