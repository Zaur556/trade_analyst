from __future__ import annotations

import unittest
from datetime import datetime, timezone

from commodity_agent.news import (
    Article,
    _annotate,
    _dedupe,
    aggregate,
    parse_feed,
    relevance,
)

RSS = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>Test</title>
<item>
  <title>Gold rallies to record high as Fed signals rate cut</title>
  <link>https://example.com/gold</link>
  <pubDate>Fri, 23 Oct 2026 16:03:48 GMT</pubDate>
  <description>&lt;p&gt;Gold jumped on &lt;b&gt;dovish&lt;/b&gt; comments.&lt;/p&gt;</description>
  <source>Reuters</source>
</item>
</channel></rss>"""


class TestFeedParsing(unittest.TestCase):
    def test_pubdate_is_parsed_not_replaced_with_now(self):
        articles = parse_feed(RSS, "Test", 1.0, "metals")
        self.assertEqual(len(articles), 1)
        art = articles[0]
        self.assertEqual(art.published.year, 2026)
        self.assertEqual(art.published.month, 10)
        self.assertEqual(art.published.hour, 16)
        self.assertEqual(art.published.tzinfo, timezone.utc)

    def test_description_html_is_stripped(self):
        art = parse_feed(RSS, "Test", 1.0, "metals")[0]
        self.assertEqual(art.summary, "Gold jumped on dovish comments.")
        self.assertEqual(art.source, "Reuters")

    def test_source_suffix_removed(self):
        rss = RSS.replace(b"</title>\n  <link>", b" - Reuters</title>\n  <link>", 1)
        art = parse_feed(rss, "Test", 1.0, "metals")[0]
        self.assertEqual(art.title, "Gold rallies to record high as Fed signals rate cut")


class TestRelevanceAndSentiment(unittest.TestCase):
    def article(self, title: str, summary: str = "") -> Article:
        return Article(
            title=title,
            url="https://example.com/x" + str(abs(hash(title))),
            source="Testwire",
            published=datetime.now(timezone.utc),
            summary=summary,
            tier=1.0,
        )

    def test_brent_headline_is_relevant_to_brent_only(self):
        text = "Brent crude oil rises as OPEC cuts output"
        self.assertGreaterEqual(relevance(text, "brent", True), 3)
        self.assertLess(relevance(text, "gold", True), 3)

    def test_opec_output_hike_is_bearish_oil(self):
        a = self.article("OPEC+ agrees to output hike, pressuring crude prices")
        _annotate(a)
        self.assertLess(a.sentiment["brent"], -0.3)

    def test_supply_attack_is_bullish_oil(self):
        a = self.article("Houthi attacks Saudi oil facilities, Brent crude jumps")
        _annotate(a)
        self.assertGreater(a.sentiment["brent"], 0.4)

    def test_fed_rate_cut_is_bullish_gold(self):
        a = self.article("Fed signals rate cut as inflation cools, gold rallies")
        _annotate(a)
        self.assertGreater(a.sentiment["gold"], 0.4)

    def test_storage_build_is_bearish_gas(self):
        a = self.article("Natural gas falls as storage build exceeds expectations")
        _annotate(a)
        self.assertLess(a.sentiment["gas"], -0.3)

    def test_degrees_of_sentiment_are_bounded(self):
        a = self.article(
            "Gold plunges then surges on Fed hike and rate cut, war and ceasefire, "
            "record high and record low"
        )
        _annotate(a)
        for value in a.sentiment.values():
            self.assertGreaterEqual(value, -1.0)
            self.assertLessEqual(value, 1.0)


class TestDedupeAndAggregate(unittest.TestCase):
    def test_dedupe_keeps_higher_tier_copy(self):
        now = datetime.now(timezone.utc)
        low = Article("Gold rises on Fed cut", "u1", "Blog", now, summary="s1", tier=0.5)
        high = Article("Gold rises on Fed cut", "u2", "Reuters", now, summary="", tier=1.2)
        out = _dedupe([low, high])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].url, "u2")
        self.assertEqual(out[0].summary, "s1")

    def test_aggregate_weights_recent_news_more(self):
        now = datetime.now(timezone.utc)
        old = Article(
            "Gold plunges as dollar strengthens",
            "u3",
            "Reuters",
            now.replace(day=max(1, now.day - 2)),
            tier=1.2,
        )
        fresh = Article(
            "Gold rallies as Fed signals rate cut",
            "u4",
            "Reuters",
            now,
            tier=1.2,
        )
        for a in (old, fresh):
            _annotate(a)
        agg = aggregate([old, fresh], "gold", now=now)
        self.assertGreater(agg.score, 0)
        self.assertEqual(agg.article_count, 2)


if __name__ == "__main__":
    unittest.main()
