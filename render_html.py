"""Self-contained HTML dashboard. No external CSS, JS, fonts or images."""

from __future__ import annotations

import html
from datetime import datetime

from .indicators import compute_indicators
from .news import global_headlines
from .pipeline import RunResult
from .signals import Signal

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def e(value) -> str:
    return html.escape(str(value), quote=True)


def price(value: float | None) -> str:
    if value is None:
        return "-"
    if abs(value) >= 1000:
        return f"{value:,.1f}"
    if abs(value) >= 10:
        return f"{value:,.2f}"
    return f"{value:,.3f}"


def pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value * 100:+.{digits}f}%"


def money(value: float | None) -> str:
    if value is None:
        return "-"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:,.2f}M"
    if abs(value) >= 1_000:
        return f"${value:,.0f}"
    return f"${value:,.0f}"


def num(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}"


def tone(value: float | None, dead: float = 0.0001) -> str:
    if value is None:
        return "flat"
    if value > dead:
        return "pos"
    if value < -dead:
        return "neg"
    return "flat"


def action_class(action: str) -> str:
    return {
        "STRONG BUY": "sbuy",
        "BUY": "buy",
        "HOLD": "hold",
        "SELL": "sell",
        "STRONG SELL": "ssell",
    }.get(action, "hold")


def dir_class(direction: str) -> str:
    return {"LONG": "long", "SHORT": "short"}.get(direction, "flat")


def ago(when: datetime, now: datetime) -> str:
    seconds = max(0.0, (now - when).total_seconds())
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def axis_fmt(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value) >= 100:
        return f"{value:,.1f}"
    return f"{value:,.2f}"


# ---------------------------------------------------------------------------
# Inline chart
# ---------------------------------------------------------------------------

def chart_svg(signal: Signal) -> str:
    bars = signal.series.bars[-126:]
    if len(bars) < 2:
        return ""
    ind = compute_indicators(signal.series.bars)
    ema = ind["ema50"][-len(bars):]

    width, height = 560, 190
    left, right, top, bottom = 46, 10, 16, 24
    lows = [b.low for b in bars]
    highs = [b.high for b in bars]
    lo, hi = min(lows), max(highs)
    pad = (hi - lo) * 0.06 or max(hi * 0.01, 0.01)
    lo -= pad
    hi += pad
    span = hi - lo or 1.0

    def x(i: int) -> float:
        return left + i * (width - left - right) / (len(bars) - 1)

    def y(v: float) -> float:
        return top + (hi - v) / span * (height - top - bottom)

    points = [(x(i), y(b.close)) for i, b in enumerate(bars)]
    path = "M " + " L ".join(f"{px:.1f},{py:.1f}" for px, py in points)
    area = (
        path
        + f" L {points[-1][0]:.1f},{height - bottom:.1f}"
        + f" L {points[0][0]:.1f},{height - bottom:.1f} Z"
    )

    ema_points = [
        (x(i), y(v)) for i, v in enumerate(ema) if v is not None
    ]
    ema_path = (
        "M " + " L ".join(f"{px:.1f},{py:.1f}" for px, py in ema_points)
        if len(ema_points) > 1
        else ""
    )

    if signal.score >= 20:
        color = "#2ecc8f"
    elif signal.score <= -20:
        color = "#ff5c6c"
    else:
        color = "#e8b44a"

    grid = []
    for frac in (0.25, 0.5, 0.75):
        gy = top + (height - top - bottom) * frac
        grid.append(
            f'<line x1="{left}" y1="{gy:.1f}" x2="{width - right}" y2="{gy:.1f}" '
            f'stroke="#263043" stroke-width="1" stroke-dasharray="3 5"/>'
        )
    labels = []
    for frac in (0.0, 0.5, 1.0):
        value = hi - span * frac
        gy = top + (height - top - bottom) * frac
        labels.append(
            f'<text x="{left - 6}" y="{gy + 3:.1f}" text-anchor="end" '
            f'fill="#6f8098" font-size="9" font-family="Consolas, monospace">'
            f"{axis_fmt(value)}</text>"
        )

    xlabels = (
        f'<text x="{left}" y="{height - 7}" fill="#6f8098" font-size="9">'
        f"{bars[0].date:%d %b}</text>"
        f'<text x="{width - right}" y="{height - 7}" text-anchor="end" fill="#6f8098" '
        f'font-size="9">{bars[-1].date:%d %b}</text>'
    )
    last_x, last_y = points[-1]
    return f"""<svg class="chart" viewBox="0 0 {width} {height}" role="img"
     aria-label="Six month price chart for {e(signal.asset.name)}">
  <defs>
    <linearGradient id="fill-{e(signal.asset.key)}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{color}" stop-opacity="0.26"/>
      <stop offset="100%" stop-color="{color}" stop-opacity="0"/>
    </linearGradient>
  </defs>
  {''.join(grid)}
  {''.join(labels)}
  <path d="{area}" fill="url(#fill-{e(signal.asset.key)})"/>
  <path d="{path}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>
  {f'<path d="{ema_path}" fill="none" stroke="#5aa9ff" stroke-width="1.1" stroke-dasharray="4 4" opacity="0.8"/>' if ema_path else ''}
  <circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3.4" fill="{color}" stroke="#0a0d12" stroke-width="1.5"/>
  {xlabels}
  <text x="{left}" y="12" fill="#5aa9ff" font-size="9" opacity="0.9">50D EMA</text>
</svg>"""


# ---------------------------------------------------------------------------
# Card blocks
# ---------------------------------------------------------------------------

def metric(label: str, value: str, klass: str = "") -> str:
    return (
        f'<div class="metric"><span>{e(label)}</span>'
        f'<b class="{klass}">{e(value)}</b></div>'
    )


