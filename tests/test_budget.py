"""Budget engine tests, including the worked example from the product spec."""

import datetime as dt
import unittest

from mealwise.budget import compute_budget, month_window


class TestComputeBudget(unittest.TestCase):
    def test_spec_worked_example(self):
        # ₹15,000 budget, ₹10,000 spent, 10 of 30 days left -> ₹500/day.
        state = compute_budget(
            budget_inr=15000, spent_inr=10000, days_in_period=30, days_elapsed=20
        )
        self.assertEqual(state.remaining_inr, 5000)
        self.assertEqual(state.days_remaining, 10)
        self.assertEqual(state.recommended_daily_inr, 500)
        self.assertEqual(state.average_daily_inr, 500)
        self.assertEqual(state.status, "within")

    def test_overspent(self):
        state = compute_budget(
            budget_inr=5000, spent_inr=6000, days_in_period=30, days_elapsed=15
        )
        self.assertEqual(state.status, "over")
        # A negative allowance is the honest signal, not something to clamp away.
        self.assertLess(state.recommended_daily_inr, 0)

    def test_last_day_does_not_divide_by_zero(self):
        state = compute_budget(
            budget_inr=5000, spent_inr=4000, days_in_period=30, days_elapsed=30
        )
        self.assertEqual(state.days_remaining, 0)
        self.assertEqual(state.recommended_daily_inr, 1000)

    def test_at_risk_projection(self):
        state = compute_budget(
            budget_inr=10000, spent_inr=6000, days_in_period=30, days_elapsed=10
        )
        self.assertEqual(state.projected_inr, 18000)
        self.assertEqual(state.status, "at-risk")

    def test_elapsed_beyond_period_is_clamped(self):
        state = compute_budget(
            budget_inr=1000, spent_inr=500, days_in_period=30, days_elapsed=99
        )
        self.assertEqual(state.days_remaining, 0)


class TestMonthWindow(unittest.TestCase):
    def test_august(self):
        days, elapsed, start = month_window(dt.datetime(2026, 8, 23, tzinfo=dt.timezone.utc))
        self.assertEqual((days, elapsed, start), (31, 23, dt.date(2026, 8, 1)))

    def test_february_leap_year(self):
        days, _, _ = month_window(dt.datetime(2028, 2, 5, tzinfo=dt.timezone.utc))
        self.assertEqual(days, 29)


if __name__ == "__main__":
    unittest.main()
