"""Tiny cached HTTP layer built on the standard library.

No third-party runtime dependency is required (requests is optional and not
used). Responses are cached on disk so repeated runs are fast, the report can
be rebuilt offline, and a flaky feed cannot take down an entire run.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class FetchError(RuntimeError):
    pass


@dataclass
class FetchResult:
    body: bytes
    url: str
    from_cache: bool
    fetched_at: float
    stale: bool = False


def cache_dir() -> Path:
    root = Path(os.environ.get("COMMODITY_AGENT_HOME", Path.cwd()))
    p = root / "data" / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


def _read_cache(url: str) -> tuple[bytes, float] | None:
    p = cache_dir() / f"{_key(url)}.json"
    if not p.exists():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return doc["body"].encode("utf-8"), float(doc["fetched_at"])
    except (OSError, ValueError, KeyError):
        return None


def _write_cache(url: str, body: bytes, fetched_at: float) -> None:
    p = cache_dir() / f"{_key(url)}.json"
    tmp = p.with_suffix(".tmp")
    doc = {"url": url, "fetched_at": fetched_at, "body": body.decode("utf-8", "replace")}
    try:
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


def fetch(
    url: str,
    *,
    ttl: int = 900,
    timeout: float = 20.0,
    retries: int = 2,
    refresh: bool = False,
    offline: bool = False,
    headers: dict[str, str] | None = None,
) -> FetchResult:
    """GET a URL with disk caching, exponential backoff and offline fallback."""
    cached = _read_cache(url)
    now = time.time()
    if cached and not refresh:
        body, ts = cached
        if now - ts <= ttl:
            return FetchResult(body, url, True, ts)

    if offline:
        if cached:
            body, ts = cached
            return FetchResult(body, url, True, ts, stale=True)
        raise FetchError(f"offline and no cache for {url}")

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            hdrs = {
                "User-Agent": UA,
                "Accept": "application/json, application/xml, text/xml, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip",
            }
            if headers:
                hdrs.update(headers)
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    body = gzip.decompress(body)
                _write_cache(url, body, now)
                return FetchResult(body, url, False, now)
        except urllib.error.HTTPError as exc:
            last_err = exc
            # 4xx other than 408/429 will not improve on retry.
            if 400 <= exc.code < 500 and exc.code not in (408, 429):
                break
        except Exception as exc:  # noqa: BLE001 - network errors are diverse
            last_err = exc

        if attempt < retries:
            time.sleep((2 ** attempt) * 0.8 + random.random() * 0.4)

    if cached:
        body, ts = cached
        return FetchResult(body, url, True, ts, stale=True)
    raise FetchError(f"GET failed for {url}: {last_err}")
