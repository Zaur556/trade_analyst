from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from commodity_agent.config import ASSETS
from commodity_agent.macro import MacroContext
from commodity_agent.pipeline import RunResult
from commodity_agent.report import render_json, render_markdown, write_reports
from commodity_agent.render_html import render_html
from commodity_agent.signals import analyze
from tests.helpers import make_series, trend


class TestReports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        series = make_series(trend(), symbol="GC=F", name="Gold")
        signal = analyze(ASSETS["gold"], series, None, None)
        cls.result = RunResult(
            generated_at=datetime(2026, 10, 23, 19, 0, tzinfo=timezone.utc),
            as_of="2026-10-23",
            signals=[signal],
            macro=MacroContext(quotes=[], series={}, values={}),
            backtests={},
            news_count=0,
            warnings=["test warning"],
            account_size=50_000,
            risk_pct=1.0,
            days=4.0,
            articles=[],
        )

    def test_html_is_self_contained_and_valid_enough(self):
        html = render_html(self.result)
        self.assertIn("<!doctype html>", html)
        self.assertIn("Commodity Signal Desk", html)
        self.assertIn("Trade plan", html)
        self.assertEqual(html.count("<article"), 1)
        self.assertNotIn(">None<", html)
        self.assertNotIn("http://", html.replace("http://www.w3.org", ""))

    def test_json_round_trips(self):
        payload = json.loads(render_json(self.result))
        self.assertEqual(payload["schema"], "commodity-signal-desk/1.0")
        self.assertEqual(len(payload["signals"]), 1)
        self.assertEqual(payload["signals"][0]["symbol"], "GC=F")
        self.assertIn("factors", payload["signals"][0])

    def test_markdown_has_decision_summary(self):
        md = render_markdown(self.result)
        self.assertIn("## Decision summary", md)
        self.assertIn("Gold", md)
        self.assertIn("Disclaimer", md)

    def test_live_refresh_meta(self):
        html = render_html(self.result, refresh_seconds=300)
        self.assertIn('http-equiv="refresh" content="300"', html)
        self.assertIn("AUTO 5 MIN", html)

    def test_write_reports_can_skip_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_reports(
                self.result, Path(tmp), archive=False, live_seconds=300
            )
            self.assertNotIn("html_stamped", paths)
            self.assertTrue(paths["html"].exists())
            self.assertIn('http-equiv="refresh"', paths["html"].read_text(encoding="utf-8"))

    def test_write_reports_creates_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_reports(self.result, Path(tmp))
            for key in ("html", "json", "md"):
                self.assertTrue(paths[key].exists(), key)
            self.assertGreater(paths["html"].stat().st_size, 10_000)


if __name__ == "__main__":
    unittest.main()
