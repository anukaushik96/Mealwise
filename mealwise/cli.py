"""Interactive Swiggy Instamart ordering CLI.

Flow: pick (or create) an address -> search and pick items -> review the real
cart -> pick a payment method -> confirm -> checkout.

Safety rules baked in:
  * The ONLY irreversible call is checkout, and it needs a typed "yes".
  * Before checkout the cart total is re-read from the server and hard-gated
    at Rs 1000 - the projection is never trusted for the decision.
  * --dry-run stops before touching your cart at all.

Usage:
  python3 -m mealwise.cli
  python3 -m mealwise.cli --dry-run     # plan only, never writes the cart
  python3 -m mealwise.cli --budget 500  # your own ceiling, below the Rs 999 max
"""

import argparse
import sys
import time

from . import swiggy_auth
from .instamart import (CHECKOUT_LIMIT_PAISE, MAX_PAYABLE_PAISE, CartPlanner,
                       cart_item_total, cart_to_pay, fee_overhead,
                       fetch_all_addresses, flatten_variations, snapshot_cart)
from .money import parse_paise, rupees
from .parse_order import parse_order, rank_variants
from .swiggy_mcp import McpError, SwiggyMcp, guard, tool_data

RULE = "-" * 74


class Abort(Exception):
    """User backed out; nothing has been ordered."""


# ---------------------------------------------------------------- prompts

def ask(prompt, default=None, allow_blank=False):
    suffix = " [%s]" % default if default else ""
    while True:
        try:
            raw = input("%s%s: " % (prompt, suffix)).strip()
        except EOFError:
            raise Abort("input ended")
        if raw:
            return raw
        if default is not None:
            return default
        if allow_blank:
            return ""
        print("  Please enter something (or Ctrl-C to quit).")


def ask_yes(prompt, default=False):
    hint = "Y/n" if default else "y/N"
    while True:
        raw = ask("%s (%s)" % (prompt, hint), default="", allow_blank=True).lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False


def ask_int(prompt, lo, hi, default=None):
    while True:
        raw = ask(prompt, default=str(default) if default is not None else None)
        try:
            value = int(raw)
        except ValueError:
            print("  Enter a number between %d and %d." % (lo, hi))
            continue
        if lo <= value <= hi:
            return value
        print("  Out of range - enter %d to %d." % (lo, hi))


# ------------------------------------------------------------ step 1: address

def create_address(client):
    print("\nNew delivery address")
    print(RULE)
    full = ask("Complete address (as you'd write it)")
    line1 = ask("House / building / street", default=full.split(",")[0].strip())
    line2 = ask("Apartment / floor / wing (blank if none)", allow_blank=True, default="")
    locality = ask("Locality / area (optional)", allow_blank=True, default="")
    city = ask("City")
    pin = ask("Postal code")
    categories = ["HOME", "WORK", "OFFICE", "FRIENDS_AND_FAMILY", "OTHER"]
    for i, c in enumerate(categories, 1):
        print("  %d. %s" % (i, c))
    category = categories[ask_int("Address type", 1, len(categories), 1) - 1]
    tag = ask("Label for this address (optional)", allow_blank=True, default="")
    name = ask("Your name")
    phone = ask("Your phone number")

    args = {
        "fullAddress": full, "addressLine": line1, "addressLine2": line2,
        "city": city, "postalCode": pin, "addressCategory": category,
        "userName": name, "userPhone": phone,
    }
    if locality:
        args["locality"] = locality
    if tag:
        args["addressTag"] = tag
    if ask_yes("Is this for someone else?"):
        args["receiverName"] = ask("Receiver's name")
        args["receiverPhone"] = ask("Receiver's phone")

    print("\nCreating address ...")
    guard(client.call_tool("create_address", args), "create_address")
    print("Address created.")


def choose_address(client):
    while True:
        addresses = fetch_all_addresses(client)
        print("\nYour saved delivery addresses")
        print(RULE)
        if not addresses:
            print("  (none saved yet)")
        for i, (label, aid) in enumerate(addresses, 1):
            print("  %2d. %-28s %s" % (i, label[:28], aid))
        print("\n   n. Add a new address")
        print("   q. Quit")

        choice = ask("Pick an address").lower()
        if choice == "q":
            raise Abort("no address chosen")
        if choice == "n":
            create_address(client)
            continue
        try:
            index = int(choice)
        except ValueError:
            print("  Enter a row number, 'n', or 'q'.")
            continue
        if 1 <= index <= len(addresses):
            label, aid = addresses[index - 1]
            print("\nDelivering to: %s (%s)" % (label, aid))
            return aid, label
        print("  No such row.")


