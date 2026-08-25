"""The parser is the last line of defence against a swallowed quantity.

Section 1.10 of docs/instamart-notes.md: a count vanished silently once,
and an item was ordered at the wrong quantity because of it.
"""

import unittest

from mealwise.parse_order import (OrderRequest, parse_order, parse_size_text,
                                  rank_variants, score_variant)


def names(requests):
    return [r.name for r in requests]


class ParseOrder(unittest.TestCase):
    def test_splits_on_commas_and_and(self):
        got = parse_order("500 gms paneer, 2 kg atta and 3 packets of maggi")
        self.assertEqual(names(got), ["paneer", "atta", "maggi"])

    def test_size_before_or_after_the_name(self):
        (a,) = parse_order("milk 1 litre")
        (b,) = parse_order("1 litre milk")
        self.assertEqual(a.name, "milk")
        self.assertEqual(str(a.size), str(b.size))

    def test_eggs_are_a_pack_size_not_a_count(self):
        # "12 eggs" means one 12-piece tray, not twelve separate items - so
        # it searches for a pack of 12 rather than ordering 12 of something.
        (eggs,) = parse_order("12 eggs")
        self.assertEqual(eggs.count, 1)
        self.assertEqual(eggs.size.total, 12)
        self.assertEqual(str(parse_order("a dozen eggs")[0].size), str(eggs.size))

    def test_a_trailing_number_is_a_count(self):
        (maggi,) = parse_order("maggi 2")
        self.assertEqual(maggi.count, 2)

    def test_brand_glued_to_a_number_is_not_a_count(self):
        # "7up" is a name; "2 maggi" is a count. The difference is the space.
        (drink,) = parse_order("7up")
        self.assertIn("7up", drink.name)
        self.assertEqual(drink.count, 1)

    def test_a_following_word_that_is_not_a_unit_keeps_the_count(self):
        (maggi,) = parse_order("2 maggi")
        self.assertEqual(maggi.count, 2)

    def test_empty_input_yields_nothing(self):
        self.assertEqual(parse_order(""), [])
        self.assertEqual(parse_order("   "), [])


class Sizes(unittest.TestCase):
    def test_grams_and_kilos_are_the_same_dimension(self):
        kg = parse_size_text("1 kg")
        g = parse_size_text("1000 g")
        self.assertEqual(kg.dimension, g.dimension)
        self.assertEqual(kg.total, g.total)

    def test_millilitres_are_not_grams(self):
        self.assertNotEqual(parse_size_text("500 ml").dimension,
                            parse_size_text("500 g").dimension)


class Ranking(unittest.TestCase):
    def request(self, text):
        (req,) = parse_order(text)
        return req

    def test_exact_size_scores_zero(self):
        req = self.request("1 kg atta")
        self.assertEqual(score_variant(req, parse_size_text("1 kg")), 0.0)

    def test_wrong_dimension_is_dropped_not_ranked_last(self):
        # Grams asked, millilitres offered: not the same product shape.
        req = self.request("1 kg atta")
        self.assertIsNone(score_variant(req, parse_size_text("1 litre")))

    def test_no_size_asked_is_neutral(self):
        req = self.request("atta")
        self.assertEqual(score_variant(req, parse_size_text("1 kg")), 1.0)

    def test_ties_at_the_same_size_are_ordered_by_price(self):
        req = self.request("1 kg rice")
        rows = [{"variant": "1 kg", "price": 11600},
                {"variant": "1 kg", "price": 8400},
                {"variant": "2 kg", "price": 9000}]
        ranked = rank_variants(req, rows)
        self.assertEqual([r[2]["price"] for r in ranked], [8400, 11600, 9000])

    def test_wrong_dimension_rows_do_not_survive_ranking(self):
        req = self.request("1 kg rice")
        ranked = rank_variants(req, [{"variant": "1 litre", "price": 100}])
        self.assertEqual(ranked, [])


if __name__ == "__main__":
    unittest.main()
