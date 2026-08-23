"""The shared UPI payment stage, used identically by Food and Instamart.

    get_payment_options -> place-order -> check_payment_status -> confirm_order

This module implements the HEADLESS path: nothing auto-polls or auto-confirms for us,
so we own the cadence and the finalisation.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .envelope import SwiggyToolError, is_failure
from .session import SwiggySession

__all__ = [
    "Cash",
    "UpiApp",
    "UpiQr",
    "PaymentChoice",
    "payment_args",
    "method_label",
    "is_pending_payment",
    "PaymentBinding",
    "PaymentOutcome",
    "settle_payment",
]


# --------------------------------------------------------------------------
# The user's pick, normalised
# --------------------------------------------------------------------------
# NPCI compliance: never collect a VPA / UPI ID from the user, and never ask which
# device they are on -- `get_payment_options` resolves both surfaces server-side.


@dataclass(frozen=True)
class Cash:
    pass


@dataclass(frozen=True)
class UpiApp:
    #: A method ``id`` copied byte-for-byte from ``get_payment_options``.
    intent_app: str


@dataclass(frozen=True)
class UpiQr:
    pass


PaymentChoice = Cash | UpiApp | UpiQr


def payment_args(choice: PaymentChoice) -> dict[str, Any]:
    """Translate a choice into place-order arguments (identical for both servers)."""
    if isinstance(choice, Cash):
        return {"paymentMethod": "Cash"}
    if isinstance(choice, UpiApp):
        return {"paymentMethod": "UPI", "intentApp": choice.intent_app}
    if isinstance(choice, UpiQr):
        return {"paymentMethod": "UPI", "generateUPIQR": True}
    raise TypeError(f"not a PaymentChoice: {choice!r}")


def method_label(method: dict[str, Any]) -> str:
    """Whichever display name this server used. Never send this -- send ``id``.

    The live server calls it ``displayName``; the docs call it ``label``.
    """
    for key in ("displayName", "label"):
        value = method.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return str(method.get("id", "?"))


def is_pending_payment(order: Any) -> bool:
    """UPI place-order response: the order exists but is NOT yet placed."""
    return isinstance(order, dict) and order.get("status") == "PENDING_PAYMENT"


# --------------------------------------------------------------------------
# Payment status
# --------------------------------------------------------------------------

# Terminal statuses per the check_payment_status table. `cart_changed` is a terminal
# FAILURE that is not a payment failure -- the cart moved under us and the order was
# never placed, so the fix is "review cart and place again", not "retry pay".
_TERMINAL_SUCCESS = frozenset({"success", "paid"})
_RETRYABLE = frozenset({"failed", "cart_changed"})


class PaymentBinding(Protocol):
    """Per-server argument binding.

    This exists because the two servers genuinely differ, and getting it wrong strands
    orders. From the "Per-server contract" table in pay-with-upi.md::

        Food       confirm_order -> orderId + addressId + lat + lng   (NOT paasId)
        Instamart  confirm_order -> orderId + paasId

    Food additionally requires addressId + lat + lng on ``check_payment_status``, or the
    server cannot reconcile the order and it stays stuck pending.
    """

    def status_args(self, pending: dict[str, Any]) -> dict[str, Any]: ...
    def confirm_args(self, pending: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PaymentOutcome:
    #: ``"placed"`` | ``"failed"`` | ``"timeout"``
    outcome: str
    order_id: str
    status: dict[str, Any] | None = None
    retryable: bool = False


# Fallbacks only for when place-order omits the window; prefer the server's values.
_FALLBACK_INTERVAL_MS = 5_000
_FALLBACK_WINDOW_MS = 300_000


async def settle_payment(
    session: SwiggySession,
    pending: dict[str, Any],
    binding: PaymentBinding,
    on_pending: Callable[[dict[str, Any]], None] | None = None,
    sleep: Callable[[float], Any] | None = None,
    now: Callable[[], float] | None = None,
) -> PaymentOutcome:
    """Drive the payment to a terminal outcome, then finalise.

    ``check_payment_status`` is a ~19s long-poll, so this sleeps between reads rather
    than tight-looping -- hammering it stresses Swiggy's payment cache. The loop is
    capped by the window the place-order response handed us.

    The outcome is read off the payload's ``status``, NOT off envelope success: a
    terminal payment failure is still a *successful status read*.
    """
    _sleep = sleep or asyncio.sleep
    _now = now or (lambda: time.monotonic() * 1000)

    interval_ms = pending.get("pollingIntervalInMs") or _FALLBACK_INTERVAL_MS
    deadline = _now() + (pending.get("maxTimeToPollForInMs") or _FALLBACK_WINDOW_MS)
    order_id = str(pending.get("orderId", "?"))

    last: dict[str, Any] | None = None
    while _now() < deadline:
        raw = await session.call_raw("check_payment_status", binding.status_args(pending))

        # A transport/tool error (bad or missing paasId) DOES use the failure envelope.
        # Everything else is the bare payload -- `success` is absent on success, so this
        # must key off `success is False` and NOT off a falsy `success`.
        if is_failure(raw):
            error = raw.get("error") or {}
            raise SwiggyToolError(
                "check_payment_status",
                error.get("message") or "status read failed",
                raw.get("hint") or error.get("reportHint"),
            )

        last = raw if isinstance(raw, dict) else {}
        if last.get("terminal"):
            break
        if on_pending is not None:
            on_pending(last)
        await _sleep(interval_ms / 1000)

    if last is not None and last.get("terminal"):
        if last.get("status") in _TERMINAL_SUCCESS:
            # Normally the backend already confirmed for us. Only confirm if it says
            # it didn't.
            if not last.get("confirmed"):
                await session.call("confirm_order", binding.confirm_args(pending))
            return PaymentOutcome("placed", order_id, last)

        # Terminal failure. Do NOT confirm. Only `failed` and `cart_changed` are worth
        # another attempt; a refund already underway must not be retried.
        return PaymentOutcome(
            "failed", order_id, last, retryable=last.get("status") in _RETRYABLE
        )

    # Hit our cap while still pending -> confirm once to finalise. The backend marks the
    # order failed if payment is still non-terminal, and a late success reconciles
    # server-side. This is the one case where we confirm without a success status.
    await session.call("confirm_order", binding.confirm_args(pending))
    return PaymentOutcome("timeout", order_id, last)
