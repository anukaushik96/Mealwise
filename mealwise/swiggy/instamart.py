"""Swiggy Instamart ordering -- POST mcp.swiggy.com/im

Journey::

    get_addresses -> search_products / your_go_to_items -> update_cart
    -> get_cart -> [list_coupons -> apply_coupon] -> get_payment_options
    -> checkout -> check_payment_status -> confirm_order -> track_order

Parameters come from the per-tool reference pages, corrected against the live server.
The order-groceries recipe is wrong about three of them (see README).

``get_delivery_status`` is deliberately absent: its own page says it is for widget/UI
polling loops and that conversational clients use ``track_order`` instead.

Live ``tools/list`` advertises 14 Instamart tools. Of the wrappers here,
``create_address``, ``delete_address`` and ``get_order_details`` are NOT advertised and
are unverified; the two coupon tools are unadvertised but still callable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..common import (
    INSTAMART_MINIMUM_INR,
    assert_within_ceiling,
    normalise_addresses,
)
from .envelope import SwiggyIndeterminateOrderError, SwiggyToolError, is_failure
from .payment import (
    PaymentChoice,
    PaymentOutcome,
    is_pending_payment,
    payment_args,
    settle_payment,
)
from .session import SwiggySession

__all__ = ["InstamartOrdering", "CouponAccess", "CheckoutResult", "INSTAMART_BINDING"]


@dataclass(frozen=True)
class CouponAccess:
    """Coupon tools are gated to whitelisted accounts.

    A not-enabled response is a business restriction, not an error, so it is modelled as
    a value rather than an exception -- an un-whitelisted account must still be able to
    order. Verified live: the gate message is
    "Coupon tools are not enabled for your account yet."
    """

    available: bool
    data: Any = None
    message: str = ""


@dataclass(frozen=True)
class CheckoutResult:
    #: ``"placed"`` (Cash: no payment leg) or ``"awaiting-payment"`` (UPI).
    kind: str
    order: dict[str, Any]


class _InstamartBinding:
    """Instamart: ``orderId`` + ``paasId`` to confirm, and no geo echo on the status read.

    This differs from Food -- do not share one binding between them.
    """

    def status_args(self, pending: dict[str, Any]) -> dict[str, Any]:
        return {"paasId": pending.get("paasId"), "orderId": pending.get("orderId")}

    def confirm_args(self, pending: dict[str, Any]) -> dict[str, Any]:
        return {"orderId": pending.get("orderId"), "paasId": pending.get("paasId")}


INSTAMART_BINDING = _InstamartBinding()


class InstamartOrdering:
    def __init__(self, session: SwiggySession) -> None:
        self._session = session

    # ----- Discover -------------------------------------------------------

    async def addresses(self) -> list[dict[str, Any]]:
        """Every saved address, across all pages.

        ``get_addresses`` paginates and caps ``pageSize`` at 10 -- an account with 23
        addresses returns only the first 10 unless you walk the pages, which silently
        hides most of the address book behind a picker that looks complete.

        Note the live payload carries NO ``lat``/``lng`` and no ``label``.
        """
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        # Bounded: `pagination.hasMore` is the server's own signal, but a malformed page
        # must not spin forever.
        for page in range(1, 51):
            raw = await self._session.call("get_addresses", {"page": page, "pageSize": 10})
            for address in normalise_addresses(raw):
                key = str(address.get("id", ""))
                if key and key not in seen:
                    seen.add(key)
                    out.append(address)
            pagination = raw.get("pagination") if isinstance(raw, dict) else None
            # No pagination block at all -> single-page response; stop.
            if not (isinstance(pagination, dict) and pagination.get("hasMore")):
                break
        return out

    async def search_products(
        self, address_id: str, query: str, offset: int | None = None
    ) -> dict[str, Any]:
        """Search the catalogue.

        Live response fields: products[].``displayName`` (there is no ``name``), and per
        variation ``spinId``, ``quantityDescription``, ``price`` (an OBJECT with
        ``mrp``/``offerPrice``/``unitLevelPrice``), ``isInStockAndAvailable`` and
        ``maxQuantity``.
        """
        args: dict[str, Any] = {"addressId": address_id, "query": query}
        if offset is not None:
            args["offset"] = offset
        return await self._session.call("search_products", args)

    async def go_to_items(self, address_id: str, offset: int | None = None) -> Any:
        """Frequently/recently ordered items -- a reorder path that skips search."""
        args: dict[str, Any] = {"addressId": address_id}
        if offset is not None:
            args["offset"] = offset
        return await self._session.call("your_go_to_items", args)

    # ----- Address book ---------------------------------------------------

    async def create_address(self, **fields: Any) -> Any:
        """NOT advertised by the live server's ``tools/list`` -- unverified."""
        return await self._session.call("create_address", fields)

    async def delete_address(self, address_id: str, *, user_confirmed: Literal[True]) -> Any:
        """Permanent and not undoable, so confirmation is required explicitly.

        NOT advertised by the live server's ``tools/list`` -- unverified.
        """
        if user_confirmed is not True:
            raise ValueError("delete_address is irreversible and requires explicit confirmation")
        return await self._session.call("delete_address", {"addressId": address_id})

    # ----- Cart -----------------------------------------------------------

    async def update_cart(self, selected_address_id: str, items: list[dict[str, Any]]) -> Any:
        """Replace the cart contents.

        This REPLACES the entire cart with ``items`` -- it is not an incremental add, so
        a multi-item basket must be sent in one call. ``selectedAddressId`` is required,
        despite the recipe omitting it.

        Each item: ``spinId`` and ``quantity`` required, ``skuId`` optional.

        Do not swap address mid-cart: clear first, or you get cross-address SKU
        mismatches.
        """
        return await self._session.call(
            "update_cart", {"selectedAddressId": selected_address_id, "items": items}
        )

    async def get_cart(self) -> dict[str, Any]:
        """The cart.

        Live shape: ``{selectedAddress, selectedAddressDetails, cartTotalAmount, items,
        billBreakdown, superData, cartId}``, where ``billBreakdown`` is
        ``{lineItems: [{label, value}], toPay: {label, value}}`` with values as
        pre-formatted strings. Coordinates for ``track_order`` live in
        ``selectedAddressDetails``.
        """
        return await self._session.call("get_cart")

    async def clear_cart(self) -> Any:
        return await self._session.call("clear_cart")

    # ----- Coupons --------------------------------------------------------

    async def list_coupons(self, address_id: str) -> CouponAccess:
        """Coupons applicable to the current cart at this address."""
        return await self._coupon_call("list_coupons", {"addressId": address_id}, "availableCoupons")

    async def apply_coupon(self, coupon_code: str) -> CouponAccess:
        """Apply a coupon and get the recalculated cart back.

        The discount lands in the returned cart's ``billBreakdown``; there is no
        documented way to remove a coupon once applied, so confirm before calling.
        """
        return await self._coupon_call("apply_coupon", {"couponCode": coupon_code}, None)

    async def _coupon_call(
        self, tool: str, args: dict[str, Any], list_key: str | None
    ) -> CouponAccess:
        """Separate the whitelist gate from a genuine failure.

        Only the not-enabled case is a value; anything else raises like every other tool.
        Both tools are absent from ``tools/list`` on a non-whitelisted account but remain
        callable, so this handles the failure envelope AND an unknown-tool rejection.
        """
        try:
            raw = await self._session.call_raw(tool, args)
        except Exception as err:  # noqa: BLE001 - re-raised unless it is the gate
            message = str(err)
            if any(
                marker in message.lower()
                for marker in ("unknown tool", "tool not found", "not found", "method not found", "not enabled")
            ):
                return CouponAccess(False, message=f"{tool} is not available on this account")
            raise

        if is_failure(raw):
            message = (raw.get("error") or {}).get("message") or "coupon tools unavailable"
            if "not enabled" in message.lower():
                return CouponAccess(False, message=message)
            raise SwiggyToolError(tool, message, raw.get("hint"))

        data = raw.get(list_key) or [] if list_key else raw
        return CouponAccess(True, data=data)

    # ----- Payment + order ------------------------------------------------

    async def payment_options(self) -> dict[str, Any]:
        """``get_payment_options`` needs no arguments -- cart context is server-side.

        This is the ONLY source of UPI methods; ``get_cart`` does not carry them despite
        what the ``checkout`` page says. Live shape: ``allMethods[]`` of
        ``{id, groupName, displayName, iconUrl, enabled}``, plus
        ``cod: {available, id, displayName}``, ``paymentAmount`` and
        ``placeOrderToolName``.
        """
        return await self._session.call("get_payment_options")

    async def checkout(
        self,
        address_id: str,
        payment: PaymentChoice,
        *,
        user_confirmed: Literal[True],
        cart_total: float | None = None,
    ) -> CheckoutResult:
        """Place the Instamart order.

        ``addressId`` is required. ``paymentMethod`` is "UPI" or "Cash" -- there is no
        "COD" value despite what the recipe shows; Cash auto-defaults if omitted.

        The docs require showing the cart, the payment methods and the full delivery
        address, and getting a clear yes, before this call -- hence ``user_confirmed``.
        """
        if user_confirmed is not True:
            raise ValueError("checkout requires explicit user confirmation")

        if cart_total is not None:
            assert_within_ceiling(cart_total, "Instamart")
            if cart_total < INSTAMART_MINIMUM_INR:
                raise ValueError(
                    f"Instamart has a Rs {INSTAMART_MINIMUM_INR} minimum; cart is "
                    f"Rs {cart_total}. Ask the user to add more items."
                )

        args = {"addressId": address_id, **payment_args(payment)}
        try:
            order = await self._session.call("checkout", args)
        except SwiggyToolError:
            # A `success: false` envelope is deterministic -- re-raise as-is.
            raise
        except Exception as err:  # noqa: BLE001
            # Anything else (5xx, socket) leaves us unable to tell whether it landed.
            raise SwiggyIndeterminateOrderError("checkout", "get_orders", err) from err

        kind = "awaiting-payment" if is_pending_payment(order) else "placed"
        return CheckoutResult(kind, order)

    async def settle(self, pending: dict[str, Any], **kwargs: Any) -> PaymentOutcome:
        """Drive an awaiting-payment order to a terminal outcome."""
        return await settle_payment(self._session, pending, INSTAMART_BINDING, **kwargs)

    # ----- Track ----------------------------------------------------------

    async def track(self, order_id: str, lat: float, lng: float) -> Any:
        """Track an order.

        ``lat`` and ``lng`` are REQUIRED by the tool, and the live ``get_addresses`` does
        NOT return them -- read them off the cart with
        :func:`~swiggy.common.read_cart_coords`.
        """
        return await self._session.call("track_order", {"orderId": order_id, "lat": lat, "lng": lng})

    async def orders(self) -> Any:
        """Use this to reconcile after an indeterminate ``checkout`` failure."""
        return await self._session.call("get_orders")

    async def order_details(self, order_id: str) -> Any:
        """NOT advertised by the live server's ``tools/list`` -- unverified."""
        return await self._session.call("get_order_details", {"orderId": order_id})

    # ----- Support --------------------------------------------------------

    async def report_error(self, **fields: Any) -> Any:
        """File an error report with the Swiggy MCP team. ``domain`` is auto-detected."""
        return await self._session.call("report_error", fields)
