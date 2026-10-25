"""News ingestion and commodity-aware sentiment.

Sources are free RSS feeds plus Google News queries. Each headline is scored
against a rule set of market events (OPEC supply changes, Fed policy, dollar
moves, inventories, weather, geopolitics) and a small financial lexicon.

The output is deliberately explainable: every score carries the topics that
triggered it, which the report surfaces as "drivers".
"""

from __future__ import annotations

import html as html_lib
import re
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from . import net
from .config import (
    FEEDS,
    GOOGLE_QUERIES,
    NEWS_HALF_LIFE_HOURS,
    NEWS_MAX_AGE_HOURS,
    NEWS_MIN_RELEVANCE,
)

ASSET_KEYS = ("brent", "wti", "gold", "silver", "gas")

# ---------------------------------------------------------------------------
# Relevance
# ---------------------------------------------------------------------------

KEYWORDS: dict[str, dict[str, float]] = {
    "brent": {
        "brent": 5, "crude": 3, "oil": 2, "opec": 3, "wti": 2, "petroleum": 2,
        "barrel": 2, "refinery": 2, "refineries": 2, "diesel": 2, "gasoline": 2,
        "saudi": 2, "iran": 1.5, "russia": 1, "hormuz": 3, "tanker": 1.5,
        "eia": 1, "inventories": 1.5, "inventory": 1.5, "sanctions": 1.5,
        "supply": 1, "demand": 1, "opec+": 3,
    },
    "wti": {
        "wti": 5, "west texas": 5, "cushing": 4, "permian": 3, "crude": 3,
        "oil": 2, "opec": 2.5, "eia": 2, "inventories": 2, "inventory": 2,
        "refinery": 2, "barrel": 2, "sanctions": 1.5, "hormuz": 2,
        "supply": 1, "demand": 1, "opec+": 2.5,
    },
    "gold": {
        "gold": 5, "bullion": 5, "xau": 5, "precious metal": 4, "precious metals": 4,
        "comex": 2, "central bank": 1.5, "fed": 2, "fomc": 2, "rate cut": 2.5,
        "rate hike": 2.5, "inflation": 1.5, "dollar": 1, "safe haven": 2,
        "safe-haven": 2, "etf": 1, "real yield": 2, "treasury": 1,
    },
    "silver": {
        "silver": 5, "xag": 5, "precious metal": 4, "precious metals": 4,
        "industrial metal": 3, "solar": 2, "photovoltaic": 2,
        "silver institute": 3, "deficit": 1.5, "gold-silver": 3,
        "gold/silver": 3, "electrification": 1.5,
    },
    "gas": {
        "natural gas": 5, "nat gas": 5, "henry hub": 5, "lng": 3, "mmbtu": 4,
        "heating": 1.5, "cooling": 1.5, "storage": 1.5, "eia": 1,
        "pipeline": 1.5, "power burn": 3, "freeze": 1, "winter": 1,
        "feedgas": 2.5, "liquefied natural gas": 4,
    },
}

MENTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "brent": re.compile(
        r"\b(brent|crude|oil|wti|opec|barrels?|petroleum|refiner(?:y|ies)|gasoline|diesel)\b",
        re.I,
    ),
    "wti": re.compile(
        r"\b(wti|west texas|permian|cushing|crude|oil|opec)\b",
        re.I,
    ),
    "gold": re.compile(r"\b(gold|bullion|xau|precious metals?)\b", re.I),
    "silver": re.compile(r"\b(silver|xag)\b", re.I),
    "gas": re.compile(
        r"\b(natural gas|nat gas|henry hub|lng|mmbtu|gas futures|gas storage|"
        r"gas demand|gas supply|feedgas)\b",
        re.I,
    ),
}


