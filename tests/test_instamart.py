"""The cart engine: the checkout gate, and reading Swiggy's own cart.

The gate exists because Swiggy refuses checkout at ₹1000 or more, so the
planner must never build a cart it cannot pay for.
"""

import unittest

from mealwise.instamart import (CHECKOUT_LIMIT_PAISE, MAX_PAYABLE_PAISE,
                                BudgetExceeded, CartPlanner, cart_item_total,
                                cart_write_warnings, fee_overhead,
                                flatten_variations)


def variation(spin="S1", sku="K1", price=5700, mrp=6500, stock=True,
              quantity="1 kg", max_quantity=9):
    return {"spinId": spin, "skuId": sku, "displayName": "Aashirvaad Atta",
            "quantityDescription": quantity, "maxQuantity": max_quantity,
            "isInStockAndAvailable": stock, "imageUrl": "https://x/y.jpg",
            "price": {"offerPrice": price, "mrp": mrp,
                      "unitLevelPrice": "5.7/100 g"}}


class FlattenVariations(unittest.TestCase):
    def test_one_row_per_in_stock_variation(self):
        products = [{"variations": [variation(), variation(spin="S2")]}]
        self.assertEqual(len(flatten_variations(products)), 2)

    def test_out_of_stock_variations_are_dropped(self):
        products = [{"variations": [variation(), variation(spin="S2", stock=False)]}]
        self.assertEqual(len(flatten_variations(products)), 1)

    def test_only_the_first_few_products_are_walked(self):
        # The list shown to a user is capped; this is where that starts.
        products = [{"variations": [variation(spin=str(i))]} for i in range(10)]
        self.assertEqual(len(flatten_variations(products, limit=6)), 6)

    def test_carries_both_ids(self):
        # spinId is the catalogue id, skuId is stock at one store. A cart
        # write needs both; neither substitutes for the other.
        (row,) = flatten_variations([{"variations": [variation()]}])
        self.assertEqual((row["spinId"], row["skuId"]), ("S1", "K1"))


class Gate(unittest.TestCase):
    def test_swiggy_limit_caps_even_a_higher_budget(self):
        planner = CartPlanner("addr", budget_paise=500000)
        self.assertEqual(planner.limit, MAX_PAYABLE_PAISE)
        self.assertLess(planner.limit, CHECKOUT_LIMIT_PAISE)

    def test_would_fit_does_not_apply_the_add(self):
        planner = CartPlanner("addr")
        planner.would_fit(50000, 2)
        self.assertEqual(planner.lines, [])

    def test_an_add_over_the_limit_raises_when_strict(self):
        planner = CartPlanner("addr")
        with self.assertRaises(BudgetExceeded):
            planner.add("S1", "K1", "Caviar", MAX_PAYABLE_PAISE + 100)

    def test_an_add_over_the_limit_reports_when_not_strict(self):
        planner = CartPlanner("addr")
        status = planner.add("S1", "K1", "Caviar", MAX_PAYABLE_PAISE + 100,
                             strict=False)
        self.assertFalse(status.ok)
        self.assertEqual(planner.lines, [])

    def test_adding_the_same_item_twice_raises_the_line(self):
        planner = CartPlanner("addr")
        planner.add("S1", "K1", "Atta", 5700)
        planner.add("S1", "K1", "Atta", 5700)
        self.assertEqual(len(planner.lines), 1)
        self.assertEqual(planner.lines[0]["quantity"], 2)

    def test_a_line_at_swiggys_cap_adds_nothing_more(self):
        planner = CartPlanner("addr")
        planner.add("S1", "K1", "Atta", 5700, quantity=2, max_quantity=2)
        planner.add("S1", "K1", "Atta", 5700, quantity=2, max_quantity=2)
        self.assertEqual(planner.lines[0]["quantity"], 2)

    def test_update_cart_args_carry_both_ids(self):
        planner = CartPlanner("addr")
        planner.add("S1", "K1", "Atta", 5700)
        args = planner.to_update_cart_args()
        self.assertEqual(args["selectedAddressId"], "addr")
        self.assertEqual(args["items"],
                         [{"spinId": "S1", "skuId": "K1", "quantity": 1}])

    def test_an_empty_basket_sends_an_empty_item_list(self):
        self.assertEqual(CartPlanner("addr").to_update_cart_args()["items"], [])


class ReadingSwiggysCart(unittest.TestCase):
    def test_item_total_prefers_the_servers_own_line(self):
        # items[] can hold entries the bill excludes (notes 1.6, 1.7), so
        # summing locally is not the truth. The bill line is.
        cart = {"billBreakdown": {"lineItems": [{"label": "Item Total",
                                                "value": "₹166.00"}]},
                "items": [{"discountedFinalPrice": "₹84"},
                          {"discountedFinalPrice": "₹69"}]}
        self.assertEqual(cart_item_total(cart), 16600)

    def test_falls_back_to_summing_when_there_is_no_bill_line(self):
        cart = {"items": [{"discountedFinalPrice": "₹84"},
                          {"discountedFinalPrice": "₹82"}]}
        self.assertEqual(cart_item_total(cart), 16600)

    def test_out_of_stock_items_are_not_summed(self):
        cart = {"items": [{"discountedFinalPrice": "₹84"},
                          {"discountedFinalPrice": "₹69",
                           "isInStockAndAvailable": False}]}
        self.assertEqual(cart_item_total(cart), 8400)

    def test_silent_changes_are_reported(self):
        # Neither of these is an error, so a caller checking only
        # success:false would show a cart that is not what it built.
        payload = {"reducedQuantityItems": [{"itemName": "Atta", "quantity": 2,
                                             "reason": "stock"}],
                   "removedOutOfStockItems": [{"itemName": "Paneer"}]}
        warnings = cart_write_warnings(payload)
        self.assertEqual(len(warnings), 2)
        self.assertIn("Atta", warnings[0])
        self.assertIn("Paneer", warnings[1])

    def test_no_warnings_on_a_clean_write(self):
        self.assertEqual(cart_write_warnings({}), [])

    def test_fees_are_the_difference_between_payable_and_items(self):
        cart = {"billBreakdown": {
            "lineItems": [{"label": "Item Total", "value": "₹166.00"}],
            "toPay": {"label": "To Pay", "value": "₹213"}}}
        self.assertEqual(fee_overhead(cart), 21300 - 16600)


if __name__ == "__main__":
    unittest.main()