# ------------------------------------------------------------- step 2: items

def measure_fees(client, planner):
    """Push the cart once and read back the true fee overhead.

    Fees are NOT flat - Small Cart Fee, surge and delivery all move with the
    cart - so guessing them makes the in-shop running total lie. One real
    round trip after the first item buys an accurate projection for the rest
    of the session.
    """
    try:
        guard(client.call_tool("update_cart", planner.to_update_cart_args()), "update_cart")
        cart = guard(client.call_tool("get_cart", {}), "get_cart")
    except McpError as exc:
        print("  (could not price the cart yet: %s)" % exc)
        return None
    fees = fee_overhead(cart)
    if fees is None:
        return None
    was, planner.fees = planner.fees, fees
    planner.fees_measured = True
    print("  Fees measured on the server: %s (was assuming %s)." % (rupees(fees), rupees(was)))
    breakdown = (cart.get("billBreakdown") or {}).get("lineItems") or []
    charges = [l.get("label") for l in breakdown if l.get("label") != "Item Total"]
    if charges:
        print("  Charges on this order: %s" % ", ".join(c for c in charges if c))
    status = planner.status()
    print("  Running total is now %s (headroom %s)."
          % (rupees(status.projected), rupees(status.headroom)))
    if not status.ok:
        print("  !! %s" % status.message())
    return fees


def resolve_request(client, planner, address_id, request, allow_probe=True):
    """Search for one parsed request and let the user confirm the variant."""
    print("\n%s\n> %s" % (RULE, request))
    try:
        found = guard(client.call_tool("search_products",
                                      {"addressId": address_id, "query": request.name}),
                      "search_products")
    except McpError as exc:
        print("  Search failed: %s" % exc)
        return

    rows = flatten_variations(tool_data(found, "products") or [])
    if not rows:
        rows = flatten_variations(tool_data(found, "similarProducts") or [])
        if rows:
            print("  No exact product match - showing similar items.")
    if not rows:
        print("  Nothing in stock for %r at this address. Skipping." % request.name)
        return

    ranked = rank_variants(request, rows)
    if not ranked:
        print("  Found %r, but nothing in the size you asked for (%s)."
              % (request.name, request.size))
        ranked = [(1.0, r["price"], r, None) for r in rows]
        ranked.sort(key=lambda r: r[1])

    shown = ranked[:8]
    for i, (score, _price, row, size) in enumerate(shown, 1):
        if request.size is None:
            fit = ""
        elif abs(score) < 1e-6:
            fit = "  <- exact size"
        else:
            fit = "  (%s)" % (size or "size ?")
        discount = ""
        if row["mrp"] and row["mrp"] > row["price"]:
            discount = " was %s" % rupees(row["mrp"])
        star = " *" if row["promoted"] else ""
        print("   %2d. %-38s %-13s %8s%s%s%s"
              % (i, ("%s %s" % (row["brand"], row["name"])).strip()[:38],
                 row["variant"][:13], rupees(row["price"]), discount, star, fit))
    print("    0. skip this item")
    if any(r[2]["promoted"] for r in shown):
        print("   * = featured/sponsored product")

    pick = ask_int("Which one?", 0, len(shown), 1)
    if pick == 0:
        print("  Skipped %s." % request.name)
        return
    row = shown[pick - 1][2]

    cap = row["maxQuantity"] or 99
    qty = request.count if request.count <= cap else cap
    if request.count > cap:
        print("  You asked for %d but only %d allowed per order." % (request.count, cap))
    qty = ask_int("Quantity (max %d)" % cap, 1, cap, qty)

    probe = planner.would_fit(row["price"], qty)
    if not probe.ok:
        print("\n  !! %s" % probe.message())
        print("  Not added.")
        return

    line_name = ("%s %s" % (row["name"], row["variant"])).strip()
    status = planner.add(row["spinId"], row["skuId"], line_name,
                         row["price"], qty, row["maxQuantity"], strict=False)
    print("  Added %d x %s at %s -> running total %s"
          % (qty, row["name"][:34], rupees(row["price"]), rupees(status.projected)))

    # First real item: price it on the server so every later add is honest.
    if allow_probe and not planner.fees_measured:
        measure_fees(client, planner)