def factor_rows(signal: Signal) -> str:
    rows = []
    for f in signal.factors:
        width = min(50.0, abs(f.score) * 50.0)
        left = 50.0 if f.score >= 0 else 50.0 - width
        rows.append(
            f"""<div class="factor">
  <div class="f-head"><span>{e(f.label)}</span>
    <span class="f-pts {tone(f.points)}">{f.points:+.1f}</span></div>
  <div class="f-track"><span class="f-zero"></span>
    <span class="f-fill {tone(f.score)}" style="left:{left:.1f}%;width:{width:.1f}%"></span></div>
  <p class="f-detail">{e(f.detail)}</p>
</div>"""
        )
    return "".join(rows)


def plan_block(signal: Signal, result: RunResult) -> str:
    p = signal.plan
    budget = result.account_size * result.risk_pct / 100.0
    if p.direction == "FLAT":
        return f"""<div class="plan flat">
  <div class="plan-head"><h3>Trade plan</h3><span class="dir flat">NO POSITION</span></div>
  <div class="plan-grid">
    <div><span>Buy trigger</span><b>{price(p.trigger_buy)}</b></div>
    <div><span>Sell trigger</span><b>{price(p.trigger_sell)}</b></div>
    <div><span>ATR (14D)</span><b>{price(p.atr)}</b></div>
    <div><span>Budget</span><b>{money(budget)}</b></div>
  </div>
  <p class="plan-note">The score sits in the neutral band. Wait for a close beyond one of
  the triggers, or for the factor mix to align.</p>
</div>"""

    stop_pct = (p.stop_distance or 0.0) / p.entry * 100.0 if p.entry else 0.0
    target_pct = (abs((p.target or 0.0) - (p.entry or 0.0)) / p.entry * 100.0) if p.entry else 0.0
    if p.contracts > 0:
        sizing = (
            f"{p.contracts} contract{'s' if p.contracts != 1 else ''} fit the "
            f"{money(budget)} risk budget."
        )
    else:
        sizing = (
            f"One contract risks {money(p.risk_per_contract)} - more than the {money(budget)} "
            f"budget. Use {signal.asset.etf} or a micro contract."
        )
    etf = (
        f'<div><span>ETF proxy</span><b>{e(signal.asset.etf)} ~{money(p.etf_notional)}</b></div>'
        if p.etf_notional
        else ""
    )
    return f"""<div class="plan">
  <div class="plan-head"><h3>Trade plan</h3>
    <span class="dir {dir_class(p.direction)}">{e(p.direction)}</span></div>
  <div class="plan-grid">
    <div><span>Entry</span><b>{price(p.entry)}</b></div>
    <div><span>Stop</span><b>{price(p.stop)} <em class="neg">-{stop_pct:.1f}%</em></b></div>
    <div><span>Target</span><b>{price(p.target)} <em class="pos">+{target_pct:.1f}%</em></b></div>
    <div><span>Reward : risk</span><b>{p.rr:.2f} : 1</b></div>
    <div><span>Risk / contract</span><b>{money(p.risk_per_contract)}</b></div>
    <div><span>Min account, 1 lot</span><b>{money(p.min_account_one_lot)}</b></div>
    {etf}
    <div><span>Notional / contract</span><b>{money(p.notional_per_contract)}</b></div>
  </div>
  <p class="plan-note">{sizing} Stops are ATR-based; targets assume a 4x ATR trend move.</p>
</div>"""


def news_block(signal: Signal, result: RunResult) -> str:
    if signal.news is None:
        return """<details class="news"><summary><span>News tape</span>
  <span class="muted">disabled for this run</span><span class="chev">+</span></summary></details>"""
    n = signal.news
    if not n.headlines:
        return f"""<details class="news"><summary><span>News tape</span>
  <span class="muted">no material stories</span><span class="chev">+</span></summary></details>"""

    items = []
    for a in n.headlines[:5]:
        s = a.sentiment.get(signal.asset.key, 0.0)
        items.append(
            f"""<li>
  <span class="dot {tone(s, 0.10)}"></span>
  <div class="news-body"><a href="{e(a.url)}" target="_blank" rel="noopener">{e(a.title)}</a>
  <span class="news-meta">{e(a.source)} &middot; {ago(a.published, result.generated_at)}</span></div>
  <span class="tone-val {tone(s, 0.10)}">{s:+.2f}</span>
</li>"""
        )
    return f"""<details class="news">
  <summary><span>News tape</span>
    <span class="muted">{n.article_count} stories &middot; {n.positive} + / {n.negative} - / {n.neutral} neutral</span>
    <span class="chev">+</span></summary>
  <ul class="newslist">{''.join(items)}</ul>
</details>"""


