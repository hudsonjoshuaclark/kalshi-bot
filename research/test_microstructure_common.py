import math
import unittest

from microstructure_common import (
    _trailing_std, market_null, quote_impulse_trades, two_touch_trades,
    unique_market_audit,
)


def bar(ts, bid, ask):
    return {"ts": ts, "bid": bid, "ask": ask, "vol": 1.0}


class MicrostructureTests(unittest.TestCase):
    def setUp(self):
        self.open_ts = 1_800_000_000
        self.close_ts = self.open_ts + 900
        self.spot = {}
        for offset in range(-40, 16):
            key = self.open_ts + 60 * offset
            self.spot[key] = {"open": 100.0, "high": 101.0, "low": 99.0,
                              "close": 100.0 + 0.01 * offset, "vol": 1.0}

    def universe(self, bars, result="yes"):
        markets = {coin: [] for coin in ("BTC", "ETH", "SOL", "XRP", "DOGE")}
        markets["BTC"] = [{
            "ticker": "T", "series": "KXBTC15M", "open_ts": self.open_ts,
            "close_ts": self.close_ts, "strike": 100.0, "result": result,
            "bars": bars,
        }]
        spots = {coin: {} for coin in markets}
        spots["BTC"] = self.spot
        return markets, spots

    def test_two_touch_uses_two_entry_fees(self):
        markets, spots = self.universe([
            bar(self.open_ts + 60, 0.24, 0.25),
            bar(self.open_ts + 120, 0.75, 0.76),
        ])
        rows = two_touch_trades(markets, spots, self.close_ts + 1, "train",
                                 {"activation": 1, "last_first": 3, "trigger": 0.25})
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["paired"])
        self.assertEqual(rows[0]["fees_by_leg"], [0.02, 0.02])
        self.assertAlmostEqual(rows[0]["pnl"], 0.46)
        self.assertTrue(unique_market_audit(rows))

    def test_two_touch_does_not_use_settlement_minute(self):
        markets, spots = self.universe([
            bar(self.open_ts + 60, 0.24, 0.25),
            bar(self.open_ts + 840, 0.75, 0.76),
        ], result="no")
        rows = two_touch_trades(markets, spots, self.close_ts + 1, "train",
                                 {"activation": 1, "last_first": 3, "trigger": 0.25})
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["paired"])
        self.assertAlmostEqual(rows[0]["pnl"], -0.27)

    def test_quote_impulse_crosses_current_ask(self):
        markets, spots = self.universe([
            bar(self.open_ts + 60, 0.40, 0.42),
            bar(self.open_ts + 180, 0.61, 0.64),
        ])
        rows = quote_impulse_trades(markets, spots, self.close_ts + 1, "train",
                                    {"entry": 3, "threshold": 0.20, "mode": "momentum"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["side"], "YES")
        self.assertAlmostEqual(rows[0]["p_paid"], 0.64)

    def test_trailing_std_is_chronological(self):
        previous_key = 10_000
        spot = {}
        price = 100.0
        spot[previous_key - 5 * 60] = {"close": price}
        expected = []
        for index, value in enumerate((0.01, -0.02, 0.03, -0.01, 0.02), start=1):
            price *= math.exp(value)
            spot[previous_key - (5 - index) * 60] = {"close": price}
            expected.append(value)
        actual = _trailing_std(spot, previous_key, lookback=5)
        mean = sum(expected) / len(expected)
        target = math.sqrt(sum((value - mean) ** 2 for value in expected) / 4)
        self.assertAlmostEqual(actual, target)

    def test_fixed_pair_market_null_is_deterministic(self):
        row = {"pnl": 0.46, "cost": 0.54, "null_fixed_payout": 1.0, "p_paid": None}
        self.assertEqual(market_null([row], simulations=100), 1.0)


if __name__ == "__main__":
    unittest.main()