def relevance(text: str, asset: str, title_bonus: bool = False) -> float:
    low = text.lower()
    score = 0.0
    for kw, weight in KEYWORDS[asset].items():
        if " " in kw or "-" in kw or "/" in kw:
            hit = kw in low
        else:
            hit = re.search(rf"\b{re.escape(kw)}\b", low) is not None
        if hit:
            score += weight
    if title_bonus:
        score *= 1.5
    return score


# ---------------------------------------------------------------------------
# Event rules: (regex, impacts, topic)
# ---------------------------------------------------------------------------

RULES: list[tuple[re.Pattern[str], dict[str, float], str]] = []


def _rule(pattern: str, impacts: dict[str, float], topic: str) -> None:
    RULES.append((re.compile(pattern, re.I), impacts, topic))


# Crude supply and policy
_rule(r"\bopec\+?\b[^.]{0,70}\b(cut|cuts|cutting|reduce|reduction|trim|lower)\b",
      {"brent": 0.60, "gas": 0.10}, "OPEC+ supply cut")
_rule(r"\bopec\+?\b[^.]{0,70}\b(hike|hikes|raise|raising|increase|boost|unwind|add|adding)\b",
      {"brent": -0.60, "gas": -0.10}, "OPEC+ supply increase")
_rule(r"\b(iran|israel|strait of hormuz|red sea|houthi|hezbollah|tanker attack|"
      r"military strike|air ?strike|attacks?|shelling|missile|war|conflict|blockade)\b",
      {"brent": 0.45, "gold": 0.30, "silver": 0.12, "gas": 0.15}, "Geopolitics")
_rule(r"\b(ceasefire|cease-fire|peace deal|peace talks|de-escalat|diplomatic breakthrough)\b",
      {"brent": -0.35, "gold": -0.20, "silver": -0.08, "gas": -0.10}, "De-escalation")
_rule(r"\b(lift|lifts|lifted|ease|eases|eased|waive|waives|waived)\b[^.]{0,25}\bsanctions?\b",
      {"brent": -0.30, "gold": -0.10}, "Sanctions relief")
_rule(r"\bsanctions?\b|\bembargo\b|\bexport ban\b|\bprice cap\b",
      {"brent": 0.30, "gold": 0.12}, "Sanctions")
_rule(r"\b(spr|strategic petroleum reserve)\b[^.]{0,40}\b(release|drawdown|draw)\b",
      {"brent": -0.25}, "SPR release")
_rule(r"\b(spr|strategic petroleum reserve)\b[^.]{0,40}\b(refill|replenish|buy)\b",
      {"brent": 0.20}, "SPR refill")
_rule(r"(?=.*\b(?:crude|oil|wti|brent|refinery|gasoline|diesel)\b)"
      r"(?=.*\b(inventor(?:y|ies)|stockpiles?|crude stocks?)\b[^.]{0,50}\b(build|builds|built|"
      r"rise|rises|increase|surge|unexpected)\b)",
      {"brent": -0.40}, "Inventory build")
_rule(r"(?=.*\b(?:crude|oil|wti|brent|refinery|gasoline|diesel)\b)"
      r"(?=.*\b(inventor(?:y|ies)|stockpiles?|crude stocks?)\b[^.]{0,50}\b(draw|draws|drew|fall|"
      r"falls|drop|drops|decline|shortfall)\b)",
      {"brent": 0.40}, "Inventory draw")
_rule(r"\b(refinery|refineries|pipeline|terminal)\b[^.]{0,45}\b(outage|shutdown|shut|fire|"
      r"force majeure|attack|explosion)\b",
      {"brent": 0.30, "gas": 0.20}, "Supply outage")
_rule(r"\b(demand|consumption)\b[^.]{0,45}\b(weak|weaker|slow|slowing|fall|falls|drop|"
      r"concern|concerns|fear|fears)\b",
      {"brent": -0.45, "silver": -0.25, "gas": -0.15}, "Demand concerns")
_rule(r"\b(demand|consumption)\b[^.]{0,45}\b(strong|stronger|robust|grow|growing|rise|"
      r"record|boost)\b",
      {"brent": 0.40, "silver": 0.25, "gas": 0.20}, "Strong demand")
