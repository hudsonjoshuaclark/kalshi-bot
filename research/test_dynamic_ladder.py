import unittest

from s16_dynamic_ladder import event_trade


def market(index, result, bars):
    return {"ticker": "T%d" % index, "series": "S", "event": "E",
            "open_ts": 0, "close_ts": 1000, "strike": float(index),
            "strike_type": "greater", "result": result, "bars": bars}


def bar(ts, bid, ask):
    return {"ts": ts, "bid": bid, "ask": ask}


class DynamicLadderTests(unittest.TestCase):
    def config(self):
        return {"activation": 0.10, "last_first": 0.80,
                "rank_gap": 1, "trigger": 0.35, "min_paid": 0.05}

    def test_completed_pair_has_guaranteed_ordering_and_two_fees(self):
        markets = [market(i, "no", []) for i in range(6)]
        # YES at lower strike 2 newly touches 30c, then NO at strike 3 is 30c.
        markets[2] = market(2, "yes", [bar(200, 0.39, 0.40), bar(300, 0.29, 0.30)])
        markets[3] = market(3, "no", [bar(400, 0.70, 0.71)])
        item = {"event": "E", "markets": markets, "open_ts": 0,
                "close_ts": 1000, "series": "S"}
        row = event_trade(item, self.config())
        self.assertTrue(row["paired"])
        self.assertLess(row["legs"][0]["strike"], row["legs"][1]["strike"])
        self.assertEqual(row["payout"], 2)
        self.assertAlmostEqual(row["cost"], 0.64)
        self.assertAlmostEqual(row["guaranteed_min_pnl"], 0.36)

    def test_uncompleted_first_leg_is_not_discarded(self):
        markets = [market(i, "no", []) for i in range(6)]
        markets[2] = market(2, "no", [bar(200, 0.39, 0.40), bar(300, 0.29, 0.30)])
        markets[3] = market(3, "no", [bar(400, 0.10, 0.11)])  # NO ask 90c: no fill
        item = {"event": "E", "markets": markets, "open_ts": 0,
                "close_ts": 1000, "series": "S"}
        row = event_trade(item, self.config())
        self.assertFalse(row["paired"])
        self.assertEqual(row["payout"], 0)
        self.assertAlmostEqual(row["pnl"], -0.32)


if __name__ == "__main__":
    unittest.main()
