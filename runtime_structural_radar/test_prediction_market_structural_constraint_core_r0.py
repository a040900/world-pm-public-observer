import unittest

from runtime_structural_radar import prediction_market_structural_constraint_core_r0 as c


class StructuralConstraintCoreTests(unittest.TestCase):
    def test_deadline_implication_direction(self):
        # earlier => later, so NO(earlier)+YES(later) is the guaranteed package.
        out = c.implication_package("earlier", .42, 100, "later", .55, 80)
        self.assertAlmostEqual(out.guaranteed_payout, 1.0)
        self.assertAlmostEqual(out.acquisition_cost, .97)
        self.assertAlmostEqual(out.net_edge, .03)
        self.assertEqual(out.capacity, 80)

    def test_threshold_implication_direction(self):
        # higher-threshold event => lower-threshold event.
        out = c.implication_package("higher", .40, 50, "lower", .57, 60)
        self.assertAlmostEqual(out.net_edge, .03)

    def test_mutually_exclusive_no_pair(self):
        out = c.mutually_exclusive_pair("a", .45, 10, "b", .50, 12)
        self.assertAlmostEqual(out.net_edge, .05)
        self.assertEqual(out.capacity, 10)

    def test_collectively_exhaustive_yes_package(self):
        out = c.collectively_exhaustive_yes((("a", .2, 10), ("b", .3, 9), ("c", .45, 8)))
        self.assertAlmostEqual(out.net_edge, .05)
        self.assertEqual(out.capacity, 8)

    def test_costs_cannot_create_fake_positive(self):
        out = c.implication_package("a", .45, 10, "b", .50, 10, fee_cost=.03, reserve_cost=.03)
        self.assertAlmostEqual(out.net_edge, -.01)

    def test_invalid_ask_rejected(self):
        with self.assertRaises(ValueError):
            c.implication_package("a", 1.0, 10, "b", .4, 10)


if __name__ == "__main__":
    unittest.main()
