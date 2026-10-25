"""Walk-forward validation of the technical core.

For every historical day (after indicator warm-up) the model is re-scored using
only data available at that close, then compared with the forward return. This
is descriptive, not a trading simulation: no costs, no slippage, overlapping
windows. It answers one question -- "has this factor mix historically leaned the
right way?" -- not "what would the P&L have been?".
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from .config import Asset
from .indicators import compute_indicators
from .market import Series
from .signals import action_for, technical_score


@dataclass
class Bucket:
    action: str
    n: int
    avg: float
    med: float
    hit: float | None
    best: float
    worst: float


@dataclass
class Backtest:
    asset_key: str
    name: str
    horizon: int
    start: str
    end: str
    samples: int
    baseline_avg: float
    baseline_up: float
    buckets: list[Bucket]
    directional_n: int
    directional_hit: float | None


def run_backtest(asset: Asset, series: Series, horizon: int = 10, warmup: int = 250) -> Backtest:
    ind = compute_indicators(series.bars)
    closes = ind["close"]
    n = len(closes)
    if n <= warmup + horizon:
        raise ValueError(f"{asset.key}: not enough history for a backtest")

    rows: list[tuple[str, float]] = []
    for i in range(warmup, n - horizon):
        score = technical_score(asset, ind, i)
        fwd = closes[i + horizon] / closes[i] - 1.0
        rows.append((action_for(score), fwd))

    all_returns = [r for _, r in rows]
    baseline_avg = sum(all_returns) / len(all_returns)
    baseline_up = sum(1 for r in all_returns if r > 0) / len(all_returns)

    order = ["STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL"]
    buckets: list[Bucket] = []
    for action in order:
        rets = [r for a, r in rows if a == action]
        if not rets:
            buckets.append(Bucket(action, 0, 0.0, 0.0, None, 0.0, 0.0))
            continue
        if action in ("STRONG BUY", "BUY"):
            hit = sum(1 for r in rets if r > 0) / len(rets)
        elif action in ("SELL", "STRONG SELL"):
            hit = sum(1 for r in rets if r < 0) / len(rets)
        else:
            hit = None
        buckets.append(
            Bucket(
                action=action,
                n=len(rets),
                avg=sum(rets) / len(rets),
                med=median(rets),
                hit=hit,
                best=max(rets),
                worst=min(rets),
            )
        )

    directional = [
        r for a, r in rows
        if a in ("STRONG BUY", "BUY", "SELL", "STRONG SELL")
    ]
    directional_hit = None
    if directional:
        correct = 0
        for a, r in rows:
            if a in ("STRONG BUY", "BUY") and r > 0:
                correct += 1
            elif a in ("SELL", "STRONG SELL") and r < 0:
                correct += 1
        directional_hit = correct / len(directional)

    return Backtest(
        asset_key=asset.key,
        name=asset.name,
        horizon=horizon,
        start=str(series.bars[warmup].date),
        end=str(series.bars[n - horizon - 1].date),
        samples=len(rows),
        baseline_avg=baseline_avg,
        baseline_up=baseline_up,
        buckets=buckets,
        directional_n=len(directional),
        directional_hit=directional_hit,
    )
