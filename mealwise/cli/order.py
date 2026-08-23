"""Staged Instamart grocery ordering from the terminal.

Deliberately NOT a one-shot "order milk" command. Each stage stops and prints what it
found, and nothing mutating happens without a flag you had to type. The docs require the
user to have seen the cart, the address and the payment method before checkout; a script
that searched and paid in one breath could not honour that.

1. ``SWIGGY_TOKEN=... python -m swiggy.cli.order milk``
     lists matching products and their variations. Reads only.
2. ``... python -m swiggy.cli.order milk --pick=3 --qty=2``
     puts that variation in the cart, prints the bill and the payment options.
3. ``... python -m swiggy.cli.order milk --pick=3 --qty=2 --place``
     asks for a typed "yes" on stdin, then checks out.

``update_cart`` REPLACES the cart, so every line goes in one call and ``--pick`` takes a
list, paired positionally with ``--qty`` (one ``--qty`` applies to all)::

    python -m swiggy.cli.order atta --pick=1,4 --qty=2,1

Both picks come from a single search, which is the honest limit of a flag-driven CLI.
For a basket spanning several searches use ``swiggy.cli.basket``.

Flags: ``--address=<id> --pick=<n,...> --qty=<n,...> --upi=<intentApp> --upi-qr --place``
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from ..common import (
    INSTAMART_MINIMUM_INR,
    ORDER_CEILING_INR,
    address_label,
    read_cart_coords,
    read_cart_total,
)
from ..swiggy.instamart import InstamartOrdering
from ..swiggy.payment import Cash, PaymentChoice, UpiApp, UpiQr, method_label
from ..swiggy.session import SwiggySession
from ._args import die, flag, num_list, positional, require_token


@dataclass
class Choice:
    """Variations are flattened into one numbered list because that number is what
    ``--pick`` refers to. You add a VARIATION to the cart, never the parent product."""

    n: int
    product: dict[str, Any]
    spin_id: str
    detail: str
    in_stock: bool
    max_quantity: int | None = None
    max_quantity_message: str | None = None


def flatten(products: list[dict[str, Any]]) -> list[Choice]:
    """Field names are from the LIVE search response, which the docs do not name: the
    size label is ``quantityDescription``, ``price`` is an OBJECT
    ``{mrp, offerPrice, unitLevelPrice}``, and stock is ``isInStockAndAvailable``."""
    out: list[Choice] = []
    for product in products:
        for variation in product.get("variations") or []:
            spin_id = variation.get("spinId")
            if not spin_id:
                continue
            price = variation.get("price") or {}
            offer, mrp = price.get("offerPrice"), price.get("mrp")
            if offer is not None:
                shown = f"₹{offer} (MRP ₹{mrp})" if mrp is not None and mrp > offer else f"₹{offer}"
            elif mrp is not None:
                shown = f"₹{mrp}"
            else:
                shown = None
            bits = [
                variation.get("quantityDescription"),
                shown,
                price.get("unitLevelPrice"),
                "OUT OF STOCK" if variation.get("isInStockAndAvailable") is False else None,
            ]
            out.append(
                Choice(
                    n=len(out) + 1,
                    product=product,
                    spin_id=spin_id,
                    detail="  ·  ".join(b for b in bits if b),
                    in_stock=variation.get("isInStockAndAvailable") is not False,
                    max_quantity=variation.get("maxQuantity"),
                    max_quantity_message=variation.get("maxQuantityMessage"),
                )
            )
    return out


def label(choice: Choice) -> str:
    """The live product title is ``displayName``; ``brand`` often repeats it."""
    name = (choice.product.get("displayName") or "").strip()
    brand = (choice.product.get("brand") or "").strip()
    if name and brand and not name.lower().startswith(brand.lower()):
        return f"{brand} {name}"
    return name or brand or "(unnamed)"


def read_payment() -> PaymentChoice:
    """Cash unless a UPI flag says otherwise.

    Never ask the user for a VPA -- NPCI rule; ``intentApp`` is a method ``id`` copied
    byte-for-byte from ``get_payment_options``. A bare ``--upi`` is an error rather than
    a fallback: silently paying by Cash when someone asked for UPI is the wrong way to
    be lenient on a money path.
    """
    upi_app = flag("upi")
    if upi_app == "":
        die("--upi needs a method id, e.g. --upi=gpay. Run without --place to list them.")
    if flag("upi-qr") is not None:
        return UpiQr()
    return UpiApp(upi_app) if upi_app else Cash()


async def main() -> None:
    token = require_token()
    query = " ".join(positional()) or "milk"
    picks = num_list("pick")
    qtys = num_list("qty") or [1]
    place = flag("place") is not None

    # One quantity applies to every pick; otherwise they pair up positionally. A
    # mismatched list is an error rather than a guess -- silently ordering the wrong
    # count is worse.
    if picks and len(qtys) not in (1, len(picks)):
        die(
            f"--qty has {len(qtys)} values but --pick has {len(picks)}. "
            f"Pass one quantity for all, or one per pick."
        )
    payment = read_payment()

    async with SwiggySession.connect("instamart", token) as session:
        im = InstamartOrdering(session)

        addresses = await im.addresses()
        if not addresses:
            die("No saved addresses on this account. Add one in the Swiggy app first.")

        wanted = flag("address")
        address = next((a for a in addresses if a.get("id") == wanted), None) if wanted else addresses[0]
        if address is None:
            die(f'No address with id "{wanted}". Available: {", ".join(str(a.get("id")) for a in addresses)}')
        print(f"Delivering to: {address_label(address)}\n")

        # --- stage 1: search ---------------------------------------------
        found = await im.search_products(str(address["id"]), query)
        choices = flatten(found.get("products") or [])

        if not choices:
            print(f'Nothing found for "{query}".')
            similar = flatten(found.get("similarProducts") or [])
            if similar:
                print("\nSimilar products the catalogue suggested:")
                for c in similar:
                    print(f"  {label(c)}  {c.detail}")
            return

        if picks is None:
            print(f'Results for "{query}" — re-run with --pick=<n> to add one:\n')
            for c in choices:
                print(f"  {c.n:>2}. {label(c)}\n      {c.detail}")
            print(f"\n  e.g. python -m swiggy.cli.order {query} --pick=1 --qty=1")
            print(f"       python -m swiggy.cli.order {query} --pick=1,2 --qty=2,1   (several lines)")
            return

        chosen: list[tuple[Choice, int]] = []
        for i, p in enumerate(picks):
            if not 1 <= p <= len(choices):
                die(f"--pick={p} is out of range; {len(choices)} variations were found.")
            chosen.append((choices[p - 1], qtys[0] if len(qtys) == 1 else qtys[i]))

        for choice, quantity in chosen:
            if not choice.in_stock:
                die(f'"{label(choice)}" is out of stock at this address. Pick another variation.')
            if choice.max_quantity is not None and quantity > choice.max_quantity:
                extra = f" — {choice.max_quantity_message}" if choice.max_quantity_message else ""
                die(f'--qty={quantity} exceeds the {choice.max_quantity} limit on "{label(choice)}"{extra}')

        # The same variation twice would send two lines with one spinId and let the
        # server decide whether that means "sum" or "last wins". Neither is documented.
        spin_ids = [c.spin_id for c, _ in chosen]
        if len(set(spin_ids)) != len(spin_ids):
            die("--pick lists the same variation twice. Ask for it once with the total quantity.")

        # --- stage 2: cart -----------------------------------------------
        print("Cart →")
        for choice, quantity in chosen:
            suffix = f"  ({choice.detail})" if choice.detail else ""
            print(f"  {quantity} × {label(choice)}{suffix}")

        await im.update_cart(
            str(address["id"]),
            [{"spinId": c.spin_id, "quantity": q} for c, q in chosen],
        )
        cart = await im.get_cart()

        # track_order requires lat/lng and get_addresses never returns them -- the
        # cart's selectedAddressDetails is the only source, so capture it now.
        coords = read_cart_coords(cart)
        total = read_cart_total(cart)

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

        if total is None:
            print(
                f"\n! Could not find the bill total, so the ₹{ORDER_CEILING_INR} ceiling and "
                f"₹{INSTAMART_MINIMUM_INR} minimum cannot be checked here. The server enforces "
                f"both itself — read the total off the cart yourself first."
            )
        else:
            print(f"\nTo pay: ₹{total:g}")

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

        # --- stage 3: checkout -------------------------------------------
        # The library's `user_confirmed` is a type/runtime guard, not a human one. This
        # is the human one -- read the cart above before answering.
        shown = "" if total is None else f" for ₹{total:g}"
        answer = input(f'\nPlace this order{shown}, paying by {type(payment).__name__}? Type "yes": ')
        if answer.strip().lower() != "yes":
            print("Cancelled. Nothing was ordered.")
            return

        # Passing the total is what arms the ceiling/minimum guards inside checkout.
        result = await im.checkout(
            str(address["id"]), payment, user_confirmed=True, cart_total=total
        )

        async def show_tracking(order_id: str) -> None:
            """Best-effort: the order is placed by now, so a missing coordinate must not
            read as an order failure."""
            if coords is None:
                print(
                    f"\nOrder placed ({order_id}). Cannot call track_order: it requires "
                    f"lat/lng and the cart carried none. Track it in the Swiggy app."
                )
                return
            print(await im.track(order_id, coords[0], coords[1]))

        if result.kind == "placed":
            print(f"\nPlaced. Order {result.order.get('orderId')} — {result.order.get('status')}")
            await show_tracking(str(result.order.get("orderId")))
        else:
            print(f"\nOrder {result.order.get('orderId')} is awaiting payment.")
            if result.order.get("bridgeUrl"):
                print(f"Pay here: {result.order['bridgeUrl']}")
            outcome = await im.settle(
                result.order, on_pending=lambda s: print(f"  … {s.get('status') or 'pending'}")
            )
            print(f"\n{outcome.outcome} — order {outcome.order_id}")
            if outcome.outcome == "placed":
                await show_tracking(outcome.order_id)


if __name__ == "__main__":
    asyncio.run(main())