_rule(r"\b(recession|hard landing|economic slowdown|global slowdown)\b",
      {"brent": -0.40, "silver": -0.35, "gold": 0.25, "gas": -0.20}, "Recession risk")
_rule(r"\b(china|beijing)\b[^.]{0,50}\b(stimulus|support|boost|easing|infrastructure)\b",
      {"brent": 0.35, "silver": 0.35}, "China stimulus")
_rule(r"\b(china|beijing)\b[^.]{0,50}\b(slowdown|weak|slump|property crisis|demand worry)\b",
      {"brent": -0.30, "silver": -0.30}, "China slowdown")

# Gold / silver macro
_rule(r"\b(fed|fomc|powell)\b[^.]{0,55}\b(cut|cuts|dovish|pause|easing|ease)\b",
      {"gold": 0.50, "silver": 0.30}, "Fed easing")
_rule(r"\b(fed|fomc|powell)\b[^.]{0,55}\b(hike|hikes|hawkish|tighten|tightening)\b",
      {"gold": -0.50, "silver": -0.30}, "Fed tightening")
_rule(r"\b(inflation|cpi|ppi)\b[^.]{0,45}\b(hot|hotter|rise|rising|accelerat|sticky|"
      r"above expectations)\b",
      {"gold": 0.30, "silver": 0.10}, "Inflation")
_rule(r"\b(dollar|dxy|greenback)\b[^.]{0,45}\b(weak|weaker|fall|falls|slide|slides|drop|drops)\b",
      {"gold": 0.40, "silver": 0.30, "brent": 0.20}, "Weak dollar")
_rule(r"\b(dollar|dxy|greenback)\b[^.]{0,45}\b(strong|stronger|rise|rises|rally|rallies|surge)\b",
      {"gold": -0.40, "silver": -0.30, "brent": -0.20}, "Strong dollar")
_rule(r"\b(central bank|central banks|pboc|people's bank)\b[^.]{0,55}\b(buy|buying|purchase|"
      r"purchases|accumulat|pile|piling|add|adding)\w*\b[^.]{0,25}\b(gold|bullion)\b",
      {"gold": 0.50}, "Central bank buying")
_rule(r"\b(etf|fund)\b[^.]{0,35}\b(inflow|inflows|buying|demand)\b",
      {"gold": 0.30, "silver": 0.25}, "ETF inflows")
_rule(r"\b(etf|fund)\b[^.]{0,35}\b(outflow|outflows|selling|liquidat)\b",
      {"gold": -0.30, "silver": -0.25}, "ETF outflows")
_rule(r"\b(safe.?haven|risk.?off|flight to safety|haven demand|haven buying)\b",
      {"gold": 0.50, "silver": 0.15, "brent": -0.15}, "Safe-haven demand")
_rule(r"\b(real yields?|treasury yields?|bond yields?)\b[^.]{0,40}\b(fall|falls|drop|drops|"
      r"decline|lower)\b",
      {"gold": 0.35}, "Falling yields")
_rule(r"\b(real yields?|treasury yields?|bond yields?)\b[^.]{0,40}\b(rise|rises|jump|"
      r"climb|higher|surge)\b",
      {"gold": -0.35}, "Rising yields")
_rule(r"\b(industrial demand|solar|photovoltaic|electrification|manufacturing)\b[^.]{0,45}"
      r"\b(strong|boost|grow|growing|rise|record|demand)\b",
      {"silver": 0.45}, "Industrial demand")
_rule(r"\b(silver (?:market )?(?:deficit|shortage)|supply deficit|above-ground deficit)\b",
      {"silver": 0.45}, "Silver deficit")
_rule(r"\b(solar|photovoltaic)\b[^.]{0,40}\b(weak|slow|cut|decline|oversupply)\b",
      {"silver": -0.30}, "Solar weakness")

