"""Parser tests against the string FORMATS the live Swiggy API returns.

These encode facts about a display-formatted API that has no schema and can change
without notice. If one of these fails, the API moved — not the test.

Order ids and merchant names here are fictional; only the formats are real, and the
formats are what the parsers care about.
"""

import datetime as dt
import unittest

from mealwise.history import (
    ParsedItem,
    item_key,
    parse_food_order,
    parse_instamart_order,
    parse_inr,
    parse_item_list,
    parse_ordered_on,
)

NOW = dt.datetime(2026, 8, 23, 18, 0, tzinfo=dt.timezone.utc)


class TestParseInr(unittest.TestCase):
    def test_rupee_string(self):
        self.assertEqual(parse_inr("₹310"), 310)

    def test_thousands_and_decimals(self):
        self.assertEqual(parse_inr("₹1,349.00"), 1349)

    def test_free_is_zero(self):
        self.assertEqual(parse_inr("FREE"), 0)

    def test_plain_number(self):
        self.assertEqual(parse_inr(258), 258)

    def test_junk_is_none(self):
        self.assertIsNone(parse_inr("n/a"))

    def test_bool_is_not_a_number(self):
        # True is an int in Python — a trap the TypeScript original could not have.
        self.assertIsNone(parse_inr(True))

    def test_empty(self):
        self.assertIsNone(parse_inr(""))


class TestParseItemList(unittest.TestCase):
    def test_real_joined_string(self):
        self.assertEqual(
            parse_item_list("Veg Noodles (1),Malai Chaap (1),Cold Coffee (1)"),
            [ParsedItem("Veg Noodles", 1), ParsedItem("Malai Chaap", 1), ParsedItem("Cold Coffee", 1)],
        )

    def test_quantities_above_one(self):
        self.assertEqual(
            parse_item_list("Paneer Tikka (2),Roti (4)"),
            [ParsedItem("Paneer Tikka", 2), ParsedItem("Roti", 4)],
        )

    def test_missing_quantity_defaults_to_one(self):
        self.assertEqual(parse_item_list("Biryani"), [ParsedItem("Biryani", 1)])

    def test_instamart_returns_objects(self):
        self.assertEqual(
            parse_item_list([{"itemName": "Country Delight Ghar Jaisa Dahi", "quantity": 1}]),
            [ParsedItem("Country Delight Ghar Jaisa Dahi", 1)],
        )

    def test_empty(self):
        self.assertEqual(parse_item_list(""), [])


class TestParseOrderedOn(unittest.TestCase):
    def test_no_year_recent_past(self):
        self.assertEqual(parse_ordered_on("August 22, 10:13 PM", NOW), dt.date(2026, 8, 22))

    def test_no_year_future_rolls_back_a_year(self):
        # December is ahead of August, so it must be LAST December, not a prediction.
        self.assertEqual(parse_ordered_on("December 25, 8:00 PM", NOW), dt.date(2025, 12, 25))

    def test_iso_timestamp(self):
        self.assertEqual(parse_ordered_on("2026-08-20T15:13:49.000Z", NOW), dt.date(2026, 8, 20))

    def test_impossible_date_rejected(self):
        self.assertIsNone(parse_ordered_on("February 31, 1:00 AM", NOW))

    def test_junk(self):
        self.assertIsNone(parse_ordered_on("yesterday", NOW))


class TestWholeOrders(unittest.TestCase):
    def test_real_food_order(self):
        order = parse_food_order(
            {
                "orderId": "100000000000001",
                "restaurantName": "Example Kitchen",
                "orderTotal": "₹310",
                "orderStatus": "Delivered",
                "orderedItems": "Veg Noodles (1),Malai Chaap (1),Cold Coffee (1)",
                "orderedTime": "August 22, 10:13 PM",
            },
            NOW,
        )
        assert order is not None
        self.assertEqual(order.source, "food")
        self.assertEqual(order.total_inr, 310)
        self.assertEqual(order.merchant, "Example Kitchen")
        self.assertEqual(order.ordered_on, dt.date(2026, 8, 22))
        self.assertEqual(len(order.items), 3)

    def test_real_instamart_order(self):
        order = parse_instamart_order(
            {
                "orderId": "100000000000002",
                "currentStatus": "DELIVERED",
                "createdAt": "2026-08-20T15:13:49.000Z",
                "totalAmount": "₹140",
                "storeName": "Instamart",
                "items": [{"itemName": "Dahi", "quantity": 1}],
            },
            NOW,
        )
        assert order is not None
        self.assertEqual(order.total_inr, 140)
        self.assertEqual(order.status, "DELIVERED")
        self.assertEqual(order.ordered_on, dt.date(2026, 8, 20))

    def test_missing_order_id_is_skipped(self):
        self.assertIsNone(parse_food_order({"orderTotal": "₹100"}))


class TestItemKey(unittest.TestCase):
    def test_normalises_case_and_spacing(self):
        self.assertEqual(item_key("  Malai   CHAAP "), "malai chaap")


if __name__ == "__main__":
    unittest.main()