def asset_card(signal: Signal, result: RunResult) -> str:
    m = signal.metrics
    d = signal.change_pct / 100.0
    stats = "".join(
        [
            metric("1D", pct(d), tone(d)),
            metric("1W", pct(m.get("ret_5d")), tone(m.get("ret_5d"))),
            metric("1M", pct(m.get("ret_20d")), tone(m.get("ret_20d"))),
            metric("YTD", pct(m.get("ret_ytd")), tone(m.get("ret_ytd"))),
            metric("RSI (14)", num(m.get("rsi"), 0)),
            metric("ADX (14)", num(m.get("adx"), 0)),
            metric("ATR / day", f"{num(m.get('atr_pct'), 1)}%"),
            metric("52W range", f"{num(m.get('range_pos'), 0)}%"),
        ]
    )
    score_left = 50.0 if signal.score >= 0 else 50.0 + signal.score / 2.0
    score_width = abs(signal.score) / 2.0
    drivers = "".join(f"<li>{e(x)}</li>" for x in signal.drivers)
    risks = "".join(f"<li>{e(x)}</li>" for x in signal.risks)
    stale = (
        '<span class="stale">STALE DATA</span>' if signal.stale else ""
    )
    return f"""<article class="card {action_class(signal.action)}">
  <div class="card-top">
    <div class="asset-id">
      <span class="sym">{e(signal.asset.short)}</span>
      <h2>{e(signal.asset.name)}</h2>
      <span class="feed">{e(signal.asset.symbol)} &middot; front-month future &middot; {e(signal.asset.unit)}</span>
    </div>
    <div class="quote">
      <span class="price">{price(signal.price)}</span>
      <span class="delta {tone(d)}">{pct(d)}</span>
      {stale}
    </div>
  </div>

  <div class="signal-line">
    <span class="badge {action_class(signal.action)}">{e(signal.action)}</span>
    <div class="score-block">
      <div class="score-head"><span>Composite score</span><strong class="{tone(signal.score)}">{signal.score:+.0f}</strong></div>
      <div class="gauge">
        <span class="gauge-zero"></span>
        <span class="gauge-fill {tone(signal.score)}" style="left:{score_left:.1f}%;width:{score_width:.1f}%"></span>
        <span class="gauge-pin" style="left:{(signal.score + 100) / 2:.1f}%"></span>
      </div>
      <div class="gauge-scale"><span>-100</span><span>0</span><span>+100</span></div>
    </div>
    <div class="confidence">
      <strong>{signal.confidence:.0f}%</strong>
      <span>{e(signal.confidence_label)} confidence</span>
    </div>
  </div>

  {chart_svg(signal)}

  <div class="metrics">{stats}</div>

  <div class="factors">{factor_rows(signal)}</div>

  {plan_block(signal, result)}

  <div class="two-col">
    <div class="why"><h3>Why</h3><ul>{drivers}</ul></div>
    <div class="watch"><h3>Watch out</h3><ul>{risks}</ul></div>
  </div>

  {news_block(signal, result)}
</article>"""


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

