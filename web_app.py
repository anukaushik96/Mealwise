"""Local web UI for Instamart ordering over Swiggy MCP.

    python3 web_app.py                 # opens http://127.0.0.1:8765
    python3 web_app.py --dry-run       # walks everything, never writes or orders
    python3 web_app.py --budget 500    # your own ceiling, under the Rs 999 max

Same engine as the CLI - CartPlanner, the Rs 1000 gate, the order parser - with
a browser in front of it. Only the front end is new.

Three deliberate constraints:

  * Stdlib only, no JavaScript. See web_ui.py for how the modal and the address
    drawer manage without it.
  * One request at a time. The server is single-threaded on purpose: there is
    exactly ONE server-side cart per Swiggy account and update_cart REPLACES
    it, so two overlapping requests could each write a cart the other believed
    it owned. Serialising is not a limitation here, it is the invariant.
  * Bound to 127.0.0.1, and every mutating form carries a per-process token.
    Any web page you have open can POST to a localhost port; without that
    token one of them could spend your money.

State lives in memory for one signed-in person, which is what a local tool is.
Nothing here is a multi-tenant server - see section 9 of INSTAMART_NOTES.md for
what hosting this would actually require.
"""

import argparse
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.parse
import webbrowser
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer

import recipe
import swiggy_auth
import web_ui
from instamart import (CHECKOUT_LIMIT_PAISE, BudgetStatus, CartPlanner,
                       cart_item_total, cart_to_pay, cart_write_warnings,
                       fee_overhead, fetch_all_addresses, search_rows)
from money import parse_paise, rupees
from parse_order import parse_order, rank_variants
from swiggy_mcp import McpError, SwiggyMcp, guard, tool_data

# There is no starting fee figure, because a fee cannot be estimated. Measured
# fee load has ranged from 4% to 123% of the item total, the SET of fee lines
# changes with cart value and time of day, and a surge fee can appear between
# two identical carts. So the planner starts at zero fees and says so: the
# running total is labelled "items only" until Swiggy has priced a real cart,
# which happens the moment the first item lands (see add_line). A guess here
# would be a number shown to someone as if the server had said it.

MAX_VARIANTS_SHOWN = 8
UPI_POLL_SECONDS = 300      # the documented cap on a pending UPI payment
EXACT = 1e-6                # a rank score this small is an exact size match


class Redirect(Exception):
    """Raised to send the browser somewhere instead of rendering."""

    def __init__(self, location):
        super().__init__(location)
        self.location = location


