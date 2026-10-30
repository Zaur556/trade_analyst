from __future__ import annotations

import unittest

from commodity_agent.config import ASSETS
from commodity_agent.signals import action_for, analyze
from tests.helpers import downtrend, flat, make_series, trend


class TestActions(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(action_for(60), "STRONG BUY")
        self.assertEqual(action_for(20), "BUY")
        self.assertEqual(action_for(0), "HOLD")
        self.assertEqual(action_for(-20), "SELL")
        self.assertEqual(action_for(-60), "STRONG SELL")


class TestAnalysis(unittest.TestCase):
    def test_uptrend_is_a_buy_with_a_long_plan(self):
        series = make_series(trend())
        signal = analyze(ASSETS["gold"], series, None, None)
        self.assertGreater(signal.score, 20)
        self.assertIn(signal.action, ("BUY", "STRONG BUY"))
        self.assertEqual(signal.plan.direction, "LONG")
        self.assertLess(signal.plan.stop, signal.plan.entry)
        self.assertGreater(signal.plan.target, signal.plan.entry)
        self.assertGreater(signal.plan.rr, 1.5)

    def test_downtrend_is_a_sell_with_a_short_plan(self):
        series = make_series(downtrend())
        signal = analyze(ASSETS["silver"], series, None, None)
        self.assertLess(signal.score, -20)
        self.assertIn(signal.action, ("SELL", "STRONG SELL"))
        self.assertEqual(signal.plan.direction, "SHORT")
        self.assertGreater(signal.plan.stop, signal.plan.entry)
        self.assertLess(signal.plan.target, signal.plan.entry)

    def test_flat_market_is_a_hold(self):
        series = make_series(flat())
        signal = analyze(ASSETS["brent"], series, None, None)
        self.assertEqual(signal.action, "HOLD")
        self.assertEqual(signal.plan.direction, "FLAT")
        self.assertIsNone(signal.plan.entry)

    def test_gas_fades_an_uptrend(self):
        # Gas is explicitly mean-reverting in this model: a strong uptrend
        # should score bearish, not bullish.
        series = make_series(trend())
        signal = analyze(ASSETS["gas"], series, None, None)
        self.assertLess(signal.score, -20)
        self.assertIn(signal.action, ("SELL", "STRONG SELL"))
        labels = {f.key: f.label for f in signal.factors}
        self.assertIn("faded", labels["trend"].lower())

    def test_live_quote_is_used_for_entry_and_trade_plan(self):
        series = make_series(trend())
        series.current_price = series.last.close * 1.01
        signal = analyze(ASSETS["gold"], series, None, None)
        self.assertAlmostEqual(signal.price, series.current_price)
        self.assertAlmostEqual(signal.plan.entry, series.current_price)
        if signal.plan.direction == "LONG":
            self.assertAlmostEqual(signal.plan.rr, (signal.plan.target - signal.plan.entry) / signal.plan.stop_distance)
        elif signal.plan.direction == "SHORT":
            self.assertAlmostEqual(signal.plan.rr, (signal.plan.entry - signal.plan.target) / signal.plan.stop_distance)

    def test_position_sizing_respects_budget(self):
        series = make_series(trend())
        signal = analyze(ASSETS["brent"], series, None, None, account_size=10_000_000, risk_pct=1.0)
        plan = signal.plan
        self.assertGreaterEqual(plan.contracts, 1)
        self.assertLessEqual(plan.risk_per_contract * plan.contracts, 100_000)
        self.assertAlmostEqual(plan.min_account_one_lot, plan.risk_per_contract / 0.01, places=2)

    def test_confidence_and_metrics_are_bounded(self):
        series = make_series(trend())
        signal = analyze(ASSETS["gold"], series, None, None)
        self.assertGreaterEqual(signal.confidence, 5)
        self.assertLessEqual(signal.confidence, 95)
        self.assertGreater(signal.metrics["range_pos"], 50)
        self.assertIn(signal.confidence_label, ("Low", "Medium", "High"))

    def test_wti_is_a_first_class_instrument(self):
        self.assertIn("wti", ASSETS)
        signal = analyze(ASSETS["wti"], make_series(trend()), None, None)
        self.assertGreater(signal.score, 20)
        self.assertEqual(signal.plan.direction, "LONG")


if __name__ == "__main__":
    unittest.main()
