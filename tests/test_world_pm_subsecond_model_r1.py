"""Offline B4 numerical regressions for the frozen subsecond model."""

import unittest

import numpy as np

from tools.research import world_pm_subsecond_model_r1 as model


class SubsecondModelTests(unittest.TestCase):
    def setUp(self):
        rows = 12
        base = np.arange(rows, dtype=np.float64)
        self.pm = np.column_stack([base + j * 0.25 for j in range(7)])
        # World columns are exact affine copies of PM columns.  After
        # standardization they are exactly in the PM span, so no World-only
        # residual signal should survive the tolerance gate.
        self.world = np.column_stack((2.0 * self.pm[:, 0] + 7.0,
                                      -3.0 * self.pm[:, 1] + 2.0))
        self.target = 0.1 + 0.02 * base + 0.001 * base**2
        self.row_ids = [f"train-{i}" for i in range(rows)]

    def test_exact_world_columns_are_pm_linear_copies_and_loss_is_zero(self):
        pair = model.FittedPair.fit(self.pm, self.world, self.target, self.row_ids)

        self.assertTrue(np.all(pair.residual_std <= model.RESIDUAL_TOLERANCE))
        self.assertFalse(np.any(pair.residual_keep))
        self.assertIs(pair.base, pair.full)

        d, summary = model.paired_loss(pair, self.pm, self.world, self.target)
        self.assertTrue(np.array_equal(d, np.zeros(len(self.pm))))
        self.assertEqual(float(np.max(np.abs(d))), 0.0)
        self.assertEqual(summary["mseBaseline"], summary["mseFull"])

    def test_rcond_and_tolerance_drop_redundant_world_residuals(self):
        pair = model.FittedPair.fit(self.pm, self.world, self.target, self.row_ids)
        self.assertEqual(model.OLS_RCOND, 1e-12)
        self.assertEqual(model.RESIDUAL_TOLERANCE, 1e-10)
        self.assertEqual(pair.residual_keep.tolist(), [False, False])
        self.assertTrue(np.all(pair.residual_std < 1e-10))

    def test_validation_replacement_does_not_change_fitted_parameters(self):
        pair = model.FittedPair.fit(self.pm, self.world, self.target, self.row_ids)
        before = pair.parameters()

        validation_pm = self.pm[::-1].copy()
        validation_world = self.world[::-1].copy()
        validation_target = np.linspace(-0.3, 0.4, len(self.pm))
        model.paired_loss(pair, validation_pm, validation_world, validation_target)

        self.assertEqual(before, pair.parameters())

    def test_bootstrap_is_row_weighted_with_equal_block_draws(self):
        # One sparse block and one dense block.  Returned S/N must retain the
        # row counts, while the bootstrap samples blocks with equal probability.
        d = np.array([0.0] + [1.0] * 9)
        timestamps = np.array([0.0] + [300.0] * 9)
        starts = np.array([0] + [300] * 9)
        result = model.bootstrap(d, timestamps, starts, 300)

        self.assertEqual([(x["startTs"], x["S"], x["N"]) for x in result["blocks"]],
                         [(0, 0.0, 1), (300, 9.0, 9)])
        self.assertEqual(result["confidenceInterval"], [0.0, 1.0])

    def test_bootstrap_uses_utc_15min_blocks_without_gap_compression(self):
        d = np.array([1.0, 3.0])
        timestamps = np.array([0.0, 1800.0])
        starts = np.array([0, 0])
        result = model.bootstrap(d, timestamps, starts, 900)

        self.assertEqual([x["startTs"] for x in result["blocks"]], [0, 1800])
        self.assertEqual([x["N"] for x in result["blocks"]], [1, 1])
        self.assertEqual([x["S"] for x in result["blocks"]], [1.0, 3.0])

    def test_correlation_rejects_zero_variance(self):
        with self.assertRaisesRegex(model.AnalysisInvalid,
                                    "CORRELATION_UNDEFINED_ZERO_VARIANCE"):
            model.correlation([1.0, 1.0, 1.0], [1.0, 2.0, 3.0])
        with self.assertRaisesRegex(model.AnalysisInvalid,
                                    "CORRELATION_UNDEFINED_ZERO_VARIANCE"):
            model.correlation([1.0, 2.0, 3.0], [2.0, 2.0, 2.0])


if __name__ == "__main__":
    unittest.main()
