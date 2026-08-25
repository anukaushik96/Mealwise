"""Cart actions in the web UI, against a stubbed Swiggy client.

The one behaviour worth pinning down: removing the LAST line has to reach
Swiggy. Every write goes through push_cart, which runs only when there is
something to price - so without an explicit clear the page would say the
cart is empty while the items sat in the live cart, still chargeable.
"""

import unittest

from mealwise import web_app
from mealwise.instamart import CartPlanner


class StubClient(object):
    """Records calls. Fails on demand, the way a tool-level error arrives."""

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def call_tool(self, name, args):
        self.calls.append((name, args))
        if self.fail:
            return {"success": False, "error": {"message": "cart busy"}}
        return {"success": True}


def make_handler(lines=1, dry_run=False, fail=False):
    """A Handler and App wired together without a socket or a session."""
    app = web_app.App.__new__(web_app.App)
    app.dry_run = dry_run
    app.payment = {"label": "UPI"}
    app.flash = []
    app.planner = CartPlanner("addr")
    for i in range(lines):
        app.planner.add("S%d" % i, "K%d" % i, "Item %d" % i, 3000)
        app.planner.lines[i]["maxQuantity"] = 9
    client = StubClient(fail)
    app.mcp = lambda: client
    app.say = lambda kind, text: app.flash.append((kind, text))
    handler = web_app.Handler.__new__(web_app.Handler)
    handler.app = app
    return handler, app, client


def act(handler, method, form):
    """Run one POST handler; every one of them ends in a redirect."""
    try:
        getattr(handler, method)(form, {})
    except web_app.Redirect as redirect:
        return redirect.args[0]
    raise AssertionError("%s did not redirect" % method)


def tools(client):
    return [name for name, _args in client.calls]


class RemovingTheLastLine(unittest.TestCase):
    def test_it_reaches_swiggy(self):
        handler, app, client = make_handler(lines=1)
        self.assertEqual(act(handler, "act_remove", {"index": "0"}), "/cart")
        self.assertEqual(app.planner.lines, [])
        self.assertEqual(tools(client), ["clear_cart"])

    def test_clear_cart_takes_no_arguments(self):
        handler, _app, client = make_handler(lines=1)
        act(handler, "act_remove", {"index": "0"})
        self.assertEqual(client.calls, [("clear_cart", {})])

    def test_stepping_the_quantity_to_zero_counts_as_removal(self):
        handler, app, client = make_handler(lines=1)
        act(handler, "act_qty", {"index": "0", "delta": "-1"})
        self.assertEqual(app.planner.lines, [])
        self.assertEqual(tools(client), ["clear_cart"])

    def test_it_says_so_rather_than_deleting_silently(self):
        # clear_cart empties the whole account cart, so a phone-added item
        # goes with it. Finding that out at checkout is not acceptable.
        _handler, app, _client = make_handler(lines=1)
        act(_handler, "act_remove", {"index": "0"})
        self.assertEqual([kind for kind, _text in app.flash], ["info"])

    def test_the_chosen_payment_method_is_dropped(self):
        handler, app, _client = make_handler(lines=1)
        act(handler, "act_remove", {"index": "0"})
        self.assertIsNone(app.payment)


class RemovingOneOfSeveral(unittest.TestCase):
    def test_it_does_not_clear_the_cart(self):
        handler, app, client = make_handler(lines=2)
        act(handler, "act_remove", {"index": "0"})
        self.assertEqual(len(app.planner.lines), 1)
        self.assertEqual(client.calls, [])

    def test_it_stays_quiet(self):
        handler, app, _client = make_handler(lines=2)
        act(handler, "act_remove", {"index": "0"})
        self.assertEqual(app.flash, [])

    def test_an_out_of_range_index_changes_nothing(self):
        handler, app, client = make_handler(lines=2)
        act(handler, "act_remove", {"index": "9"})
        self.assertEqual(len(app.planner.lines), 2)
        self.assertEqual(client.calls, [])


class WhenThingsGoWrong(unittest.TestCase):
    def test_a_dry_run_writes_nothing_and_claims_nothing(self):
        handler, app, client = make_handler(lines=1, dry_run=True)
        act(handler, "act_remove", {"index": "0"})
        self.assertEqual(app.planner.lines, [])
        self.assertEqual(client.calls, [])
        self.assertEqual(app.flash, [])

    def test_a_failed_clear_is_reported_as_a_failure(self):
        handler, app, _client = make_handler(lines=1, fail=True)
        act(handler, "act_remove", {"index": "0"})
        kinds = [kind for kind, _text in app.flash]
        self.assertEqual(kinds, ["bad"])
        self.assertIn("could not be cleared", app.flash[0][1])

    def test_a_failed_clear_still_empties_the_local_basket(self):
        # The alternative is a basket that disagrees with the screen.
        handler, app, _client = make_handler(lines=1, fail=True)
        act(handler, "act_remove", {"index": "0"})
        self.assertEqual(app.planner.lines, [])


class QuantityControls(unittest.TestCase):
    def test_swiggys_per_item_cap_is_enforced(self):
        handler, app, client = make_handler(lines=1)
        app.planner.lines[0]["quantity"] = 9      # already at maxQuantity
        act(handler, "act_qty", {"index": "0", "delta": "1"})
        self.assertEqual(app.planner.lines[0]["quantity"], 9)
        self.assertEqual([kind for kind, _t in app.flash], ["warn"])
        self.assertEqual(client.calls, [])

    def test_a_normal_increment_is_applied(self):
        handler, app, _client = make_handler(lines=1)
        act(handler, "act_qty", {"index": "0", "delta": "1"})
        self.assertEqual(app.planner.lines[0]["quantity"], 2)


if __name__ == "__main__":
    unittest.main()
