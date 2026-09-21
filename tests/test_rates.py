from __future__ import annotations

import unittest

from factorio_ai_lab.metrics.rates import normalized_rate_ratio, rate_per_second


class RateMetricsTests(unittest.TestCase):
    def test_rate_per_second_normalizes_window(self) -> None:
        self.assertAlmostEqual(rate_per_second(36, 24), 1.5)
        self.assertAlmostEqual(rate_per_second(74, 32), 2.3125)

    def test_ratio_compares_rates_not_raw_counts(self) -> None:
        ratio = normalized_rate_ratio(
            candidate_count=74,
            candidate_duration_s=32,
            baseline_count=36,
            baseline_duration_s=24,
        )
        self.assertIsNotNone(ratio)
        self.assertAlmostEqual(ratio or 0.0, 1.5416666666666667)
        self.assertNotAlmostEqual(ratio or 0.0, 74 / 36)

    def test_invalid_duration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            rate_per_second(1, 0)


if __name__ == "__main__":
    unittest.main()
