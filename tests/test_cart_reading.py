"""Cart-total and coordinate probing.

`get_cart` documents NO field for the payable total, and `get_addresses` returns no
coordinates at all — both of these read live shapes that the docs do not describe.
"""

import unittest

from mealwise.common import address_label, read_cart_coords, read_cart_total


class TestReadCartTotal(unittest.TestCase):
    def test_live_cart_total_amount(self):
        self.assertEqual(read_cart_total({"cartTotalAmount": "₹197"}), 197)

    def test_live_bill_breakdown_to_pay(self):
        cart = {"billBreakdown": {"lineItems": [], "toPay": {"label": "To Pay", "value": "₹162"}}}
        self.assertEqual(read_cart_total(cart), 162)

    def test_rupee_string_with_thousands(self):
        self.assertEqual(read_cart_total({"cartTotal": "₹1,349"}), 1349)

    def test_item_price_is_not_mistaken_for_the_total(self):
        # The case that matters: a deep search would return 62 here.
        self.assertIsNone(read_cart_total({"items": [{"itemName": "Milk", "mrp": 62}]}))

    def test_unknown_shape(self):
        self.assertIsNone(read_cart_total({"foo": "bar"}))

    def test_not_a_dict(self):
        self.assertIsNone(read_cart_total(None))

    def test_zero_is_a_real_total(self):
        self.assertEqual(read_cart_total({"cartTotal": 0}), 0)


class TestReadCartCoords(unittest.TestCase):
    def test_from_selected_address_details(self):
        cart = {"selectedAddressDetails": {"lat": 12.9197, "lng": 77.6472}}
        self.assertEqual(read_cart_coords(cart), (12.9197, 77.6472))

    def test_absent(self):
        self.assertIsNone(read_cart_coords({"selectedAddressDetails": {}}))


class TestAddressLabel(unittest.TestCase):
    def test_tag_and_line(self):
        label = address_label({"id": "x", "addressTag": "Home", "addressLine": "20/1, HSR"})
        self.assertEqual(label, "Home — 20/1, HSR")

    def test_falls_back_to_id_when_nothing_else(self):
        # The live API sends no `label` field, so the id is the last resort.
        self.assertEqual(address_label({"id": "183029481"}), "183029481")


if __name__ == "__main__":
    unittest.main()
