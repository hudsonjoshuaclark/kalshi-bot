"""Small stdlib regression tests for the corrected volatility machinery."""

import math
import unittest

from volatility_common import build_observations, fee, norm_ppf, trades_for


class VolatilityCommonTests(unittest.TestCase):
    def test_fee_uses_order_level_cent_ceiling(self):
        self.assertEqual(fee(1, 0.50), 0.02)
        self.assertEqual(fee(10, 0.50), 0.18)
        self.assertEqual(fee(1, 0.90), 0.01)

    def test_inverse_normal(self):
        self.assertAlmostEqual(norm_ppf(0.5), 0.0, places=12)
        self.assertAlmostEqual(norm_ppf(0.975), 1.95996398, places=6)

    def test_coinbase_key_is_previous_minute_and_rv_units_are_absolute(self):
        # Entry is t=60.  Coinbase key 0 closes exactly at t=60; key 60 is the
        # following candle and must never be used despite its absurd price.
        spot = {}
        for index, ts in enumerate(range(-1800, 1, 60)):
            price = 100.0 + 0.03 * index + (0.04 if index % 2 else -0.04)
            if ts == 0:
                price = 101.0
            spot[ts] = {"open": price - 0.01, "high": price + 0.02,
                        "low": price - 0.02, "close": price, "vol": 1.0}
        spot[60] = {"open": 999.0, "high": 1001.0, "low": 998.0,
                    "close": 1000.0, "vol": 1.0}
        market = {
            "ticker": "SYNTH", "series": "KXBTC15M", "open_ts": 0,
            "close_ts": 900, "strike": 100.0, "result": "yes",
            "bars": [{"ts": 60, "bid": 0.59, "ask": 0.61, "vol": 5.0}],
        }
        markets = {coin: ([market] if coin == "BTC" else [])
                   for coin in ("BTC", "ETH", "SOL", "XRP", "DOGE")}
        spots = {coin: (spot if coin == "BTC" else {})
                 for coin in ("BTC", "ETH", "SOL", "XRP", "DOGE")}
        rows = build_observations(markets, spots, (1,), ("std30",),
                                  cut_ts=1000, partition="train")[(1, "std30")]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["spot"], 101.0)
        self.assertAlmostEqual(rows[0]["rv_abs"], rows[0]["spot"] * rows[0]["rv_rel"])
        self.assertTrue(math.isfinite(rows[0]["ratio"]))

    def test_trade_crosses_correct_side_and_applies_fee(self):
        obs = {
            "ticker": "SYNTH", "series": "KXBTC15M", "coin": "BTC", "outcome": 1,
            "spot_above_strike": True, "ratio": 2.0, "abs_z": 0.4,
            "spread": 0.02, "bid": 0.68, "ask": 0.70,
        }
        config = {"threshold": 1.5, "regime": "iv_high", "min_abs_z": 0.0,
                  "min_paid": 0.0, "max_paid": 1.0, "max_spread": 1.0}
        rows = trades_for([obs], config)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["side"], "YES")
        self.assertAlmostEqual(rows[0]["pnl"], 1.0 - 0.70 - fee(1, 0.70))


if __name__ == "__main__":
    unittest.main()
