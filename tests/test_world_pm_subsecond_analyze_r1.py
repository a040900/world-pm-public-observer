"""Offline raw-row and fold-level B4 checks; never opens a feed."""
import unittest

import numpy as np

from tools.research import world_pm_subsecond_analyze_r1 as analysis
from tools.research import world_pm_subsecond_model_r1 as numerical


def synthetic_rows(start, count, seed, copies=False):
    rng = np.random.default_rng(seed)
    pm = rng.normal(size=(count, 7))
    world = (np.column_stack((2 * pm[:, 0] - pm[:, 2], pm[:, 5] + 3 * pm[:, 6]))
             if copies else rng.normal(size=(count, 2)))
    target = 0.003 * pm[:, 0] + 0.01 * world[:, 0] + rng.normal(scale=0.001, size=count)
    times = start + np.arange(count) * 0.5
    return analysis.Rows(times, (times // 300).astype(int) * 300, pm, world,
                         target, np.zeros(count), {})


def alter(rows, seed):
    rng = np.random.default_rng(seed)
    return analysis.Rows(rows.timestamps.copy(), rows.market_starts.copy(),
                         rng.normal(size=rows.pm.shape), rng.normal(size=rows.world.shape),
                         rng.normal(size=rows.target.shape), rows.pm_update_age.copy(), rows.rejected)


class SubsecondAnalyzerTests(unittest.TestCase):
    def test_b4_linear_copies_through_fold_selection_and_outer_evaluation(self):
        train = synthetic_rows(0, 400, 1, copies=True)
        validation = synthetic_rows(14400, 120, 2, copies=True)
        k, fits, scores = analysis.select_horizon({h: (train, validation) for h in numerical.HORIZONS})
        self.assertEqual(k, 0.5)  # All four exact zero improvements tie.
        for h in numerical.HORIZONS:
            self.assertFalse(fits[h].residual_keep.any())
            self.assertLessEqual(float(fits[h].residual_std.max()), 1e-10)
            self.assertEqual(scores[h]["delta"], 0.0)
        outer = analysis.evaluate_selected(train, validation)
        self.assertEqual(outer["score"]["delta"], 0.0)
        self.assertEqual(outer["bootstrap5min"]["confidenceInterval"], [0.0, 0.0])
        self.assertEqual(outer["conditionalCalculationVerdict"], "NO-GO")

    def test_b4_inner_replacement_preserves_inner_training_parameters(self):
        train = synthetic_rows(0, 100, 10)
        validation = synthetic_rows(14400, 60, 11)
        original = {h: (train, validation) for h in numerical.HORIZONS}
        changed = {h: (train, alter(validation, int(100 * h))) for h in numerical.HORIZONS}
        _k1, fits1, _s1 = analysis.select_horizon(original)
        _k2, fits2, _s2 = analysis.select_horizon(changed)
        for h in numerical.HORIZONS:
            self.assertEqual(fits1[h].parameters(), fits2[h].parameters())

    def test_b4_outer_replacement_cannot_change_selected_lag_or_outer_fit(self):
        inner_train = synthetic_rows(0, 100, 20)
        inner_validation = synthetic_rows(14400, 60, 21)
        folds = {h: (inner_train, inner_validation) for h in numerical.HORIZONS}
        outer_train = synthetic_rows(0, 160, 22)
        outer_test = synthetic_rows(21600, 80, 23)
        k1, _fits, _scores = analysis.select_horizon(folds)
        first = analysis.evaluate_selected(outer_train, outer_test)
        second = analysis.evaluate_selected(outer_train, alter(outer_test, 24))
        k2, _fits, _scores = analysis.select_horizon(folds)
        self.assertEqual(k1, k2)
        self.assertEqual(first["parameters"], second["parameters"])

    def test_b4_missing_endpoint_drops_row_but_valid_zero_label_survives(self):
        quotes = [{"sequence": 0, "market_start": 0, "venue": "pm", "side": "up",
                   "receive_time": 0.1, "source_time": 0, "mid": 0.5}]
        quotes += [{"sequence": i + 1, "market_start": 0, "venue": "world", "side": "yes",
                    "receive_time": i + 0.1, "source_time": i, "mid": 0.5} for i in range(300)]
        markets = [{"startTs": 0, "endTs": 300}]
        good = analysis.build_rows(quotes, {}, markets, 0.5)
        row = np.flatnonzero(good.timestamps == 33.5)
        self.assertEqual(len(row), 1)
        self.assertEqual(good.target[row[0]], 0)
        # A one-sided World quote cannot supply a mid at the 32s-lookback endpoint.
        incomplete = [dict(q) for q in quotes]
        incomplete[2]["mid"] = None  # Source=1, first available=1.1.
        missing = analysis.build_rows(incomplete, {}, markets, 0.5)
        self.assertNotIn(33.5, missing.timestamps)
        self.assertGreater(missing.rejected["0"]["WORLD_MID_INVALID"], 0)
        # An old but healthy quiet PM snapshot is deliberately retained.
        self.assertGreater(float(good.pm_update_age.max()), 290)

    def test_strict_receipt_and_all_endpoint_ages(self):
        quote = {"sequence": 0, "market_start": 0, "venue": "world", "side": "yes",
                 "receive_time": 1.0, "source_time": 0, "mid": 0.5}
        index = analysis.QuoteIndex([quote])
        with self.assertRaises(analysis.MissingEndpoint):
            index.at(0, "world", "yes", 1.0)
        self.assertEqual(index.at(0, "world", "yes", 4.0)["mid"], 0.5)
        with self.assertRaisesRegex(analysis.MissingEndpoint, "AGE_EXCEEDED"):
            index.at(0, "world", "yes", 4.5)

    def test_expanded_gap_is_whole_interval_not_only_end_padding(self):
        events = [{"type": "gap_open", "market_start": 0, "venue": "pm", "gap_start": 50},
                  {"type": "gap_close", "market_start": 0, "venue": "pm", "gap_start": 50, "gap_end": 150}]
        self.assertEqual(analysis.expanded_gaps(events, 300), {0: [(20, 180)]})

    def test_source_clock_ahead_of_endpoint_is_not_qualified(self):
        quote = {"sequence": 0, "market_start": 0, "venue": "world", "side": "yes",
                 "receive_time": 1.0, "source_time": 3.0, "mid": 0.5}
        with self.assertRaisesRegex(analysis.MissingEndpoint, "SOURCE_TIME_AHEAD"):
            analysis.QuoteIndex([quote]).at(0, "world", "yes", 2.0)


if __name__ == "__main__":
    unittest.main()
