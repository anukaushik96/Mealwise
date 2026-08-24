"""Instamart cart planning with a running checkout-limit check.

Why projection instead of asking the server after every add:
there is exactly ONE server-side cart per account and update_cart REPLACES
it, so candidate carts cannot be evaluated concurrently - parallel calls
would clobber each other. Instead we measure the fee overhead once, then
project every add locally (instant, no network), and reconcile with the
server before checkout, which is the only irreversible step.

The projection is sound because Swiggy's fees are flat rupee amounts
(handling, packaging, late-night, delivery) - only GST scales, and it
scales with the fees, not the items.
"""

import re

from money import parse_paise, rupees

# checkout is refused at or above ₹1000, so the largest payable cart is ₹999.
CHECKOUT_LIMIT_PAISE = 100000
MAX_PAYABLE_PAISE = CHECKOUT_LIMIT_PAISE - 100

_ADDRESS_LINE = re.compile(r"\[([^\]]+)\][^\n]*?\(ID:\s*([A-Za-z0-9]+)\)")


class BudgetExceeded(Exception):
    """Raised when a projected cart total would be refused at checkout."""

    def __init__(self, status):
        super().__init__(status.message())
        self.status = status


class BudgetStatus(object):
    def __init__(self, item_total, fees, limit, measured):
        self.item_total = item_total
        self.fees = fees
        self.limit = limit
        self.measured = measured  # False while fees are still an assumption

    @property
    def projected(self):
        return self.item_total + self.fees

    @property
    def over_by(self):
        """Amount past the ₹1000 line; 0 when within the limit."""
        return max(0, self.projected - CHECKOUT_LIMIT_PAISE)

    @property
    def must_remove(self):
        """What has to come out of the cart to become payable."""
        return max(0, self.projected - self.limit)

    @property
    def ok(self):
        return self.projected <= self.limit

    @property
    def headroom(self):
        return max(0, self.limit - self.projected)

    @property
    def breaches_checkout_limit(self):
        # The limit is exclusive: exactly Rs 1000 is already refused.
        return self.projected >= CHECKOUT_LIMIT_PAISE

    def message(self):
        if self.ok:
            return "Projected total %s (items %s + fees %s) - %s of headroom." % (
                rupees(self.projected), rupees(self.item_total),
                rupees(self.fees), rupees(self.headroom))
        qualifier = "" if self.measured else " (fees still estimated)"
        if self.breaches_checkout_limit:
            # Swiggy itself will refuse this one.
            if self.over_by > 0:
                return (
                    "Total payment %s exceeds the %s checkout limit by %s.%s "
                    "Remove at least %s to proceed."
                    % (rupees(self.projected), rupees(CHECKOUT_LIMIT_PAISE),
                       rupees(self.over_by), qualifier, rupees(self.must_remove)))
            return (
                "Total payment %s hits the %s checkout limit exactly - the most "
                "Swiggy will accept is %s.%s Remove at least %s to proceed."
                % (rupees(self.projected), rupees(CHECKOUT_LIMIT_PAISE),
                   rupees(MAX_PAYABLE_PAISE), qualifier, rupees(self.must_remove)))
        # Under Swiggy's limit but over the ceiling the user asked for.
        return (
            "Total payment %s is over your budget of %s by %s.%s"
            % (rupees(self.projected), rupees(self.limit),
               rupees(self.must_remove), qualifier))


