# Commodity Signal Desk

A self-contained buy/sell decision agent for **Brent, WTI, gold, silver and natural gas**.
It pulls market data and news, scores five factors, produces a transparent signal with a
trade plan, and writes a dashboard you can open in a browser.

No API keys. No paid data. No third-party Python packages. Python 3.10+.

```powershell
cd commodity-agent
python -m commodity_agent run --open
```

The report lands in `reports/latest.html` (plus `latest.json` and `latest.md`).

---

## What it does

1. **Market data** - two years of daily OHLCV for the five futures plus five macro series
   from Yahoo Finance's public chart endpoint.
2. **News** - 13 curated RSS feeds and 9 Google News searches, deduplicated and scored over a
   96-hour window. The rule set understands OPEC+ supply changes, Fed policy, the dollar,
   inventories, storage, weather, LNG, sanctions, wars and ceasefires.
3. **Five factors** - trend, momentum, mean reversion, news flow and macro. Each is squashed
   to `[-1, +1]` and weighted per instrument.
4. **Decision** - a composite score in `[-100, +100]`, an action, a confidence estimate and
   the reasons behind it.
5. **Trade plan** - ATR stop, 4x ATR target, reward:risk, risk per contract, position size,
   minimum account for one lot and an ETF proxy for smaller accounts.
6. **Validation** - a walk-forward check of the technical core against 10-day forward returns.

The output is deliberately explainable. Every factor carries a one-line read, and every
headline behind the news score is listed with its source and timestamp.

---

## Example run (23 Oct 2026)

| Asset | Price | 1D | Score | Signal | Confidence | Plan |
|---|---:|---:|---:|---|---:|---|
| Brent Crude Oil | 112.05 | -0.99% | +44 | **BUY** | 62% Medium | Long, stop 102.65, target 129.14, R:R 1.82 |
| WTI Crude Oil | 103.78 | +4.33% | +43 | **BUY** | 66% Medium | Long, stop 93.67, target 122.16, R:R 1.82 |
| Gold | 4,012.6 | -1.18% | -24 | **SELL** | 66% Medium | Short, stop 4,186.9, target 3,695.6, R:R 1.82 |
| Silver | 54.05 | -1.30% | -37 | **SELL** | 64% Medium | Short, stop 57.09, target 48.52, R:R 1.82 |
| Natural Gas | 3.059 | +9.41% | -10 | **HOLD** | 78% High | Flat; buy above 3.350, sell below 2.710 |

Context: a Middle East supply shock had bid the crude complex up sharply year-to-date, while
gold and silver were correcting on Fed-hike fears. WTI outperformed Brent as the Brent–WTI
spread narrowed. The model bought both oil trends, sold the metal downtrends, and refused to
chase gas after a one-day 9% spike.

---

## Commands

| Command | What it does |
|---|---|
| `python -m commodity_agent run` | Full pipeline, writes HTML/JSON/Markdown |
| `python -m commodity_agent run --assets gold,silver` | Subset of instruments |
| `python -m commodity_agent run --refresh --open` | Bypass cache, open dashboard |
| `python -m commodity_agent run --offline` | Rebuild from cached data only |
| `python -m commodity_agent run --no-news` | Technical + macro only |
| `python -m commodity_agent ask "should I buy gold?"` | Plain-English answer with plan |
| `python -m commodity_agent explain gas` | Full factor-by-factor breakdown |
| `python -m commodity_agent backtest --horizon 20` | Validation table |
| `python -m commodity_agent watch --interval 5` | Live loop: refresh, rewrite, auto-reload |
| `python -m commodity_agent watch --interval 5 --email` | Live loop + change-only Gmail alerts |
| `python -m commodity_agent run --email` | One-shot run + change-only Gmail alert |
| `python -m commodity_agent alert-test --dry-run` | Validate Gmail config without sending |
| `python -m commodity_agent alert-test` | Send a Gmail test message |
| `python -m commodity_agent run --account 100000 --risk 0.5` | Position sizing for your account |

Windows shortcut:

```powershell
.\run.cmd --assets brent --open
.\run.cmd watch --interval 5 --open
```

Useful flags: `--days 3`, `--account 25000`, `--risk 1.5`, `--no-backtest`, `--color`.

---

## Live 5-minute mode

```powershell
python -m commodity_agent watch --interval 5 --open
```

While it runs:

- prices are refreshed with a cache of at most **225 seconds**, so a 5-minute cycle always
  gets a fresh print;
