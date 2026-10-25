"""Gmail change alerts.

The watcher evaluates signals every few minutes but should not email on every
cycle. This module compares the current run with the last *alerted* baseline and
sends only when something material changes:

* action flip (BUY -> HOLD, SELL -> BUY, ...)
* confidence shift of 15 percentage points or more
* price crossing a previously published stop, target or flat-market trigger

State lives in ``reports/alert_state.json``. It is updated only on baseline or
after a successful send, so a failed email is retried on the next cycle.
"""

from __future__ import annotations

import html as html_lib
import json
import os
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from .pipeline import RunResult

DEFAULT_ENV_FILE = Path.home() / ".commodity-agent" / "gmail.env"
LOCAL_ENV_FILE = Path(__file__).resolve().parent.parent / "gmail.env"
STATE_NAME = "alert_state.json"
CONFIDENCE_ALERT_POINTS = 15.0


class AlertConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class AlertConfig:
    user: str
    password: str
    to: str
    host: str = "smtp.gmail.com"
    port: int = 465

    def describe(self) -> str:
        return f"{self.user} -> {self.to} via {self.host}:{self.port}"


@dataclass
class Change:
    asset_key: str
    asset_name: str
    kind: str            # action | confidence | stop | target | trigger
    headline: str
    detail: str


@dataclass
class AlertOutcome:
    status: str          # baseline | no-change | sent
    changes: list[Change]
    message: str


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines without overriding real environment variables."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key or ""):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _env_paths(env_file: str | Path | None) -> list[Path]:
    if env_file:
        return [Path(env_file)]
    explicit = os.environ.get("GMAIL_ENV_FILE")
    if explicit:
        return [Path(explicit)]
    # Prefer a location outside OneDrive/git; fall back to the project root.
    return [DEFAULT_ENV_FILE, LOCAL_ENV_FILE]


def load_config(to: str | None = None, env_file: str | Path | None = None) -> AlertConfig:
    paths = _env_paths(env_file)
    for path in paths:
        _load_env_file(path)

    user = os.environ.get("GMAIL_USER", "").strip()
    password = "".join(os.environ.get("GMAIL_APP_PASSWORD", "").split())
    dest = (to or os.environ.get("ALERT_TO") or user).strip()

    if not user or not password:
        where = " or ".join(str(p) for p in paths)
        raise AlertConfigError(
            "Gmail is not configured. Create a gmail.env with GMAIL_USER and "
            "GMAIL_APP_PASSWORD (see gmail.env.example), or set those environment "
            f"variables. Preferred path: {where}. Use a Google App Password, not your "
            "normal account password."
        )
    if "@" not in user:
        raise AlertConfigError(f"GMAIL_USER does not look like an email address: {user!r}")
    if not dest or "@" not in dest:
        raise AlertConfigError(
            "No valid recipient. Set ALERT_TO (or pass --to / --email-to)."
        )

    host = os.environ.get("GMAIL_SMTP_HOST", "smtp.gmail.com").strip() or "smtp.gmail.com"
    try:
        port = int(os.environ.get("GMAIL_SMTP_PORT", "465"))
    except ValueError:
        port = 465

    return AlertConfig(user=user, password=password, to=dest, host=host, port=port)


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------