class CartPlanner(object):
    """Builds an item list locally, checking the limit on every add."""

    def __init__(self, address_id, fee_overhead_paise=0, fees_measured=False,
                 budget_paise=None):
        self.address_id = address_id
        self.fees = fee_overhead_paise
        self.fees_measured = fees_measured
        # Never plan above what checkout will accept, even if the user's
        # own budget is higher.
        self.limit = min(budget_paise or MAX_PAYABLE_PAISE, MAX_PAYABLE_PAISE)
        self.lines = []

    def item_total(self):
        return sum(l["price"] * l["quantity"] for l in self.lines)

    def status(self, extra_paise=0):
        return BudgetStatus(self.item_total() + extra_paise, self.fees,
                            self.limit, self.fees_measured)

    def would_fit(self, price_paise, quantity=1):
        """Check an add WITHOUT applying it - the pre-flight the UI needs."""
        return self.status(extra_paise=price_paise * quantity)

    def add(self, spin_id, sku_id, name, price_paise, quantity=1,
            max_quantity=None, strict=True):
        """Add an item. Returns BudgetStatus; raises BudgetExceeded if strict."""
        if max_quantity is not None and quantity > max_quantity:
            raise ValueError("%s allows at most %d per order (asked %d)"
                             % (name, max_quantity, quantity))
        probe = self.would_fit(price_paise, quantity)
        if not probe.ok:
            if strict:
                raise BudgetExceeded(probe)
            return probe
        self.lines.append({
            "spinId": spin_id, "skuId": sku_id, "name": name,
            "price": price_paise, "quantity": quantity,
        })
        return self.status()

    def remove(self, spin_id):
        before = len(self.lines)
        self.lines = [l for l in self.lines if l["spinId"] != spin_id]
        return len(self.lines) != before

    def to_update_cart_args(self):
        """update_cart wants selectedAddressId, and BOTH spinId and skuId."""
        return {
            "selectedAddressId": self.address_id,
            "items": [
                {"spinId": l["spinId"], "skuId": l["skuId"], "quantity": l["quantity"]}
                for l in self.lines
            ],
        }


# ---------- reading server state ----------

def parse_address_list(payload):
    """get_addresses returns prose only - no JSON - so parse "(ID: ...)"."""
    text = (payload or {}).get("_text", "")
    return [(label.strip(), aid) for label, aid in _ADDRESS_LINE.findall(text)]


def cart_item_total(cart):
    """Item subtotal, preferring the server's own "Item Total" line.

    Summing items[] locally is NOT reliable: the cart can contain entries the
    server excludes from the bill (out of stock, or added by another session),
    which made fee_overhead() report negative fees. The billBreakdown line is
    what the customer is actually charged for, so trust it and only fall back
    to summing when it is absent.
    """
    for line in ((cart.get("billBreakdown") or {}).get("lineItems") or []):
        label = (line.get("label") or "").strip().lower()
        if label in ("item total", "items total", "subtotal", "item subtotal"):
            parsed = parse_paise(line.get("value"))
            if parsed is not None:
                return parsed

    total = 0
    for item in cart.get("items", []) or []:
        if item.get("isInStockAndAvailable") is False:
            continue  # not billed, so it must not count toward the subtotal
        price = parse_paise(item.get("discountedFinalPrice"))
        if price is None:
            price = parse_paise(item.get("mrp")) or 0
        total += price * int(item.get("quantity", 1) or 1)
    return total


def cart_to_pay(cart):
    """The authoritative amount that will be charged, or None."""
    breakdown = cart.get("billBreakdown") or {}
    to_pay = parse_paise((breakdown.get("toPay") or {}).get("value"))
    if to_pay is None:
        to_pay = parse_paise(cart.get("cartTotalAmount"))
    return to_pay


def fee_overhead(cart):
    """toPay minus the item subtotal = every fee, tax and rounding combined.

    Derived by subtraction on purpose: billBreakdown.lineItems is an
    open-ended list of server-generated labels, and get_orders is known to
    omit discount lines, so no sum over named fields can be trusted.
    """
    to_pay = cart_to_pay(cart)
    if to_pay is None:
        return None
    return to_pay - cart_item_total(cart)


def snapshot_cart(cart):
    """Capture the live cart so it can be restored after probing."""
    return [
        {"spinId": i.get("spinId"), "skuId": i.get("skuId"),
         "quantity": int(i.get("quantity", 1) or 1)}
        for i in (cart.get("items") or [])
        if i.get("spinId") and i.get("skuId")
    ]


def cheapest_variation(product):
    """Lowest offerPrice variation that is actually in stock."""
    best = None
    for var in product.get("variations", []) or []:
        if not var.get("isInStockAndAvailable"):
            continue
        price = parse_paise((var.get("price") or {}).get("offerPrice"))
        if price is None:
            price = parse_paise((var.get("price") or {}).get("mrp"))
        if price is None:
            continue
        if best is None or price < best["price"]:
            best = {
                "spinId": var.get("spinId"), "skuId": var.get("skuId"),
                "name": var.get("displayName") or product.get("displayName"),
                "variant": var.get("quantityDescription"),
                "price": price,
                "maxQuantity": var.get("maxQuantity"),
            }
    return best