class App(object):
    def __init__(self, port, budget_paise=None, dry_run=False):
        self.port = port
        self.budget_paise = budget_paise
        self.dry_run = dry_run
        # An attacker's page can POST to this port but cannot read this token,
        # so requiring it on every mutation is what keeps checkout ours.
        self.nonce = secrets.token_urlsafe(24)
        self.pending_auth = {}
        self.reset_session()
        self.adopt_cached_session()

    # ------------------------------------------------------------ session

    def reset_session(self):
        self.token = None
        self.client = None
        self.user_id = None
        self.addresses = None
        self.address_id = None
        self.address_label = None
        self.planner = None
        self.parsed = []          # parsed but not yet confirmed
        self.suggested_from = None  # the intent a suggested list came from
        self.queue = []           # confirmed, awaiting a manual variant pick
        self.payment = None
        self.payment_options = None
        self.display_name = None
        self.flash = []
        self.draft = ""
        self.order = None
        self.poll_until = None
        self.status_text = None
        self.checked_at = None

    def adopt_cached_session(self):
        """Skip the login screen when a valid 5-day token is already stored."""
        token = swiggy_auth.cached_token()
        if not token:
            return
        self.token = token
        self.user_id = swiggy_auth.cached_user_id()
        self.display_name = swiggy_auth.load_prefs(self.user_id).get("display_name")

    def remember(self, **values):
        """Merge into the stored preferences.

        Merge, not replace: these keys are written from different places, and
        an address change must not quietly delete the name.
        """
        prefs = swiggy_auth.load_prefs(self.user_id)
        prefs.update(values)
        swiggy_auth.save_prefs(self.user_id, prefs)

    @property
    def signed_in(self):
        return bool(self.token)

    def mcp(self):
        if not self.token:
            raise Redirect("/")
        if self.client is None:
            self.client = SwiggyMcp(self.token, server="instamart")
            self.client.initialize()
        return self.client

    def sign_out(self, message=None, kind="info"):
        swiggy_auth.logout()
        self.reset_session()
        if message:
            self.say(kind, message)

    # ------------------------------------------------------------ messages

    def say(self, kind, text, raw=False):
        self.flash.append((kind, text if raw else escape(str(text))))

    def take_flash(self):
        out, self.flash = self.flash, []
        return out

    def ctx(self, keep_flash=False):
        return {
            "signed_in": self.signed_in,
            "user_id": self.user_id,
            "display_name": self.display_name,
            "addresses": self.addresses,
            "address_id": self.address_id,
            "address_label": self.address_label,
            "nonce": self.nonce,
            "dry_run": self.dry_run,
            "flash": self.flash if keep_flash else self.take_flash(),
            "lines": self.planner.lines if self.planner else [],
            "status": self.planner.status() if self.planner else None,
            "payment": self.payment,
            "draft": self.draft,
            "suggested_from": self.suggested_from,
            "recipes_on": recipe.available(),
        }

    # ------------------------------------------------------------ addresses

    def load_addresses(self, force=False):
        if self.addresses is None or force:
            self.addresses = fetch_all_addresses(self.mcp())
        return self.addresses

    def ensure_address(self):
        """Settle on a delivery address: the last one used, else the default.

        "Nearest to you" is not on offer. Reading the device's location needs
        JavaScript, and the Instamart MCP exposes no nearest-address tool, so
        guessing would mean inventing a fact about where someone is. The first
        address Swiggy returns is the account default; after that we remember
        what they actually picked.
        """
        self.load_addresses()
        if self.address_id and any(a[1] == self.address_id for a in self.addresses):
            return True
        remembered = swiggy_auth.load_prefs(self.user_id).get("address_id")
        for label, address_id in self.addresses:
            if address_id == remembered:
                self.set_address(address_id, label, quiet=True)
                return True
        if not self.addresses:
            return False
        # Nothing remembered: take the account default, but only if a store
        # actually serves it. Bounded to a few probes so a first visit cannot
        # turn into two dozen searches.
        for label, address_id in self.addresses[:4]:
            if self.serves_groceries(address_id):
                self.set_address(address_id, label, quiet=True)
                if address_id != self.addresses[0][1]:
                    self.say("info", "Nothing is being delivered to %s right now, "
                                     "so this order is set to %s. Change it top-left."
                             % (self.addresses[0][0], label))
                return True
        label, address_id = self.addresses[0]
        self.set_address(address_id, label, quiet=True)
        self.say("warn", "No store seems to be serving your first few saved "
                         "addresses right now. Pick another one top-left, or try "
                         "again later.")
        return True

    def set_address(self, address_id, label, quiet=False):
        """Switch address, rebuilding the basket because skuIds do not travel.

        A skuId names a variant's stock at the store serving one address; the
        same product at another address has a different one. Carrying the
        basket across would send ids the new store has never heard of, which
        surfaces later as a bogus "item unavailable".
        """
        had_lines = bool(self.planner and self.planner.lines)
        changed = address_id != self.address_id
        self.address_id = address_id
        self.address_label = label
        self.payment = None
        self.payment_options = None
        if had_lines and changed and not self.dry_run:
            try:
                guard(self.mcp().call_tool("clear_cart", {}), "clear_cart")
            except McpError as exc:
                self.say("warn", "Could not clear the old cart: %s" % exc)
        # Fees vary by address and time of day, so a measurement made at the
        # old address is not evidence about this one.
        self.planner = CartPlanner(address_id, 0, False, self.budget_paise)
        if had_lines and changed:
            self.say("warn", "Basket cleared - item ids are only valid for the "
                             "address they were found at, so this order starts fresh.")
        self.remember(address_id=address_id)

    def serves_groceries(self, address_id):
        """Does a store actually serve this address right now?

        A saved address is not necessarily a serviceable one. Observed live:
        the FIRST address on a real account - the one Swiggy returns as the
        default - returned "Found 0 product(s)" for every query, with no error
        and success:true, while every other address on the same account
        returned 20. Read literally that looks like an empty catalogue.

        Probed with a staple rather than the user's own list, so the answer is
        about the address rather than about what they happened to ask for.
        """
        try:
            rows, _similar = search_rows(self.mcp(), address_id, "milk", limit=1)
        except McpError:
            return False
        return bool(rows)

    def ensure_planner(self):
        if self.planner is None:
            self.planner = CartPlanner(self.address_id, 0, False, self.budget_paise)
        return self.planner

    # ------------------------------------------------------------ cart

    def push_cart(self):
        """Write the basket, read back what Swiggy actually holds, and price it.

        Always both calls. update_cart's own response does not prove what the
        cart now contains - the account is live state that the phone app can
        write to at the same time - so the read is the only source of truth.
        """
        client = self.mcp()
        written = guard(client.call_tool("update_cart",
                                        self.planner.to_update_cart_args()), "update_cart")
        warnings = cart_write_warnings(written)
        cart = guard(client.call_tool("get_cart", {}), "get_cart")

        fees = fee_overhead(cart)
        if fees is not None:
            self.planner.fees = fees
            self.planner.fees_measured = True

        return cart, warnings

    def cart_rows(self, cart):
        """One row per item SWIGGY says is in the cart - not per item we sent.

        These are two different lists, and the difference is the whole point.
        The bill is computed from the server's cart, so anything in it is
        being paid for; rendering our own basket instead meant an item added
        from the phone app was charged for, warned about, and yet had no row
        and no way to remove it. Observed live: a Laadi Pav worth Rs 69 in a
        Rs 354 bill, with five rows on screen and six in the cart.

        Rows carry the planner index when the item is ours, so the quantity
        and remove controls keep working; a row with index None is a stranger
        and can only be dealt with by rebuilding the cart.
        """
        rows = []
        for item in (cart.get("items") or []):
            spin, sku = item.get("spinId"), item.get("skuId")
            line = self.planner.find(spin, sku) if (spin and sku) else None
            price = parse_paise(item.get("discountedFinalPrice"))
            if price is None:
                price = parse_paise(item.get("mrp")) or 0
            rows.append({
                "name": ("%s %s" % (item.get("itemName") or "?",
                                    item.get("itemVariant") or "")).strip(),
                "price": price,
                "quantity": int(item.get("quantity", 1) or 1),
                "image": item.get("imageUrl"),
                "max_quantity": item.get("maxQuantity") or 99,
                "ours": line is not None,
                "index": self.planner.lines.index(line) if line is not None else None,
                # An item the server is holding but not billing. Never silent:
                # it is why a total can disagree with the rows above it.
                "in_stock": item.get("isInStockAndAvailable") is not False,
            })
        return rows

    def measure_fees(self):
        """One round trip so the running total stops being a guess."""
        if self.dry_run or not self.planner.lines or self.planner.fees_measured:
            return
        try:
            self.push_cart()
        except McpError as exc:
            self.say("warn", "Could not price the basket yet: %s" % exc)

    def cart_view(self):
        if self.dry_run:
            # A dry run writes no cart, so Swiggy never prices one, so there is
            # no fee figure to show - not even a guess at one.
            status = self.planner.status()
            fee_line = (("Fees, taxes and delivery", rupees(status.fees))
                        if status.measured else
                        ("Fees, taxes and delivery",
                         "not priced - a dry run writes no cart"))
            return {
                "rows": [{"name": l["name"], "price": l["price"],
                          "quantity": l["quantity"], "image": l.get("image"),
                          "max_quantity": l.get("maxQuantity") or 99,
                          "ours": True, "index": i, "in_stock": True,
                          "billed": True}
                         for i, l in enumerate(self.planner.lines)],
                "foreign": [],
                "strangers_are_free": False,
                "unbilled_paise": 0,
                "bill_lines": [("Item total", rupees(status.item_total)), fee_line],
                "to_pay": status.projected,
                "projected": status.projected,
                "drift": False,
                "warnings": [],
                "blocked": not status.ok,
                "gate_message": status.message(),
                "near_limit": False,
                "payment_options": self.load_payment_options(),
            }

        projected = self.planner.status().projected
        cart, warnings = self.push_cart()
        to_pay = cart_to_pay(cart)
        if to_pay is None:
            raise McpError("Swiggy returned a cart with no payable total")

        # Decide on the server's figure, never the projection (field notes, 2).
        status = BudgetStatus(cart_item_total(cart), to_pay - cart_item_total(cart),
                              self.planner.limit, True)
        bill = [(line.get("label") or "?", line.get("value") or "?")
                for line in ((cart.get("billBreakdown") or {}).get("lineItems") or [])]
        if not bill:
            bill = [("Item total", rupees(cart_item_total(cart)))]
        rows = self.cart_rows(cart)
        # Is a stranger actually being charged for? Do not guess - subtract.
        #
        # Observed live 2026-08-25: a Laadi Pav sat in items[] with
        # isInStockAndAvailable true, at the same storeId as everything else,
        # and was NOT in the Item Total - which came to exactly the client's
        # own basket. So items[] can list something the bill excludes, and the
        # in-stock flag does not explain it. Section 1.6 warned that items[]
        # holds entries the server does not bill; this says the same thing
        # about cart membership itself.
        #
        # Telling someone they are paying Rs 69 they are not is as bad as
        # hiding it, so the label comes from the difference between what the
        # cart holds and what the cart bills.
        billed_total = cart_item_total(cart)
        rows_total = sum(r["price"] * r["quantity"] for r in rows)
        foreign = [r for r in rows if not r["ours"]]
        foreign_total = sum(r["price"] * r["quantity"] for r in foreign)
        unbilled = rows_total - billed_total
        strangers_are_free = bool(foreign) and abs(unbilled - foreign_total) < 100
        for row in rows:
            row["billed"] = row["in_stock"] and not (
                strangers_are_free and not row["ours"])
        return {
            "rows": rows,
            "foreign": foreign,
            "strangers_are_free": strangers_are_free,
            # anything the cart holds but does not bill, beyond the strangers
            "unbilled_paise": max(0, unbilled - (foreign_total if strangers_are_free else 0)),
            "bill_lines": bill,
            "to_pay": to_pay,
            "projected": projected,
            "drift": abs(to_pay - projected) >= 100,
            "warnings": warnings,
            "blocked": to_pay >= CHECKOUT_LIMIT_PAISE or to_pay > self.planner.limit,
            "gate_message": status.message(),
            "near_limit": 0 < CHECKOUT_LIMIT_PAISE - to_pay <= 10000,
            "payment_options": self.load_payment_options(),
        }

    # ------------------------------------------------------------ payment

    def load_payment_options(self):
        """The only legitimate source of payment ids is this tool's response."""
        if self.payment_options is not None:
            return self.payment_options
        try:
            payload = guard(self.mcp().call_tool("get_payment_options", {}),
                            "get_payment_options")
        except McpError as exc:
            self.say("warn", "Could not load payment methods: %s" % exc)
            return None

        options = []
        platforms = tool_data(payload, "platforms") or {}
        for method in (platforms.get("mobile") or {}).get("methods", []) or []:
            options.append({
                "group": "UPI apps on this device",
                "name": method.get("displayName") or "UPI app",
                "hint": "Opens the app to approve. Needs it installed here.",
                "label": "%s (UPI app)" % (method.get("displayName") or "UPI"),
                "args": {"paymentMethod": "UPI", "intentApp": method.get("id")},
                "upi": True,
            })
        for method in (platforms.get("desktop") or {}).get("methods", []) or []:
            options.append({
                "group": "Scan a QR code",
                "name": method.get("displayName") or "UPI QR",
                "hint": "Scan with any UPI app on your phone.",
                "label": "%s (UPI QR)" % (method.get("displayName") or "UPI QR"),
                "args": {"paymentMethod": "UPI", "generateUPIQR": True},
                "upi": True,
            })
        cod = tool_data(payload, "cod") or {}
        if cod.get("available"):
            options.append({
                "group": "Pay on delivery",
                "name": cod.get("displayName") or "Cash on delivery",
                "hint": "Pay the rider. Confirms straight away.",
                "label": "%s (pay the rider)" % (cod.get("displayName") or "Cash"),
                "args": {"paymentMethod": "Cash"},
                "upi": False,
            })
        self.payment_options = options
        return options

    # ------------------------------------------------------------ resolving

    def resolve(self, request):
        """Search one parsed line. Auto-add only on an exact size match.

        Anything less than exact goes to the user: a "closest match" on
        groceries is a judgement about their dinner, not about data.

        Returns (outcome, pending) where outcome is "added", "choose" or
        "none" - the caller needs to tell "found nothing" apart from "handled
        it", because nothing found for EVERY line means something different
        (usually an unserviceable address) than nothing found for one.
        """
        rows, used_similar = search_rows(self.mcp(), self.address_id, request.name)
        if not rows:
            self.say("warn", "Nothing in stock for %r at this address." % request.name)
            return "none", None

        ranked = rank_variants(request, rows)
        if not ranked:
            # Everything came back in a different unit than was asked for.
            ranked = [(1.0, row["price"], row, None)
                      for row in sorted(rows, key=lambda r: r["price"])]

        top_score, _price, top_row, _size = ranked[0]
        exact = request.size is not None and top_score < EXACT
        only_one = len(ranked) == 1

        if exact and not used_similar:
            cap = top_row["maxQuantity"] or 99
            quantity = min(request.count, cap)
            if quantity < request.count:
                self.say("warn", "%s: Swiggy allows only %d per order, so %d added."
                         % (top_row["name"], cap, quantity))
            probe = self.planner.would_fit(top_row["price"], quantity)
            if not probe.ok:
                self.say("bad", "%s not added - %s" % (top_row["name"], probe.message()))
                return "added", None
            self.add_line(top_row, quantity)
            return "added", None

        why = []
        if used_similar:
            why.append("No exact match for %r, so these are similar items."
                       % request.name)
        elif request.size is None:
            why.append("You did not say what size, so pick the one you want.")
        elif only_one:
            why.append("Only one size is in stock, and it is not the %s you asked for."
                       % request.size)
        else:
            why.append("Nothing came back in exactly %s - pick the closest."
                       % request.size)
        options = [{"row": row, "size": size, "exact": score < EXACT}
                   for score, _p, row, size in ranked[:MAX_VARIANTS_SHOWN]]
        caps = [o["row"]["maxQuantity"] or 99 for o in options]
        return "choose", {
            "request": request,
            "options": options,
            "why": " ".join(why),
            "max_quantity": min(max(caps), 20),
            "quantity": min(request.count, max(caps), 20),
            "remaining": 0,
        }

    def add_line(self, row, quantity):
        first = not self.planner.lines
        existing = self.planner.find(row["spinId"], row["skuId"])
        before = existing["quantity"] if existing else 0
        self.planner.add(row["spinId"], row["skuId"],
                         ("%s %s" % (row["name"], row["variant"])).strip(),
                         row["price"], quantity, row["maxQuantity"], strict=False)
        line = self.planner.find(row["spinId"], row["skuId"])
        if line is not None:
            # Keep the per-variant cap so the cart's +/- buttons cannot exceed
            # it, and the photo so the cart can show what was chosen.
            line["maxQuantity"] = row["maxQuantity"] or 99
            line["image"] = row.get("image")
            if existing is not None and line["quantity"] == before:
                self.say("warn", "%s is already at Swiggy's limit of %d per "
                                 "order, so nothing was added."
                         % (row["name"], row["maxQuantity"] or 99))
            elif existing is not None:
                self.say("info", "%s was already in your basket - it is now x%d."
                         % (row["name"], line["quantity"]))
        if first:
            # Fees cannot be known before a cart exists, so price one the
            # instant there is something to price. One round trip here is what
            # keeps every later line in this batch honest instead of guessed.
            self.measure_fees()


