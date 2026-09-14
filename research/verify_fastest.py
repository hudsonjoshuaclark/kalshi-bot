import json
import os
import unittest


class FastestVerification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.chdir(os.path.dirname(os.path.abspath(__file__)))
        with open("fastest_strategy.json", "r", encoding="utf-8") as handle:
            cls.final = json.load(handle)
        with open("fast_markets_oos_results.json", "r", encoding="utf-8") as handle:
            cls.fast = json.load(handle)
        with open("profit_rate_sizing.json", "r", encoding="utf-8") as handle:
            cls.sizing = json.load(handle)
        with open(os.path.join("..", "config.robust-rv.paper.json"), "r", encoding="utf-8") as handle:
            cls.config = json.load(handle)

    def test_search_audit(self):
        self.assertEqual(self.final["global_searches"], 442)
        self.assertEqual(self.fast["new_searches"], 144)
        self.assertEqual(442, 298 + 144)

    def test_dead_families_never_opened(self):
        for item in self.fast["results"][:2]:
            self.assertIsNone(item["test"])
            self.assertLess(item["train"]["pnl"], 0)

    def test_gpu_holdout(self):
        gpu = self.fast["results"][-1]
        self.assertEqual(gpu["test"]["n"], 12)
        self.assertAlmostEqual(gpu["test"]["pnl"], 0.74)
        self.assertEqual(gpu["corrected_market_null_p"], 1.0)

    def test_sizing_and_paper_invariants(self):
        one, two = self.sizing["results"]["test"][:2]
        self.assertAlmostEqual(one["pnl"], 7.148)
        self.assertGreater(two["pnl_per_day"], one["pnl_per_day"])
        self.assertFalse(self.sizing["historical_depth_available"])
        self.assertEqual(self.config["mode"], "paper")
        self.assertEqual(self.config["strategy"], "robust-rv")
        self.assertEqual(self.config["risk"]["maxContracts"], 1)
        self.assertNotIn("keyId", self.config["api"])

    def test_report_exists(self):
        self.assertGreater(os.path.getsize("FASTEST_PLAUSIBLE_AUTOMATION.md"), 2500)


if __name__ == "__main__":
    unittest.main()
