"""Cross-asset macro context: dollar, yields, volatility, growth proxies."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from .config import MACRO, MacroSymbol
from .market import Series, fetch_series


@dataclass
class MacroQuote:
    key: str
    name: str
    symbol: str
    price: float
    unit: str
    change_1d: float          # percent, or basis points for yields
    change_20d: float         # percent, or basis points for yields
    change_kind: str          # "pct" | "bp"
    stale: bool = False


@dataclass
class MacroContext:
    quotes: list[MacroQuote]
    series: dict[str, Series]
    values: dict[str, float]
    brent_wti_spread: float | None = None
    warnings: list[str] | None = None


def _quote(spec: MacroSymbol, series: Series, is_yield: bool = False) -> MacroQuote:
    closes = series.closes
    last = closes[-1]
    prev = closes[-2] if len(closes) > 1 else last
    back = closes[-21] if len(closes) > 20 else closes[0]
    if is_yield:
        d1 = (last - prev) * 100.0
        d20 = (last - back) * 100.0
        kind = "bp"
    else:
        d1 = (last / prev - 1.0) * 100.0 if prev else 0.0
        d20 = (last / back - 1.0) * 100.0 if back else 0.0
        kind = "pct"
    return MacroQuote(
        key=spec.key,
        name=spec.name,
        symbol=spec.symbol,
        price=last,
        unit=spec.unit,
        change_1d=d1,
        change_20d=d20,
        change_kind=kind,
        stale=series.stale,
    )


def fetch_macro(
    *,
    refresh: bool = False,
    offline: bool = False,
    brent: Series | None = None,
    wti: Series | None = None,
    gold: Series | None = None,
    ttl: int = 600,
) -> MacroContext:
    series: dict[str, Series] = {}
    warnings: list[str] = []

    def job(spec: MacroSymbol):
        return spec, fetch_series(
            spec.symbol, range_="1y", ttl=ttl, refresh=refresh, offline=offline
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(job, spec) for spec in MACRO]
        for fut in as_completed(futures):
            try:
                spec, s = fut.result()
                series[spec.key] = s
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Macro {type(exc).__name__}: {str(exc)[:100]}")

    quotes: list[MacroQuote] = []
    for spec in MACRO:
        s = series.get(spec.key)
        if s is None:
            continue
        quotes.append(_quote(spec, s, is_yield=spec.key == "yield10"))

    values: dict[str, float] = {}
    if "dxy" in series:
        values["dxy_ret20"] = series["dxy"].closes[-1] / series["dxy"].closes[-21] - 1.0
    if "yield10" in series:
        values["yield_chg20"] = series["yield10"].closes[-1] - series["yield10"].closes[-21]
    if "vix" in series:
        values["vix"] = series["vix"].closes[-1]
        values["vix_chg20"] = series["vix"].closes[-1] / series["vix"].closes[-21] - 1.0
    if "spx" in series:
        values["spx_ret20"] = series["spx"].closes[-1] / series["spx"].closes[-21] - 1.0
    if "copper" in series:
        values["copper_ret20"] = series["copper"].closes[-1] / series["copper"].closes[-21] - 1.0
    if gold is not None and len(gold.bars) > 20:
        values["gold_ret20"] = gold.closes[-1] / gold.closes[-21] - 1.0

    spread = None
    if brent is not None and wti is not None:
        spread = brent.last.close - wti.last.close
    if spread is not None:
        values["brent_wti_spread"] = spread

    return MacroContext(quotes=quotes, series=series, values=values, brent_wti_spread=spread,
                        warnings=warnings)
