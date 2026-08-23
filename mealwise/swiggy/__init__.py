"""Swiggy MCP client — Food and Instamart.

Food and Instamart are separate endpoints with separate carts, so ordering across both
means two sessions. Every parameter and response field here was verified against the
live server; see the repo README for where the published docs disagree.
"""

from .envelope import SwiggyIndeterminateOrderError, SwiggyToolError, is_failure, unwrap
from .food import FoodOrdering
from .instamart import CouponAccess, InstamartOrdering
from .payment import (
    Cash,
    PaymentChoice,
    PaymentOutcome,
    UpiApp,
    UpiQr,
    method_label,
    settle_payment,
)
from .session import SwiggyServer, SwiggySession

__all__ = [
    "SwiggySession",
    "SwiggyServer",
    "InstamartOrdering",
    "FoodOrdering",
    "CouponAccess",
    "Cash",
    "UpiApp",
    "UpiQr",
    "PaymentChoice",
    "PaymentOutcome",
    "method_label",
    "settle_payment",
    "SwiggyToolError",
    "SwiggyIndeterminateOrderError",
    "is_failure",
    "unwrap",
]
