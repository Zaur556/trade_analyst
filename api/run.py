"""Serverless dashboard and scheduled API for the Commodity Signal Desk.

The root path serves the dashboard. The protected /api/run endpoint runs market/news
analysis and either sends change-only alerts or a scheduled two-hour digest.

Vercel functions have a read-only filesystem and are ephemeral, so:
  * market/news cache and the local state mirror live under /tmp
  * the authoritative alert state lives in Upstash Redis over HTTPS
  * the dashboard HTML is served from source; no generated report is persisted

Environment variables:
  CRON_SECRET                 required; Vercel Cron/external scheduler sends it
  GMAIL_USER                  Gmail sender
  GMAIL_APP_PASSWORD          Google App Password
  ALERT_TO                    recipient (defaults to GMAIL_USER)
  UPSTASH_REDIS_REST_URL      Upstash REST URL (or KV_REST_API_URL)
  UPSTASH_REDIS_REST_TOKEN    Upstash REST token (or KV_REST_API_TOKEN)
  NEWS_DAYS                   optional, default 4
  ACCOUNT_SIZE                optional, default 50000
  RISK_PCT                    optional, default 1.0
"""

from __future__ import annotations

import hmac
import json
import os
import sys
import tempfile
import traceback
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HOME = Path(
    os.environ.get("COMMODITY_AGENT_HOME")
    or (Path(tempfile.gettempdir()) / "commodity-agent")
)
os.environ.setdefault("COMMODITY_AGENT_HOME", str(HOME))
OUT_DIR = HOME / "reports"
STATE_KEY = "commodity:alert_state:v1"

from commodity_agent.alerts import (  # noqa: E402
    AlertConfigError,
    load_config,
    process_alerts,
    send_test_email,
)
from commodity_agent.config import ORDER  # noqa: E402
from commodity_agent.dashboard import DASHBOARD_HTML  # noqa: E402
from commodity_agent.pipeline import run  # noqa: E402


class StateStoreError(RuntimeError):
    pass


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _redis_credentials() -> tuple[str, str] | None:
    url = (
        os.environ.get("UPSTASH_REDIS_REST_URL")
        or os.environ.get("KV_REST_API_URL")
        or ""
    ).strip()
    token = (
        os.environ.get("UPSTASH_REDIS_REST_TOKEN")
        or os.environ.get("KV_REST_API_TOKEN")
        or ""
    ).strip()
    if not url or not token:
        if os.environ.get("VERCEL"):
            raise StateStoreError(
                "Persistent alert state is not configured. Add Upstash Redis from "
                "the Vercel Marketplace and set UPSTASH_REDIS_REST_URL and "
                "UPSTASH_REDIS_REST_TOKEN (or KV_REST_API_URL / KV_REST_API_TOKEN)."
            )
        return None
    return url.rstrip("/"), token


def _redis(path: str, method: str = "GET", body: bytes | None = None):
    creds = _redis_credentials()
    if creds is None:
        return {}
    base, token = creds
    request = Request(
        base + path,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=12) as response:
            raw = response.read()
    except Exception as exc:  # noqa: BLE001 - network and HTTP errors alike
        raise StateStoreError(f"Upstash request failed: {type(exc).__name__}") from exc
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise StateStoreError("Upstash returned a non-JSON response.") from exc


def load_remote_state() -> dict | None:
    if _redis_credentials() is None:
        return None
    data = _redis(f"/get/{quote(STATE_KEY, safe='')}")
    raw = data.get("result") if isinstance(data, dict) else None
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise StateStoreError("Stored alert state is not valid JSON.") from exc
    return parsed if isinstance(parsed, dict) else None


def save_remote_state(document: dict) -> None:
    if _redis_credentials() is None:
        return
    value = json.dumps(document, separators=(",", ":"))
    body = json.dumps([["SET", STATE_KEY, value]]).encode("utf-8")
    _redis("/pipeline", method="POST", body=body)


def _hydrate_state_file() -> None:
    state_file = OUT_DIR / "alert_state.json"
    remote = load_remote_state()
    if remote is None:
        if state_file.exists():
            state_file.unlink()
        return
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(remote), encoding="utf-8")