# Natural gas
_rule(r"\b(cold|colder|freeze|freezing|arctic blast|snow|heating degree|cold snap)\b",
      {"gas": 0.50}, "Cold weather")
_rule(r"\b(heat wave|hotter|above-normal temperatures|cooling demand|heat dome)\b",
      {"gas": 0.40}, "Heat / cooling demand")
_rule(r"\b(mild|warmer|warm weather|below-normal demand|above-normal storage)\b",
      {"gas": -0.45}, "Mild weather")
_rule(r"(?=.*\b(?:natural gas|nat gas|henry hub|lng|gas)\b)"
      r"(?=.*\b(storage|inventories|inventory)\b[^.]{0,50}\b(build|builds|surplus|above average|"
      r"rise|rises|increase|inject)\b)",
      {"gas": -0.50}, "Storage build")
_rule(r"(?=.*\b(?:natural gas|nat gas|henry hub|lng|gas)\b)"
      r"(?=.*\b(storage|inventories|inventory)\b[^.]{0,50}\b(draw|draws|deficit|below average|"
      r"fall|falls|decline|withdraw)\b)",
      {"gas": 0.50}, "Storage draw")
_rule(r"\b(lng|export terminal|feedgas|liquefied natural gas)\b[^.]{0,50}\b(record|rise|"
      r"rises|grow|growing|increase|start|demand|boom)\b",
      {"gas": 0.35}, "LNG demand")
_rule(r"\b(lng|export terminal|feedgas)\b[^.]{0,40}\b(outage|maintenance|halt|shutdown|"
      r"disruption)\b",
      {"gas": -0.30}, "LNG outage")
_rule(r"\b(production|output)\b[^.]{0,40}\b(record|rise|rises|grow|growing|high|increase)\b",
      {"gas": -0.35}, "Rising production")
_rule(r"\b(production|output)\b[^.]{0,40}\b(cut|cuts|fall|falls|decline|shut|curtail)\b",
      {"gas": 0.30}, "Production cuts")
_rule(r"\b(hurricane|tropical storm)\b[^.]{0,50}\b(gulf|production|platform|offshore)\b",
      {"gas": 0.35, "brent": 0.20}, "Hurricane risk")
_rule(r"\b(power burn|electricity demand|data center|data centres|ai power)\b",
      {"gas": 0.30}, "Power demand")

# Cross-asset
_rule(r"\b(supply disruption|supply shock|output disruption)\b",
      {"brent": 0.35, "gas": 0.30}, "Supply disruption")
_rule(r"\b(oversupply|glut|surplus|market share war)\b",
      {"brent": -0.40, "gas": -0.30}, "Oversupply")
_rule(r"\b(tariffs?|trade war|export controls?)\b",
      {"brent": -0.25, "silver": -0.20, "gold": 0.10}, "Tariffs / trade")
_rule(r"\b(technical breakout|breaks? out|key resistance|golden cross)\b",
      {"brent": 0.15, "gold": 0.15, "silver": 0.15, "gas": 0.15}, "Technical breakout")

LEXICON: list[tuple[re.Pattern[str], float]] = [
    (re.compile(r"\b(surge|surges|surged|soar|soars|soared|rally|rallies|rallied|jump|jumps|"
                r"jumped|climb|climbs|climbed|spike|spikes|spiked|rebound|rebounds|lift|lifts|"
                r"lifted|gain|gains|advance|advances)\b", re.I), 0.28),
    (re.compile(r"\b(plunge|plunges|plunged|tumble|tumbles|tumbled|slump|slumps|slumped|"
                r"sink|sinks|sank|fall|falls|fell|drop|drops|dropped|decline|declines|"
                r"declined|slide|slides|slid|loss|losses|weaken|weakens|weaker)\b", re.I), -0.28),
    (re.compile(r"\b(record high|all-time high|multi-year high|highest since)\b", re.I), 0.30),
    (re.compile(r"\b(record low|multi-year low|lowest since)\b", re.I), -0.30),
    (re.compile(r"\b(beat|beats|upgrade|upgraded|bullish|optimism|strong demand|tight supply|"
                r"undersupplied)\b", re.I), 0.20),
    (re.compile(r"\b(miss|misses|downgrade|downgraded|bearish|pessimism|weak demand|"
                r"oversupply|glut|worries|worried|fears)\b", re.I), -0.20),
]

