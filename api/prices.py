"""Public, read-only diagnostics for the Lighter commodity price feed.

This route exposes only market prices already public on Lighter. It requires no
CRON_SECRET and never exposes account, trading, or alert credentials.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from datetime import timezone

from commodity_agent.config import ASSETS, ORDER
from commodity_agent.lighter import MARKETS, fetch_quotes
from commodity_agent.market import fetch_series


def _price_check(key: str, quote) -> dict:
    asset = ASSETS[key]
    item = {
        "asset": key,
        "expected_symbols": list(MARKETS[key]),
        "lighter_symbol": quote.market_symbol,
        "lighter_price": quote.price,
        "price_type": quote.price_type,
        "fetched_at": quote.fetched_at.astimezone(timezone.utc).isoformat(),
        "stale": quote.stale,
        "yahoo_reference": None,
        "difference_pct": None,
        "tolerance_pct": 35.0 if key == "gas" else 20.0,
        "would_be_accepted": None,
        "validation_note": "Yahoo cross-check not available",
    }
    try:
        series = fetch_series(asset.symbol, ttl=60)
        yahoo = series.current_price if series.current_price is not None else series.last.close
        difference = abs(quote.price / yahoo - 1.0) * 100.0 if yahoo else None
        tolerance = item["tolerance_pct"]
        accepted = difference is not None and difference <= tolerance
        item.update({
            "yahoo_reference": yahoo,
            "difference_pct": round(difference, 3) if difference is not None else None,
            "would_be_accepted": accepted,
            "validation_note": "Within configured price tolerance" if accepted else "Outside configured price tolerance; main pipeline falls back to Yahoo Finance",
        })
    except Exception as exc:  # noqa: BLE001 - diagnostic must still show the Lighter response
        item["validation_note"] = f"Yahoo comparison unavailable: {type(exc).__name__}"
    return item


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - Vercel Python function API
        try:
            quotes = fetch_quotes(ttl=0, refresh=True)
            markets = [_price_check(key, quotes[key]) for key in ORDER if key in quotes]
            missing = [key for key in ORDER if key not in quotes]
            payload = {
                "ok": bool(quotes),
                "provider": "Lighter public REST API",
                "endpoint": "https://mainnet.zklighter.elliot.ai/api/v1/orderBookDetails",
                "fetched_at": max((q.fetched_at for q in quotes.values())).astimezone(timezone.utc).isoformat(),
                "markets_found": len(quotes),
                "markets_expected": len(ORDER),
                "missing_markets": missing,
                "markets": markets,
                "note": "Lighter prices are reference perpetual-market prices, not exchange-traded futures execution quotes.",
            }
            status = 200 if quotes else 502
        except Exception as exc:  # noqa: BLE001
            payload = {
                "ok": False,
                "provider": "Lighter public REST API",
                "error": f"{type(exc).__name__}: {str(exc)[:240]}",
                "markets_found": 0,
                "markets_expected": len(ORDER),
                "markets": [],
            }
            status = 502

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
