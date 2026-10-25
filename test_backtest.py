from __future__ import annotations

import unittest

from commodity_agent.backtest import run_backtest
from commodity_agent.config import ASSETS
from tests.helpers import make_series, trend


class TestBacktest(unittest.TestCase):
    def test_backtest_buckets_sum_to_samples(self):
        series = make_series(trend(360))
        result = run_backtest(ASSETS["gold"], series, horizon=10, warmup=250)
        self.assertEqual(result.samples, sum(b.n for b in result.buckets))
        self.assertEqual(result.horizon, 10)
        self.assertIsNotNone(result.directional_hit)
        self.assertGreater(result.samples, 50)

    def test_backtest_rejects_short_history(self):
        series = make_series(trend(100))
        with self.assertRaises(ValueError):
            run_backtest(ASSETS["gold"], series, horizon=10, warmup=250)


if __name__ == "__main__":
    unittest.main()
