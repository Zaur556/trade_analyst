from __future__ import annotations

import unittest

from commodity_agent.indicators import (
    adx_wilder,
    atr_wilder,
    bollinger,
    ema,
    macd,
    percentile_rank,
    rsi_wilder,
    sma,
)


class TestMovingAverages(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(sma([1, 2, 3, 4, 5], 3), [None, None, 2.0, 3.0, 4.0])

    def test_ema_seed_and_recursion(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        out = ema(values, 3)
        self.assertIsNone(out[0])
        self.assertIsNone(out[1])
        self.assertEqual(out[2], 2.0)              # SMA seed
        self.assertAlmostEqual(out[3], 3.0)         # 4*.5 + 2*.5
        self.assertAlmostEqual(out[4], 4.0)         # 5*.5 + 3*.5

    def test_ema_too_short(self):
        self.assertEqual(ema([1, 2], 3), [None, None])


class TestOscillators(unittest.TestCase):
    def test_rsi_all_gains_is_100(self):
        closes = [float(i) for i in range(1, 40)]
        out = rsi_wilder(closes, 14)
        self.assertAlmostEqual(out[-1], 100.0)

    def test_rsi_all_losses_is_zero(self):
        closes = [float(i) for i in range(40, 1, -1)]
        out = rsi_wilder(closes, 14)
        self.assertAlmostEqual(out[-1], 0.0)

    def test_atr_constant_range(self):
        n = 40
        highs = [10.0] * n
        lows = [8.0] * n
        closes = [9.0] * n
        out = atr_wilder(highs, lows, closes, 14)
        self.assertAlmostEqual(out[-1], 2.0)

    def test_macd_histogram_identity(self):
        closes = [100 + i * 0.4 + (i % 5) for i in range(80)]
        line, signal, hist = macd(closes)
        for a, b, h in zip(line, signal, hist):
            if a is not None and b is not None:
                self.assertAlmostEqual(h, a - b)


class TestBandsAndTrend(unittest.TestCase):
    def test_bollinger_constant_series_collapses(self):
        up, mid, low = bollinger([5.0] * 30, 20, 2.0)
        self.assertAlmostEqual(up[-1], 5.0)
        self.assertAlmostEqual(low[-1], 5.0)
        self.assertAlmostEqual(mid[-1], 5.0)

    def test_adx_uptrend_bullish(self):
        closes = [100 + i for i in range(80)]
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        adx, pdi, mdi = adx_wilder(highs, lows, closes, 14)
        self.assertGreater(adx[-1], 20)
        self.assertGreater(pdi[-1], mdi[-1])

    def test_percentile_rank(self):
        self.assertAlmostEqual(percentile_rank([1, 2, 3, 4], 2.5), 0.5)
        self.assertAlmostEqual(percentile_rank([1, 2, 3, 4], 99), 1.0)


if __name__ == "__main__":
    unittest.main()