# ---------------------------------------------------------------- routing

class Handler(BaseHTTPRequestHandler):
    app = None
    server_version = "mealwise"
    sys_version = ""

    # -------------------------------------------------------- plumbing

    def log_message(self, fmt, *args):
        pass  # the console carries the app's own output, not a request log

    def _send(self, body, status=200, content_type="text/html; charset=utf-8"):
        raw = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        # Nothing is embeddable and nothing is loaded from elsewhere.
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        # No form-action: signing in has to POST here and then redirect out
        # to Swiggy's authorization page, and browsers have disagreed about
        # whether form-action follows a redirect. default-src 'none' already
        # blocks scripts, images and frames, which is the part that matters.
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; style-src 'unsafe-inline'; "
                         "img-src %s; base-uri 'none'"
                         % web_ui.IMAGE_HOST.rstrip("/"))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def _redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _form(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1 << 20:
            return {}
        raw = self.rfile.read(length).decode("utf-8", "replace")
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}

    def _check_origin(self, form):
        """A mutation must come from a page this server rendered."""
        if form.get("_t") != self.app.nonce:
            return False
        site = self.headers.get("Sec-Fetch-Site")
        return site is None or site == "same-origin"

    def _int(self, form, name, default=0):
        try:
            return int(form.get(name, default))
        except (TypeError, ValueError):
            return default

    # -------------------------------------------------------- dispatch

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        app = self.app
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

        if path == "/favicon.ico":
            self._send(b"", 204, "image/x-icon")
            return

        form = self._form() if method == "POST" else {}
        if method == "POST" and not self._check_origin(form):
            self._send(web_ui.error_page(
                app.ctx(), "That form was stale",
                "It did not carry this session's token, so nothing was done. "
                "Reload the page and try again."), 403)
            return

        routes = {
            ("GET", "/"): self.view_home,
            ("POST", "/login"): self.act_login,
            ("GET", "/callback"): self.view_callback,
            ("POST", "/logout"): self.act_logout,
            ("POST", "/profile"): self.act_profile,
            ("POST", "/address/select"): self.act_select_address,
            ("GET", "/address/new"): self.view_new_address,
            ("POST", "/address/new"): self.act_new_address,
            ("POST", "/order"): self.act_order,
            ("GET", "/review"): self.view_review,
            ("POST", "/review"): self.act_review,
            ("GET", "/choose"): self.view_choose,
            ("POST", "/choose"): self.act_choose,
            ("GET", "/cart"): self.view_cart,
            ("POST", "/cart/qty"): self.act_qty,
            ("POST", "/cart/remove"): self.act_remove,
            ("POST", "/cart/rebuild"): self.act_rebuild,
            ("POST", "/payment"): self.act_payment,
            ("GET", "/confirm"): self.view_confirm,
            ("POST", "/checkout"): self.act_checkout,
            ("GET", "/placed"): self.view_placed,
            ("GET", "/payment/pending"): self.view_pending,
        }
        handler = routes.get((method, path))
        if handler is None:
            self._send(web_ui.error_page(app.ctx(), "No such page",
                                         "%s is not part of this app." % path), 404)
            return

        try:
            body = handler(form, query)
        except Redirect as exc:
            self._redirect(exc.location)
            return
        except McpError as exc:
            self._render_mcp_error(exc)
            return
        except swiggy_auth.AuthError as exc:
            self._send(web_ui.error_page(app.ctx(), "Swiggy sign-in failed",
                                         str(exc), relogin=True))
            return
        if body is not None:
            self._send(body)

    def _render_mcp_error(self, exc):
        app = self.app
        if exc.status == 401:
            # The 5-day token is the whole session and there is no refresh
            # token, so a 401 means signing in again - nothing subtler.
            app.sign_out("Your Swiggy session expired. Sign in again to carry on.",
                         kind="warn")
            self._send(web_ui.login_page("Your Swiggy session expired - "
                                         "please sign in again.", app.nonce))
            return
        self._send(web_ui.error_page(app.ctx(), "Swiggy would not do that",
                                     str(exc), retry="/cart"))

    # -------------------------------------------------------- auth views

    def view_home(self, form, query):
        app = self.app
        if not app.signed_in:
            return web_ui.login_page(nonce=app.nonce)
        if not app.ensure_address():
            app.say("warn", "You have no saved addresses yet - add one to start.")
            raise Redirect("/address/new")
        app.ensure_planner()
        return web_ui.home_page(app.ctx())

    def act_login(self, form, query):
        app = self.app
        redirect_uri = "http://127.0.0.1:%d/callback" % app.port
        pending = swiggy_auth.begin_login(redirect_uri)
        # Only ever a couple of these; drop anything older than 10 minutes.
        cutoff = time.time() - 600
        app.pending_auth = {s: p for s, p in app.pending_auth.items()
                            if p["started_at"] > cutoff}
        app.pending_auth[pending["state"]] = pending
        raise Redirect(pending["auth_url"])

    def view_callback(self, form, query):
        """Swiggy's redirect lands here - the second half of the login."""
        app = self.app
        if query.get("error"):
            return web_ui.login_page("Swiggy declined the sign-in: %s %s"
                                     % (query.get("error"),
                                        query.get("error_description", "")),
                                     app.nonce)
        state = query.get("state")
        pending = app.pending_auth.pop(state, None)
        if not pending:
            # No matching state means this redirect was not one we started.
            return web_ui.login_page("That sign-in did not match a request from "
                                     "this app. Start again.", app.nonce)
        if not query.get("code"):
            return web_ui.login_page("Swiggy sent no authorization code.",
                                     app.nonce)

        record = swiggy_auth.finish_login(pending, query["code"])
        app.reset_session()
        app.token = record["access_token"]
        app.user_id = record.get("user_id")
        raise Redirect("/")

    def act_profile(self, form, query):
        """Set the label this app shows for you.

        Swiggy's API never sends a name, so there is nothing to look up: this
        is the person's own label, stored beside their session on this machine.
        """
        app = self.app
        name = (form.get("display_name") or "").strip()[:40]
        app.display_name = name or None
        app.remember(display_name=app.display_name)
        raise Redirect("/")

    def act_logout(self, form, query):
        self.app.sign_out("Signed out. Your session was removed from this machine.")
        raise Redirect("/")

    # -------------------------------------------------------- addresses

    def act_select_address(self, form, query):
        app = self.app
        app.load_addresses()
        for label, address_id in app.addresses:
            if address_id == form.get("address_id"):
                app.set_address(address_id, label)
                raise Redirect("/")
        app.say("bad", "That address is no longer on your account.")
        app.load_addresses(force=True)
        raise Redirect("/")

    def view_new_address(self, form, query):
        return web_ui.address_form_page(self.app.ctx())

    def act_new_address(self, form, query):
        """Create an address, letting Swiggy resolve the coordinates.

        The docs are explicit that the user gives one address string and the
        client splits it, rather than being interrogated field by field - so
        the parts are derived here and shown for correction.
        """
        app = self.app
        full = (form.get("fullAddress") or "").strip()
        name = (form.get("userName") or "").strip()
        phone = (form.get("userPhone") or "").strip()
        if not (full and name and phone):
            return web_ui.address_form_page(
                app.ctx(), form, "The address, your name and your phone number "
                                 "are all required.")

        parts = [p.strip() for p in full.split(",") if p.strip()]
        city, postal = _split_city_and_pin(parts)
        args = {
            "fullAddress": full,
            "addressLine": (form.get("addressLine") or "").strip() or (parts[0] if parts else full),
            "addressLine2": (form.get("addressLine2") or "").strip(),
            "city": (form.get("city") or "").strip() or city,
            "postalCode": (form.get("postalCode") or "").strip() or postal,
            "addressCategory": form.get("addressCategory") or "HOME",
            "userName": name,
            "userPhone": phone,
        }
        if not args["city"] or not args["postalCode"]:
            return web_ui.address_form_page(
                app.ctx(), form, "I could not find a city and a postal code in "
                                 "that address - fill them in under \"correct the "
                                 "parts\" below.")
        locality = (form.get("locality") or "").strip()
        if locality:
            args["locality"] = locality

        if not app.display_name:
            app.display_name = name[:40]
            app.remember(display_name=app.display_name)
        created = guard(app.mcp().call_tool("create_address", args), "create_address")
        address_id = tool_data(created, "addressId")
        app.load_addresses(force=True)
        if address_id:
            label = next((l for l, a in app.addresses if a == address_id), full[:40])
            app.set_address(address_id, label)
        else:
            # No addressId came back, so nothing switched and the chip is
            # unchanged - which means this one does have to be said.
            app.say("ok", "Address saved. Pick it from the top-left.")
        raise Redirect("/")

    # -------------------------------------------------------- ordering

    def act_order(self, form, query):
        """Read the box - as a list of products, or as something to cook.

        The model is only consulted when the text asks what to buy rather than
        saying what to buy, so a plain list stays instant, free and offline.
        Whatever it suggests is treated exactly like typed words from here on:
        parsed, and shown for confirmation before anything is searched.
        """
        app = self.app
        text = (form.get("text") or "").strip()
        app.draft = text
        app.suggested_from = None
        if not text:
            app.say("warn", "Type what you need first.")
            raise Redirect("/")

        # "bread omelette" is a dish, but it contains no verb to detect - a
        # bare dish name looks exactly like a product name. So detection
        # handles the phrasings it can ("I want to make ...") and the button
        # covers the rest, which is the only way to be sure.
        asked = form.get("mode") == "recipe"
        if asked and not recipe.available():
            app.say("warn", "No Gemini API key is configured, so ingredients "
                            "cannot be worked out. Reading it as a list.")
        if recipe.available() and (asked or recipe.is_recipe_request(text)):
            try:
                lines = recipe.expand(text)
            except recipe.RecipeError as exc:
                app.say("warn", "Could not work out a shopping list for that "
                                "(%s). List the items yourself and I will find "
                                "them." % exc)
                lines = []
            if not lines:
                # Do NOT fall through to the parser. "I want to make a fruit
                # custard" parses to a product called "to make fruit custard",
                # and searching Instamart for that helps nobody - it just
                # turns a failed suggestion into a confusing empty result.
                # Their text is still in the box; let them rewrite it.
                raise Redirect("/")
            app.suggested_from = text
            text = "\n".join(lines)

        requests = parse_order(text)
        if not requests:
            app.say("bad", "I could not pick any items out of that. Try "
                           "\"name + size\", like \"500 gms paneer\" - or "
                           "describe a dish, like \"I want to make a mango "
                           "smoothie\".")
            raise Redirect("/")
        app.parsed = requests
        raise Redirect("/review")

    def view_review(self, form, query):
        app = self.app
        if not app.parsed:
            raise Redirect("/")
        return web_ui.review_page(app.ctx(), app.parsed)

    def act_review(self, form, query):
        """Search every confirmed line; queue whatever needs a human choice.

        First, though: anything typed into "anything missing" is parsed and
        added to the list, and the screen comes back so the additions are
        echoed too. A suggested list is often nearly right, and re-typing the
        whole thing to add one item would be silly.
        """
        app = self.app
        if not app.parsed:
            raise Redirect("/")

        extra = (form.get("add") or "").strip()
        if extra:
            more = parse_order(extra)
            if more:
                app.parsed = list(app.parsed) + more
                # Say it out loud. Either button lands here when the box has
                # text, so someone who typed and pressed "Yes, find these"
                # needs to know why they are back on the same screen.
                app.say("ok", "Added to the list - check it, then confirm.")
            else:
                app.say("warn", "Could not pick any items out of %r - try "
                                "\"name + size\", like \"200 g ice cream\"."
                        % extra[:60])
            raise Redirect("/review")

        requests, app.parsed = app.parsed, []
        app.suggested_from = None
        app.ensure_planner()
        outcomes = []
        for request in requests:
            try:
                outcome, pending = app.resolve(request)
            except McpError as exc:
                app.say("warn", "Search failed for %r: %s" % (request.name, exc))
                outcomes.append("error")
                continue
            outcomes.append(outcome)
            if pending:
                app.queue.append(pending)
        app.draft = ""
        if outcomes and all(o in ("none", "error") for o in outcomes):
            # Not one item found. A single miss is a stock gap; every item
            # missing usually means no store is serving this address - which
            # the API reports as a cheerful "Found 0 product(s)".
            app.say("warn", "Not one of those came back for %s. That address may "
                            "not be served right now - try another from the "
                            "top-left, or a different wording."
                    % (app.address_label or "this address"))
        if app.queue:
            raise Redirect("/choose")
        if not app.planner.lines:
            app.say("warn", "Nothing could be added, so your basket is still empty.")
            raise Redirect("/")
        app.measure_fees()
        raise Redirect("/cart")

    def view_choose(self, form, query):
        app = self.app
        if not app.queue:
            raise Redirect("/cart")
        item = dict(app.queue[0])
        item["remaining"] = len(app.queue) - 1
        return web_ui.choose_page(app.ctx(), item)

    def act_choose(self, form, query):
        app = self.app
        if not app.queue:
            raise Redirect("/cart")
        item = app.queue.pop(0)

        if form.get("action") == "skip":
            app.say("info", "Skipped %s." % item["request"].name)
        else:
            index = self._int(form, "pick", -1)
            if 0 <= index < len(item["options"]):
                row = item["options"][index]["row"]
                cap = row["maxQuantity"] or 99
                quantity = max(1, min(self._int(form, "quantity", 1), cap))
                probe = app.planner.would_fit(row["price"], quantity)
                if probe.ok:
                    app.add_line(row, quantity)
                else:
                    app.say("bad", "%s not added - %s" % (row["name"], probe.message()))
            else:
                app.say("warn", "Nothing was selected, so %s was skipped."
                        % item["request"].name)

        if app.queue:
            raise Redirect("/choose")
        if not app.planner.lines:
            app.say("warn", "Your basket is empty.")
            raise Redirect("/")
        app.measure_fees()
        raise Redirect("/cart")

    # -------------------------------------------------------- cart

    def view_cart(self, form, query):
        app = self.app
        if not app.signed_in:
            raise Redirect("/")
        app.ensure_planner()
        if not app.planner.lines:
            return web_ui.cart_page(app.ctx(), None)
        view = app.cart_view()
        if view["blocked"] and app.payment:
            app.payment = None
        return web_ui.cart_page(app.ctx(keep_flash=False), view)

    def act_qty(self, form, query):
        app = self.app
        index = self._int(form, "index", -1)
        delta = self._int(form, "delta", 0)
        lines = app.planner.lines if app.planner else []
        if 0 <= index < len(lines):
            line = lines[index]
            wanted = line["quantity"] + delta
            cap = line.get("maxQuantity") or 99
            if wanted <= 0:
                lines.pop(index)
            elif wanted > cap:
                app.say("warn", "Swiggy allows at most %d of %s per order."
                        % (cap, line["name"]))
            else:
                probe = app.planner.would_fit(line["price"], delta)
                if delta > 0 and not probe.ok:
                    app.say("bad", probe.message())
                else:
                    line["quantity"] = wanted
        raise Redirect("/cart")

    def act_remove(self, form, query):
        app = self.app
        index = self._int(form, "index", -1)
        lines = app.planner.lines if app.planner else []
        if 0 <= index < len(lines):
            lines.pop(index)
        raise Redirect("/cart")

    def act_rebuild(self, form, query):
        """Empty the cart on Swiggy's side, then write back only our basket.

        This exists because update_cart did not evict a stranger: the docs say
        it replaces the whole cart, and live it kept an item added from the
        phone app anyway (field notes 1.7). clear_cart is the only other lever
        there is. If the item returns, something else is writing to the
        account right now - which is worth knowing rather than fighting.
        """
        app = self.app
        if app.dry_run:
            raise Redirect("/cart")
        try:
            guard(app.mcp().call_tool("clear_cart", {}), "clear_cart")
        except McpError as exc:
            app.say("bad", "Could not empty the cart: %s" % exc)
            raise Redirect("/cart")
        if app.planner.lines:
            app.push_cart()
        app.say("ok", "Cart rebuilt from your basket alone.")
        raise Redirect("/cart")

    def act_payment(self, form, query):
        app = self.app
        options = app.load_payment_options() or []
        index = self._int(form, "index", -1)
        if 0 <= index < len(options):
            app.payment = options[index]
        else:
            app.say("warn", "That payment method is no longer offered.")
            app.payment_options = None
        raise Redirect("/cart")

    # -------------------------------------------------------- checkout

    def view_confirm(self, form, query):
        app = self.app
        if app.dry_run:
            return web_ui.error_page(app.ctx(), "Dry run",
                                     "Ordering is disabled in dry-run mode.", "/cart")
        if not (app.planner and app.planner.lines):
            raise Redirect("/cart")
        if not app.payment:
            app.say("warn", "Choose how to pay first.")
            raise Redirect("/cart")

        # Re-price from scratch: this is the last screen before real money.
        view = app.cart_view()
        if view["blocked"]:
            app.say("bad", view["gate_message"])
            raise Redirect("/cart")
        return web_ui.confirm_page(app.ctx(), {
            "to_pay": view["to_pay"],
            "item_count": len(app.planner.lines),
        })

    def act_checkout(self, form, query):
        """The one irreversible call in the app."""
        app = self.app
        if app.dry_run:
            return web_ui.error_page(app.ctx(), "Dry run",
                                     "Nothing is ordered in dry-run mode.", "/cart")
        if not (app.planner and app.planner.lines and app.payment):
            raise Redirect("/cart")

        expected = self._int(form, "expect_paise", -1)
        view = app.cart_view()
        if view["blocked"]:
            app.say("bad", view["gate_message"])
            raise Redirect("/cart")
        if view["to_pay"] != expected:
            # The price moved between the confirmation screen and the click.
            # Nobody consented to the new number, so bounce it back.
            app.say("warn", "The total changed from %s to %s while you were "
                            "confirming, so nothing was ordered. Check it and "
                            "try again." % (rupees(expected), rupees(view["to_pay"])))
            raise Redirect("/cart")

        args = {"addressId": app.address_id}
        args.update(app.payment["args"])
        result = guard(app.mcp().call_tool("checkout", args), "checkout")

        app.order = {
            "order_id": tool_data(result, "orderId"),
            "status": tool_data(result, "status"),
            "paas_id": tool_data(result, "paasId"),
            "transaction_id": tool_data(result, "transactionId"),
            # The checkout response is the only trustworthy record of what was
            # charged and how - get_orders overwrites both.
            "total": parse_paise(tool_data(result, "cartTotal")) or view["to_pay"],
            "method": tool_data(result, "paymentMethod") or app.payment["label"],
            "message": (result.get("_text") or "").strip(),
        }
        app.planner.lines = []
        app.payment_options = None
        status = str(app.order["status"] or "").upper()
        if app.payment["upi"] or status == "PENDING_PAYMENT":
            app.poll_until = time.time() + UPI_POLL_SECONDS
            raise Redirect("/payment/pending")
        raise Redirect("/placed")

    def view_placed(self, form, query):
        app = self.app
        if not app.order:
            raise Redirect("/")
        order = app.order
        status = str(order.get("status") or "").upper()
        if status in ("PENDING_PAYMENT", "PENDING"):
            headline, detail = "Order awaiting payment", (
                "Swiggy has the order but the payment is not settled. Check the "
                "Swiggy app if you have already approved it.")
        else:
            # The docs ask for the tool's own success line to be shown as-is.
            headline = "Instamart order placed successfully"
            detail = order.get("message") or ""
        return web_ui.placed_page(app.ctx(), {
            "headline": headline,
            "detail": detail[:300],
            "order_id": order.get("order_id"),
            "status": order.get("status"),
            "total": order.get("total"),
            "track": ("track_order(orderId=%s)" % order["order_id"]
                      if order.get("order_id") else None),
        })

    def view_pending(self, form, query):
        """Check a pending UPI payment once per page load - never in a loop.

        The docs warn against tight polling, and the payment widget that would
        normally auto-confirm does not exist here, so the page refreshes itself
        every 15 seconds and each load makes exactly one status call.
        """
        app = self.app
        order = app.order
        if not order:
            raise Redirect("/")
        if not order.get("paas_id"):
            app.say("warn", "Swiggy returned no payment id, so this cannot be "
                            "tracked here. Check the Swiggy app.")
            raise Redirect("/placed")

        keep = app.poll_until is not None and time.time() < app.poll_until
        try:
            payload = app.mcp().call_tool(
                "check_payment_status",
                {"paasId": order["paas_id"], "orderId": order["order_id"]})
            status = str(tool_data(payload, "status")
                         or tool_data(payload, "paymentStatus")
                         or (payload.get("_text") or ""))[:120]
        except McpError as exc:
            status = "could not check just now: %s" % exc
            payload = None

        app.status_text = status
        app.checked_at = time.strftime("%H:%M:%S")
        upper = status.upper()
        if payload is not None and ("SUCCESS" in upper or "PAID" in upper):
            guard(app.mcp().call_tool("confirm_order", {
                "orderId": order["order_id"], "paasId": order["paas_id"],
                "transactionId": order.get("transaction_id") or ""}), "confirm_order")
            order["status"] = "CONFIRMED"
            app.poll_until = None
            app.say("ok", "Payment confirmed and the order is finalised.")
            raise Redirect("/placed")
        if payload is not None and "FAIL" in upper:
            # Do not confirm a failed payment; the user must start over.
            app.poll_until = None
            app.say("bad", "The payment failed. Start a fresh payment from the "
                           "Swiggy app - do not assume this order is paid.")
            raise Redirect("/placed")

        return web_ui.pending_payment_page(app.ctx(), {
            "order_id": order.get("order_id"),
            "total": order.get("total"),
            "status_text": status,
            "checked_at": app.checked_at,
            "keep_polling": keep,
        })


