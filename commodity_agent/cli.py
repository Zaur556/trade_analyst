"""Command line interface.

    python -m commodity_agent run
    python -m commodity_agent run --assets gold,silver --refresh --open
    python -m commodity_agent ask "should I buy brent oil?"
    python -m commodity_agent explain gas
    python -m commodity_agent backtest --horizon 20
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from . import __version__
from .alerts import (
    AlertConfigError,
    format_outcome,
    load_config,
    process_alerts,
    send_test_email,
)
from .backtest import run_backtest
from .config import ASSETS, ORDER
from .market import fetch_series
from .pipeline import RunResult, parse_assets, run
from .report import fmt_money, fmt_num, fmt_pct, fmt_price, render_console, write_reports
from .signals import Signal


def _common(parser: argparse.ArgumentParser, *, reports: bool = True) -> None:
    parser.add_argument("--assets", default="all", help="all or comma-separated: brent,wti,gold,silver,gas")
    parser.add_argument("--account", type=float, default=50_000.0, help="account equity in USD (default 50000)")
    parser.add_argument("--risk", type=float, default=1.0, help="risk per trade in percent of equity (default 1.0)")
    parser.add_argument("--refresh", action="store_true", help="bypass the HTTP cache")
    parser.add_argument("--offline", action="store_true", help="use cached data only")
    parser.add_argument("--no-news", action="store_true", help="disable the news layer")
    parser.add_argument("--no-backtest", action="store_true", help="skip signal validation")
    parser.add_argument("--color", action="store_true", help="ANSI colours in the terminal")
    if reports:
        parser.add_argument("--out", default="reports", help="report directory (default reports)")
        parser.add_argument("--open", action="store_true", help="open the HTML report when done")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="commodity-agent",
        description="Buy/sell decision support for Brent, WTI, gold, silver and natural gas.",
    )
    p.add_argument("--version", action="version", version=f"commodity-signal-desk {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run the full pipeline and write reports")
    _common(run_p)
    run_p.add_argument("--days", type=float, default=4.0, help="news lookback window in days (default 4)")
    run_p.add_argument("--email", action="store_true", help="email Gmail alerts when signals change")
    run_p.add_argument("--email-to", default=None, help="recipient override (default ALERT_TO)")

    ask_p = sub.add_parser("ask", help="ask a plain-English buy/sell question")
    ask_p.add_argument("question", nargs="+", help='e.g. "should I buy gold?"')
    _common(ask_p, reports=False)

    explain_p = sub.add_parser("explain", help="show the full factor breakdown for one instrument")
    explain_p.add_argument("asset", choices=ORDER)
    _common(explain_p, reports=False)

    bt_p = sub.add_parser("backtest", help="validate the technical core on history")
    bt_p.add_argument("--assets", default="all")
    bt_p.add_argument("--horizon", type=int, default=10, help="forward return horizon in trading days")
    bt_p.add_argument("--refresh", action="store_true")
    bt_p.add_argument("--offline", action="store_true")

    watch_p = sub.add_parser("watch", help="refresh continuously every N minutes")
    _common(watch_p)
    watch_p.add_argument("--interval", type=float, default=5.0, help="minutes between refreshes (default 5)")
    watch_p.add_argument("--cycles", type=int, default=0, help="stop after N cycles (0 = forever)")
    watch_p.add_argument("--days", type=float, default=4.0, help="news lookback window in days")
    watch_p.add_argument("--no-clear", action="store_true", help="do not clear the terminal between cycles")
    watch_p.add_argument("--email", action="store_true", help="email Gmail alerts when signals change")
    watch_p.add_argument("--email-to", default=None, help="recipient override (default ALERT_TO)")

    alert_p = sub.add_parser("alert-test", help="send a Gmail test message")
    alert_p.add_argument("--to", default=None, help="recipient override (default ALERT_TO)")
    alert_p.add_argument("--dry-run", action="store_true", help="validate config without sending")

    return p


def _write(result: RunResult, out: str) -> Path:
    out_dir = Path(out).resolve()
    paths = write_reports(result, out_dir)
    result.out_dir = out_dir
    return paths["html"]


def _brief(signal: Signal) -> str:
    s = signal
    p = s.plan
    lines: list[str] = []
    lines.append(f"{s.asset.name} ({s.asset.symbol})  -  {s.action}  {s.score:+.0f}/100  "
                 f"[{s.confidence:.0f}% {s.confidence_label} confidence]")
    lines.append(
        f"Price {fmt_price(s.price)} {s.asset.unit}  ({s.change_pct:+.2f}% today)  |  "
        f"RSI {fmt_num(s.metrics.get('rsi'), 0)}  |  ADX {fmt_num(s.metrics.get('adx'), 0)}  |  "
        f"ATR {fmt_num(s.metrics.get('atr_pct'), 1)}%/day"
    )
    lines.append("")
    if p.direction == "FLAT":
        lines.append(f"No position. Buy trigger {fmt_price(p.trigger_buy)}; "
                     f"sell trigger {fmt_price(p.trigger_sell)}.")
    else:
        lines.append(
            f"{p.direction} at {fmt_price(p.entry)}  |  stop {fmt_price(p.stop)}  |  "
            f"target {fmt_price(p.target)}  |  reward:risk {p.rr:.2f}"
        )
        lines.append(
            f"Risk/contract {fmt_money(p.risk_per_contract)}  |  {p.contracts} contract(s) fit  |  "
            f"min account for 1 lot {fmt_money(p.min_account_one_lot)}"
        )
        if p.etf_notional:
            lines.append(
                f"ETF proxy: about {fmt_money(p.etf_notional)} of {s.asset.etf} for the same dollar risk."
            )
    lines.append("")
    lines.append("Why:")
    for d in s.drivers:
        lines.append(f"  - {d}")
    lines.append("")
    lines.append("Watch:")
    for r in s.risks:
        lines.append(f"  - {r}")
    if s.news and s.news.headlines:
        lines.append("")
        lines.append("Latest headlines:")
        for a in s.news.headlines[:3]:
            lines.append(f"  - [{a.sentiment.get(s.asset.key, 0):+.2f}] {a.title} ({a.source})")
    return "\n".join(lines)


def _explain(signal: Signal) -> str:
    lines = [_brief(signal), "", "Factor breakdown:"]
    for f in signal.factors:
        lines.append(f"  {f.label:20s} score {f.score:+.2f}  weight {f.weight * 100:4.0f}%  "
                     f"contribution {f.points:+6.1f} pts")
        lines.append(f"    {f.detail}")
    return "\n".join(lines)


def _detect_assets(text: str) -> list[str]:
    low = text.lower()
    found: list[str] = []
    aliases = {
        "brent": ("brent", "crude", "oil"),
        "wti": ("wti", "west texas", "cushing", "oil.wti"),
        "gold": ("gold", "bullion", "xau"),
        "silver": ("silver", "xag"),
        "gas": ("natural gas", "nat gas", "natgas", "lng", "gas"),
    }
    for key in ORDER:
        for word in aliases[key]:
            if word in low:
                found.append(key)
                break
    return found


def cmd_run(args) -> int:
    try:
        keys = parse_assets(args.assets)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    alert_config = None
    if args.email:
        try:
            alert_config = load_config(to=args.email_to)
        except AlertConfigError as exc:
            print(f"Email alerts disabled: {exc}", file=sys.stderr)

    result = run(
        keys,
        account_size=args.account,
        risk_pct=args.risk,
        refresh=args.refresh,
        offline=args.offline,
        no_news=args.no_news,
        no_backtest=args.no_backtest,
    )
    html_path = _write(result, args.out)
    print(render_console(result, color=args.color))
    print(f"\nHTML report: {html_path}")

    if alert_config is not None:
        try:
            outcome = process_alerts(result, html_path.parent, config=alert_config)
            print(f"Email: {format_outcome(outcome)}")
        except Exception as exc:  # noqa: BLE001
            print(f"Email alert failed: {exc}", file=sys.stderr)
            print("The report was still written; the alert will retry next run.", file=sys.stderr)

    if args.open:
        if os.name == "nt":
            os.startfile(html_path)  # type: ignore[attr-defined]
        else:
            webbrowser.open(html_path.as_uri())
    return 0


def cmd_explain(args) -> int:
    result = run(
        [args.asset],
        account_size=args.account,
        risk_pct=args.risk,
        refresh=args.refresh,
        offline=args.offline,
        no_news=args.no_news,
        no_backtest=True,
    )
    print(_explain(result.signals[0]))
    return 0


def cmd_ask(args) -> int:
    question = " ".join(args.question)
    keys = _detect_assets(question) or list(ORDER)
    result = run(
        keys,
        account_size=args.account,
        risk_pct=args.risk,
        refresh=args.refresh,
        offline=args.offline,
        no_news=args.no_news,
        no_backtest=True,
    )
    print(f"Q: {question}")
    print()
    if len(result.signals) == 1:
        print(_brief(result.signals[0]))
    else:
        print(_brief(result.signals[0]))
        print("\nAlso checked: " + ", ".join(s.asset.name for s in result.signals[1:]))
        for s in result.signals[1:]:
            print(f"  {s.asset.short:8s} {s.action:11s} {s.score:+6.1f}  {s.drivers[0]}")
    print()
    print("Decision support only; not investment advice.")
    return 0


def cmd_watch(args) -> int:
    try:
        keys = parse_assets(args.assets)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    interval_s = max(60.0, args.interval * 60.0)
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "watch.log"
    clear = sys.stdout.isatty() and not args.no_clear
    market_ttl = max(60, min(300, int(interval_s * 0.75)))
    news_ttl = max(300, int(interval_s * 2))
    opened = False
    last_result: RunResult | None = None
    cycle = 0

    alert_config = None
    if args.email:
        try:
            alert_config = load_config(to=args.email_to)
        except AlertConfigError as exc:
            print(f"Email alerts are enabled but Gmail is not configured:\n{exc}", file=sys.stderr)
            return 2

    def log(line: str) -> None:
        try:
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(line.rstrip() + "\n")
        except OSError:
            pass

    print(f"Watch mode: every {interval_s / 60:g} min, {len(keys)} instruments. Ctrl+C to stop.")
    print(f"Prices cache <= {market_ttl}s, news cache <= {news_ttl}s. Log: {log_path}")
    if alert_config is not None:
        print(f"Email alerts: {alert_config.describe()} (change-only)")
    else:
        print("Email alerts: off")

    try:
        while True:
            cycle += 1
            started = time.monotonic()
            if clear:
                print("\x1b[2J\x1b[H", end="")
            print(
                f"LIVE WATCH  |  cycle {cycle}  |  every {interval_s / 60:g} min  |  "
                f"{datetime.now():%H:%M:%S}  |  Ctrl+C to stop"
            )
            try:
                result = run(
                    keys,
                    days=args.days,
                    account_size=args.account,
                    risk_pct=args.risk,
                    refresh=args.refresh,
                    offline=args.offline,
                    no_news=args.no_news,
                    no_backtest=args.no_backtest,
                    market_ttl=market_ttl,
                    news_ttl=news_ttl,
                )
                paths = write_reports(
                    result, out_dir, archive=False, live_seconds=int(interval_s)
                )
                result.out_dir = out_dir
                last_result = result
                print(render_console(result, color=args.color))
                summary = " | ".join(
                    f"{s.asset.short} {s.score:+.1f} {s.action}" for s in result.signals
                )
                log(
                    f"{datetime.now().astimezone().isoformat(timespec='seconds')} | "
                    f"cycle {cycle} | {summary} | news {result.news_count} | "
                    f"{time.monotonic() - started:.1f}s"
                )
                if alert_config is not None:
                    try:
                        outcome = process_alerts(result, out_dir, config=alert_config)
                        if outcome.status == "sent":
                            print(f"EMAIL SENT: {format_outcome(outcome)}")
                            log(
                                f"{datetime.now().astimezone().isoformat(timespec='seconds')} | "
                                f"cycle {cycle} | EMAIL {format_outcome(outcome)}"
                            )
                        elif outcome.status == "baseline":
                            print(f"Email alerts armed: {outcome.message}")
                    except Exception as exc:  # noqa: BLE001
                        print(f"Email alert failed: {exc}")
                        log(
                            f"{datetime.now().astimezone().isoformat(timespec='seconds')} | "
                            f"cycle {cycle} | EMAIL ERROR {type(exc).__name__}: {str(exc)[:200]}"
                        )
                if args.open and not opened:
                    if os.name == "nt":
                        os.startfile(paths["html"])  # type: ignore[attr-defined]
                    else:
                        webbrowser.open(paths["html"].as_uri())
                    opened = True
            except Exception as exc:  # noqa: BLE001 - a bad cycle must not kill the watcher
                msg = f"[{datetime.now():%H:%M:%S}] cycle {cycle} failed: {type(exc).__name__}: {exc}"
                print(msg)
                log(
                    f"{datetime.now().astimezone().isoformat(timespec='seconds')} | "
                    f"cycle {cycle} | ERROR {type(exc).__name__}: {str(exc)[:200]}"
                )

            if args.cycles and cycle >= args.cycles:
                break

            elapsed = time.monotonic() - started
            sleep_for = max(10.0, interval_s - elapsed)
            next_at = datetime.now().astimezone() + timedelta(seconds=sleep_for)
            print(
                f"\nNext refresh at {next_at:%H:%M:%S} "
                f"(in {sleep_for / 60:.1f} min). Ctrl+C to stop."
            )
            time.sleep(sleep_for)
    except KeyboardInterrupt:
        print("\nWatch stopped.")
    finally:
        # Remove the auto-refresh meta so a closed watcher cannot leave a stale
        # browser tab reloading forever.
        if last_result is not None:
            try:
                write_reports(last_result, out_dir, archive=False, live_seconds=None)
            except Exception:  # noqa: BLE001
                pass
    return 0


def cmd_alert_test(args) -> int:
    try:
        config = load_config(to=args.to)
    except AlertConfigError as exc:
        print(f"Gmail not configured:\n{exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        mode = "SSL" if config.port == 465 else "STARTTLS"
        print("Gmail configuration looks valid.")
        print(f"From: {config.user}")
        print(f"To:   {config.to}")
        print(f"SMTP: {config.host}:{config.port} ({mode})")
        print("Dry run - no email sent.")
        return 0

    try:
        send_test_email(config)
    except Exception as exc:  # noqa: BLE001
        print(f"Test email failed: {exc}", file=sys.stderr)
        return 1
    print(f"Test email sent to {config.to}.")
    return 0


def cmd_backtest(args) -> int:
    try:
        keys = parse_assets(args.assets)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    def load(key: str):
        return key, fetch_series(ASSETS[key].symbol, refresh=args.refresh, offline=args.offline)

    print(f"Technical-core validation, {args.horizon}-day forward returns\n")
    print(f"{'ASSET':14s}{'SAMPLES':>8s}{'BASE UP':>9s}{'HIT':>7s}"
          f"{'STRONG BUY':>12s}{'BUY':>8s}{'SELL':>8s}{'STRONG SELL':>13s}")
    print("-" * 80)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(load, key): key for key in keys}
        for fut in futures:
            try:
                key, series = fut.result()
            except Exception as exc:  # noqa: BLE001
                print(f"  {type(exc).__name__}: {exc}")
                continue
            try:
                b = run_backtest(ASSETS[key], series, horizon=args.horizon)
            except Exception as exc:  # noqa: BLE001
                print(f"{key:14s} skipped: {exc}")
                continue
            by = {x.action: x for x in b.buckets}

            def avg(action: str) -> str:
                x = by.get(action)
                return fmt_pct(x.avg) if x and x.n else "-"

            hit = f"{b.directional_hit * 100:.0f}%" if b.directional_hit is not None else "-"
            print(f"{key:14s}{b.samples:>8d}{b.baseline_up * 100:>8.0f}%{hit:>7s}"
                  f"{avg('STRONG BUY'):>12s}{avg('BUY'):>8s}{avg('SELL'):>8s}{avg('STRONG SELL'):>13s}")
    print("-" * 80)
    print("Descriptive only: overlapping windows, no costs or slippage.")
    return 0


def _ensure_streams() -> None:
    """pythonw.exe starts with sys.stdout/stderr = None; give it a log file."""
    if sys.stdout is None or sys.stderr is None:
        try:
            log_path = Path.cwd() / "reports" / "background.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            stream = log_path.open("a", encoding="utf-8", buffering=1)
        except OSError:
            stream = open(os.devnull, "w", encoding="utf-8")
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _ensure_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "explain":
        return cmd_explain(args)
    if args.command == "ask":
        return cmd_ask(args)
    if args.command == "backtest":
        return cmd_backtest(args)
    if args.command == "watch":
        return cmd_watch(args)
    if args.command == "alert-test":
        return cmd_alert_test(args)
    parser.print_help()
    return 1