def shop(client, planner, address_id, allow_probe=True):
    """Take the whole order as a sentence, then resolve each item in turn."""
    print("\n%s" % RULE)
    print("What would you like to order?")
    print("Give it as one sentence - product and size, comma separated. For example:")
    print("  500 gms paneer, 2 kg atta, 12 eggs and 3 packets of maggi")

    while True:
        sentence = ask("Your order (blank = done)", allow_blank=True, default="")
        if not sentence:
            if planner.lines:
                return
            if ask_yes("Nothing in the cart - quit?", default=True):
                raise Abort("empty cart")
            continue

        requests = parse_order(sentence)
        if not requests:
            print("  Could not pick any items out of that. Try 'name + size', "
                  "e.g. '500 gms paneer'.")
            continue

        print("\n  I read that as:")
        for request in requests:
            size = str(request.size) if request.size else "any size"
            print("    - %-28s %-12s x%d" % (request.name, size, request.count))
        if not ask_yes("Look right?", default=True):
            continue

        for request in requests:
            resolve_request(client, planner, address_id, request, allow_probe)

        status = planner.status()
        print("\n%s\nCart: %d line(s), total so far %s (headroom %s)"
              % (RULE, len(planner.lines), rupees(status.projected),
                 rupees(status.headroom)))
        if not ask_yes("Add anything else?", default=False):
            if planner.lines:
                return
            if ask_yes("Cart is empty - quit?", default=True):
                raise Abort("empty cart")


# --------------------------------------------------- step 3: real cart + gate

def show_cart(cart):
    print("\nYour cart on Swiggy's side")
    print(RULE)
    for item in cart.get("items", []) or []:
        price = parse_paise(item.get("discountedFinalPrice")) or 0
        qty = int(item.get("quantity", 1) or 1)
        print("  %-44s x%-3d %10s" % (
            ("%s %s" % (item.get("itemName", "?"), item.get("itemVariant", ""))).strip()[:44],
            qty, rupees(price * qty)))
    breakdown = cart.get("billBreakdown") or {}
    print(RULE)
    for line in breakdown.get("lineItems", []) or []:
        print("  %-44s      %10s" % (line.get("label", "?"), line.get("value", "?")))
    to_pay = cart_to_pay(cart)
    print("  %-44s      %10s" % ("TO PAY", rupees(to_pay)))
    return to_pay


def push_and_gate(client, planner):
    """Write the cart, re-read the true total, and hard-gate it at Rs 1000."""
    while True:
        guard(client.call_tool("update_cart", planner.to_update_cart_args()), "update_cart")
        cart = guard(client.call_tool("get_cart", {}), "get_cart")
        to_pay = show_cart(cart)

        if to_pay is None:
            raise McpError("cart returned no payable total; refusing to continue")

        projected = planner.status().projected
        drift = to_pay - projected
        if drift:
            print("\n  (projection was %s; server says %s - drift %s)"
                  % (rupees(projected), rupees(to_pay), rupees(drift)))

        # Now that the server has priced a real cart, replace any assumed fee
        # overhead with the measured one so later passes project accurately.
        measured_fees = fee_overhead(cart)
        if measured_fees is not None and measured_fees != planner.fees:
            if not planner.fees_measured:
                print("  Fee overhead measured at %s (was assuming %s)."
                      % (rupees(measured_fees), rupees(planner.fees)))
            planner.fees = measured_fees
            planner.fees_measured = True

        if to_pay < CHECKOUT_LIMIT_PAISE and to_pay <= planner.limit:
            return cart, to_pay

        # Over the line: this is the error the user asked for.
        if to_pay >= CHECKOUT_LIMIT_PAISE:
            print("\n  !! ERROR: total payment %s exceeds the %s checkout limit by %s."
                  % (rupees(to_pay), rupees(CHECKOUT_LIMIT_PAISE),
                     rupees(to_pay - CHECKOUT_LIMIT_PAISE)))
        else:
            print("\n  !! Total %s is over your own budget of %s by %s."
                  % (rupees(to_pay), rupees(planner.limit), rupees(to_pay - planner.limit)))
        must_remove = to_pay - min(planner.limit, MAX_PAYABLE_PAISE)
        print("     Swiggy will refuse this order. Remove at least %s." % rupees(must_remove))

        if not planner.lines:
            raise Abort("cart is over the limit and there is nothing left to remove")
        print("\n  Current lines:")
        for i, line in enumerate(planner.lines, 1):
            print("   %2d. %-40s x%-3d %10s"
                  % (i, line["name"][:40], line["quantity"],
                     rupees(line["price"] * line["quantity"])))
        print("    0. quit without ordering")
        index = ask_int("Remove which line?", 0, len(planner.lines), 1)
        if index == 0:
            raise Abort("over the limit")
        removed = planner.lines.pop(index - 1)
        print("  Removed %s." % removed["name"][:44])


# ------------------------------------------------------------ step 4: payment

