"""Build and place a multi-item Instamart basket.

``swiggy.cli.order`` picks from ONE search, which is fine for a single item but cannot
express a real grocery list: ``update_cart`` REPLACES the cart, so every line -- however
many searches found them -- has to go in a single call. This script takes the spinIds
directly, which is the only stable way to name a variation across searches::

    python -m swiggy.cli.order  dahi                        # find spinIds
    python -m swiggy.cli.basket 5V35CLXB8O:1 Q3L8WG2OTP:1   # build cart, show bill
    python -m swiggy.cli.basket 5V35CLXB8O:1 --place --max-total=258

Flags: ``--address=<id> --upi=<intentApp> --upi-qr --place --max-total=<n>``
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..common import (
    INSTAMART_MINIMUM_INR,
    ORDER_CEILING_INR,
    address_label,
    read_cart_coords,
    read_cart_total,
)
from ..swiggy.instamart import InstamartOrdering
from ..swiggy.payment import method_label
from ..swiggy.session import SwiggySession
from ._args import die, flag, positional, require_token
from .order import read_payment


def parse_lines() -> list[dict[str, Any]]:
    """``spinId:qty``, qty defaulting to 1."""
    lines: list[dict[str, Any]] = []
    for arg in positional():
        spin_id, _, raw_qty = arg.partition(":")
        if not spin_id:
            die(f'Could not read a spinId from "{arg}". Use spinId:qty.')
        try:
            quantity = 1 if raw_qty == "" else int(raw_qty)
        except ValueError:
            quantity = 0
        if quantity < 1:
            die(f'Quantity for {spin_id} must be a positive whole number, got "{raw_qty}".')
        lines.append({"spinId": spin_id, "quantity": quantity})

    if not lines:
        die("Nothing to add. Pass one or more spinId:qty (find them with swiggy.cli.order).")

    spin_ids = [line["spinId"] for line in lines]
    if len(set(spin_ids)) != len(spin_ids):
        die("A spinId is listed twice. Ask for it once with the total quantity.")
    return lines


def read_max_total() -> float | None:
    """Refuse to check out above this amount.

    Instamart pricing is LIVE -- a Rain Fee appeared and was renamed "High Demand Surge
    Fee" mid-session -- so the total shown when a human said "yes" can differ from the
    total at checkout. This pins the approval to a number: above it, the script aborts
    instead of paying more than was agreed. Essential when the confirmation is scripted
    rather than typed.
    """
    raw = flag("max-total")
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        die(f'--max-total must be a number, got "{raw}"')
        return None


async def main() -> None:
    token = require_token()
    lines = parse_lines()
    place = flag("place") is not None
    max_total = read_max_total()
    payment = read_payment()

    async with SwiggySession.connect("instamart", token) as session:
        im = InstamartOrdering(session)

        addresses = await im.addresses()
        wanted = flag("address")
        address = next((a for a in addresses if a.get("id") == wanted), None) if wanted else (addresses[0] if addresses else None)
        if address is None:
            die(f'No address with id "{wanted}".' if wanted else "No saved addresses on this account.")
        print(f"Delivering to: {address_label(address)}")

        await im.update_cart(str(address["id"]), lines)
        cart = await im.get_cart()
        items = cart.get("items") or []

        print(f"\n--- cart ({len(items)} line{'' if len(items) == 1 else 's'}) ---")
        for item in items:
            price = item.get("discountedFinalPrice") or item.get("mrp")
            variant = f" [{item['itemVariant']}]" if item.get("itemVariant") else ""
            oos = "   OUT OF STOCK" if item.get("isInStockAndAvailable") is False else ""
            print(f"  {item.get('quantity')} × {item.get('itemName') or '(unnamed)'}{variant}  ₹{price}{oos}")

        # Every requested spinId must come back, or the server dropped a line silently.
        returned = {str(item.get("spinId")) for item in items}
        missing = [line["spinId"] for line in lines if line["spinId"] not in returned]
        if missing:
            print(f"\n! Not in the returned cart: {', '.join(missing)}")
            print("  The server dropped these — likely unavailable at this address.")

        # Live shape: billBreakdown.lineItems[] = {label, value} and toPay = {label,
        # value}, where value is a pre-formatted string like "₹91.00".
        breakdown = cart.get("billBreakdown") or {}
        rows = breakdown.get("lineItems") or []
        if rows or breakdown.get("toPay"):
            print("\n--- bill ---")
            for row in rows:
                print(f"  {str(row.get('label','')):<24} {row.get('value','')}")
            to_pay = breakdown.get("toPay")
            if isinstance(to_pay, dict):
                print(f"  {'-' * 24}")
                print(f"  {str(to_pay.get('label','')):<24} {to_pay.get('value','')}")

        coords = read_cart_coords(cart)
        total = read_cart_total(cart)
        if total is None:
            print(
                f"\n! Could not read the bill total, so the ₹{ORDER_CEILING_INR} ceiling "
                f"and ₹{INSTAMART_MINIMUM_INR} minimum cannot be checked here."
            )
        else:
            print(f"\nTo pay: ₹{total:g}")
            if total < INSTAMART_MINIMUM_INR:
                print(f"  ! Below the ₹{INSTAMART_MINIMUM_INR} minimum — checkout will refuse.")
            if total >= ORDER_CEILING_INR:
                print(f"  ! At or above the ₹{ORDER_CEILING_INR} beta ceiling — order in the app.")

        options = await im.payment_options()
        print("\n--- payment options ---")
        for method in options.get("allMethods") or []:
            off = "   (unavailable for this cart)" if method.get("enabled") is False else ""
            print(f"  --upi={str(method.get('id','')):<18}{method_label(method)}{off}")
        cod = options.get("cod") or {}
        if cod and cod.get("available") is not False:
            print(f"  (default)           {cod.get('displayName') or cod.get('label') or 'Cash on Delivery'}")

        if not place:
            print(f"\nNothing has been ordered. Add --place to check out with: {type(payment).__name__}")
            return

        if max_total is not None:
            if total is None:
                die(
                    f"\n--max-total={max_total:g} was given but the bill total could not be "
                    f"read. Refusing to check out against an unknown amount."
                )
            if total > max_total:
                die(
                    f"\nABORTED: the cart is now ₹{total:g}, above the ₹{max_total:g} you "
                    f"approved. Nothing was ordered — re-confirm at the new price."
                )
            print(f"\nTotal ₹{total:g} is within the approved ₹{max_total:g}.")

        shown = "" if total is None else f" for ₹{total:g}"
        answer = input(f'\nPlace this order{shown}, paying by {type(payment).__name__}? Type "yes": ')
        if answer.strip().lower() != "yes":
            print("Cancelled. Nothing was ordered.")
            return

        result = await im.checkout(str(address["id"]), payment, user_confirmed=True, cart_total=total)

        async def track(order_id: str) -> None:
            if coords is None:
                print(f"Order placed ({order_id}). track_order needs lat/lng, which the cart lacked.")
                return
            print(await im.track(order_id, coords[0], coords[1]))

        if result.kind == "placed":
            print(f"\nPlaced. Order {result.order.get('orderId')} — {result.order.get('status')}")
            await track(str(result.order.get("orderId")))
        else:
            print(f"\nOrder {result.order.get('orderId')} is awaiting payment.")
            if result.order.get("bridgeUrl"):
                print(f"Pay here: {result.order['bridgeUrl']}")
            outcome = await im.settle(
                result.order, on_pending=lambda s: print(f"  … {s.get('status') or 'pending'}")
            )
            print(f"\n{outcome.outcome} — order {outcome.order_id}")
            if outcome.outcome == "placed":
                await track(outcome.order_id)


if __name__ == "__main__":
    asyncio.run(main())