TOP_SOURCES = {
    "reuters": 1.30, "bloomberg": 1.30, "financial times": 1.20, "ft": 1.20,
    "the wall street journal": 1.20, "wall street journal": 1.20, "wsj": 1.20,
    "associated press": 1.10, "ap": 1.10, "cnbc": 1.10, "the economist": 1.20,
    "barron's": 1.10, "nikkei": 1.10, "s&p global": 1.10, "argus media": 1.10,
    "platts": 1.10, "the new york times": 1.10, "bbc": 1.05,
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "at", "as",
    "is", "are", "was", "were", "be", "been", "with", "by", "from", "after",
    "before", "over", "under", "amid", "into", "than", "that", "this", "its",
    "it", "s", "new", "says", "said",
}


@dataclass
class Article:
    title: str
    url: str
    source: str
    published: datetime
    summary: str = ""
    tier: float = 1.0
    topic_hint: str = "markets"
    sentiment: dict[str, float] = field(default_factory=dict)
    relevance: dict[str, float] = field(default_factory=dict)
    topics: list[str] = field(default_factory=list)

    def age_hours(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return max(0.0, (now - self.published).total_seconds() / 3600.0)

    def source_tier(self) -> float:
        key = self.source.strip().lower()
        for name, tier in TOP_SOURCES.items():
            if name in key:
                return max(self.tier, tier)
        return self.tier


@dataclass
class NewsAggregate:
    asset: str
    score: float
    article_count: int
    positive: int
    negative: int
    neutral: int
    effective_n: float
    coverage: float
    topics: list[tuple[str, int]]
    headlines: list[Article]


# ---------------------------------------------------------------------------
# Fetching / parsing
# ---------------------------------------------------------------------------

def _clean(text: str, limit: int | None = None) -> str:
    if not text:
        return ""
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "..."
    return text


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(el: ET.Element, name: str) -> str:
    wanted = name.lower()
    for child in el:
        if _local(child.tag) == wanted:
            return "".join(child.itertext()).strip()
    return ""


def _parse_date(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def parse_feed(body: bytes | str, feed_name: str, tier: float, topic: str) -> list[Article]:
    if isinstance(body, str):
        body = body.encode("utf-8", "replace")
    root = ET.fromstring(body)
    articles: list[Article] = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title = _clean(_child_text(el, "title"), 300)
        if not title:
            continue
        link = _child_text(el, "link")
        if not link:
            for child in el:
                if _local(child.tag) == "link" and child.attrib.get("href"):
                    link = child.attrib["href"]
                    break
        link = link or _child_text(el, "guid")
        summary = _clean(
            _child_text(el, "description")
            or _child_text(el, "summary")
            or _child_text(el, "encoded")
            or _child_text(el, "content"),
            500,
        )
        source = _clean(_child_text(el, "source"), 80) or feed_name
        published = (
            _parse_date(_child_text(el, "pubDate"))
            or _parse_date(_child_text(el, "published"))
            or _parse_date(_child_text(el, "updated"))
            or _parse_date(_child_text(el, "date"))
        )
        if published is None:
            published = datetime.now(timezone.utc)
        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)].strip()
        articles.append(
            Article(
                title=title,
                url=link.strip(),
                source=source,
                published=published,
                summary=summary,
                tier=tier,
                topic_hint=topic,
            )
        )
    return articles