def choose_payment(client):
    payload = guard(client.call_tool("get_payment_options", {}), "get_payment_options")
    options = []
    platforms = payload.get("platforms") or {}

    for method in (platforms.get("mobile") or {}).get("methods", []) or []:
        options.append({
            "label": "%s (UPI app - needs the app on THIS device)" % method.get("displayName"),
            "args": {"paymentMethod": "UPI", "intentApp": method.get("id")},
            "upi": True,
        })
    for method in (platforms.get("desktop") or {}).get("methods", []) or []:
        options.append({
            "label": "%s (scan a QR with any UPI app)" % method.get("displayName"),
            "args": {"paymentMethod": "UPI", "generateUPIQR": True},
            "upi": True,
        })
    cod = payload.get("cod") or {}
    if cod.get("available"):
        options.append({
            "label": "%s (pay the rider)" % (cod.get("displayName") or "Cash on delivery"),
            "args": {"paymentMethod": "Cash"},
            "upi": False,
        })

    if not options:
        raise McpError("no payment methods offered for this cart")

    print("\nHow would you like to pay?")
    print(RULE)
    for i, option in enumerate(options, 1):
        print("  %2d. %s" % (i, option["label"]))
    print("   0. cancel")
    index = ask_int("Payment method", 0, len(options), len(options))
    if index == 0:
        raise Abort("no payment method chosen")
    chosen = options[index - 1]
    print("  Paying with: %s" % chosen["label"])
    return chosen


# ----------------------------------------------------------- step 5: checkout

def settle_upi(client, result):
    """Poll the payment once per interval, then finalize. Never tight-loop."""
    paas_id = result.get("paasId")
    order_id = result.get("orderId")
    if not paas_id:
        print("  No paasId in the checkout response; check the Swiggy app for status.")
        return
    print("\n  Payment is pending. Approve it in your UPI app.")
    deadline = time.time() + 300  # the documented 5-minute cap
    status = None
    while time.time() < deadline:
        time.sleep(15)
        try:
            payload = client.call_tool("check_payment_status",
                                       {"paasId": paas_id, "orderId": order_id})
        except McpError as exc:
            print("  status check failed: %s" % exc)
            break
        status = (payload.get("status") or payload.get("paymentStatus")
                  or payload.get("_text", ""))[:60]
        print("  payment status: %s" % status)
        upper = str(status).upper()
        if "SUCCESS" in upper or "PAID" in upper:
            guard(client.call_tool("confirm_order",
                                   {"orderId": order_id, "paasId": paas_id,
                                    "transactionId": result.get("transactionId") or ""}),
                  "confirm_order")
            print("  Payment confirmed; order finalized.")
            return
        if "FAIL" in upper:
            # Docs: do NOT confirm_order on FAILED - the user must start over.
            print("  Payment failed. Start a fresh payment from the Swiggy app.")
            return
    print("  Payment still pending at the 5-minute cap; finalizing so it does not linger.")
    try:
        client.call_tool("confirm_order", {"orderId": order_id, "paasId": paas_id})
    except McpError as exc:
        print("  confirm_order failed: %s" % exc)


def checkout(client, address_id, label, cart, to_pay, payment):
    print("\n" + "=" * 74)
    print("CONFIRM YOUR ORDER")
    print("=" * 74)
    print("  Deliver to  : %s (%s)" % (label, address_id))
    print("  Items       : %d" % len(cart.get("items") or []))
    print("  You will pay: %s" % rupees(to_pay))
    print("  Payment      : %s" % payment["label"])
    print("=" * 74)
    print("This places a REAL order and charges REAL money.")
    if ask("Type 'yes' to place the order", allow_blank=True, default="").lower() != "yes":
        raise Abort("not confirmed")

    args = {"addressId": address_id}
    args.update(payment["args"])
    result = guard(client.call_tool("checkout", args), "checkout")

    order_id = result.get("orderId")
    status = result.get("status")
    print("\n  Order placed. orderId=%s status=%s total=%s"
          % (order_id, status, rupees(parse_paise(result.get("cartTotal")))))
    if str(status).upper() == "PENDING_PAYMENT" or payment["upi"]:
        settle_upi(client, result)

    details = (cart.get("selectedAddressDetails") or {})
    if order_id and details.get("lat") and details.get("lng"):
        print("\n  Track it with:")
        print("    track_order(orderId=%s, lat=%s, lng=%s)"
              % (order_id, details["lat"], details["lng"]))
    return result


