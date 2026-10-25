"""Factor scoring, composite signal, confidence and trade plan.

The model is intentionally transparent: five factors, fixed weights per
instrument, a bounded score in [-100, +100], and explicit reasons. No black box.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from .config import Asset
from .indicators import compute_indicators, percentile_rank
from .macro import MacroContext
from .market import Series, daily_change
from .news import NewsAggregate


def _tanh(x: float) -> float:
    return math.tanh(max(-3.0, min(3.0, x)))


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class Factor:
    key: str
    label: str
    score: float
    weight: float
    points: float
    detail: str


@dataclass
class TradePlan:
    direction: str                 # "LONG" | "SHORT" | "FLAT"
    entry: float | None
    stop: float | None
    target: float | None
    stop_distance: float | None
    rr: float | None
    risk_per_contract: float | None
    contracts: int
    notional_per_contract: float | None
    min_account_one_lot: float | None
    etf_notional: float | None
    trigger_buy: float
    trigger_sell: float
    atr: float


@dataclass
class Signal:
    asset: Asset
    series: Series
    price: float
    change: float
    change_pct: float
    score: float
    action: str
    confidence: float
    confidence_label: str
    factors: list[Factor]
    news: NewsAggregate | None
    plan: TradePlan
    drivers: list[str]
    risks: list[str]
    metrics: dict[str, float | None]
    stale: bool = False


# ---------------------------------------------------------------------------
# Individual factors
# ---------------------------------------------------------------------------

def _trend(ind: dict, i: int) -> tuple[float, str]:
    c = ind["close"][i]
    e20, e50, e200 = ind["ema20"][i], ind["ema50"][i], ind["ema200"][i]
    adx, pdi, mdi = ind["adx"][i], ind["pdi"][i], ind["mdi"][i]
    if None in (e20, e50, e200):
        return 0.0, "Insufficient history for the 200-day trend."

    t1 = _tanh((c / e20 - 1.0) / 0.020)
    t2 = _tanh((e20 / e50 - 1.0) / 0.020)
    t3 = _tanh((e50 / e200 - 1.0) / 0.050)
    slope = (e50 - ind["ema50"][i - 20]) / ind["ema50"][i - 20] if i >= 20 else 0.0
    t4 = _tanh(slope / 0.040)
    if adx is not None and pdi is not None and mdi is not None:
        t5 = (1.0 if pdi > mdi else -1.0) * min(1.0, adx / 35.0)
        adx_txt = f"ADX {adx:.0f} ({'bull' if pdi > mdi else 'bear'})"
    else:
        t5 = 0.0
        adx_txt = "ADX n/a"
    score = 0.20 * t1 + 0.20 * t2 + 0.20 * t3 + 0.15 * t4 + 0.25 * t5
    detail = (
        f"Price {_pct(c / e20 - 1)} vs 20D EMA, {_pct(e20 / e50 - 1)} 20/50 spread, "
        f"{_pct(e50 / e200 - 1)} 50/200 spread; {adx_txt}."
    )
    return _clamp(score), detail


def _momentum(ind: dict, i: int) -> tuple[float, str]:
    rsi = ind["rsi"][i]
    hist = ind["macd_hist"][i]
    atr = ind["atr"][i]
    closes = ind["close"]
    if rsi is None or hist is None or atr is None or atr <= 0 or i < 20:
        return 0.0, "Insufficient history for momentum."

    ret20 = closes[i] / closes[i - 20] - 1.0
    window = closes[max(0, i - 251) : i + 1]
    hi, lo = max(window), min(window)
    pos52 = 2 * (closes[i] - lo) / (hi - lo) - 1.0 if hi > lo else 0.0

    rsi_s = _tanh((rsi - 50.0) / 18.0)
    macd_s = _tanh(hist / (atr * 0.8))
    ret_s = _tanh(ret20 / 0.080)
    score = 0.30 * rsi_s + 0.30 * macd_s + 0.20 * ret_s + 0.20 * pos52
    detail = (
        f"RSI {rsi:.0f}, MACD histogram {hist:+.2f} ({hist / atr:+.2f} ATR), "
        f"20D return {_pct(ret20)}, {pos52 * 50 + 50:.0f}% of 52W range."
    )
    return _clamp(score), detail


def _meanrev(ind: dict, i: int) -> tuple[float, str]:
    c = ind["close"][i]
    up, lo, e20, atr = ind["bb_up"][i], ind["bb_lo"][i], ind["ema20"][i], ind["atr"][i]
    rsi, adx = ind["rsi"][i], ind["adx"][i]
    if None in (up, lo, e20, atr) or atr <= 0 or rsi is None:
        return 0.0, "Insufficient history for mean reversion."

    band = up - lo
    pb = (c - lo) / band if band > 0 else 0.5
    bb_s = -_tanh((pb - 0.5) / 0.45)
    dist_s = -_tanh((c - e20) / atr / 2.0)
    if pb < 0:
        band_txt = f"{abs(pb) * 100:.0f}% below the lower Bollinger band"
    elif pb > 1:
        band_txt = f"{(pb - 1) * 100:.0f}% above the upper Bollinger band"
    else:
        band_txt = f"{pb * 100:.0f}% of the Bollinger band"
    if rsi > 70:
        ext = -(rsi - 70.0) / 30.0
        ext_txt = f"overbought RSI {rsi:.0f}"
    elif rsi < 30:
        ext = (30.0 - rsi) / 30.0
        ext_txt = f"oversold RSI {rsi:.0f}"
    else:
        ext = 0.0
        ext_txt = "no RSI extreme"
    regime = max(0.25, min(1.0, 1.0 - (adx or 0.0) / 70.0))
    score = (0.45 * bb_s + 0.35 * dist_s + 0.20 * ext) * regime
    detail = (
        f"{band_txt}, {((c - e20) / atr):+.1f} ATR from 20D EMA, "
        f"{ext_txt}."
    )
    return _clamp(score), detail


def _macro_components(asset_key: str, ctx: MacroContext | None) -> tuple[float, str]:
    if ctx is None:
        return 0.0, "Macro context unavailable."
    v = ctx.values
    dxy = _tanh(v.get("dxy_ret20", 0.0) / 0.030)
    yld = _tanh(v.get("yield_chg20", 0.0) / 0.50)
    vix = _tanh(v.get("vix_chg20", 0.0) / 0.35)
    spx = _tanh(v.get("spx_ret20", 0.0) / 0.050)
    growth = _tanh(
        (v.get("copper_ret20", 0.0) - v.get("gold_ret20", 0.0)) / 0.080
    )

    if asset_key == "gold":
        weights = {"dxy": -0.35, "yld": -0.30, "vix": 0.15, "spx": -0.10, "growth": -0.10}
    elif asset_key == "silver":
        weights = {"dxy": -0.25, "yld": -0.15, "vix": 0.10, "spx": 0.10, "growth": 0.40}
    elif asset_key in ("brent", "wti"):
        weights = {"dxy": -0.15, "yld": 0.10, "vix": -0.20, "spx": 0.25, "growth": 0.30}
    else:  # gas is a domestic market; macro matters less
        weights = {"dxy": -0.20, "yld": -0.05, "vix": -0.15, "spx": 0.15, "growth": 0.15}

    parts = {"dxy": dxy, "yld": yld, "vix": vix, "spx": spx, "growth": growth}
    total_w = sum(abs(w) for w in weights.values())
    score = sum(weights[k] * parts[k] for k in weights) / total_w if total_w else 0.0

    labels = {
        "dxy": f"DXY {v.get('dxy_ret20', 0) * 100:+.1f}%/20D",
        "yld": f"10Y {v.get('yield_chg20', 0) * 100:+.0f}bp",
        "vix": f"VIX {v.get('vix', 0):.0f} ({v.get('vix_chg20', 0) * 100:+.0f}%)",
        "spx": f"S&P {v.get('spx_ret20', 0) * 100:+.1f}%/20D",
        "growth": f"copper {v.get('copper_ret20', 0) * 100:+.1f}%/20D",
    }
    detail = "Macro score from " + ", ".join(labels.values()) + "."
    return _clamp(score), detail


def _news_factor(agg: NewsAggregate | None) -> tuple[float, str]:
    if agg is None:
        return 0.0, "News layer disabled."
    if agg.article_count == 0:
        return 0.0, "No material stories in the 96h window."
    top = agg.topics[0][0] if agg.topics else "general coverage"
    tone = "positive" if agg.score > 0.10 else "negative" if agg.score < -0.10 else "mixed"
    detail = (
        f"{agg.article_count} stories ({agg.positive} +, {agg.negative} -, "
        f"{agg.neutral} neutral), {tone} tone, led by {top.lower()}; "
        f"effective sample {agg.effective_n:.1f}."
    )
    return _clamp(agg.score), detail


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------

def action_for(score: float) -> str:
    if score >= 45:
        return "STRONG BUY"
    if score >= 20:
        return "BUY"
    if score > -20:
        return "HOLD"
    if score > -45:
        return "SELL"
    return "STRONG SELL"


def analyze(
    asset: Asset,
    series: Series,
    news_agg: NewsAggregate | None,
    ctx: MacroContext | None,
    *,
    account_size: float = 50_000.0,
    risk_pct: float = 1.0,
) -> Signal:
    ind = compute_indicators(series.bars)
    i = len(series.bars) - 1
    close = ind["close"][i]
    atr = ind["atr"][i] or (close * 0.02)

    trend, trend_detail = _trend(ind, i)
    momentum, mom_detail = _momentum(ind, i)
    meanrev, mr_detail = _meanrev(ind, i)
    if asset.fade_trend:
        trend = -trend
        momentum = -momentum
        trend_detail = "Fade mode (gas mean-reverts; extended trends are faded): " + trend_detail
        mom_detail = "Fade mode (gas mean-reverts; extended trends are faded): " + mom_detail
    news_s, news_detail = _news_factor(news_agg)
    macro_s, macro_detail = _macro_components(asset.key, ctx)

    raw = {
        "trend": (trend, trend_detail, "Trend (faded)" if asset.fade_trend else "Trend"),
        "momentum": (
            momentum, mom_detail, "Momentum (faded)" if asset.fade_trend else "Momentum"
        ),
        "meanrev": (meanrev, mr_detail, "Mean reversion"),
        "news": (news_s, news_detail, "News flow"),
        "macro": (macro_s, macro_detail, "Macro"),
    }

    weights = dict(asset.weights)
    for key in ("news", "macro"):
        if (key == "news" and news_agg is None) or (key == "macro" and ctx is None):
            weights.pop(key, None)
    total_w = sum(weights.values())
    weights = {k: v / total_w for k, v in weights.items()}

    factors: list[Factor] = []
    for key, (score, detail, label) in raw.items():
        if key not in weights:
            continue
        w = weights[key]
        factors.append(
            Factor(key=key, label=label, score=score, weight=w, points=100.0 * w * score,
                   detail=detail)
        )

    composite = sum(f.points for f in factors)
    composite = max(-100.0, min(100.0, composite))
    action = action_for(composite)

    # Agreement: how tightly the factors cluster around the composite direction.
    mean = sum(f.weight * f.score for f in factors)
    var = sum(f.weight * (f.score - mean) ** 2 for f in factors)
    agreement = max(0.0, 1.0 - math.sqrt(var) / 0.55)
    data_quality = min(1.0, len(series.bars) / 300.0) * (0.85 if series.stale else 1.0)
    news_cov = news_agg.coverage if news_agg else 0.5
    adx = ind["adx"][i] or 0.0
    trend_clarity = min(1.0, adx / 35.0)
    confidence = 100.0 * (
        0.40 * agreement + 0.20 * data_quality + 0.20 * news_cov + 0.20 * trend_clarity
    )
    confidence = max(5.0, min(95.0, confidence))
    conf_label = "High" if confidence >= 70 else "Medium" if confidence >= 45 else "Low"

    delta, delta_pct = daily_change(series)
    display_price = series.current_price if series.current_price is not None else close
    plan = _plan(asset, ind, i, composite, account_size, risk_pct)

    metrics = _metrics(ind, i, series, delta_pct)
    drivers = [
        f"{f.label} {f.points:+.0f} pts - {f.detail}"
        for f in sorted(factors, key=lambda f: abs(f.points), reverse=True)[:3]
    ]
    risks = _risks(asset, ind, i, composite, confidence, news_agg, series, delta_pct)

    return Signal(
        asset=asset,
        series=series,
        price=display_price,
        change=delta,
        change_pct=delta_pct,
        score=composite,
        action=action,
        confidence=confidence,
        confidence_label=conf_label,
        factors=factors,
        news=news_agg,
        plan=plan,
        drivers=drivers,
        risks=risks,
        metrics=metrics,
        stale=series.stale,
    )


def _plan(
    asset: Asset, ind: dict, i: int, score: float, account_size: float, risk_pct: float
) -> TradePlan:
    close = ind["close"][i]
    atr = ind["atr"][i] or close * 0.02
    highs, lows = ind["high"], ind["low"]
    swing_high = max(highs[max(0, i - 9) : i + 1])
    swing_low = min(lows[max(0, i - 9) : i + 1])

    trigger_buy = max(highs[max(0, i - 19) : i + 1]) + 0.10 * atr
    trigger_sell = min(lows[max(0, i - 19) : i + 1]) - 0.10 * atr

    if score >= 20:
        direction = "LONG"
        raw_stop = min(close - 1.6 * atr, swing_low - 0.25 * atr)
        stop = max(raw_stop, close - 2.2 * atr)
        stop = min(stop, close - 0.9 * atr)
        stop_dist = close - stop
        target = close + 4.0 * atr
        rr = (target - close) / stop_dist if stop_dist > 0 else None
    elif score <= -20:
        direction = "SHORT"
        raw_stop = max(close + 1.6 * atr, swing_high + 0.25 * atr)
        stop = min(raw_stop, close + 2.2 * atr)
        stop = max(stop, close + 0.9 * atr)
        stop_dist = stop - close
        target = close - 4.0 * atr
        rr = (close - target) / stop_dist if stop_dist > 0 else None
    else:
        return TradePlan(
            direction="FLAT", entry=None, stop=None, target=None, stop_distance=None,
            rr=None, risk_per_contract=None, contracts=0, notional_per_contract=None,
            min_account_one_lot=None, etf_notional=None, trigger_buy=trigger_buy,
            trigger_sell=trigger_sell, atr=atr,
        )

    risk_per_contract = stop_dist * asset.multiplier
    budget = max(0.0, account_size * max(0.01, risk_pct) / 100.0)
    contracts = int(budget // risk_per_contract) if risk_per_contract > 0 else 0
    notional = close * asset.multiplier
    min_account = risk_per_contract / (max(0.01, risk_pct) / 100.0)
    stop_pct = stop_dist / close if close else 0.0
    etf_notional = (budget / stop_pct) if stop_pct > 0 else None
    return TradePlan(
        direction=direction,
        entry=close,
        stop=stop,
        target=target,
        stop_distance=stop_dist,
        rr=rr,
        risk_per_contract=risk_per_contract,
        contracts=contracts,
        notional_per_contract=notional,
        min_account_one_lot=min_account,
        etf_notional=etf_notional,
        trigger_buy=trigger_buy,
        trigger_sell=trigger_sell,
        atr=atr,
    )


def _metrics(ind: dict, i: int, series: Series, delta_pct: float) -> dict[str, float | None]:
    closes = ind["close"]
    last = closes[i]
    window = closes[max(0, i - 251) : i + 1]
    hi52, lo52 = max(window), min(window)
    year = series.bars[i].date.year
    ytd_base = None
    for j in range(i, -1, -1):
        if series.bars[j].date.year < year:
            ytd_base = closes[j]
            break
    vols = [b.volume for b in series.bars]
    avg5 = sum(vols[-5:]) / 5 if len(vols) >= 5 else 0
    avg60 = sum(vols[-60:]) / 60 if len(vols) >= 60 else 0

    def ret(n: int) -> float | None:
        return last / closes[i - n] - 1.0 if i >= n else None

    return {
        "ema20": ind["ema20"][i],
        "ema50": ind["ema50"][i],
        "ema200": ind["ema200"][i],
        "rsi": ind["rsi"][i],
        "atr": ind["atr"][i],
        "atr_pct": (ind["atr"][i] / last * 100.0) if ind["atr"][i] else None,
        "adx": ind["adx"][i],
        "macd_hist": ind["macd_hist"][i],
        "bb_pos": (
            (last - ind["bb_lo"][i]) / (ind["bb_up"][i] - ind["bb_lo"][i]) * 100.0
            if ind["bb_up"][i] and ind["bb_lo"][i] and ind["bb_up"][i] > ind["bb_lo"][i]
            else None
        ),
        "vol20": ind["vol20"][i],
        "ret_1d": delta_pct / 100.0,
        "ret_5d": ret(5),
        "ret_20d": ret(20),
        "ret_60d": ret(60),
        "ret_ytd": (last / ytd_base - 1.0) if ytd_base else None,
        "high52": hi52,
        "low52": lo52,
        "range_pos": ((last - lo52) / (hi52 - lo52) * 100.0) if hi52 > lo52 else 50.0,
        "range_pos_pctile": percentile_rank(window, last) * 100.0,
        "volume_ratio": (avg5 / avg60) if avg60 else None,
    }


def _risks(
    asset: Asset,
    ind: dict,
    i: int,
    score: float,
    confidence: float,
    news: NewsAggregate | None,
    series: Series,
    change_pct: float,
) -> list[str]:
    out: list[str] = []
    rsi = ind["rsi"][i]
    atr = ind["atr"][i] or 0.0
    close = ind["close"][i]
    adx = ind["adx"][i] or 0.0
    atr_pct = atr / close * 100.0 if close else 0.0

    if abs(change_pct) >= 6.0:
        out.append(
            f"One-day move {change_pct:+.1f}%: a spike like this often needs a session or two "
            f"to consolidate before a signal is reliable."
        )

    if rsi is not None and rsi >= 72 and score > 0:
        out.append(f"Overbought: RSI {rsi:.0f}. Entering here pays up for momentum.")
    if rsi is not None and rsi <= 28 and score < 0:
        out.append(f"Oversold: RSI {rsi:.0f}. Shorting here risks a violent squeeze.")
    if atr_pct >= 3.0:
        out.append(f"Volatility is high ({atr_pct:.1f}% ATR/day); size down accordingly.")
    if adx < 18:
        out.append(f"Trendless tape (ADX {adx:.0f}); breakouts are prone to failure.")
    if confidence < 45:
        out.append("Low factor agreement - this is a weak-conviction signal.")
    if 15 <= abs(score) <= 25:
        out.append(f"Score {score:+.0f} sits near a decision threshold; a small move flips it.")
    window = ind["close"][max(0, i - 251) : i + 1]
    hi, lo = max(window), min(window)
    if hi > lo:
        pos = (close - lo) / (hi - lo) * 100.0
        if pos <= 12 and score < 0:
            out.append(f"Price is at {pos:.0f}% of its 52W range; mean-reversion bounce risk is elevated.")
        if pos >= 88 and score > 0:
            out.append(f"Price is at {pos:.0f}% of its 52W range; late-stage chase risk.")
    if series.stale:
        out.append("Data is from cache and may be stale.")
    if asset.key == "brent":
        out.append("Geopolitical premium can unwind fast on de-escalation; headlines dominate.")
    elif asset.key == "gas":
        out.append("Weather is the dominant driver and is only partly captured by news flow.")
    elif asset.key == "silver":
        out.append("Silver is high-beta to gold and industrial cycles; stops need room.")
    else:
        out.append("Gold reacts sharply to real yields and Fed repricing; event risk is two-sided.")
    if news and news.article_count >= 8:
        out.append("Heavy news volume raises the chance of a crowded, headline-driven move.")
    return out[:4]


def technical_score(asset: Asset, ind: dict, i: int) -> float:
    """Trend + momentum + mean-reversion only. Used by the backtester."""
    t, _ = _trend(ind, i)
    m, _ = _momentum(ind, i)
    r, _ = _meanrev(ind, i)
    if asset.fade_trend:
        t, m = -t, -m
    w = asset.weights
    denom = w["trend"] + w["momentum"] + w["meanrev"]
    return 100.0 * (w["trend"] * t + w["momentum"] * m + w["meanrev"] * r) / denom


def format_price(value: float | None, unit: str = "") -> str:
    if value is None:
        return "-"
    if value >= 1000:
        return f"{value:,.1f}"
    if value >= 10:
        return f"{value:,.2f}"
    return f"{value:,.3f}"


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"