CSS = """
:root{
  --bg:#080b11; --bg2:#0b1018; --panel:#101620; --panel2:#0c121b;
  --line:#202a3a; --line2:#2b384c;
  --text:#e8eef6; --muted:#8b9bb0; --dim:#5f7088;
  --green:#2ecc8f; --green2:#0f3a2d; --red:#ff5c6c; --red2:#3a1520;
  --amber:#e8b44a; --amber2:#3a2c12; --blue:#5aa9ff; --silver:#b8c4d6;
  --mono:"Cascadia Mono","SFMono-Regular","SF Mono",Consolas,"Liberation Mono",monospace;
  --sans:"Segoe UI",system-ui,-apple-system,"Helvetica Neue",Arial,sans-serif;
}
*{box-sizing:border-box}
html{background:var(--bg)}
body{
  margin:0; color:var(--text); font-family:var(--sans); line-height:1.45;
  background:
    radial-gradient(900px 500px at 15% -10%, rgba(90,169,255,.10), transparent 65%),
    radial-gradient(900px 500px at 95% 0%, rgba(232,180,74,.08), transparent 60%),
    var(--bg);
}
a{color:var(--blue);text-decoration:none}
a:hover{text-decoration:underline}
.page{max-width:1440px;margin:0 auto;padding:28px 26px 60px}

/* Masthead */
.masthead{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;
  padding-bottom:20px;border-bottom:1px solid var(--line);margin-bottom:18px}
.brand{display:flex;align-items:center;gap:16px;min-width:0}
.brand>div{min-width:0}
.mark{width:52px;height:52px;border:1px solid var(--line2);border-radius:12px;
  display:grid;place-items:center;font-family:var(--mono);font-size:15px;font-weight:700;
  letter-spacing:.5px;color:var(--amber);
  background:linear-gradient(145deg,rgba(232,180,74,.14),rgba(232,180,74,.02));
  box-shadow:inset 0 0 24px rgba(232,180,74,.06)}
h1{font-size:26px;line-height:1.1;margin:0;letter-spacing:-.4px}
.sub{color:var(--muted);font-size:12.5px;margin:5px 0 0}
.meta{text-align:right;font-family:var(--mono);font-size:11px;color:var(--muted);
  display:flex;flex-direction:column;gap:7px;align-items:flex-end}
.pill{display:inline-block;border:1px solid var(--line2);border-radius:999px;
  padding:4px 10px;font-size:10px;letter-spacing:.6px;text-transform:uppercase;color:var(--muted)}
.pill.live{color:var(--green);border-color:rgba(46,204,143,.35);background:rgba(46,204,143,.07)}
.pill.live:before{content:"";display:inline-block;width:6px;height:6px;border-radius:50%;
  background:var(--green);margin-right:6px;vertical-align:1px}
.pill.auto{color:var(--amber);border-color:rgba(232,180,74,.35);background:rgba(232,180,74,.07)}
.pill.auto:before{content:"";display:inline-block;width:6px;height:6px;border-radius:50%;
  background:var(--amber);margin-right:6px;vertical-align:1px}

/* Macro strip */
.ticker{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:8px;margin:18px 0}
.tick{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:9px 11px}
.tick .t-name{font-size:9.5px;letter-spacing:.9px;text-transform:uppercase;color:var(--dim);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tick .t-row{display:flex;align-items:baseline;justify-content:space-between;gap:6px;margin-top:3px}
.tick b{font-family:var(--mono);font-size:14px;font-weight:600}
.tick em{font-style:normal;font-family:var(--mono);font-size:10.5px}
.tick small{display:block;color:var(--dim);font-size:9px;margin-top:1px}

/* Brief */
.brief{display:grid;grid-template-columns:1.4fr 1fr 1fr;gap:10px;margin:16px 0 22px}
.brief-box{background:linear-gradient(180deg,var(--panel),var(--panel2));
  border:1px solid var(--line);border-radius:12px;padding:13px 15px}
.kicker{display:block;font-size:9.5px;letter-spacing:1.1px;text-transform:uppercase;color:var(--dim);margin-bottom:5px}
.brief-box strong{font-size:15px;font-weight:600;display:block}
.brief-box p{margin:4px 0 0;color:var(--muted);font-size:11.5px}

/* Grid + cards */
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(500px,1fr));gap:16px;align-items:start}
.card{position:relative;background:linear-gradient(180deg,var(--panel),var(--panel2));
  border:1px solid var(--line);border-radius:16px;padding:18px;
  box-shadow:0 18px 50px rgba(0,0,0,.28);overflow:hidden}
.card:before{content:"";position:absolute;left:0;right:0;top:0;height:3px;background:var(--amber)}
.card.buy:before,.card.sbuy:before{background:var(--green)}
.card.sell:before,.card.ssell:before{background:var(--red)}
.card.sbuy{box-shadow:0 18px 50px rgba(46,204,143,.08)}
.card.ssell{box-shadow:0 18px 50px rgba(255,92,108,.08)}
.card.summary:before{background:var(--blue)}
.card.summary .quote .price{font-size:24px;letter-spacing:-.5px}

.card-top{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}
.sym{font-family:var(--mono);font-size:10px;letter-spacing:1.6px;color:var(--amber)}
.card.buy .sym,.card.sbuy .sym{color:var(--green)}
.card.sell .sym,.card.ssell .sym{color:var(--red)}
.asset-id h2{font-size:20px;margin:2px 0 2px;letter-spacing:-.3px}
.feed{font-size:10.5px;color:var(--dim);font-family:var(--mono)}
.quote{text-align:right;display:flex;flex-direction:column;align-items:flex-end;gap:2px}
.quote .price{font-family:var(--mono);font-size:30px;font-weight:600;letter-spacing:-1px;line-height:1}
.quote .delta{font-family:var(--mono);font-size:12.5px}
.stale{font-size:8.5px;letter-spacing:.8px;color:var(--amber);border:1px solid var(--amber2);
  padding:1px 5px;border-radius:4px;margin-top:3px}

.signal-line{display:grid;grid-template-columns:auto 1fr auto;gap:14px;align-items:center;
  margin:16px 0 6px;padding:12px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.badge{font-size:11px;font-weight:700;letter-spacing:1px;padding:7px 11px;border-radius:7px;
  border:1px solid var(--line2);white-space:nowrap}
.badge.sbuy{background:var(--green);color:#04140e;border-color:var(--green)}
.badge.buy{background:rgba(46,204,143,.12);color:var(--green);border-color:rgba(46,204,143,.4)}
.badge.hold{background:rgba(232,180,74,.10);color:var(--amber);border-color:rgba(232,180,74,.35)}
.badge.sell{background:rgba(255,92,108,.12);color:var(--red);border-color:rgba(255,92,108,.4)}
.badge.ssell{background:var(--red);color:#1a0508;border-color:var(--red)}
.score-head{display:flex;justify-content:space-between;font-size:10px;color:var(--dim);
  text-transform:uppercase;letter-spacing:.8px;margin-bottom:6px}
.score-head strong{font-family:var(--mono);font-size:12px;letter-spacing:0}
.gauge{position:relative;height:8px;border-radius:999px;background:#0a0f17;border:1px solid #1a2433}
.gauge-fill{position:absolute;top:0;bottom:0;border-radius:999px;opacity:.85}
.gauge-fill.pos{background:linear-gradient(90deg,rgba(46,204,143,.35),var(--green))}
.gauge-fill.neg{background:linear-gradient(90deg,var(--red),rgba(255,92,108,.35))}
.gauge-fill.flat{background:var(--amber)}
.gauge-zero{position:absolute;left:50%;top:-3px;bottom:-3px;width:1px;background:#4a5a72}
.gauge-pin{position:absolute;top:50%;width:2px;height:16px;background:var(--text);
  transform:translate(-1px,-50%);box-shadow:0 0 6px rgba(255,255,255,.5)}
.gauge-scale{display:flex;justify-content:space-between;font-size:8.5px;color:var(--dim);
  font-family:var(--mono);margin-top:4px}
.confidence{text-align:right}
.confidence strong{display:block;font-family:var(--mono);font-size:18px}
.confidence span{font-size:9.5px;color:var(--dim);text-transform:uppercase;letter-spacing:.5px}

.chart{width:100%;height:auto;display:block;margin:7px 0 2px}

.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);
  border:1px solid var(--line);border-radius:10px;overflow:hidden;margin:10px 0 14px}
.metric{background:var(--panel2);padding:7px 9px}
.metric span{display:block;font-size:8.5px;letter-spacing:.7px;text-transform:uppercase;color:var(--dim)}
.metric b{font-family:var(--mono);font-size:12px;font-weight:500}
.pos{color:var(--green)} .neg{color:var(--red)} .flat{color:var(--muted)}

.factors{display:flex;flex-direction:column;gap:7px;padding:11px 0 4px;border-top:1px solid var(--line)}
.factor{display:grid;grid-template-columns:1fr;gap:3px}
.f-head{display:flex;justify-content:space-between;font-size:11px;color:var(--text)}
.f-pts{font-family:var(--mono);font-size:11px}
.f-track{position:relative;height:4px;background:#0a0f17;border-radius:999px}
.f-zero{position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:#37455a}
.f-fill{position:absolute;top:0;bottom:0;border-radius:999px;opacity:.85}
.f-fill.pos{background:var(--green)} .f-fill.neg{background:var(--red)} .f-fill.flat{background:var(--amber)}
.f-detail{margin:0;color:var(--muted);font-size:10px;line-height:1.35}

.plan{border:1px solid var(--line);background:rgba(90,169,255,.035);border-radius:12px;
  padding:12px 13px;margin:14px 0}
.plan-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:9px}
.plan h3,.two-col h3{margin:0;font-size:10px;letter-spacing:1.1px;text-transform:uppercase;color:var(--dim)}
.dir{font-family:var(--mono);font-size:10.5px;letter-spacing:1px;padding:3px 8px;border-radius:5px;border:1px solid}
.dir.long{color:var(--green);border-color:rgba(46,204,143,.4);background:rgba(46,204,143,.08)}
.dir.short{color:var(--red);border-color:rgba(255,92,108,.4);background:rgba(255,92,108,.08)}
.dir.flat{color:var(--amber);border-color:rgba(232,180,74,.35);background:rgba(232,180,74,.08)}
.plan-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:9px 12px}
.plan-grid span{display:block;font-size:8.5px;text-transform:uppercase;letter-spacing:.6px;color:var(--dim)}
.plan-grid b{font-family:var(--mono);font-size:11.5px;font-weight:500}
.plan-grid em{font-style:normal;font-size:9.5px;display:block}
.plan-note{margin:9px 0 0;color:var(--muted);font-size:10px;line-height:1.4}

.two-col{display:grid;grid-template-columns:1.15fr 1fr;gap:14px;margin:12px 0 12px}
.two-col ul{margin:6px 0 0;padding-left:13px;color:var(--muted);font-size:10.5px;line-height:1.4}
.two-col li{margin-bottom:4px}
.why li::marker{color:var(--green)}
.watch li::marker{color:var(--amber)}

.summary-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:16px 0 4px}
.summary-grid div{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:9px 10px}
.summary-grid span{display:block;font-size:8.5px;text-transform:uppercase;letter-spacing:.7px;color:var(--dim)}
.summary-grid b{font-family:var(--mono);font-size:12px;font-weight:500}
.summary-read,.summary-levels{border-top:1px solid var(--line);margin-top:12px;padding-top:10px}
.summary-read h3,.summary-levels h3{margin:0 0 6px;font-size:10px;letter-spacing:1.1px;
  text-transform:uppercase;color:var(--dim)}
.summary-read p{margin:0;color:var(--muted);font-size:11px;line-height:1.5}
.summary-levels ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:5px}
.summary-levels li{display:flex;justify-content:space-between;gap:10px;font-size:10.5px;
  color:var(--muted);font-family:var(--mono)}
.summary-levels li b{color:var(--text);font-weight:500}

.news{border-top:1px solid var(--line);padding-top:9px;margin-top:3px}
.news summary{display:flex;align-items:center;gap:8px;cursor:pointer;list-style:none;
  font-size:10.5px;color:var(--text);text-transform:uppercase;letter-spacing:.7px}
.news summary::-webkit-details-marker{display:none}
.news .muted{color:#7f90a8;text-transform:none;letter-spacing:0;font-size:10px;margin-left:auto}
.chev{color:var(--dim);font-family:var(--mono);font-size:13px;transition:transform .15s}
.news[open] .chev{transform:rotate(45deg)}
.newslist{list-style:none;margin:9px 0 0;padding:0;display:flex;flex-direction:column;gap:7px}
.newslist li{display:grid;grid-template-columns:7px 1fr auto;gap:8px;align-items:start}
.dot{width:6px;height:6px;border-radius:50%;margin-top:5px;background:var(--dim)}
.dot.pos{background:var(--green)} .dot.neg{background:var(--red)}
.news-body a{font-size:10.5px;line-height:1.35;color:#cdd8e6;display:block}
.news-meta{display:block;color:var(--dim);font-size:9px;margin-top:1px}
.tone-val{font-family:var(--mono);font-size:9.5px;padding-top:2px}

/* Secondary sections */
.section{margin-top:30px;padding-top:22px;border-top:1px solid var(--line)}
.section-head{display:flex;justify-content:space-between;align-items:baseline;gap:16px;margin-bottom:12px}
.section h2{font-size:13px;letter-spacing:1.1px;text-transform:uppercase;margin:0}
.section .note{color:var(--dim);font-size:10.5px}
.news-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:8px}
.news-card{display:block;background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:11px 13px}
.news-card a{font-size:12px;color:var(--text);line-height:1.35;display:block}
.news-card:hover{border-color:var(--line2)}
.news-card span{display:block;color:var(--dim);font-size:9.5px;margin-top:4px;font-family:var(--mono)}

table{width:100%;border-collapse:collapse;font-size:11px}
th{text-align:right;color:var(--dim);font-size:9px;text-transform:uppercase;letter-spacing:.7px;
  font-weight:500;padding:7px 8px;border-bottom:1px solid var(--line2)}
th:first-child,td:first-child{text-align:left}
td{padding:9px 8px;border-bottom:1px solid var(--line);font-family:var(--mono);font-size:11px}
tr:last-child td{border-bottom:none}
.hit{display:flex;align-items:center;gap:8px}
.hitbar{width:54px;height:4px;border-radius:999px;background:#0a0f17;overflow:hidden}
.hitbar span{display:block;height:100%;background:var(--green)}
.hitbar span.mid{background:var(--amber)} .hitbar span.low{background:var(--red)}

.method{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
.method div{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:13px 15px}
.method h3{margin:0 0 6px;font-size:10px;text-transform:uppercase;letter-spacing:.9px;color:var(--amber)}
.method p{margin:0;color:var(--muted);font-size:11px;line-height:1.5}
.method code{font-family:var(--mono);font-size:10.5px;color:var(--silver)}

.disclaimer{margin-top:26px;border:1px solid rgba(232,180,74,.28);background:rgba(232,180,74,.05);
  border-radius:10px;padding:14px 16px;color:#c7b58e;font-size:10.5px;line-height:1.55}
.disclaimer b{color:var(--amber)}
.footer{margin-top:18px;color:var(--dim);font-size:9.5px;display:flex;justify-content:space-between;gap:12px;
  font-family:var(--mono)}

@media(max-width:1100px){
  .brief{grid-template-columns:1fr 1fr}
  .grid{grid-template-columns:1fr}
}
@media(max-width:700px){
  .page{padding:18px 14px 40px; overflow-x:hidden}
  .masthead{flex-direction:column;gap:12px}
  .brand{align-items:flex-start;min-width:0}
  .brand>div{min-width:0}
  h1{font-size:22px}
  .sub{font-size:11px;overflow-wrap:anywhere}
  .meta{text-align:left;align-items:flex-start;flex-wrap:wrap;max-width:100%}
  .meta span{overflow-wrap:anywhere}
  .ticker{grid-template-columns:repeat(2,1fr)}
  .brief{grid-template-columns:1fr}
  .brief-box{min-width:0}
  .brief-box strong,.brief-box p{overflow-wrap:anywhere}
  .card{padding:14px;border-radius:13px;min-width:0}
  .quote .price{font-size:24px}
  .signal-line{grid-template-columns:1fr auto;gap:10px}
  .badge{justify-self:start}
  .score-block{grid-column:1/-1;grid-row:2}
  .confidence{grid-column:2;grid-row:1;text-align:right}
  .plan-grid{grid-template-columns:repeat(2,1fr)}
  .plan-head{flex-wrap:wrap;gap:6px}
  .two-col{grid-template-columns:1fr}
  .metrics{grid-template-columns:repeat(2,1fr)}
  .card-top{flex-direction:column;gap:8px}
  .quote{text-align:left;align-items:flex-start}
  .news summary{flex-wrap:wrap}
  .news .muted{margin-left:0;width:100%}
  .f-detail,.two-col ul,.news-body a{font-size:11px}
  .section-head{flex-direction:column;gap:4px}
}
@media print{
  body{background:#fff;color:#111}
  .page{max-width:none;padding:0}
  .card,.tick,.brief-box,.plan,.method div,.news-card{background:#fff;border-color:#ccc;box-shadow:none}
  .card{break-inside:avoid}
  .muted,.f-detail,.two-col ul,.plan-note{color:#444}
  a{color:#111}
}
"""


