"""The pages are built by %-formatting long HTML strings.

That style is fast to read but easy to break: one placeholder too many and
a page renders with a literal "%s" in it, or raises. These tests render
every page and check the obvious ways that goes wrong.
"""

import unittest

from mealwise import web_ui
from mealwise.parse_order import parse_order


def context(**overrides):
    ctx = {"signed_in": True, "user_id": "1", "display_name": "Anu",
           "addresses": [], "address_id": "addr", "address_label": "Home",
           "nonce": "TESTNONCE", "dry_run": False, "flash": [], "lines": [],
           "status": None, "payment": None, "draft": "", "suggested_from": None,
           "recipes_on": False}
    ctx.update(overrides)
    return ctx


def cart_view(**overrides):
    view = {"rows": [{"name": "Aashirvaad Atta 1 kg", "price": 5700,
                      "quantity": 2, "image": None, "max_quantity": 9,
                      "ours": True, "index": 0, "in_stock": True,
                      "billed": True}],
            "foreign": [], "strangers_are_free": False, "unbilled_paise": 0,
            "bill_lines": [("Item total", "₹114")], "to_pay": 11400,
            "warnings": [], "blocked": False, "gate_message": "",
            "payment_options": []}
    view.update(overrides)
    return view


class EveryPageRenders(unittest.TestCase):
    def assert_complete(self, html):
        self.assertTrue(html.startswith("<!doctype html>"))
        # A leftover placeholder means the % tuple did not match the string.
        body = html.split("</style>")[-1]
        self.assertNotIn("%s", body)
        self.assertNotIn("%d", body)

    def test_cart(self):
        self.assert_complete(web_ui.cart_page(context(), cart_view()))

    def test_empty_cart(self):
        html = web_ui.cart_page(context(), None)
        self.assert_complete(html)
        self.assertIn("Your cart is empty", html)

    def test_review_typed_list(self):
        html = web_ui.review_page(context(), parse_order("1 kg atta, 1 kg rice"))
        self.assert_complete(html)
        self.assertIn("Did I read that right?", html)

    def test_review_suggested_list(self):
        html = web_ui.review_page(
            context(suggested_from="I want to make a mango smoothie"),
            parse_order("1 kg mango, 1 litre milk"))
        self.assert_complete(html)
        self.assertIn("mango smoothie", html)

    def test_choose(self):
        (request,) = parse_order("1 litre milk")
        item = {"request": request, "why": "Pick the closest.", "remaining": 1,
                "max_quantity": 5, "quantity": 1,
                "options": [{"row": {"name": "Amul Gold", "brand": "Amul",
                                     "variant": "500 ml", "price": 3500,
                                     "mrp": 4000, "maxQuantity": 9,
                                     "promoted": False, "image": None,
                                     "unitPrice": "7/100 ml"},
                             "size": None, "exact": False}]}
        self.assert_complete(web_ui.choose_page(context(), item))


class TheListCheck(unittest.TestCase):
    def test_only_the_confirm_button_is_offered(self):
        html = web_ui.review_page(context(), parse_order("1 kg atta"))
        self.assertIn("Yes, find these", html)
        self.assertNotIn("Add to list", html)
        self.assertNotIn("Edit the list", html)

    def test_the_missing_items_box_stays(self):
        # It still works: any submit with text in it parses and comes back.
        html = web_ui.review_page(context(), parse_order("1 kg atta"))
        self.assertIn('name="add"', html)


class Escaping(unittest.TestCase):
    def test_a_typed_item_name_cannot_inject_markup(self):
        (request,) = parse_order("<script>alert(1)</script> milk")
        html = web_ui.review_page(context(), [request])
        self.assertNotIn("<script>alert(1)</script>", html)

    def test_an_address_label_cannot_inject_markup(self):
        html = web_ui.cart_page(context(address_label="<b>Home</b>"),
                                cart_view())
        self.assertNotIn("<b>Home</b>", html)


class Mutations(unittest.TestCase):
    def test_every_form_carries_the_nonce(self):
        # An attacker's page can POST to this port; it cannot read the token.
        html = web_ui.cart_page(context(), cart_view())
        self.assertEqual(html.count("<form"), html.count("TESTNONCE"))


if __name__ == "__main__":
    unittest.main()