- news is refreshed at most every **10 minutes** (less load on the feeds);
- `latest.html`, `latest.json` and `latest.md` are rewritten in place, with no archive spam;
- the open dashboard auto-reloads every 5 minutes (the header shows `AUTO 5 MIN`);
- each cycle appends a one-line summary to `reports/watch.log`;
- **Ctrl+C** stops the watcher and removes the auto-refresh tag, leaving a static snapshot.

Options:

```powershell
# test one cycle and exit
python -m commodity_agent watch --cycles 1 --no-clear

# 2.5-minute cadence, only Brent and gold
python -m commodity_agent watch --interval 2.5 --assets brent,gold

# force every feed on every cycle (heavier; may hit rate limits)
python -m commodity_agent watch --interval 5 --refresh

# keep the terminal scrollback
python -m commodity_agent watch --no-clear
```

The interval is in minutes (minimum 1). The auto-reload tag is only present while the watcher
is running; a plain `run` always writes a static report.

---

## Auto-start at login (no terminal)

A hidden Startup shortcut is installed at:

```text
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Commodity Signal Desk.lnk
```

It launches, with no console window:

```text
pythonw.exe -m commodity_agent watch --interval 5 --email --no-clear --out reports
```

from the project directory. It starts at your next login; it is already running now.

- Double-click the **Commodity Dashboard** shortcut on the Desktop (or open
  `reports/latest.html`). While the watcher is alive, that page reloads itself every 5 minutes
  from the newly written file.
- If the tab was already open before the watcher started, press **F5** once; after that it
  refreshes automatically.
- Cycle summary: `reports/watch.log`. Hidden console output: `reports/background.log`.
- **Stop it:** Task Manager → Details → `pythonw.exe` whose command line contains
  `commodity_agent watch` → End task. Closing the browser does **not** stop it.
- **Disable auto-start:** Win+R → `shell:startup` → delete the
  `Commodity Signal Desk` shortcut. This does not stop the current process; do both if you
  want it fully off.
- **Re-enable:** copy the shortcut back to that Startup folder, or recreate it with
  `pythonw.exe` as the target and the arguments above.
- If you force-stop the watcher, the open dashboard keeps its last auto-refresh tag and will
  show a stale snapshot until you close or reload the tab.

This is why the hidden watcher writes `background.log`: `pythonw` has no stdout/stderr, so
console output is redirected there. No PowerShell window is involved.

---

## Gmail alerts

The watcher can email you when something **materially changes** — not every five minutes.

### Setup

1. Turn on **2-Step Verification** on your Google account.
2. Create an **App Password** at <https://myaccount.google.com/apppasswords>
   (choose “Mail” and your device). Use that 16-character password, not your normal one.
3. Copy the example to a private location outside OneDrive/git and fill it in:

   ```powershell
   New-Item -ItemType Directory -Force "$HOME\.commodity-agent" | Out-Null
   Copy-Item gmail.env.example "$HOME\.commodity-agent\gmail.env"
   notepad "$HOME\.commodity-agent\gmail.env"
   ```

   ```ini
   GMAIL_USER=you@gmail.com
   GMAIL_APP_PASSWORD=abcd efgh ijkl mnop
   ALERT_TO=you@gmail.com
   ```

   The app checks `~\.commodity-agent\gmail.env` first, then the project-root
   `gmail.env`. Both are ignored by git; prefer the home path because the project
   lives under OneDrive and the fallback file would sync to the cloud.
   You can also set the same values as Windows environment variables, or point
   `GMAIL_ENV_FILE` at a different file.
4. Validate without sending:

   ```powershell
   python -m commodity_agent alert-test --dry-run
   ```

5. Send a real test:

   ```powershell
   python -m commodity_agent alert-test
   ```

6. Run live with alerts:

   ```powershell
   python -m commodity_agent watch --interval 5 --email --open
   ```

   Or, for a once-a-day email, `python -m commodity_agent run --email`.

### What triggers an email

- **Action flip** — e.g. Brent BUY → HOLD.
- **Confidence move** of 15 percentage points or more, same action.
- **Stop or target hit** — price trades through the levels published in the last alert.
- **Flat-market trigger hit** — gas crosses the buy or sell trigger from the last alert.

The first observation only saves a baseline; it does not email. State is stored in
`reports/alert_state.json` and is updated **after a successful send**, so a transient Gmail
failure is retried next cycle. No-change polls do not move the baseline, which lets
confidence and trigger thresholds accumulate over time.

Email includes the change, the current five-instrument book, the trade plans and the local
dashboard path. Override the recipient with `--email-to` or `ALERT_TO`.

### Gmail notes

- An App Password requires 2-Step Verification. Google Workspace admins can disable them;
  in that case use an SMTP relay or OAuth-based sending.