def _num(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _price(value: float | None) -> str:
    if value is None:
        return "-"
    if abs(value) >= 1000:
        return f"{value:,.1f}"
    if abs(value) >= 10:
        return f"{value:,.2f}"
    return f"{value:,.3f}"


def _plan_text(signal) -> str:
    p = signal.plan
    if p.direction == "FLAT":
        return f"flat; buy > {_price(p.trigger_buy)}, sell < {_price(p.trigger_sell)}"
    return (
        f"{p.direction} {_price(p.entry)}, stop {_price(p.stop)}, "
        f"target {_price(p.target)}, R:R {p.rr:.2f}"
    )


def detect_changes(previous: dict, result: RunResult) -> list[Change]:
    prev_assets = previous.get("assets") or {}
    changes: list[Change] = []

    for signal in result.signals:
        old = prev_assets.get(signal.asset.key)
        if not isinstance(old, dict):
            continue
        old_action = str(old.get("action") or "")
        old_score = _num(old.get("score"))
        old_conf = _num(old.get("confidence"))
        old_price = _num(old.get("price"))
        old_stop = _num(old.get("stop"))
        old_target = _num(old.get("target"))
        old_direction = str(old.get("direction") or "")

        if old_action and old_action != signal.action:
            detail = (
                f"Score {old_score if old_score is not None else 0:+.1f} -> "
                f"{signal.score:+.1f}; confidence "
                f"{old_conf if old_conf is not None else 0:.0f}% -> "
                f"{signal.confidence:.0f}%; price {_price(old_price)} -> {_price(signal.price)}."
            )
            changes.append(
                Change(
                    signal.asset.key,
                    signal.asset.name,
                    "action",
                    f"{signal.asset.name}: {old_action} -> {signal.action}",
                    detail,
                )
            )
            continue

        if old_conf is not None and abs(signal.confidence - old_conf) >= CONFIDENCE_ALERT_POINTS:
            changes.append(
                Change(
                    signal.asset.key,
                    signal.asset.name,
                    "confidence",
                    f"{signal.asset.name}: confidence {old_conf:.0f}% -> {signal.confidence:.0f}%",
                    f"Same action ({signal.action}), but conviction moved "
                    f"{signal.confidence - old_conf:+.0f} points. Score {signal.score:+.1f}.",
                )
            )

        # Price trading through a previously published level.
        if old_price is not None and old_action in ("BUY", "STRONG BUY", "SELL", "STRONG SELL"):
            if old_direction == "LONG" or old_action in ("BUY", "STRONG BUY"):
                if old_stop is not None and old_stop < old_price and signal.price <= old_stop:
                    changes.append(
                        Change(
                            signal.asset.key, signal.asset.name, "stop",
                            f"{signal.asset.name}: stop hit at {_price(old_stop)}",
                            f"Price fell from {_price(old_price)} to {_price(signal.price)}, "
                            f"through the long stop at {_price(old_stop)}.",
                        )
                    )
                elif old_target is not None and old_target > old_price and signal.price >= old_target:
                    changes.append(
                        Change(
                            signal.asset.key, signal.asset.name, "target",
                            f"{signal.asset.name}: target hit at {_price(old_target)}",
                            f"Price rose from {_price(old_price)} to {_price(signal.price)}, "
                            f"through the long target at {_price(old_target)}.",
                        )
                    )
            else:
                if old_stop is not None and old_stop > old_price and signal.price >= old_stop:
                    changes.append(
                        Change(
                            signal.asset.key, signal.asset.name, "stop",
                            f"{signal.asset.name}: stop hit at {_price(old_stop)}",
                            f"Price rose from {_price(old_price)} to {_price(signal.price)}, "
                            f"through the short stop at {_price(old_stop)}.",
                        )
                    )
                elif old_target is not None and old_target < old_price and signal.price <= old_target:
                    changes.append(
                        Change(
                            signal.asset.key, signal.asset.name, "target",
                            f"{signal.asset.name}: target hit at {_price(old_target)}",
                            f"Price fell from {_price(old_price)} to {_price(signal.price)}, "
                            f"through the short target at {_price(old_target)}.",
                        )
                    )

        # Flat-market breakout triggers.
        if old_action == "HOLD" and old_price is not None:
            trigger_buy = _num(old.get("trigger_buy"))
            trigger_sell = _num(old.get("trigger_sell"))
            if trigger_buy is not None and old_price < trigger_buy <= signal.price:
                changes.append(
                    Change(
                        signal.asset.key, signal.asset.name, "trigger",
                        f"{signal.asset.name}: buy trigger hit at {_price(trigger_buy)}",
                        f"Price rose from {_price(old_price)} through {_price(trigger_buy)}. "
                        f"Score is now {signal.score:+.1f} ({signal.action}).",
                    )
                )
            elif trigger_sell is not None and old_price > trigger_sell >= signal.price:
                changes.append(
                    Change(
                        signal.asset.key, signal.asset.name, "trigger",
                        f"{signal.asset.name}: sell trigger hit at {_price(trigger_sell)}",
                        f"Price fell from {_price(old_price)} through {_price(trigger_sell)}. "
                        f"Score is now {signal.score:+.1f} ({signal.action}).",
                    )
                )

    return changes


# ---------------------------------------------------------------------------
# Email content
# ---------------------------------------------------------------------------

def _action_color(action: str) -> str:
    if "BUY" in action:
        return "#047857"
    if "SELL" in action:
        return "#b91c1c"
    return "#92400e"


def build_message(
    result: RunResult, changes: list[Change], out_dir: Path
) -> tuple[str, str, str]:
    stamp = f"{result.generated_at:%d %b %Y %H:%M %Z}"

    if len(changes) == 1:
        subject = f"[Commodity Signal] {changes[0].headline}"
    else:
        names = ", ".join(dict.fromkeys(c.asset_name for c in changes))
        subject = f"[Commodity Signal] {len(changes)} updates: {names}"
    subject = subject.replace("\n", " ").replace("\r", " ")[:150]

    text_lines = [
        "Commodity Signal Desk - signal changes",
        stamp,
        "",
        "CHANGES",
    ]
    for change in changes:
        text_lines.append(f"* {change.headline}")
        text_lines.append(f"  {change.detail}")
    text_lines += ["", "CURRENT BOOK"]
    for signal in result.signals:
        text_lines.append(
            f"{signal.asset.name}: {signal.action} {signal.score:+.0f}/100, "
            f"confidence {signal.confidence:.0f}% | {_plan_text(signal)}"
        )
    text_lines.append("")
    dashboard = out_dir / "latest.html"
    if dashboard.exists():
        text_lines.append(f"Dashboard: {dashboard}")
        text_lines.append("")
    text_lines.append("Decision support only, not investment advice. Futures are leveraged.")
    text_body = "\n".join(text_lines)

    change_html = []
    colors = {
        "action": "#92400e",
        "confidence": "#1d4ed8",
        "stop": "#b91c1c",
        "target": "#047857",
        "trigger": "#7c3aed",
    }
    for change in changes:
        color = colors.get(change.kind, "#92400e")
        change_html.append(
            f'<li style="margin:0 0 12px 0;padding-left:12px;border-left:3px solid {color};">'
            f'<strong style="color:#111827;">{html_lib.escape(change.headline)}</strong><br>'
            f'<span style="color:#4b5563;font-size:13px;">{html_lib.escape(change.detail)}</span>'
            f"</li>"
        )

    book_rows = []
    for signal in result.signals:
        book_rows.append(
            "<tr>"
            f'<td style="padding:7px 8px;border-bottom:1px solid #e5e7eb;">{html_lib.escape(signal.asset.name)}</td>'
            f'<td style="padding:7px 8px;border-bottom:1px solid #e5e7eb;color:{_action_color(signal.action)};font-weight:600;">{html_lib.escape(signal.action)}</td>'
            f'<td style="padding:7px 8px;border-bottom:1px solid #e5e7eb;text-align:right;">{signal.score:+.0f}</td>'
            f'<td style="padding:7px 8px;border-bottom:1px solid #e5e7eb;text-align:right;">{signal.confidence:.0f}%</td>'
            f'<td style="padding:7px 8px;border-bottom:1px solid #e5e7eb;font-size:12px;color:#4b5563;">{html_lib.escape(_plan_text(signal))}</td>'
            "</tr>"
        )

    dashboard = out_dir / "latest.html"
    dashboard_html = (
        f'<p style="margin:18px 0 0;font-size:12px;color:#6b7280;">'
        f'Dashboard: <span style="font-family:Consolas,monospace;">'
        f"{html_lib.escape(str(dashboard))}</span></p>"
        if dashboard.exists()
        else ""
    )

    html_body = f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#f3f4f6;font-family:Segoe UI,Arial,sans-serif;color:#111827;">
<div style="max-width:680px;margin:0 auto;padding:24px 16px;">
  <div style="background:#0b1018;color:#f9fafb;padding:18px 20px;border-radius:10px 10px 0 0;">
    <div style="font-size:11px;letter-spacing:1.4px;color:#e8b44a;">COMMODITY SIGNAL DESK</div>
    <h1 style="margin:4px 0 0;font-size:21px;">Signal change</h1>
    <div style="margin-top:6px;color:#9ca3af;font-size:12px;">{html_lib.escape(stamp)}</div>
  </div>
  <div style="background:#ffffff;padding:20px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 10px 10px;">
    <ul style="list-style:none;margin:0 0 20px 0;padding:0;">{''.join(change_html)}</ul>
    <h2 style="font-size:13px;letter-spacing:.9px;text-transform:uppercase;color:#6b7280;margin:0 0 8px;">Current book</h2>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr>
        <th style="text-align:left;padding:6px 8px;border-bottom:1px solid #d1d5db;color:#6b7280;font-size:11px;">Asset</th>
        <th style="text-align:left;padding:6px 8px;border-bottom:1px solid #d1d5db;color:#6b7280;font-size:11px;">Signal</th>
        <th style="text-align:right;padding:6px 8px;border-bottom:1px solid #d1d5db;color:#6b7280;font-size:11px;">Score</th>
        <th style="text-align:right;padding:6px 8px;border-bottom:1px solid #d1d5db;color:#6b7280;font-size:11px;">Conf.</th>
        <th style="text-align:left;padding:6px 8px;border-bottom:1px solid #d1d5db;color:#6b7280;font-size:11px;">Plan</th>
      </tr></thead>
      <tbody>{''.join(book_rows)}</tbody>
    </table>
    {dashboard_html}
    <p style="margin:10px 0 0;font-size:11px;color:#9ca3af;line-height:1.5;">
      Decision support only, not investment advice. Futures are leveraged; verify prices with your broker.
    </p>
  </div>
</div>
</body></html>"""
    return subject, text_body, html_body


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def send_email(config: AlertConfig, subject: str, text_body: str, html_body: str) -> None:
    message = EmailMessage()
    message["From"] = config.user
    message["To"] = config.to
    message["Subject"] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    try:
        context = ssl.create_default_context()
        if config.port == 465:
            with smtplib.SMTP_SSL(
                config.host, config.port, timeout=30, context=context
            ) as smtp:
                smtp.login(config.user, config.password)
                smtp.send_message(message)
        else:
            # Port 587 (and 25) use STARTTLS rather than implicit TLS.
            with smtplib.SMTP(config.host, config.port, timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                smtp.login(config.user, config.password)
                smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise AlertConfigError(
            "Gmail rejected the login. Use a 16-character Google App Password "
            "(not your normal password) and make sure 2-Step Verification is on."
        ) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise AlertConfigError(f"Gmail refused the recipient {config.to!r}.") from exc
    except (OSError, smtplib.SMTPException) as exc:
        raise RuntimeError(
            f"Could not send via {config.host}:{config.port}: {exc}. "
            "Check the network/firewall, or set GMAIL_SMTP_PORT=587."
        ) from exc


def send_test_email(config: AlertConfig, sender=send_email) -> None:
    subject = "[Commodity Signal] Gmail alerts are working"
    text_body = (
        "Commodity Signal Desk - Gmail test\n\n"
        "If you can read this, change-only alerts are configured correctly.\n\n"
        "You will receive an email only when an action flips, confidence moves by "
        "15+ points, or price crosses a published stop/target/trigger.\n\n"
        "Decision support only, not investment advice."
    )
    html_body = """<!doctype html>
<html><body style="font-family:Segoe UI,Arial,sans-serif;color:#111827;">
<div style="max-width:620px;margin:0 auto;padding:24px;">
  <div style="font-size:11px;letter-spacing:1.4px;color:#b45309;">COMMODITY SIGNAL DESK</div>
  <h1 style="font-size:22px;margin:6px 0 10px;">Gmail alerts are working</h1>
  <p style="color:#374151;line-height:1.55;">You will receive an email only when an action flips,
  confidence moves by 15+ points, or price crosses a published stop, target or trigger.</p>
  <p style="color:#6b7280;font-size:12px;">Decision support only, not investment advice.</p>
</div>
</body></html>"""
    sender(config, subject, text_body, html_body)


# ---------------------------------------------------------------------------
# State and orchestration
# ---------------------------------------------------------------------------

def _snapshot(result: RunResult) -> dict:
    assets: dict[str, dict] = {}
    for signal in result.signals:
        plan = signal.plan
        assets[signal.asset.key] = {
            "price": round(signal.price, 6),
            "score": round(signal.score, 3),
            "action": signal.action,
            "confidence": round(signal.confidence, 3),
            "direction": plan.direction,
            "stop": round(plan.stop, 6) if plan.stop is not None else None,
            "target": round(plan.target, 6) if plan.target is not None else None,
            "trigger_buy": round(plan.trigger_buy, 6),
            "trigger_sell": round(plan.trigger_sell, 6),
        }
    return {
        "schema": "commodity-signal-desk/alerts/1",
        "updated_at": result.generated_at.isoformat(),
        "assets": assets,
    }


def load_state(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    tmp.replace(path)


def save_state(path: Path, result: RunResult) -> None:
    _write_state(path, _snapshot(result))


def process_alerts(
    result: RunResult,
    out_dir: Path,
    *,
    config: AlertConfig,
    sender=send_email,
) -> AlertOutcome:
    out_dir = Path(out_dir)
    state_file = out_dir / STATE_NAME
    previous = load_state(state_file)
    previous_assets = previous.get("assets") or {}

    if not previous_assets:
        save_state(state_file, result)
        return AlertOutcome(
            "baseline", [], "Baseline saved; no email sent for the first observation."
        )

    current_keys = {s.asset.key for s in result.signals}
    new_keys = current_keys - set(previous_assets)
    if new_keys:
        # Adding an instrument should not fire an alert for it; arm a baseline
        # while preserving the existing baselines for the other instruments.
        snapshot = _snapshot(result)
        for key in new_keys:
            if key in snapshot["assets"]:
                previous_assets[key] = snapshot["assets"][key]
        previous["assets"] = previous_assets
        previous["updated_at"] = result.generated_at.isoformat()
        previous.setdefault("schema", "commodity-signal-desk/alerts/1")
        _write_state(state_file, previous)
        names = ", ".join(sorted(new_keys))
        return AlertOutcome(
            "baseline", [], f"Baseline added for new instrument(s): {names}."
        )

    changes = detect_changes(previous, result)
    if not changes:
        # Deliberately do not move the baseline: confidence thresholds and
        # breakout crossings must be measured from the last alert, not from the
        # previous five-minute poll.
        return AlertOutcome("no-change", [], "No material signal changes.")

    subject, text_body, html_body = build_message(result, changes, out_dir)
    sender(config, subject, text_body, html_body)
    save_state(state_file, result)
    return AlertOutcome(
        "sent", changes, f"Sent {len(changes)} change(s) to {config.to}."
    )


def format_outcome(outcome: AlertOutcome) -> str:
    if outcome.status == "sent":
        return outcome.message + " " + "; ".join(c.headline for c in outcome.changes)
    return outcome.message