def _split_city_and_pin(parts):
    """Pull a city and a 6-digit PIN out of a comma-written address."""
    postal, city = "", ""
    for chunk in reversed(parts):
        words = chunk.replace("-", " ").split()
        digits = [w for w in words if w.isdigit() and len(w) == 6]
        if digits and not postal:
            postal = digits[-1]
            rest = " ".join(w for w in words if w != digits[-1]).strip()
            if rest and not city:
                city = rest
            continue
        if not city and not any(ch.isdigit() for ch in chunk):
            city = chunk
    return city, postal


def _lsof(port):
    """PIDs listening on a TCP port, via lsof. Empty if lsof is unavailable."""
    for binary in ("lsof", "/usr/sbin/lsof"):
        try:
            out = subprocess.check_output(
                [binary, "-nP", "-tiTCP:%d" % port, "-sTCP:LISTEN"],
                stderr=subprocess.DEVNULL)
        except (OSError, subprocess.CalledProcessError):
            continue
        return [int(pid) for pid in out.split() if pid.strip().isdigit()]
    return []


def stale_instance(port):
    """PID of an EARLIER COPY OF THIS APP holding the port, or None.

    Restarting is the normal way to pick up a change, and a leftover instance
    makes the new one die at bind time with a message that scrolls past. The
    symptom is baffling: no browser opens and the page still shows the old
    code, because the old server is the one answering.

    Only ever matches this program. Anything else on the port is somebody
    else's business and is reported rather than killed.
    """
    for pid in _lsof(port):
        if pid == os.getpid():
            continue
        try:
            command = subprocess.check_output(
                ["ps", "-o", "command=", "-p", str(pid)],
                stderr=subprocess.DEVNULL).decode("utf-8", "replace")
        except (OSError, subprocess.CalledProcessError):
            continue
        if os.path.basename(__file__).split(".")[0] in command:
            return pid
    return None


