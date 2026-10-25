"""Terminal, JSON and Markdown renderers."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .pipeline import RunResult
from .render_html import render_html
from .signals import Signal


def fmt_price(value: float | None, unit: str = "") -> str:
    if value is None:
        return "-"
    if value >= 1000:
        return f"{value:,.1f}"
    if value >= 10:
        return f"{value:,.2f}"
    return f"{value:,.3f}"


def fmt_pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value * 100:+.{digits}f}%"


def fmt_money(value: float | None) -> str:
    if value is None:
        return "-"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:,.2f}M"
    if abs(value) >= 1_000:
        return f"${value:,.0f}"
    return f"${value:,.0f}"


def fmt_num(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}"


def _plan_line(s: Signal) -> str:
    p = s.plan
    if p.direction == "FLAT":
        return f"FLAT  buy>{fmt_price(p.trigger_buy)}  sell<{fmt_price(p.trigger_sell)}"
    return (
        f"{p.direction}  entry {fmt_price(p.entry)}  stop {fmt_price(p.stop)}  "
        f"target {fmt_price(p.target)}  R:R {p.rr:.2f}"
    )


def render_console(result: RunResult, color: bool = False) -> str:
    if color:
        reset, bold, dim = "\x1b[0m", "\x1b[1m", "\x1b[2m"
        green, red, amber = "\x1b[32m", "\x1b[31m", "\x1b[33m"
    else:
        reset = bold = dim = green = red = amber = ""

    def tone(action: str, text: str) -> str:
        if not color:
            return text
        if "BUY" in action:
            return f"{green}{text}{reset}"
        if "SELL" in action:
            return f"{red}{text}{reset}"
        return f"{amber}{text}{reset}"

    lines: list[str] = []
    lines.append("=" * 84)
    lines.append(f"{bold}COMMODITY SIGNAL DESK{reset}  |  {result.generated_at:%d %b %Y %H:%M %Z}")
    lines.append(
        f"Data through {result.as_of}  |  {result.news_count:,} headlines  |  "
        f"account ${result.account_size:,.0f} @ {result.risk_pct:.1f}% risk"
    )
    lines.append("=" * 84)
    lines.append(
        f"{'ASSET':14s}{'PRICE':>11s}{'1D':>8s}{'SCORE':>8s}  {'SIGNAL':12s}"
        f"{'CONF':>6s}  PLAN"
    )
    lines.append("-" * 84)
    for s in result.signals:
        lines.append(
            f"{s.asset.short:14s}{fmt_price(s.price):>11s}"
            f"{s.change_pct:>+7.2f}%{s.score:>+8.1f}  "
            f"{tone(s.action, s.action):12s}"
            f"{s.confidence:>5.0f}%  {_plan_line(s)}"
        )
    lines.append("-" * 84)

    buys = sum(1 for s in result.signals if "BUY" in s.action)
    sells = sum(1 for s in result.signals if "SELL" in s.action)
    holds = len(result.signals) - buys - sells
    avg = sum(s.score for s in result.signals) / len(result.signals)
    lines.append(f"Breadth: {buys} buy / {holds} hold / {sells} sell  |  average score {avg:+.1f}")
    vix = next((q for q in result.macro.quotes if q.key == "vix"), None)
    if vix:
        lines.append(f"Macro: VIX {vix.price:.1f} ({vix.change_20d:+.0f}% 20D)  |  "
                     f"DXY {fmt_num(next((q.price for q in result.macro.quotes if q.key == 'dxy'), None))}")
    lines.append("")
    for s in result.signals:
        lines.append(f"{tone(s.action, s.action):12s} {s.asset.short:7s}  {s.drivers[0]}")
    if result.warnings:
        lines.append("")
        lines.append("Data notes:")
        for w in result.warnings[:8]:
            lines.append(f"  ! {w}")
    lines.append("")
    lines.append(f"Reports: {result.out_dir / 'latest.html'}  |  {result.out_dir / 'latest.md'}")
    return "\n".join(lines)


def _signal_dict(s: Signal) -> dict:
    return {
        "key": s.asset.key,
        "name": s.asset.name,
        "symbol": s.asset.symbol,
        "etf": s.asset.etf,
        "unit": s.asset.unit,
        "price": round(s.price, 4),
        "change": round(s.change, 4),
        "change_pct": round(s.change_pct, 4),
        "score": round(s.score, 2),
        "action": s.action,
        "confidence": round(s.confidence, 1),
        "confidence_label": s.confidence_label,
        "stale": s.stale,
        "factors": [
            {
                "key": f.key,
                "label": f.label,
                "score": round(f.score, 3),
                "weight": round(f.weight, 3),
                "points": round(f.points, 2),
                "detail": f.detail,
            }
            for f in s.factors
        ],
        "plan": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(s.plan).items()},
        "metrics": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.metrics.items()},
        "drivers": s.drivers,
        "risks": s.risks,
        "news": None
        if s.news is None
        else {
            "score": round(s.news.score, 3),
            "article_count": s.news.article_count,
            "positive": s.news.positive,
            "negative": s.news.negative,
            "neutral": s.news.neutral,
            "effective_n": round(s.news.effective_n, 1),
            "coverage": round(s.news.coverage, 3),
            "topics": [{"topic": t, "weight": round(w, 2)} for t, w in s.news.topics],
            "headlines": [
                {
                    "title": a.title,
                    "source": a.source,
                    "url": a.url,
                    "published": a.published.isoformat(),
                    "sentiment": round(a.sentiment.get(s.asset.key, 0.0), 3),
                    "relevance": round(a.relevance.get(s.asset.key, 0.0), 2),
                    "topics": a.topics,
                }
                for a in s.news.headlines
            ],
        },
    }


def render_json(result: RunResult, live_seconds: int | None = None) -> str:
    payload = {
        "schema": "commodity-signal-desk/1.0",
        "generated_at": result.generated_at.isoformat(),
        "as_of": result.as_of,
        "live_refresh_seconds": live_seconds,
        "account_size": result.account_size,
        "risk_pct": result.risk_pct,
        "news_window_days": result.days,
        "news_count": result.news_count,
        "macro": {
            "quotes": [asdict(q) for q in result.macro.quotes],
            "values": {k: round(v, 6) for k, v in result.macro.values.items()},
            "brent_wti_spread": result.macro.brent_wti_spread,
        },
        "signals": [_signal_dict(s) for s in result.signals],
        "backtests": {k: asdict(v) for k, v in result.backtests.items()},
        "warnings": result.warnings,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def render_markdown(result: RunResult) -> str:
    out: list[str] = []
    out.append("# Commodity Signal Desk")
    out.append("")
    out.append(
        f"**{result.generated_at:%d %b %Y %H:%M %Z}** | data through "
        f"**{result.as_of}** | {result.news_count:,} headlines over {result.days:.0f} days"
    )
    out.append("")
    out.append("## Decision summary")
    out.append("")
    out.append("| Asset | Price | 1D | Score | Signal | Confidence | Trade plan |")
    out.append("|---|---:|---:|---:|---|---:|---|")
    for s in result.signals:
        p = s.plan
        if p.direction == "FLAT":
            plan = f"Flat; buy > {fmt_price(p.trigger_buy)}, sell < {fmt_price(p.trigger_sell)}"
        else:
            plan = (
                f"{p.direction} @ {fmt_price(p.entry)}; stop {fmt_price(p.stop)}; "
                f"target {fmt_price(p.target)}; R:R {p.rr:.2f}"
            )
        out.append(
            f"| **{s.asset.name}** ({s.asset.symbol}) | {fmt_price(s.price)} {s.asset.unit} | "
            f"{s.change_pct:+.2f}% | {s.score:+.0f} | **{s.action}** | "
            f"{s.confidence:.0f}% {s.confidence_label} | {plan} |"
        )
    out.append("")
    out.append("> Decision support only. Futures are leveraged; size positions to the stop.")
    out.append("")

    if result.macro.quotes:
        out.append("## Macro backdrop")
        out.append("")
        out.append("| Indicator | Level | 1D | 20D |")
        out.append("|---|---:|---:|---:|")
        for q in result.macro.quotes:
            unit = " bp" if q.change_kind == "bp" else "%"
            out.append(
                f"| {q.name} | {q.price:,.3f}{(' ' + q.unit) if q.unit else ''} | "
                f"{q.change_1d:+.1f}{unit} | {q.change_20d:+.1f}{unit} |"
            )
        out.append("")

    for s in result.signals:
        out.append(f"## {s.asset.name} - {s.action} ({s.score:+.0f}/100, {s.confidence_label} confidence)")
        out.append("")
        out.append(
            f"**{fmt_price(s.price)} {s.asset.unit}**, {s.change_pct:+.2f}% on the day. "
            f"RSI {fmt_num(s.metrics.get('rsi'), 0)}, ADX {fmt_num(s.metrics.get('adx'), 0)}, "
            f"ATR {fmt_num(s.metrics.get('atr_pct'), 1)}%/day."
        )
        out.append("")
        out.append("| Factor | Score | Weight | Points | Read |")
        out.append("|---|---:|---:|---:|---|")
        for f in s.factors:
            out.append(
                f"| {f.label} | {f.score:+.2f} | {f.weight * 100:.0f}% | {f.points:+.1f} | {f.detail} |"
            )
        out.append("")
        p = s.plan
        if p.direction != "FLAT":
            out.append("**Trade plan**")
            out.append("")
            out.append(
                f"- {p.direction} at {fmt_price(p.entry)}; stop {fmt_price(p.stop)} "
                f"({p.stop_distance / p.entry * 100:.1f}% risk); target {fmt_price(p.target)}; R:R {p.rr:.2f}"
            )
            out.append(
                f"- Risk per contract {fmt_money(p.risk_per_contract)}; "
                f"{p.contracts} contract(s) at ${result.account_size:,.0f} / {result.risk_pct:.1f}%; "
                f"minimum account for one lot {fmt_money(p.min_account_one_lot)}"
            )
            if p.etf_notional:
                out.append(f"- ETF proxy {s.asset.etf}: about {fmt_money(p.etf_notional)} notional for the same dollar risk")
            out.append("")
        else:
            out.append(
                f"**No trade.** Reconsider on a close above {fmt_price(p.trigger_buy)} "
                f"or below {fmt_price(p.trigger_sell)}."
            )
            out.append("")
        out.append("**Why**")
        out.append("")
        for d in s.drivers:
            out.append(f"- {d}")
        out.append("")
        out.append("**Watch out**")
        out.append("")
        for r in s.risks:
            out.append(f"- {r}")
        out.append("")
        if s.news and s.news.headlines:
            out.append(
                f"**News tape** - {s.news.article_count} stories "
                f"({s.news.positive} positive / {s.news.negative} negative / {s.news.neutral} neutral)"
            )
            out.append("")
            for a in s.news.headlines[:4]:
                out.append(f"- [{a.title}]({a.url}) - {a.source}, {a.published:%d %b %H:%M} UTC")
            out.append("")

    if result.backtests:
        out.append("## Signal validation (technical core, 10-day forward return)")
        out.append("")
        out.append("| Asset | Samples | Baseline up | Directional hit | Strong buy avg | Strong sell avg |")
        out.append("|---|---:|---:|---:|---:|---:|")
        for key, b in result.backtests.items():
            sb = next((x for x in b.buckets if x.action == "STRONG BUY"), None)
            ss = next((x for x in b.buckets if x.action == "STRONG SELL"), None)
            hit = f"{b.directional_hit * 100:.0f}%" if b.directional_hit is not None else "-"
            out.append(
                f"| {b.name} | {b.samples} | {b.baseline_up * 100:.0f}% | {hit} | "
                f"{fmt_pct(sb.avg) if sb and sb.n else '-'} | {fmt_pct(ss.avg) if ss and ss.n else '-'} |"
            )
        out.append("")
        out.append(
            "_Descriptive only: overlapping windows, no costs, no slippage. "
            "Not a tradable track record._"
        )
        out.append("")

    out.append("## Method")
    out.append("")
    out.append(
        "Composite of trend, momentum, mean reversion, news flow and macro, weighted per "
        "instrument. Scores run from -100 to +100; +/-20 defines a directional bias and "
        "+/-45 a strong one. Natural gas runs in fade mode because it mean-reverts. Stops are "
        "ATR-based (roughly 1.6-2.2 ATR) and targets 4 ATR. News is scored with an event rule "
        "set (OPEC+, Fed, dollar, inventories, weather, geopolitics) over a 96-hour window."
    )
    out.append("")
    out.append(
        "**Disclaimer:** This is decision support, not investment advice. Commodity futures "
        "are leveraged and can lose more than the margin posted. Verify prices with your broker "
        "before trading."
    )
    out.append("")
    return "\n".join(out)


def write_reports(
    result: RunResult,
    out_dir: Path,
    *,
    archive: bool = True,
    live_seconds: int | None = None,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = result.generated_at.strftime("%Y%m%d_%H%M")
    paths = {
        "html": out_dir / "latest.html",
        "json": out_dir / "latest.json",
        "md": out_dir / "latest.md",
    }
    if archive:
        paths["html_stamped"] = out_dir / f"commodity_{stamp}.html"
    html = render_html(result, refresh_seconds=live_seconds)
    paths["html"].write_text(html, encoding="utf-8")
    if archive:
        paths["html_stamped"].write_text(html, encoding="utf-8")
    paths["json"].write_text(render_json(result, live_seconds), encoding="utf-8")
    paths["md"].write_text(render_markdown(result), encoding="utf-8")
    return paths
