import unittest

from tools.research import world_pm_world_specific_incremental_r1 as r


class IncrementalLeadR1Tests(unittest.TestCase):
    def test_proxy_and_conditioning_semantics(self):
        self.assertAlmostEqual(r._normalized_two_side(.6, .4), .6)
        self.assertEqual(r._conditioning_class(1, -1), "WORLD_CEX_DISAGREE")
        self.assertEqual(r._conditioning_class(-1, -1), "WORLD_CEX_SAME")
        self.assertEqual(r._conditioning_class(1, 0), "CEX_NO_CONSENSUS")

    def test_future_scoring_is_directional_and_predeclared(self):
        self.assertEqual(r._score_future(.5, .51, 1, -1), "PM_FOLLOWS_WORLD")
        self.assertEqual(r._score_future(.5, .49, 1, -1), "PM_FOLLOWS_CEX")
        self.assertEqual(r._score_future(.5, .5, 1, -1), "PM_FLAT")

    def test_gate_requires_four_windows_and_twenty_scorable(self):
        rows = []
        for window in range(1, 5):
            for i in range(5):
                rows.append({
                    "windowIndex": window,
                    "conditioningClass": "WORLD_CEX_DISAGREE",
                    "future": {
                        "1": {"score": "PM_FOLLOWS_WORLD"},
                        "3": {"score": "PM_FOLLOWS_WORLD" if i < 4 else "PM_FOLLOWS_CEX"},
                        "5": {"score": "PM_FOLLOWS_WORLD"},
                    },
                })
        gate = r._continuation_gate(rows)
        self.assertTrue(gate["continuationGatePassed"])
        self.assertEqual(gate["scorableDisagreementAt3s"], 20)
        rows = [x for x in rows if x["windowIndex"] != 4]
        self.assertFalse(r._continuation_gate(rows)["continuationGatePassed"])

    def test_gate_rejects_opposite_short_horizon_majority(self):
        rows = []
        for window in range(1, 5):
            for _ in range(5):
                rows.append({
                    "windowIndex": window,
                    "conditioningClass": "WORLD_CEX_DISAGREE",
                    "future": {
                        "1": {"score": "PM_FOLLOWS_CEX"},
                        "3": {"score": "PM_FOLLOWS_WORLD"},
                        "5": {"score": "PM_FOLLOWS_WORLD"},
                    },
                })
        gate = r._continuation_gate(rows)
        self.assertTrue(gate["oppositeMajorityAt1s"])
        self.assertFalse(gate["continuationGatePassed"])

    def test_no_crossing_below_threshold(self):
        event = r._classify_episode(
            threshold=.01,
            at=100.0,
            world_current=.505,
            world_prior=.5,
            cex={"valid": True, "sign": -1},
            pm={"valid": True, "proxy": .5},
            window_index=1,
            sequence=1,
        )
        self.assertIsNone(event)


if __name__ == "__main__":
    unittest.main()
