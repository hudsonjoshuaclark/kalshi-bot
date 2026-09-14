"""Mechanical verification of final research artifacts."""

import json
import os
import unittest


class FinalResearchVerification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.chdir(os.path.dirname(os.path.abspath(__file__)))
        with open("final_recommendation.json", "r", encoding="utf-8") as handle:
            cls.final = json.load(handle)
        with open("volatility_oos_results.json", "r", encoding="utf-8") as handle:
            cls.vol = json.load(handle)
        with open("microstructure_oos_results.json", "r", encoding="utf-8") as handle:
            cls.micro = json.load(handle)
        with open("s16_dynamic_ladder_train.json", "r", encoding="utf-8") as handle:
            cls.ladder = json.load(handle)

    def test_global_search_count(self):
        self.assertEqual(self.final["global_search_count"], 298)
        self.assertEqual(298, self.vol["global_search_count"] +
                         self.micro["audit"]["new_search_count"] + self.ladder["search_count"])

    def test_recommendation_matches_frozen_winner(self):
        chosen = next(item for item in self.vol["results"]
                      if item["strategy"] == "Robust RV estimator")
        self.assertEqual(self.final["recommendation"]["config"], chosen["config"])
        self.assertEqual(self.final["recommendation"]["size"], "1 contract per qualifying market")
        self.assertAlmostEqual(chosen["test"]["pnl"], 7.148)
        self.assertEqual(chosen["test"]["n"], 200)

    def test_partition_and_null_audits(self):
        self.assertTrue(all(self.vol["completion_audit"].values()))
        self.assertTrue(self.micro["audit"]["one_observation_per_test_market"])
        self.assertTrue(self.micro["audit"]["train_before_cut"])
        self.assertTrue(self.micro["audit"]["promoted_test_at_or_after_cut"])
        self.assertEqual(self.micro["audit"]["null_redraws"], 20_000)

    def test_final_report_exists(self):
        self.assertGreater(os.path.getsize("FINAL_RESEARCH_RESULTS.md"), 1000)


if __name__ == "__main__":
    unittest.main()