def macro_strip(result: RunResult) -> str:
    items = []
    for q in result.macro.quotes:
        unit = " bp" if q.change_kind == "bp" else "%"
        items.append(
            f"""<div class="tick">
  <span class="t-name">{e(q.name)}</span>
  <div class="t-row"><b>{num(q.price, 2)}</b>
    <em class="{tone(q.change_20d)}">{q.change_20d:+.1f}{unit}</em></div>
  <small>20-day change</small>
</div>"""
        )
    spread = result.macro.brent_wti_spread
    if spread is not None:
        items.append(
            f"""<div class="tick">
  <span class="t-name">Brent - WTI</span>
  <div class="t-row"><b>{num(spread, 2)}</b><em class="flat">USD</em></div>
  <small>Crude quality spread</small>
</div>"""
        )
    return f'<section class="ticker" aria-label="Macro dashboard">{"".join(items)}</section>'


def validation_table(result: RunResult) -> str:
    if not result.backtests:
        return ""
    rows = []
    for b in result.backtests.values():
        hit = b.directional_hit * 100 if b.directional_hit is not None else 0
        cls = "" if hit >= 55 else "mid" if hit >= 50 else "low"
        sb = next((x for x in b.buckets if x.action == "STRONG BUY"), None)
        ss = next((x for x in b.buckets if x.action == "STRONG SELL"), None)
        rows.append(
            f"""<tr>
  <td>{e(b.name)}</td>
  <td>{b.samples}</td>
  <td>{b.baseline_up * 100:.0f}%</td>
  <td><div class="hit"><div class="hitbar"><span class="{cls}" style="width:{min(100, hit):.0f}%"></span></div>{hit:.0f}%</div></td>
  <td class="{tone(sb.avg) if sb and sb.n else 'flat'}">{pct(sb.avg) if sb and sb.n else "-"}</td>
  <td class="{tone(-ss.avg) if ss and ss.n else 'flat'}">{pct(ss.avg) if ss and ss.n else "-"}</td>
</tr>"""
        )
    return f"""<section class="section" id="validation">
  <div class="section-head"><h2>Signal validation</h2>
    <span class="note">10-day forward returns &middot; technical core only &middot; 2 years</span></div>
  <table>
    <thead><tr><th>Asset</th><th>Samples</th><th>Baseline up</th><th>Directional hit</th>
      <th>Strong buy avg</th><th>Strong sell avg</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  <p class="note" style="margin:10px 0 0">Descriptive, not a track record: overlapping windows,
  no costs, no slippage, no position sizing. A 52% hit rate is close to noise; treat the
  backtest as a sanity check rather than proof of edge. Colour shows whether the bucket worked:
  green is a positive forward return for buys and a negative one for sells.</p>
</section>"""


