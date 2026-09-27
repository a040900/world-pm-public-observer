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

    def test_pm_cutoff_rejects_future_receipt_even_if_source_timestamp_is_old(self):
        rows = [
            {
                "bids": [{"price": 0.49}],
                "asks": [{"price": 0.51}],
                "sourceTimestampMs": 999900.0,
                "effectiveAt": 1000.100,
                "connectionGeneration": 1,
            },
            {
                "bids": [{"price": 0.49}],
                "asks": [{"price": 0.51}],
                "sourceTimestampMs": 999900.0,
                "effectiveAt": 999.990,
                "connectionGeneration": 1,
            },
        ]
        out = r._pm_proxy_from_states(rows, cutoff=1000.000)
        self.assertFalse(out["valid"])
        self.assertEqual(out["reason"], "PM_STATE_INVALID_OR_FUTURE_RECEIPT")

    def test_pm_cutoff_accepts_only_states_observer_available_by_cutoff(self):
        rows = [
            {
                "bids": [{"price": 0.59}],
                "asks": [{"price": 0.61}],
                "sourceTimestampMs": 999900.0,
                "effectiveAt": 999.990,
                "connectionGeneration": 1,
            },
            {
                "bids": [{"price": 0.39}],
                "asks": [{"price": 0.41}],
                "sourceTimestampMs": 999900.0,
                "effectiveAt": 999.995,
                "connectionGeneration": 1,
            },
        ]
        out = r._pm_proxy_from_states(rows, cutoff=1000.000)
        self.assertTrue(out["valid"])
        self.assertAlmostEqual(out["proxy"], 0.6)
        self.assertLessEqual(out["upEffectiveAt"], out["cutoffAt"])
        self.assertLessEqual(out["downEffectiveAt"], out["cutoffAt"])

    def test_cex_at_or_before_never_uses_future_receipt(self):
        from collections import deque
        points = deque([
            {"receivedAt": 999.9, "price": 100.0},
            {"receivedAt": 1000.1, "price": 101.0},
        ])
        point = r._at_or_before(points, 1000.0)
        self.assertEqual(point["price"], 100.0)

    def test_pm_cutoff_rejects_cross_generation_pair(self):
        rows = [
            {"bids": [{"price": .59}], "asks": [{"price": .61}], "sourceTimestampMs": 1.0,
             "effectiveAt": 1000.0, "connectionGeneration": 1},
            {"bids": [{"price": .39}], "asks": [{"price": .41}], "sourceTimestampMs": 1.0,
             "effectiveAt": 1000.0, "connectionGeneration": 2},
        ]
        out = r._pm_proxy_from_states(rows, cutoff=1000.0)
        self.assertFalse(out["valid"])
        self.assertEqual(out["reason"], "PM_CONNECTION_GENERATION_MISMATCH")

    def test_causal_timeline_does_not_move_duplicate_state_availability_forward(self):
        book = object.__new__(r.CausalTimelineBook)
        book.history = {"x": []}
        book._state_point = lambda token, at: {
            "bids": [{"price": .49}], "asks": [{"price": .51}],
            "sourceTimestampMs": at * 1000, "effectiveAt": at,
            "connectionGeneration": 1, "localDigest": "same",
        }
        book._record("x", 1000.0)
        book._record("x", 1000.2)
        self.assertEqual(book.state_at("x", 1000.1)["effectiveAt"], 1000.0)


if __name__ == "__main__":
    unittest.main()
