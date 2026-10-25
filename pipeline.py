"""Pipeline orchestration: fetch -> score -> validate -> report."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .backtest import Backtest, run_backtest
from .config import ASSETS, ORDER
from .macro import MacroContext, fetch_macro
from .market import Series, fetch_series
from .news import Article, aggregate, fetch_news
from .signals import Signal, analyze


@dataclass
class RunResult:
    generated_at: datetime
    as_of: str
    signals: list[Signal]
    macro: MacroContext
    backtests: dict[str, Backtest]
    news_count: int
    warnings: list[str]
    account_size: float
    risk_pct: float
    days: float
    articles: list[Article] = field(default_factory=list)
    out_dir: Path = Path("reports")


def parse_assets(value: str | None) -> list[str]:
    if not value or value.strip().lower() == "all":
        return list(ORDER)
    keys = [v.strip().lower() for v in value.split(",") if v.strip()]
    unknown = [k for k in keys if k not in ASSETS]
    if unknown:
        raise ValueError(f"Unknown asset(s): {', '.join(unknown)}. Use: {', '.join(ORDER)}")
    return keys


def run(
    asset_keys: list[str] | None = None,
    *,
    days: float = 4.0,
    account_size: float = 50_000.0,
    risk_pct: float = 1.0,
    refresh: bool = False,
    offline: bool = False,
    no_news: bool = False,
    no_backtest: bool = False,
    market_ttl: int = 600,
    news_ttl: int = 900,
) -> RunResult:
    keys = asset_keys or list(ORDER)
    warnings: list[str] = []

    def load(key: str) -> Series:
        return fetch_series(
            ASSETS[key].symbol, ttl=market_ttl, refresh=refresh, offline=offline
        )

    series_map: dict[str, Series] = {}
    with ThreadPoolExecutor(max_workers=min(4, len(keys))) as pool:
        futures = {pool.submit(load, key): key for key in keys}
        for fut, key in futures.items():
            try:
                series_map[key] = fut.result()
                warnings.extend(series_map[key].warnings)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{ASSETS[key].name}: market data failed ({str(exc)[:120]})")

    if not series_map:
        raise RuntimeError("No market data could be loaded. Check the network or run with --offline after a cached fetch.")

    articles: list[Article] = []
    if not no_news:
        try:
            articles, news_warnings = fetch_news(
                days=days, refresh=refresh, offline=offline, ttl=news_ttl
            )
            warnings.extend(news_warnings)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"News layer failed: {str(exc)[:120]}")

    ctx = fetch_macro(
        refresh=refresh,
        offline=offline,
        brent=series_map.get("brent"),
        wti=series_map.get("wti"),
        gold=series_map.get("gold"),
        ttl=market_ttl,
    )
    warnings.extend(ctx.warnings or [])

    signals: list[Signal] = []
    for key in keys:
        series = series_map.get(key)
        if series is None:
            continue
        news_agg = None if no_news else aggregate(articles, key)
        signals.append(
            analyze(
                ASSETS[key],
                series,
                news_agg,
                ctx,
                account_size=account_size,
                risk_pct=risk_pct,
            )
        )

    backtests: dict[str, Backtest] = {}
    if not no_backtest:
        for signal in signals:
            try:
                backtests[signal.asset.key] = run_backtest(signal.asset, signal.series, horizon=10)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{signal.asset.name}: backtest skipped ({str(exc)[:90]})")

    as_of = max(s.last.date for s in series_map.values())
    generated = datetime.now().astimezone()

    if (generated.date() - as_of).days > 4:
        warnings.append(
            f"Latest market bar is {as_of}; the feed may be stale or the market has been closed."
        )

    # Stable de-duplication of warnings.
    seen: set[str] = set()
    unique_warnings: list[str] = []
    for w in warnings:
        if w not in seen:
            seen.add(w)
            unique_warnings.append(w)

    return RunResult(
        generated_at=generated,
        as_of=str(as_of),
        signals=signals,
        macro=ctx,
        backtests=backtests,
        news_count=len(articles),
        warnings=unique_warnings,
        account_size=account_size,
        risk_pct=risk_pct,
        days=days,
        articles=articles,
    )