def _google_url(query: str) -> str:
    q = urllib.parse.quote_plus(f"{query} when:3d")
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def _fetch_feed(
    name: str, url: str, tier: float, topic: str, refresh: bool, offline: bool, ttl: int
):
    result = net.fetch(url, ttl=ttl, timeout=20, refresh=refresh, offline=offline)
    return parse_feed(result.body, name, tier, topic)


def fetch_news(
    *,
    days: float = NEWS_MAX_AGE_HOURS / 24.0,
    refresh: bool = False,
    offline: bool = False,
    ttl: int = 900,
) -> tuple[list[Article], list[str]]:
    jobs: list[tuple[str, str, float, str]] = [
        (f.name, f.url, f.tier, f.topic) for f in FEEDS
    ]
    for q in GOOGLE_QUERIES:
        jobs.append((f"Google News - {q}", _google_url(q), 0.70, "aggregated"))

    articles: list[Article] = []
    warnings: list[str] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_fetch_feed, name, url, tier, topic, refresh, offline, ttl): name
            for name, url, tier, topic in jobs
        }
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                articles.extend(fut.result())
            except Exception as exc:  # noqa: BLE001 - one bad feed must not kill the run
                warnings.append(f"{name}: {type(exc).__name__} {str(exc)[:90]}")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    fresh = [a for a in articles if cutoff <= a.published <= now + timedelta(hours=2)]
    for a in fresh:
        _annotate(a)

    deduped = _dedupe(fresh)
    deduped.sort(key=lambda a: a.published, reverse=True)
    if not deduped:
        warnings.append("No recent news items were retrieved.")
    return deduped, warnings


def _dedupe(articles: list[Article]) -> list[Article]:
    best: dict[str, Article] = {}
    for a in articles:
        tokens = sorted(
            {t for t in re.findall(r"[a-z0-9]+", a.title.lower()) if t not in STOPWORDS}
        )[:14]
        key = " ".join(tokens)
        if not key:
            continue
        existing = best.get(key)
        if existing is None:
            best[key] = a
        else:
            # Keep the higher-tier copy, but preserve richer summary/url.
            if a.source_tier() > existing.source_tier():
                if not a.summary and existing.summary:
                    a.summary = existing.summary
                best[key] = a
            elif not existing.summary and a.summary:
                existing.summary = a.summary
    return list(best.values())


