"""Budget engine — pure functions, no I/O, so the maths is testable on its own.

All money in whole rupees. Deliberately does not clamp `recommended_daily_inr` to zero
when overspent: a negative number is the honest signal that today's allowance is already
gone, and the UI decides how to say so.
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass

__all__ = ["BudgetState", "compute_budget", "month_window"]


@dataclass(frozen=True)
class BudgetState:
    budget_inr: int
    spent_inr: int
    remaining_inr: int
    days_remaining: int
    average_daily_inr: int
    recommended_daily_inr: int
    projected_inr: int
    status: str  # "within" | "at-risk" | "over"


def compute_budget(
    *, budget_inr: int, spent_inr: int, days_in_period: int, days_elapsed: int
) -> BudgetState:
    elapsed = max(1, min(days_elapsed, days_in_period))
    days_remaining = max(0, days_in_period - elapsed)
    remaining = budget_inr - spent_inr

    average_daily = round(spent_inr / elapsed)
    # On the last day the whole remainder is today's allowance — dividing by zero days
    # would otherwise blow up.
    recommended_daily = remaining if days_remaining == 0 else round(remaining / days_remaining)
    projected = round(average_daily * days_in_period)

    if remaining < 0:
        status = "over"
    elif projected > budget_inr:
        status = "at-risk"
    else:
        status = "within"

    return BudgetState(
        budget_inr=budget_inr,
        spent_inr=spent_inr,
        remaining_inr=remaining,
        days_remaining=days_remaining,
        average_daily_inr=average_daily,
        recommended_daily_inr=recommended_daily,
        projected_inr=projected,
        status=status,
    )


def month_window(now: dt.datetime | None = None) -> tuple[int, int, dt.date]:
    """(days_in_period, days_elapsed, period_start) for the default monthly budget."""
    now = now or dt.datetime.now(dt.timezone.utc)
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    return days_in_month, now.day, dt.date(now.year, now.month, 1)
