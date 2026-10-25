"""Market data ingestion from Yahoo Finance's public chart endpoint."""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from . import net


@dataclass
class Bar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class Series:
    symbol: str
    name: str
    bars: list[Bar]
    currency: str = "USD"
    exchange: str = ""
    stale: bool = False
    source: str = "Yahoo Finance"
    warnings: list[str] = field(default_factory=list)
    # Latest quote supplied by Yahoo metadata; daily bars remain the model history.
    current_price: float | None = None
    quote_time: datetime | None = None

    @property
    def last(self) -> Bar:
        return self.bars[-1]

    @property
    def prev(self) -> Bar | None:
        return self.bars[-2] if len(self.bars) >= 2 else None

    @property
    def closes(self) -> list[float]:
        return [b.close for b in self.bars]


def _chart_url(symbol: str, range_: str = "2y", interval: str = "1d") -> str:
    sym = urllib.parse.quote(symbol, safe="")
    return (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
        f"?range={range_}&interval={interval}&includePrePost=false&events=div%2Csplit"
    )


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        v = float(value)
        if v != v:  # NaN
            return None
        return v
    except (TypeError, ValueError):
        return None


def parse_chart(payload: bytes | str, symbol: str) -> Series:
    doc = json.loads(payload)
    chart = doc.get("chart") or {}
    if chart.get("error"):
        raise ValueError(f"Yahoo error for {symbol}: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise ValueError(f"Yahoo returned no result for {symbol}")
    res = results[0]
    meta = res.get("meta") or {}
    timestamps = res.get("timestamp") or []
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []

    by_day: dict[date, Bar] = {}
    for i, ts in enumerate(timestamps):
        close = _f(closes[i] if i < len(closes) else None)
        if close is None or close <= 0:
            continue
        high = _f(highs[i] if i < len(highs) else None) or close
        low = _f(lows[i] if i < len(lows) else None) or close
        open_ = _f(opens[i] if i < len(opens) else None) or close
        vol = _f(volumes[i] if i < len(volumes) else None) or 0.0
        day = datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
        # Keep the freshest print if a date appears twice.
        by_day[day] = Bar(day, open_, max(high, close, open_), min(low, close, open_), close, vol)

    bars = [by_day[d] for d in sorted(by_day)]
    if len(bars) < 30:
        raise ValueError(f"Only {len(bars)} usable bars for {symbol}")

    name = meta.get("shortName") or meta.get("longName") or symbol
    market_price = _f(meta.get("regularMarketPrice"))
    if market_price is None or market_price <= 0:
        market_price = bars[-1].close
    market_ts = _f(meta.get("regularMarketTime"))
    quote_time = (
        datetime.fromtimestamp(int(market_ts), tz=timezone.utc)
        if market_ts is not None and market_ts > 0
        else None
    )

    return Series(
        symbol=symbol,
        name=str(name),
        bars=bars,
        currency=str(meta.get("currency") or "USD"),
        exchange=str(meta.get("fullExchangeName") or meta.get("exchangeName") or ""),
        current_price=market_price,
        quote_time=quote_time,
    )


def fetch_series(
    symbol: str,
    *,
    range_: str = "2y",
    interval: str = "1d",
    ttl: int = 600,
    refresh: bool = False,
    offline: bool = False,
) -> Series:
    url = _chart_url(symbol, range_, interval)
    result = net.fetch(url, ttl=ttl, timeout=25, refresh=refresh, offline=offline)
    series = parse_chart(result.body, symbol)
    series.stale = result.stale
    if result.from_cache and result.stale:
        series.warnings.append(f"{symbol}: live fetch failed; using cached data")
    return series


def daily_change(series: Series) -> tuple[float, float]:
    """(absolute, percent) change of the latest bar vs the previous close."""
    prev = series.prev
    if prev is None or prev.close == 0:
        return 0.0, 0.0
    latest = series.current_price if series.current_price is not None else series.last.close
    delta = latest - prev.close
    return delta, delta / prev.close * 100.0


def dollar_value(series: Series, price_diff: float, multiplier: float) -> float:
    return price_diff * multiplier
