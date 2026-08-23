"""Shared between the Food and Instamart ordering flows."""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "ORDER_CEILING_INR",
    "INSTAMART_MINIMUM_INR",
    "CartCeilingError",
    "assert_within_ceiling",
    "normalise_addresses",
    "address_label",
    "read_cart_coords",
    "read_cart_total",
]

# Both servers refuse to place orders at or above Rs 1000 while MCP is in beta.
#
#   Food:      "Order placement is NOT allowed for cart values of Rs 1000 or more."
#   Instamart: "Checkout is NOT allowed for cart values above the allowed limit"
#
# Checked client-side too, so we fail before the mutating call rather than after.
ORDER_CEILING_INR = 1000

# Instamart has a Rs 99 minimum (docs/build/recipes/order-groceries.md, step 4).
# Confirmed live: the Rs 20 "Small Cart Fee" disappears once the item total clears it.
INSTAMART_MINIMUM_INR = 99


class CartCeilingError(Exception):
    def __init__(self, total: float, service: str) -> None:
        self.total = total
        self.service = service
        super().__init__(
            f"{service} cart total Rs {total} is at or above the Rs {ORDER_CEILING_INR} "
            f"beta ceiling. Tell the user to place this order in the Swiggy app instead."
        )


def assert_within_ceiling(total: float, service: str) -> None:
    if total >= ORDER_CEILING_INR:
        raise CartCeilingError(total, service)


def normalise_addresses(data: Any) -> list[dict[str, Any]]:
    """``get_addresses`` may hand back the bare array or an object wrapping it.

    The docs do not pin this down and the reference pages disagree elsewhere, so
    normalise both rather than betting on one. Live, it is ``{"addresses": [...]}``.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("addresses"), list):
        return data["addresses"]
    return []


def address_label(address: dict[str, Any]) -> str:
    """A human label for an address.

    The live server sends no ``label`` field -- each address carries only
    ``{id, addressLine, phoneNumber, addressCategory, addressTag}`` -- so fall through
    the fields it does send and reach the opaque id only as a last resort.
    """
    tag = next(
        (
            v
            for v in (address.get("addressTag"), address.get("addressCategory"))
            if isinstance(v, str) and v.strip()
        ),
        None,
    )
    line = address.get("addressLine")
    line = line.strip() if isinstance(line, str) else ""
    short = f"{line[:57]}…" if len(line) > 60 else line
    if tag and short:
        return f"{tag} — {short}"
    return tag or short or str(address.get("id", "?"))


def _coerce_amount(value: Any) -> float | None:
    """Accept 349, "349", "Rs349" and "1,349" -- the docs pin down none of these."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[₹,\s]", "", value)
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def read_cart_coords(cart: Any) -> tuple[float, float] | None:
    """Delivery coordinates for ``track_order``, which requires ``lat`` and ``lng``.

    ``get_addresses`` omits them entirely, so they have to come off the cart, where the
    live payload nests them under ``selectedAddressDetails``.
    """
    if not isinstance(cart, dict):
        return None
    for scope in (cart, cart.get("selectedAddressDetails"), cart.get("selectedAddress")):
        if not isinstance(scope, dict):
            continue
        lat = _coerce_amount(scope.get("lat", scope.get("latitude")))
        lng = _coerce_amount(scope.get("lng", scope.get("longitude")))
        if lat is not None and lng is not None:
            return (lat, lng)
    return None


# `get_cart` documents no field for the payable total, so the key is probed.
# `cartTotalAmount` is what the live server actually sends (as a string).
_TOTAL_KEYS = (
    "cartTotalAmount",
    "cartTotal",
    "toPay",
    "payableAmount",
    "grandTotal",
    "orderTotal",
    "billTotal",
    "finalTotal",
    "totalAmount",
    "total",
)

_BILL_CONTAINERS = ("billBreakdown", "bill", "summary", "cart", "checkout")

_TOTAL_ROW = re.compile(r"^\s*(to\s*pay|grand\s*total|order\s*total|total)\s*$", re.I)


def _from_rows(rows: list[Any]) -> float | None:
    """A bill breakdown rendered as rows, e.g. ``[{"label": "To Pay", "value": "₹162"}]``."""
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = next(
            (row[k] for k in ("title", "name", "key", "label") if isinstance(row.get(k), str)),
            None,
        )
        if not label or not _TOTAL_ROW.match(label):
            continue
        for k in ("value", "amount", "total", "price"):
            amount = _coerce_amount(row.get(k))
            if amount is not None:
                return amount
    return None


def read_cart_total(cart: Any) -> float | None:
    """Best-effort read of the cart's payable total.

    Only used to run the ceiling/minimum guards BEFORE the mutating checkout call; the
    server enforces both itself, so an unreadable total is a warning, not a blocker.

    Deliberately not a deep search: a recursive walk would happily mistake a line
    item's price for the cart total. Only the top level and one level into known bill
    containers are considered.
    """
    if not isinstance(cart, dict):
        return None

    scopes: list[dict[str, Any]] = [cart]
    for key in _BILL_CONTAINERS:
        nested = cart.get(key)
        if isinstance(nested, dict):
            scopes.append(nested)
            # Live: `billBreakdown.toPay` is itself {"label", "value"}, not a number.
            row = _from_rows([nested])
            if row is not None:
                return row
            inner = nested.get("toPay")
            if isinstance(inner, dict):
                row = _from_rows([inner])
                if row is not None:
                    return row
        elif isinstance(nested, list):
            row = _from_rows(nested)
            if row is not None:
                return row

    for scope in scopes:
        for key in _TOTAL_KEYS:
            amount = _coerce_amount(scope.get(key))
            if amount is not None:
                return amount
    return None
