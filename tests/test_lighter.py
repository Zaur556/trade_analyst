from __future__ import annotations
import unittest
from datetime import datetime, timezone
from commodity_agent.lighter import parse_quotes

class TestLighterQuotes(unittest.TestCase):
    def test_parses_five_commodity_markets_and_prefers_index_price(self):
        payload={"order_book_details":[
            {"symbol":"BRENTOIL","index_price":"81.25","mark_price":"81.40","last_trade_price":"81.35"},
            {"symbol":"WTI","index_price":"77.10"},{"symbol":"XAU","index_price":"2735.50"},
            {"symbol":"XAG","index_price":"32.25"},{"symbol":"NATGAS","index_price":"3.10"}]}
        fetched=datetime(2026,10,30,tzinfo=timezone.utc)
        quotes=parse_quotes(payload,fetched)
        self.assertEqual(set(quotes),{"brent","wti","gold","silver","gas"})
        self.assertEqual(quotes["brent"].price,81.25)
        self.assertEqual(quotes["brent"].price_type,"index")
        self.assertEqual(quotes["gold"].market_symbol,"XAU")
        self.assertEqual(quotes["gas"].fetched_at,fetched)

    def test_accepts_usd_suffix_symbols(self):
        quotes=parse_quotes({"order_books":[{"symbol":"XAU/USD","mark_price":"2750"},{"symbol":"XAG-USD","mark_price":"32"}]})
        self.assertEqual(quotes["gold"].price,2750)
        self.assertEqual(quotes["silver"].price,32)

    def test_ignores_inactive_market_and_accepts_last_price_fallback(self):
        quotes = parse_quotes({"order_book_details": [
            {"symbol": "XAU", "status": "inactive", "index_price": "2700"},
            {"symbol": "XAG", "status": "active", "last_price": "31.5"},
        ]})
        self.assertNotIn("gold", quotes)
        self.assertEqual(quotes["silver"].price, 31.5)
        self.assertEqual(quotes["silver"].price_type, "last trade")

    def test_ignores_nonpositive_and_unknown_prices(self):
        quotes=parse_quotes({"order_book_details":[{"symbol":"XAU","index_price":"0","mark_price":"-1"},{"symbol":"BTC","index_price":"65000"}]})
        self.assertEqual(quotes,{})

if __name__=="__main__": unittest.main()
