"""Static configuration: instruments, factor weights, feeds, thresholds.

Everything that a user might reasonably want to tune lives here rather than
being scattered through the scoring code.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Asset:
    key: str
    name: str
    short: str
    symbol: str          # Yahoo Finance continuous front-month future
    etf: str             # liquid 1x ETF proxy for smaller accounts
    unit: str            # "USD/bbl", "USD/oz", ...
    multiplier: float    # units per futures contract
    tick: float          # minimum price increment
    tick_value: float    # USD value of one tick per contract
    weights: dict[str, float]
    extra_queries: tuple[str, ...] = ()
    fade_trend: bool = False   # gas is mean-reverting: fade extended trend signals


# Factor weights. Must sum to 1.0 per asset. Volatility is *not* a directional
# factor here; it feeds confidence and position sizing instead.
ASSETS: dict[str, Asset] = {
    "brent": Asset(
        key="brent",
        name="Brent Crude Oil",
        short="BRENT",
        symbol="BZ=F",
        etf="BNO",
        unit="USD/bbl",
        multiplier=1_000,
        tick=0.01,
        tick_value=10.0,
        weights={"trend": 0.35, "momentum": 0.20, "meanrev": 0.10, "news": 0.25, "macro": 0.10},
        extra_queries=("OPEC oil supply", "Iran sanctions oil"),
    ),
    "wti": Asset(
        key="wti",
        name="WTI Crude Oil",
        short="WTI",
        symbol="CL=F",
        etf="USO",
        unit="USD/bbl",
        multiplier=1_000,
        tick=0.01,
        tick_value=10.0,
        weights={"trend": 0.35, "momentum": 0.20, "meanrev": 0.10, "news": 0.25, "macro": 0.10},
        extra_queries=("WTI crude oil", "Cushing crude inventories"),
    ),
    "gold": Asset(
        key="gold",
        name="Gold",
        short="GOLD",
        symbol="GC=F",
        etf="GLD",
        unit="USD/oz",
        multiplier=100,
        tick=0.10,
        tick_value=10.0,
        weights={"trend": 0.30, "momentum": 0.15, "meanrev": 0.10, "news": 0.20, "macro": 0.25},
        extra_queries=("Federal Reserve gold", "central bank gold buying"),
    ),
    "silver": Asset(
        key="silver",
        name="Silver",
        short="SILVER",
        symbol="SI=F",
        etf="SLV",
        unit="USD/oz",
        multiplier=5_000,
        tick=0.005,
        tick_value=25.0,
        weights={"trend": 0.30, "momentum": 0.20, "meanrev": 0.10, "news": 0.20, "macro": 0.20},
        extra_queries=("silver industrial demand", "silver deficit"),
    ),
    "gas": Asset(
        key="gas",
        name="Natural Gas",
        short="NAT GAS",
        symbol="NG=F",
        etf="UNG",
        unit="USD/MMBtu",
        multiplier=10_000,
        tick=0.001,
        tick_value=10.0,
        weights={"trend": 0.40, "momentum": 0.15, "meanrev": 0.20, "news": 0.20, "macro": 0.05},
        extra_queries=("natural gas storage", "LNG exports"),
        fade_trend=True,
    ),
}

ORDER: tuple[str, ...] = ("brent", "wti", "gold", "silver", "gas")


@dataclass(frozen=True)
class MacroSymbol:
    key: str
    name: str
    symbol: str
    unit: str
    note: str


MACRO: tuple[MacroSymbol, ...] = (
    MacroSymbol("dxy", "US Dollar Index", "DX-Y.NYB", "", "A stronger dollar is a headwind for USD-priced commodities."),
    MacroSymbol("yield10", "US 10Y Yield", "^TNX", "%", "Real yields drive gold; energy is more growth-sensitive."),
    MacroSymbol("vix", "VIX", "^VIX", "", "Risk-off spikes help gold, hurt cyclical energy."),
    MacroSymbol("spx", "S&P 500", "^GSPC", "", "Growth / risk appetite proxy."),
    MacroSymbol("copper", "Copper", "HG=F", "USD/lb", "Industrial-cycle proxy, especially for silver."),
)


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    tier: float          # 1.0 = top-tier / primary, lower = secondary
    topic: str


# Curated feeds. RSS is used because it is free, key-less and stable enough.
FEEDS: tuple[Feed, ...] = (
    Feed("Investing.com Commodities", "https://www.investing.com/rss/news_11.rss", 1.0, "markets"),
    Feed("Investing.com Economy", "https://www.investing.com/rss/news_14.rss", 1.0, "macro"),
    Feed("OilPrice.com", "https://oilprice.com/rss/main", 1.0, "energy"),
    Feed("FT Commodities", "https://www.ft.com/commodities?format=rss", 1.2, "markets"),
    Feed("WSJ Markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml", 1.2, "markets"),
    Feed("CNBC Economy", "https://www.cnbc.com/id/20910258/device/rss/rss.html", 1.1, "macro"),
    Feed("EIA Today in Energy", "https://www.eia.gov/rss/todayinenergy.xml", 1.2, "energy"),
    Feed("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml", 1.2, "policy"),
    Feed("Mining.com", "https://www.mining.com/feed/", 1.0, "metals"),
    Feed("Rigzone", "https://www.rigzone.com/news/rss/rigzone_latest.aspx", 0.9, "energy"),
    Feed("Natural Gas Intel", "https://www.naturalgasintel.com/feed/", 1.0, "energy"),
    Feed("BBC World", "http://feeds.bbci.co.uk/news/world/rss.xml", 1.0, "geopolitics"),
    Feed("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", 0.9, "geopolitics"),
)

# Google News queries give broad, per-instrument coverage and catch the
# political/geopolitical stories that trade feeds miss.
GOOGLE_QUERIES: tuple[str, ...] = (
    "brent oil price",
    "WTI crude oil",
    "gold price",
    "silver price",
    "natural gas price",
    "OPEC+ production",
    "Iran oil sanctions",
    "Federal Reserve interest rates",
    "gold safe haven",
)

NEWS_MAX_AGE_HOURS = 96
NEWS_HALF_LIFE_HOURS = 36.0
NEWS_MIN_RELEVANCE = 3.0

# Composite score -> action. Symmetric bands, deliberately hard to flip.
THRESHOLDS: tuple[tuple[float, str], ...] = (
    (45.0, "STRONG BUY"),
    (20.0, "BUY"),
    (-20.0, "HOLD"),
    (-45.0, "SELL"),
    (-100.0, "STRONG SELL"),
)

RISK_FREE_LABEL = "decision support, not investment advice"