def _annotate(article: Article) -> None:
    title = article.title
    summary = article.summary
    for key in ASSET_KEYS:
        rel_title = relevance(title, key, title_bonus=True)
        rel_summary = relevance(summary, key) * 0.7
        article.relevance[key] = round(rel_title + rel_summary, 2)

    scores = {k: 0.0 for k in ASSET_KEYS}
    topics: list[str] = []
    mentioned = [k for k, rx in MENTION_PATTERNS.items() if rx.search(f"{title} {summary}")]

    for pattern, impacts, topic in RULES:
        matched = pattern.search(title)
        weight = 1.0
        if not matched and summary:
            matched = pattern.search(summary)
            weight = 0.5
        if matched:
            for asset, delta in impacts.items():
                scores[asset] += delta * weight
                # WTI trades the same crude complex. When the story is about
                # oil, carry most of Brent's event impact across rather than
                # duplicating every rule dictionary.
                if asset == "brent" and "wti" in mentioned:
                    scores["wti"] += delta * weight * 0.85
            if topic not in topics:
                topics.append(topic)

    for pattern, delta in LEXICON:
        if pattern.search(title):
            for asset in mentioned:
                scores[asset] += delta
        elif summary and pattern.search(summary):
            for asset in mentioned:
                scores[asset] += delta * 0.5

    for key in ASSET_KEYS:
        scores[key] = max(-1.0, min(1.0, scores[key]))
    article.sentiment = scores
    article.topics = topics[:3]


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate(articles: list[Article], asset: str, now: datetime | None = None) -> NewsAggregate:
    now = now or datetime.now(timezone.utc)
    relevant_all = [a for a in articles if a.relevance.get(asset, 0) >= NEWS_MIN_RELEVANCE]
    directional = [a for a in relevant_all if a.sentiment.get(asset, 0) != 0]

    def rank_key(a: Article) -> float:
        return a.relevance.get(asset, 0) * (0.5 ** (a.age_hours(now) / 48.0)) * a.source_tier()

    # Cap each outlet so one syndicated wire cannot dominate the aggregate.
    capped: list[Article] = []
    per_source: dict[str, int] = {}
    for a in sorted(directional, key=rank_key, reverse=True):
        src = a.source.strip().lower()
        if per_source.get(src, 0) >= 6:
            continue
        per_source[src] = per_source.get(src, 0) + 1
        capped.append(a)

    weights: list[float] = []
    values: list[float] = []
    for a in capped:
        age = a.age_hours(now)
        recency = 0.5 ** (age / NEWS_HALF_LIFE_HOURS)
        rel = min(1.0, a.relevance[asset] / 9.0)
        w = recency * a.source_tier() * (0.65 + 0.35 * rel)
        weights.append(w)
        values.append(a.sentiment[asset])

    total_w = sum(weights)
    if total_w > 0:
        mean = sum(w * v for w, v in zip(weights, values)) / total_w
        eff_n = (total_w ** 2) / sum(w * w for w in weights)
    else:
        mean = 0.0
        eff_n = 0.0

    coverage = min(1.0, eff_n / 8.0)
    score = mean * (0.55 + 0.45 * coverage)
    score = max(-1.0, min(1.0, score))

    pos = sum(1 for a in relevant_all if a.sentiment.get(asset, 0) > 0.10)
    neg = sum(1 for a in relevant_all if a.sentiment.get(asset, 0) < -0.10)
    neu = len(relevant_all) - pos - neg

    topic_counts: dict[str, float] = {}
    for a in capped:
        w = 0.5 ** (a.age_hours(now) / NEWS_HALF_LIFE_HOURS) * a.source_tier()
        for t in a.topics:
            topic_counts[t] = topic_counts.get(t, 0.0) + w

    # Rank headlines by relevance/recency, with a per-source cap for diversity.
    ranked = sorted(relevant_all, key=rank_key, reverse=True)
    headlines: list[Article] = []
    seen_urls: set[str] = set()
    per_source_headlines: dict[str, int] = {}
    for a in ranked:
        if a.url in seen_urls:
            continue
        src = a.source.strip().lower()
        if per_source_headlines.get(src, 0) >= 2:
            continue
        seen_urls.add(a.url)
        per_source_headlines[src] = per_source_headlines.get(src, 0) + 1
        headlines.append(a)
        if len(headlines) >= 6:
            break

    return NewsAggregate(
        asset=asset,
        score=score,
        article_count=len(relevant_all),
        positive=pos,
        negative=neg,
        neutral=neu,
        effective_n=eff_n,
        coverage=coverage,
        topics=sorted(topic_counts.items(), key=lambda kv: kv[1], reverse=True)[:5],
        headlines=headlines,
    )


def global_headlines(articles: list[Article], limit: int = 6) -> list[Article]:
    def relevant(a: Article) -> bool:
        text = f"{a.title} {a.summary}".lower()
        return bool(
            re.search(
                r"\b(opec|fed|fomc|sanctions?|tariffs?|election|war|ceasefire|iran|"
                r"russia|china|inflation|recession|dollar)\b",
                text,
            )
        )

    pool = [a for a in articles if relevant(a)]
    pool.sort(
        key=lambda a: a.source_tier() * (0.5 ** (a.age_hours() / 48.0)),
        reverse=True,
    )
    out: list[Article] = []
    seen: set[str] = set()
    for a in pool:
        key = re.sub(r"[^a-z0-9]+", "", a.title.lower())[:70]
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
        if len(out) >= limit:
            break
    return out
