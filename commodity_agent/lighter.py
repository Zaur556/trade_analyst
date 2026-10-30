"""Public Lighter market-data adapter for live commodity reference quotes."""
from __future__ import annotations
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from . import net

DEFAULT_BASE_URL = "https://mainnet.zklighter.elliot.ai"
MARKETS: dict[str, tuple[str, ...]] = {
    "brent": ("BRENTOIL", "BRENT"), "wti": ("WTI",),
    "gold": ("XAU",), "silver": ("XAG",), "gas": ("NATGAS", "NATURALGAS"),
}

@dataclass(frozen=True)
class LighterQuote:
    asset_key: str
    market_symbol: str
    price: float
    price_type: str
    fetched_at: datetime
    stale: bool = False

def _number(value: Any) -> float | None:
    try: number = float(value)
    except (TypeError, ValueError): return None
    return number if number > 0 and number == number and number != float("inf") else None

def _market_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list): return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict): return []
    for key in ("order_book_details", "order_books", "markets", "data"):
        value = payload.get(key)
        if isinstance(value, list): return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            nested = _market_rows(value)
            if nested: return nested
    return []

def _symbol(row: dict[str, Any]) -> str:
    raw = str(row.get("symbol") or row.get("market_symbol") or row.get("name") or "")
    return "".join(char for char in raw.upper() if char.isalnum())

def parse_quotes(payload: bytes | str | dict[str, Any] | list[Any], fetched_at: datetime | None = None) -> dict[str, LighterQuote]:
    if isinstance(payload, (bytes, str)): payload = json.loads(payload)
    rows = _market_rows(payload)
    fetched_at = fetched_at or datetime.now(timezone.utc)
    quotes: dict[str, LighterQuote] = {}
    for asset_key, aliases in MARKETS.items():
        candidates = []
        for row in rows:
            symbol = _symbol(row)
            if not any(symbol == alias or symbol.startswith(alias + suffix) for alias in aliases for suffix in ("", "USD", "USDC", "PERP")):
                continue
            status = str(row.get("status") or "").strip().lower()
            if status and status not in ("active", "1", "true"):
                continue
            price, price_type = None, ""
            for field, label in (("index_price", "index"), ("oracle_price", "oracle"),
                                 ("mark_price", "mark"), ("last_trade_price", "last trade"),
                                 ("last_price", "last trade"), ("price", "market")):
                price = _number(row.get(field))
                if price is not None:
                    price_type = label
                    break
            if price is not None: candidates.append((symbol, price, price_type))
        if candidates:
            symbol, price, price_type = candidates[0]
            quotes[asset_key] = LighterQuote(asset_key, symbol, price, price_type, fetched_at)
    return quotes

def fetch_quotes(*, ttl: int = 60, refresh: bool = False, offline: bool = False) -> dict[str, LighterQuote]:
    """Fetch public quotes; no API key or trading permission is required."""
    base_url = os.environ.get("LIGHTER_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    result = net.fetch(base_url + "/api/v1/orderBookDetails", ttl=ttl, timeout=12,
                       retries=1, refresh=refresh, offline=offline)
    quotes = parse_quotes(result.body, datetime.fromtimestamp(result.fetched_at, tz=timezone.utc))
    if not quotes: raise ValueError("Lighter orderBookDetails returned no recognized active commodity prices")
    if result.stale:
        quotes = {key: LighterQuote(q.asset_key, q.market_symbol, q.price, q.price_type, q.fetched_at, True)
                  for key, q in quotes.items()}
    return quotes