- If port 465 is blocked, set `GMAIL_SMTP_PORT=587` in `gmail.env` (STARTTLS).
- Revoke the App Password from your Google account if it leaks.
- Gmail has daily sending limits; change-only alerts are far below them.
- To re-baseline (for example after changing the model), delete
  `reports/alert_state.json`; the next run saves a fresh baseline and sends nothing.
- If you edit `gmail.env` while the watcher is running, restart the watcher to pick it up.
- If the test email does not arrive, check spam and add your own sender address to contacts.

---

## Run on Vercel (email while the laptop is off)

The local watcher stops when the laptop shuts down. The included Vercel deployment runs the
same agent as a Python function, so it keeps checking and emailing while your laptop is off:

- `api/run.py` — Vercel function, GET `/api/run`
- **Upstash Redis** — persistent alert baseline (Vercel functions are ephemeral)
- **Gmail SMTP** — the same change-only email code
- **Hosted dashboard** — served from the root path; scheduled emails still run through `/api/run`

Vercel **Pro** supports a native 5-minute cron. Vercel **Hobby** only allows cron once per day,
so Hobby needs a free external scheduler (cron-job.org) calling the function every 5 minutes.

Full setup: **[VERCEL.md](VERCEL.md)**. The code is included but **not deployed automatically** —
you need to add the Vercel project, Upstash database and environment variables.

For this Vercel Hobby project, the included GitHub Actions workflow `.github/workflows/two-hour-refresh.yml` calls the production endpoint every two hours and emails the full current book, even when no signal has materially changed. Add a repository Actions secret named `CRON_SECRET` matching the Vercel Production secret; see [VERCEL.md](VERCEL.md). The ordinary API call still supports change-only alerts.

When Vercel is live, stop the local `pythonw` watcher and remove the Startup shortcut:
the two deployments have separate alert-state stores, so running both would send duplicate
emails.

---

## How the decision is made

### Factors and weights

| Factor | What it measures | Brent | WTI | Gold | Silver | Gas |
|---|---|---:|---:|---:|---:|---:|
| Trend | Price vs EMA 20/50/200, EMA slopes, ADX/DI | 35% | 35% | 30% | 30% | 40% |
| Momentum | RSI, MACD histogram, 20-day return, 52-week position | 20% | 20% | 15% | 20% | 15% |
| Mean reversion | Bollinger position, ATR distance from EMA, RSI extremes | 10% | 10% | 10% | 10% | 20% |
| News flow | 96-hour event-rule sentiment, recency and source weighted | 25% | 25% | 20% | 20% | 20% |
| Macro | Dollar, 10-year yield, VIX, S&P 500, copper/gold | 10% | 10% | 25% | 20% | 5% |

**Natural gas runs in fade mode.** Gas is structurally mean-reverting, so its trend and
momentum readings are inverted before weighting: an extended uptrend is a sell signal, not a
buy signal. The report labels these factors "faded" and explains the inversion. The choice is
supported by the validation table, where gas trend-following had a negative information
coefficient over the sample.

### Action bands

| Score | Action |
|---:|---|
| `>= +45` | STRONG BUY |
| `>= +20` | BUY |
| `-20 .. +20` | HOLD |
| `<= -20` | SELL |
| `<= -45` | STRONG SELL |

Confidence blends factor agreement (40%), data quality (20%), news coverage (20%) and trend
clarity (20%). It is a measure of how coherent the evidence is, **not** the probability of a
win.

### Trade plan

- **Entry**: last close (the report notes when the last bar is an intraday snapshot).
- **Stop**: 1.6x ATR, capped at 2.2x, widened to the 10-day swing only when that swing is
  close. Long stops sit below entry, short stops above.
- **Target**: 4x ATR, giving roughly 1.8:1 to 2.2:1.
- **Size**: `account * risk% / (stop distance * contract multiplier)`, floored to whole
  contracts. If one contract exceeds the budget, the report says so and gives the equivalent
  ETF notional.
- **Flat**: shows the 20-day breakout triggers instead of a position.

Futures multipliers used: Brent and WTI 1,000 bbl, gold 100 oz, silver 5,000 oz, gas 10,000 MMBtu.
Those are standard CME/ICE contract sizes; verify with your broker.

---

## Data sources

**Market data**: Yahoo Finance chart API, continuous front-month futures (`BZ=F`, `CL=F`,
`GC=F`, `SI=F`, `NG=F`) plus `DX-Y.NYB`, `^TNX`, `^VIX`, `^GSPC`, `HG=F`.

**News**:

- Investing.com Commodities and Economy
- FT Commodities, WSJ Markets, CNBC Economy
- OilPrice.com, Rigzone, Natural Gas Intelligence
- EIA Today in Energy, Federal Reserve press releases
- Mining.com, BBC World, Al Jazeera
- Google News searches for Brent, WTI, gold, silver, gas, OPEC+, Iran sanctions, the Fed and
  safe-haven flows

All data is cached in `data/cache/` (market data 10 minutes, news 15 minutes). If a fetch
fails, the last cached copy is used and flagged. `--refresh` bypasses the TTL; `--offline`
never touches the network.

**Why no LLM?** The agent is deterministic and reproducible: the same inputs produce the same
score, with no API key, cost or hallucination surface. The news layer uses an explicit event
rule set instead. Wiring an LLM in for narrative commentary is a small extension, but it is
not required for the decision.

---

## Configuration

Everything worth tuning is in `commodity_agent/config.py`:

- `ASSETS` - instruments, Yahoo symbols, contract specs, factor weights, fade mode.
- `FEEDS` / `GOOGLE_QUERIES` - news sources.
- `THRESHOLDS` - action bands (also mirrored in `signals.action_for`).
- `NEWS_MAX_AGE_HOURS`, `NEWS_HALF_LIFE_HOURS`, `NEWS_MIN_RELEVANCE`.

To add an instrument, add an `Asset` entry, a news keyword set and mention pattern in
`news.py`, and a macro weight profile in `signals._macro_components`. The pipeline and report
pick it up automatically.

Position sizing defaults live in the CLI (`--account`, `--risk`), not in code.

---

## Automation

Run it every weekday morning with Task Scheduler:

```powershell
schtasks /create /tn "Commodity Signal Desk" /sc weekly /d MON,TUE,WED,THU,FRI /st 08:00 /tr "C:\path\to\commodity-agent\run.cmd"
```

Or keep a PowerShell window open:

```powershell
while ($true) { python -m commodity_agent run; Start-Sleep -Seconds 900 }
```

The HTTP cache makes repeated runs cheap. Do not run it more than a few times an hour against
Yahoo or Google News.

---

## Project layout

```
commodity-agent/
  commodity_agent/
    config.py        instruments, weights, feeds, thresholds
    net.py           cached HTTP with retries and offline fallback
    market.py        Yahoo chart parsing -> daily bars
    indicators.py    EMA, RSI, ATR, MACD, Bollinger, ADX (stdlib only)
    news.py          RSS/Google News, relevance, event rules, aggregation
    macro.py         dollar, yields, VIX, S&P, copper, WTI spread
    signals.py       factors, composite, confidence, trade plan
    alerts.py        Gmail change-only alerts and alert state
    backtest.py      walk-forward technical validation
    pipeline.py      orchestration and RunResult
    report.py        terminal, JSON, Markdown
    render_html.py   self-contained dashboard with inline SVG
    cli.py           run / watch / ask / explain / backtest / alert-test
  tests/             52 tests, no network required
  api/run.py         Vercel email-only cron endpoint
  vercel.json        Vercel function configuration
  VERCEL.md          Vercel deployment guide
  requirements.txt   stdlib-only runtime (no packages)
  reports/           generated output
  gmail.env.example  app-password template (copy to ~/.commodity-agent/gmail.env)
  run.cmd
```

---

## Tests

```powershell
python -m unittest discover -s tests -v
```

The suite covers indicator math, RSS date parsing (a regression guard: a case bug once made
every article look brand new), relevance, event sentiment, gas fade behaviour, position
sizing, backtest bucket integrity, live-refresh meta, Gmail change detection, Vercel state
storage and all report renderers.

---

## Limitations

- **No weather model, storage balance, CFTC positioning, options skew or intraday data.**
  Gas in particular is weather-driven; the news layer only sees what is published.
- **News scoring is lexical.** It handles common market events well, but irony, novel events
  and subtle policy language can be misread.
- **The backtest is descriptive.** Overlapping windows, no costs, no slippage, no execution
  model. A 52% hit rate is close to noise. It is a sanity check, not proof of edge.
- **Futures are large.** One Brent contract risks thousands of dollars per ATR-based stop.
  The report shows the ETF proxy precisely because most accounts cannot size the future.
- **Yahoo data is indicative.** The last daily bar can be an intraday snapshot; verify prices
  with your broker before acting.
- **Geopolitical premium is unstable.** A ceasefire headline can erase a week of gains in an
  hour.

---

## Disclaimer

This is decision support, not investment advice. Commodity futures are leveraged and can lose
more than the margin posted; gaps can jump stops. The model does not know your portfolio,
horizon, taxes or risk capacity. Size every position so that being wrong is survivable.