# ---------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        prog="python3 -m mealwise.cli",
        description="Order groceries from Swiggy Instamart, in the terminal")
    ap.add_argument("--dry-run", action="store_true",
                    help="plan only; never writes the cart or orders")
    ap.add_argument("--budget", type=float, default=None,
                    help="your own ceiling in rupees (capped at the Rs 999 payable max)")
    ap.add_argument("--login", action="store_true",
                    help="sign in as a different Swiggy number (discards the cached session)")
    ap.add_argument("--logout", action="store_true",
                    help="sign out of the active account and exit")
    ap.add_argument("--all", action="store_true",
                    help="with --logout: sign out of every account on this machine")
    ap.add_argument("--accounts", action="store_true",
                    help="list Swiggy accounts signed in on this machine")
    ap.add_argument("--use", metavar="USER_ID", default=None,
                    help="switch to another already signed-in account (no OTP)")
    ap.add_argument("--whoami", action="store_true",
                    help="print the signed-in Swiggy account and exit")
    args = ap.parse_args()

    if args.logout:
        swiggy_auth.logout(all_accounts=args.all)
        return 0
    if args.accounts:
        sessions = swiggy_auth.list_sessions()
        if not sessions:
            print("No accounts signed in on this machine.")
        active = str(swiggy_auth.cached_user_id())
        for uid, left in sessions:
            state = "expired" if left <= 0 else "%dh %dm left" % (left // 3600, (left % 3600) // 60)
            print("  %-12s %-14s %s" % (uid, state, "<- active" if uid == active else ""))
        return 0
    if args.use:
        if swiggy_auth.use_account(args.use):
            print("Switched to account %s." % args.use)
        else:
            print("No valid session for account %s - run with --login." % args.use)
            return 1
        return 0
    if args.whoami:
        uid = swiggy_auth.cached_user_id()
        print("Signed in as Swiggy account %s" % uid if uid else "Not signed in.")
        return 0

    # No cached session, or --login: this runs the browser + OTP flow, so a
    # different person on this machine signs in as themselves.
    token = swiggy_auth.login(force=args.login)
    client = SwiggyMcp(token, server="instamart")
    client.initialize()
    print("Connected to Swiggy Instamart as account %s." % swiggy_auth.cached_user_id())
    print("(Not you? Re-run with --login to sign in with your own number.)")

    address_id, label = choose_address(client)

    # Measure the real fee overhead so the running projection is honest.
    cart = guard(client.call_tool("get_cart", {}), "get_cart")
    existing = snapshot_cart(cart)
    fees = fee_overhead(cart)
    measured = fees is not None and existing
    if not measured:
        # No guess goes here. A fee cannot be estimated - see section 1.1 of
        # docs/instamart-notes.md - so the running total is items only, and says so,
        # until the first cart write lets the server price it.
        fees = 0
        print("\nNo cart to price yet, so fees are unknown: the running total is"
              " items only until your first item is added.")
    else:
        print("\nMeasured fee overhead from your current cart: %s" % rupees(fees))
        print("You already have %d item(s) worth %s in the cart."
              % (len(existing), rupees(cart_item_total(cart))))
        print("Building a new order REPLACES them (update_cart replaces the whole cart).")
        if not ask_yes("Continue and replace the existing cart?", default=True):
            raise Abort("kept the existing cart")

    budget_paise = int(round(args.budget * 100)) if args.budget else None
    planner = CartPlanner(address_id, fees, bool(measured), budget_paise)
    print("Ceiling for this order: %s" % rupees(planner.limit))

    shop(client, planner, address_id, allow_probe=not args.dry_run)

    if args.dry_run:
        final = planner.status()
        print("\n%s\nDRY RUN - cart NOT written, nothing ordered." % RULE)
        if final.measured:
            print("Planned %d line(s): items %s + fees %s = %s"
                  % (len(planner.lines), rupees(final.item_total),
                     rupees(final.fees), rupees(final.projected)))
        else:
            # Do not print "+ fees Rs 0" - zero is not what the fees are, it
            # is what we know about them.
            print("Planned %d line(s): items %s. Fees unpriced - a dry run "
                  "writes no cart for Swiggy to price."
                  % (len(planner.lines), rupees(final.item_total)))
        print(final.message())
        return 0

    cart, to_pay = push_and_gate(client, planner)
    payment = choose_payment(client)
    checkout(client, address_id, label, cart, to_pay, payment)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Abort as exc:
        print("\nStopped: %s. Nothing was ordered." % exc)
        sys.exit(1)
    except (swiggy_auth.AuthError, McpError) as exc:
        print("\nFAILED: %s" % exc)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted. Nothing was ordered.")
        sys.exit(130)