def port_free(port):
    """Can we actually bind it? The only test that means anything.

    lsof reporting no listener is not the same thing: after the old process
    dies its socket can linger a moment, and bind still fails with EADDRINUSE.
    Binding a throwaway socket is the real answer - and since it never listens
    or connects, closing it leaves nothing in TIME_WAIT.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def replace_stale_instance(port, wait=5.0):
    """Stop our own leftover server so this one can bind. True if the port is free."""
    pid = stale_instance(port)
    if pid is None:
        return False
    print("An older copy of this app (pid %d) still holds port %d - stopping it."
          % (pid, port))
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        print("  could not stop it: %s" % exc)
        return False
    deadline = time.time() + wait
    while time.time() < deadline:
        if port_free(port):
            return True
        time.sleep(0.2)
    return port_free(port)


def open_browser(url):
    """Open the app in a browser, and report honestly if we could not.

    On macOS the stdlib picks MacOSXOSAScript, which drives the browser over
    AppleScript - and that does nothing at all, silently, unless the terminal
    running this has been granted Automation permission. /usr/bin/open goes
    through Launch Services instead, needs no permission, and is what actually
    works. Try it first, keep webbrowser as the fallback elsewhere, and never
    swallow the failure: the URL is the one thing the user needs.
    """
    if sys.platform == "darwin" and os.path.exists("/usr/bin/open"):
        try:
            if subprocess.call(["/usr/bin/open", url]) == 0:
                return True
        except OSError:
            pass
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description="Web UI for Instamart ordering")
    ap.add_argument("--port", type=int, default=swiggy_auth.REDIRECT_PORT,
                    help="port to serve on (default %d)" % swiggy_auth.REDIRECT_PORT)
    ap.add_argument("--budget", type=float, default=None,
                    help="your own ceiling in rupees (capped at the Rs 999 payable max)")
    ap.add_argument("--dry-run", action="store_true",
                    help="walk the whole UI without writing the cart or ordering")
    ap.add_argument("--no-browser", action="store_true",
                    help="do not open a browser window")
    args = ap.parse_args()

    budget_paise = int(round(args.budget * 100)) if args.budget else None
    Handler.app = App(args.port, budget_paise, args.dry_run)

    # 127.0.0.1, not 0.0.0.0: this holds a live payment-capable session and has
    # no login of its own, so it must not be reachable from the network.
    try:
        server = HTTPServer(("127.0.0.1", args.port), Handler)
    except OSError:
        # Nearly always our own previous run. Take the port over rather than
        # exiting with a message that scrolls away while the old code keeps
        # serving the browser.
        if replace_stale_instance(args.port):
            try:
                server = HTTPServer(("127.0.0.1", args.port), Handler)
            except OSError as exc:
                print("Port %d is still busy: %s" % (args.port, exc))
                return 1
        else:
            print("Cannot listen on 127.0.0.1:%d - something else is using it."
                  % args.port)
            print("That something is not this app. The CLI's login server uses "
                  "%d too." % swiggy_auth.REDIRECT_PORT)
            print("Find it with:  lsof -nP -iTCP:%d -sTCP:LISTEN" % args.port)
            print("Or pick another port:  python3 %s --port 9000"
                  % os.path.basename(__file__))
            return 1

    url = "http://127.0.0.1:%d/" % args.port
    print("%s is running at %s" % (web_ui.BRAND, url))
    if args.dry_run:
        print("DRY RUN - the cart is never written and no order can be placed.")
    if budget_paise:
        print("Ceiling for this session: %s" % rupees(budget_paise))
    print("Sessions are stored under %s, never in this folder." % swiggy_auth.TOKEN_DIR)
    print("Press Ctrl-C to stop.")
    if not args.no_browser and not open_browser(url):
        print("Could not open a browser for you - open %s yourself." % url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. Nothing in flight was ordered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