def global_news(result: RunResult) -> str:
    items = global_headlines(result.articles, limit=8)
    if not items:
        return ""
    cards = []
    for a in items:
        cards.append(
            f"""<div class="news-card">
  <a href="{e(a.url)}" target="_blank" rel="noopener">{e(a.title)}</a>
  <span>{e(a.source)} &middot; {ago(a.published, result.generated_at)} &middot; {e(', '.join(a.topics[:2]) or 'markets')}</span>
</div>"""
        )
    return f"""<section class="section">
  <div class="section-head"><h2>Geopolitics &amp; policy tape</h2>
    <span class="note">Headlines that move more than one commodity</span></div>
  <div class="news-grid">{''.join(cards)}</div>
</section>"""


def methodology() -> str:
    return """<section class="section" id="method">
  <div class="section-head"><h2>How the model works</h2>
    <span class="note">Transparent, rule-based, no black box</span></div>
  <div class="method">
    <div><h3>Five factors</h3><p>Trend (EMA 20/50/200, ADX), momentum (RSI, MACD, 20-day
    return, 52-week position), mean reversion (Bollinger, distance from EMA, RSI extremes),
    news flow (event rules over 96 hours) and macro (dollar, yields, VIX, S&amp;P, copper).
    Each factor is squashed to <code>[-1, +1]</code>.</p></div>
    <div><h3>Per-instrument weights</h3><p>Brent leans on trend, momentum and geopolitics.
    Gold and silver weight macro heavily. Natural gas runs in <b>fade mode</b>: its trend and
    momentum readings are inverted because gas mean-reverts, and news/weather carry more
    weight.</p></div>
    <div><h3>Decision rule</h3><p>Weighted score in <code>[-100, +100]</code>.
    <code>&ge; +45</code> strong buy, <code>&ge; +20</code> buy, between <code>-20</code> and
    <code>+20</code> hold, <code>&le; -20</code> sell, <code>&le; -45</code> strong sell.
    Confidence blends factor agreement, data quality, news coverage and trend clarity.</p></div>
    <div><h3>Trade construction</h3><p>Stop at roughly 1.6x ATR (capped at 2.2x, widened to
    the 10-day swing only when it is close). Target at 4x ATR, giving about 1.8:1 to 2.2:1.
    Position size is derived from a fixed fraction of account equity, not from conviction.</p></div>
    <div><h3>Data</h3><p>Daily OHLCV from Yahoo Finance continuous front-month futures.
    Headlines from 13 curated RSS feeds plus 9 Google News searches. The last daily bar can be
    an intraday snapshot; prices are indicative, not executable.</p></div>
    <div><h3>Known limits</h3><p>No weather model, no storage balance, no CFTC positioning,
    no options skew, no intraday microstructure. News scoring is lexical and rule-based, so
    irony and novel events can be misread. Geopolitical premium can vanish in one session.</p></div>
  </div>
</section>"""