def _persist_state_file() -> None:
    state_file = OUT_DIR / "alert_state.json"
    if not state_file.exists():
        return
    try:
        document = json.loads(state_file.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise StateStoreError("Local alert state is not valid JSON.") from exc
    save_remote_state(document)


def _signal_payload(result) -> list[dict]:
    payload = []
    for signal in result.signals:
        plan = signal.plan
        payload.append(
            {
                "key": signal.asset.key,
                "name": signal.asset.name,
                "unit": signal.asset.unit,
                "price": round(signal.price, 4),
                "change_pct": round(signal.change_pct, 3),
                "quote_time": (
                    signal.series.quote_time.isoformat()
                    if signal.series.quote_time is not None
                    else None
                ),
                "quote_source": signal.series.source,
                "quote_stale": bool(signal.series.stale),
                "quote_note": ("Lighter underlying index/mark quote; Yahoo Finance supplies historical futures candles. Verify against your broker before trading." if signal.series.source.startswith("Lighter") else "Yahoo Finance quote may be delayed; verify against your broker before trading."),
                "score": round(signal.score, 1),
                "action": signal.action,
                "confidence": round(signal.confidence, 1),
                "confidence_label": signal.confidence_label,
                "factors": [
                    {
                        "key": factor.key,
                        "label": factor.label,
                        "score": round(factor.score, 3),
                        "weight": round(factor.weight, 3),
                        "points": round(factor.points, 2),
                        "detail": factor.detail,
                    }
                    for factor in signal.factors
                ],
                "drivers": signal.drivers,
                "risks": signal.risks,
                "metrics": signal.metrics,
                "plan": {
                    "direction": plan.direction,
                    "entry": plan.entry,
                    "stop": plan.stop,
                    "target": plan.target,
                    "stop_distance": plan.stop_distance,
                    "rr": plan.rr,
                    "risk_per_contract": plan.risk_per_contract,
                    "contracts": plan.contracts,
                    "notional_per_contract": plan.notional_per_contract,
                    "min_account_one_lot": plan.min_account_one_lot,
                    "etf_notional": plan.etf_notional,
                    "trigger_buy": plan.trigger_buy,
                    "trigger_sell": plan.trigger_sell,
                    "atr": plan.atr,
                },
            }
        )
    return payload


def run_job(
    preview: bool = False, test_email: bool = False, digest: bool = False
) -> dict:
    """Run one analysis cycle; digest sends a scheduled book email every cycle."""
    if test_email:
        config = load_config()
        send_test_email(config)
        return {"ok": True, "test_email": "sent", "to": config.to}

    if not preview:
        config = load_config()
        _hydrate_state_file()

    result = run(
        list(ORDER),
        days=_env_float("NEWS_DAYS", 4.0),
        account_size=_env_float("ACCOUNT_SIZE", 50_000.0),
        risk_pct=_env_float("RISK_PCT", 1.0),
        no_backtest=True,
        # Refresh market and macro data at most once per minute.
        market_ttl=60,
        news_ttl=600,
    )

    if preview:
        return {
            "ok": True,
            "preview": True,
            "generated_at": result.generated_at.isoformat(),
            "as_of": result.as_of,
            "news_count": result.news_count,
            "signals": _signal_payload(result),
            "warnings": result.warnings[:5],
        }

    outcome = process_alerts(
        result, OUT_DIR, config=config, always_send=digest
    )
    _persist_state_file()

    return {
        "ok": True,
        "generated_at": result.generated_at.isoformat(),
        "as_of": result.as_of,
        "news_count": result.news_count,
        "alert_status": outcome.status,
        "alert_message": outcome.message,
        "email_mode": "two_hour_digest" if digest else "change_only",
        "changes": [
            {"asset": change.asset_name, "kind": change.kind, "headline": change.headline}
            for change in outcome.changes
        ],
        "signals": _signal_payload(result),
        "warnings": result.warnings[:5],
    }


class handler(BaseHTTPRequestHandler):
    """Vercel Python entrypoint: GET/POST /api/run."""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._serve()

    def do_POST(self) -> None:  # noqa: N802
        self._serve()

    def _serve(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/dashboard", "/dashboard/"):
                body = DASHBOARD_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            secret = os.environ.get("CRON_SECRET", "").strip()
            if not secret:
                self._json(503, {"ok": False, "error": "CRON_SECRET is not configured."})
                return

            query = parse_qs(parsed.query)
            supplied = self.headers.get("Authorization", "")
            query_token = (query.get("token") or [""])[0]
            if not (
                hmac.compare_digest(supplied, f"Bearer {secret}")
                or (query_token and hmac.compare_digest(query_token, secret))
            ):
                self._json(401, {"ok": False, "error": "Unauthorized."})
                return

            preview = (query.get("preview") or ["0"])[0].lower() in ("1", "true", "yes")
            test_email = (query.get("test_email") or ["0"])[0].lower() in ("1", "true", "yes")
            digest = (query.get("digest") or ["0"])[0].lower() in ("1", "true", "yes")
            payload = run_job(preview=preview, test_email=test_email, digest=digest)
            self._json(200, payload)
        except AlertConfigError as exc:
            self._json(503, {"ok": False, "error": str(exc)})
        except StateStoreError as exc:
            self._json(503, {"ok": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - show a useful Vercel log
            traceback.print_exc()
            self._json(
                500,
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            )

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
