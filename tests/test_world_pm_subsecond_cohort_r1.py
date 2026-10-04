"""Full qualified/purged cohort-level B4 regressions, synthetic only."""
import copy
import unittest
from unittest.mock import patch

import numpy as np

from tools.research import world_pm_subsecond_analyze_r1 as analysis
from tools.research import world_pm_subsecond_model_r1 as numerical


START = 1_800_000_000  # Synthetic market-aligned UTC, never a sampling schedule.
END = START + 43_200


def fixture(copies=False):
    """Fixed qualification mask: 500 eligible anchors per market, 36k/half."""
    rng = np.random.default_rng(20261004)
    times = np.concatenate([START + window * 300 + 40 + np.arange(500) * 0.5
                            for window in range(144)])
    pm = rng.normal(size=(len(times), 7))
    world = (np.column_stack((2 * pm[:, 0] - 3 * pm[:, 2], pm[:, 5] + pm[:, 6]))
             if copies else rng.normal(size=(len(times), 2)))
    rows = {}
    for k in numerical.HORIZONS:
        y = 0.005 * pm[:, 0] + (k * 0.01) * world[:, 0] + rng.normal(scale=0.001, size=len(times))
        rows[k] = analysis.Rows(times.copy(), (times // 300).astype(int) * 300,
                                pm.copy(), world.copy(), y, np.full(len(times), 0.1), {})
    return rows


def run(rows):
    return analysis.analyze_rows(rows, START, END, receive_times_reliable=True,
                                 capture_complete=True, markets=[
                                     {"startTs": START + i * 300, "endTs": START + (i + 1) * 300}
                                     for i in range(144)])


class CohortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = fixture()
        cls.original_report = run(cls.original)

    def test_b4_linear_copies_through_full_cohort_produce_exact_zero(self):
        report = run(fixture(copies=True))
        self.assertGreaterEqual(report["qualification"]["firstHalfCommonGrids"], 30_000)
        self.assertEqual(report["verdict"], "NO-GO")
        for fit in [entry["parameters"] for entry in report["inner"].values()] + [report["outer"]["parameters"]]:
            self.assertEqual(fit["residualKeep"], [False, False])
            self.assertTrue(all(std <= 1e-10 for std in fit["residualStd"]))
            self.assertTrue(fit["reuseBaseline"])
        self.assertEqual(report["outer"]["score"]["delta"], 0.0)
        self.assertEqual(report["outer"]["bootstrap5min"]["confidenceInterval"], [0.0, 0.0])

    def test_b4_inner_changes_leave_0_4h_parameters_unchanged(self):
        changed = copy.deepcopy(self.original)
        for rows in changed.values():
            mask = (rows.timestamps >= START + 14400) & (rows.timestamps < START + 21600)
            rows.pm[mask] *= -2
            rows.world[mask] *= 3
            rows.target[mask] *= -4
        report = run(changed)
        for k in numerical.HORIZONS:
            self.assertEqual(self.original_report["inner"][str(k)]["parameters"],
                             report["inner"][str(k)]["parameters"])
        # No assertion on k*: inner validation deliberately controls selection.

    def test_b4_outer_changes_leave_k_and_0_6h_parameters_unchanged(self):
        changed = copy.deepcopy(self.original)
        for rows in changed.values():
            mask = rows.timestamps >= START + 21600
            rows.pm[mask] *= -2
            rows.world[mask] *= 3
            rows.target[mask] *= -4
        report = run(changed)
        self.assertEqual(self.original_report["selectedHorizonSeconds"], report["selectedHorizonSeconds"])
        self.assertEqual(self.original_report["outer"]["parameters"], report["outer"]["parameters"])
        self.assertNotEqual(self.original_report["outer"]["score"], report["outer"]["score"])

    def test_common_first_half_grid_not_individual_horizon_counts(self):
        changed = copy.deepcopy(self.original)
        rows = changed[2.0]
        first = rows.timestamps < START + 21600
        mask = ~first
        mask[np.flatnonzero(first)[:29_999]] = True
        changed[2.0] = rows.subset(mask)
        with patch.object(analysis, "select_horizon", side_effect=AssertionError("must not fit")):
            report = run(changed)
        self.assertEqual(report["verdict"], "NO_RESULT_DATA_INSUFFICIENT")
        self.assertEqual(report["qualification"]["firstHalfCommonGrids"], 29_999)

    def test_unchosen_second_half_does_not_block_selected_horizon(self):
        changed = copy.deepcopy(self.original)
        k = self.original_report["selectedHorizonSeconds"]
        for other in numerical.HORIZONS:
            if other != k:
                r = changed[other]
                changed[other] = r.subset(r.timestamps < START + 21600)
        report = run(changed)
        self.assertEqual(report["selectedHorizonSeconds"], k)
        self.assertEqual(report["verdict"], self.original_report["verdict"])

    def test_upfront_purge_including_outer_training_and_end(self):
        boundary = START + 14400
        ts = np.array([boundary - 2, boundary - 0.5, boundary,
                       START + 21600 - 0.5, END - 0.5])
        rows = analysis.Rows(ts, (ts // 300).astype(int) * 300, np.ones((5, 7)),
                             np.ones((5, 2)), np.ones(5), np.ones(5), {})
        final = analysis.purge(rows, 1.0, START, END)
        self.assertEqual(final.timestamps.tolist(), [boundary - 2, boundary])

    def test_world_error_gap_not_expanded(self):
        events = [{"type": "gap_open", "market_start": START, "venue": "world", "gap_start": START + 20},
                  {"type": "gap_close", "market_start": START, "venue": "world", "gap_start": START + 20,
                   "gap_end": START + 40}]
        self.assertEqual(analysis.expanded_gaps(events, END), {})

    def test_unreliable_receipts_have_data_no_result_priority(self):
        with patch.object(analysis, "select_horizon", side_effect=AssertionError("must not fit")):
            report = analysis.analyze_rows(self.original, START, END, receive_times_reliable=False,
                                           capture_complete=True, markets=[])
        self.assertEqual(report["verdict"], "NO_RESULT_DATA_INSUFFICIENT")


if __name__ == "__main__":
    unittest.main()