def overview_card(result: RunResult) -> str:
    signals = result.signals
    buys = sum(1 for s in signals if "BUY" in s.action)
    sells = sum(1 for s in signals if "SELL" in s.action)
    holds = len(signals) - buys - sells
    avg_score = sum(s.score for s in signals) / len(signals) if signals else 0.0
    avg_conf = sum(s.confidence for s in signals) / len(signals) if signals else 0.0
    spread = result.macro.brent_wti_spread

    oil = [s for s in signals if s.asset.key in ("brent", "wti")]
    metals = [s for s in signals if s.asset.key in ("gold", "silver")]
    gas = next((s for s in signals if s.asset.key == "gas"), None)

    read: list[str] = []
    if oil and all("BUY" in s.action for s in oil):
        read.append("The crude complex is the long side.")
    elif oil and all("SELL" in s.action for s in oil):
        read.append("The crude complex is the short side.")
    if len(oil) == 2:
        brent = next((s for s in oil if s.asset.key == "brent"), oil[0])
        wti = next((s for s in oil if s.asset.key == "wti"), oil[1])
        rel = wti.change_pct - brent.change_pct
        read.append(
            f"WTI is {'outperforming' if rel >= 0 else 'lagging'} Brent by "
            f"{abs(rel):.1f} points today."
        )
    if len(metals) == 2 and all("SELL" in s.action for s in metals):
        read.append("Both precious metals are shorts; oversold squeezes are the main risk.")
    if gas is not None:
        read.append(
            f"Gas is flat after a {gas.change_pct:+.1f}% session; it needs a close beyond "
            f"{price(gas.plan.trigger_buy)} or {price(gas.plan.trigger_sell)}."
        )
    if not read:
        read.append("No clear cross-asset alignment; treat each signal on its own.")

    levels = []
    for signal in signals:
        p = signal.plan
        if p.direction == "FLAT":
            level = f"buy > {price(p.trigger_buy)} / sell < {price(p.trigger_sell)}"
        else:
            level = f"stop {price(p.stop)} / target {price(p.target)}"
        levels.append(
            f'<li><b>{e(signal.asset.short)}</b><span>{e(signal.action)} &middot; '
            f"{e(level)}</span></li>"
        )

    return f"""<article class="card summary">
  <div class="card-top">
    <div class="asset-id">
      <span class="sym">BOOK</span>
      <h2>Cross-asset overview</h2>
      <span class="feed">{len(signals)} instruments &middot; one risk budget</span>
    </div>
    <div class="quote">
      <span class="price">{avg_score:+.0f}</span>
      <span class="delta flat">average score</span>
    </div>
  </div>

  <div class="summary-grid">
    <div><span>Breadth</span><b>{buys} buy &middot; {holds} hold &middot; {sells} sell</b></div>
    <div><span>Average confidence</span><b>{avg_conf:.0f}%</b></div>
    <div><span>Brent - WTI</span><b>{num(spread, 2) if spread is not None else "-"}</b></div>
    <div><span>High confidence</span><b>{max(signals, key=lambda s: s.confidence).asset.short if signals else "-"} {max((s.confidence for s in signals), default=0):.0f}%</b></div>
  </div>

  <div class="summary-read">
    <h3>Read</h3>
    <p>{e(" ".join(read))}</p>
  </div>

  <div class="summary-levels">
    <h3>Levels that matter</h3>
    <ul>{''.join(levels)}</ul>
  </div>
</article>"""


def render_html(result: RunResult, refresh_seconds: int | None = None) -> str:
    signals = result.signals
    buys = sum(1 for s in signals if "BUY" in s.action)
    sells = sum(1 for s in signals if "SELL" in s.action)
    holds = len(signals) - buys - sells
    avg_score = sum(s.score for s in signals) / len(signals) if signals else 0.0
    avg_conf = sum(s.confidence for s in signals) / len(signals) if signals else 0.0

    vix = next((q for q in result.macro.quotes if q.key == "vix"), None)
    if vix is None:
        regime = "Unknown"
    elif vix.price < 15:
        regime = "Calm volatility"
    elif vix.price < 20:
        regime = "Normal volatility"
    elif vix.price < 28:
        regime = "Elevated volatility"
    else:
        regime = "Stressed volatility"

    if avg_score >= 20:
        bias = "bullish"
    elif avg_score <= -20:
        bias = "bearish"
    else:
        bias = "two-sided"

    cards = "".join(asset_card(s, result) for s in signals)
    if len(signals) >= 3 and len(signals) % 2 == 1:
        cards += overview_card(result)
    stamped = f"{result.generated_at:%d %b %Y, %H:%M %Z}"
    data_note = (
        "Includes today's session; the last bar may be an intraday snapshot."
        if str(result.generated_at.date()) == result.as_of
        else f"Latest completed bar: {result.as_of}."
    )
    vix_text = (
        f"VIX {num(vix.price, 1)} ({vix.change_20d:+.0f}% 20D)"
        if vix
        else "VIX unavailable"
    )
    dxy_20d = result.macro.values.get("dxy_ret20", 0.0)
    dxy_text = (
        f"Dollar {'stronger' if dxy_20d > 0 else 'weaker'} over 20 days; "
        f"cross-asset backdrop is {'a headwind' if dxy_20d > 0 else 'a tailwind'} "
        f"for USD commodities."
    )
    book_text = (
        "No directional signals."
        if not buys and not sells
        else "The book is mixed."
        if bias == "two-sided"
        else f"The book is leaning {bias}."
    )
    refresh_tag = ""
    live_pill = ""
    if refresh_seconds and refresh_seconds > 0:
        secs = int(refresh_seconds)
        refresh_tag = (
            f'<meta http-equiv="refresh" content="{secs}">\n'
            f'<meta http-equiv="cache-control" content="no-store">'
        )
        live_pill = f'<span class="pill auto">AUTO {secs / 60:g} MIN</span>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh_tag}
<title>Commodity Signal Desk - {e(result.as_of)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="page">

  <header class="masthead">
    <div class="brand">
      <div class="mark">CSD</div>
      <div>
        <h1>Commodity Signal Desk</h1>
        <p class="sub">Brent &middot; WTI &middot; Gold &middot; Silver &middot; Natural gas &mdash; daily buy/sell decision brief</p>
      </div>
    </div>
    <div class="meta">
      <span>{e(stamped)}</span>
      {live_pill}
      <span class="pill live">Data through {e(result.as_of)}</span>
      <span>{result.news_count:,} headlines &middot; {len(signals)} instruments</span>
    </div>
  </header>

  {macro_strip(result)}

  <section class="brief" aria-label="Run summary">
    <div class="brief-box">
      <span class="kicker">Regime</span>
      <strong>{e(regime)}</strong>
      <p>{e(vix_text)}. {e(dxy_text)}</p>
    </div>
    <div class="brief-box">
      <span class="kicker">Breadth</span>
      <strong>{buys} buy &middot; {holds} hold &middot; {sells} sell</strong>
      <p>Average score {avg_score:+.0f} / 100; average confidence {avg_conf:.0f}%.
      {e(book_text)}</p>
    </div>
    <div class="brief-box">
      <span class="kicker">Data &amp; method</span>
      <strong>{e(data_note)}</strong>
      <p>Yahoo Finance futures, 13 RSS feeds, 9 Google News searches. Stops and targets are
      ATR-based. This is decision support, not advice.</p>
    </div>
  </section>

  <main class="grid">
    {cards}
  </main>

  {global_news(result)}

  {validation_table(result)}

  {methodology()}

  <div class="disclaimer">
    <b>Risk notice.</b> This tool produces a systematic, rules-based opinion from public data.
    It is not investment advice, a solicitation, or a guarantee. Commodity futures are leveraged:
    a move smaller than the stop can still produce losses larger than the margin posted, and
    gaps can jump the stop. Headline sentiment is approximate, prices may be delayed or wrong,
    and the model has no knowledge of your portfolio, taxes, horizon or risk capacity. Verify
    every price with your broker and size positions so that being wrong is survivable.
  </div>

  <div class="footer">
    <span>commodity-signal-desk v1.0 &middot; generated {e(stamped)}</span>
    <span>scores are not predictions &middot; backtests are descriptive</span>
  </div>

</div>
</body>
</html>"""
