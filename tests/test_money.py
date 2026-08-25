"""Money is integer paise everywhere, because floats drift on repeated adds."""

import unittest

from mealwise.money import parse_paise, rupees


class ParsePaise(unittest.TestCase):
    def test_display_strings(self):
        self.assertEqual(parse_paise("₹234.00"), 23400)
        self.assertEqual(parse_paise("₹257"), 25700)
        self.assertEqual(parse_paise("₹1,234.50"), 123450)

    def test_bare_numbers(self):
        # search_products returns money as a plain number, not a string.
        self.assertEqual(parse_paise(144), 14400)
        self.assertEqual(parse_paise(1.5), 150)

    def test_free_is_zero_not_none(self):
        # "FREE" is a real charge of nothing; None would read as "unknown".
        for word in ("FREE", "free", "waived", "-", ""):
            self.assertEqual(parse_paise(word), 0, word)

    def test_unparseable_is_none(self):
        for junk in (None, "abc", "₹", True, False):
            self.assertIsNone(parse_paise(junk), junk)

    def test_negatives(self):
        self.assertEqual(parse_paise("-₹50"), -5000)
        self.assertEqual(parse_paise("(₹50)"), -5000)


class Rupees(unittest.TestCase):
    def test_trims_pointless_decimals(self):
        self.assertEqual(rupees(23400), "₹234")
        self.assertEqual(rupees(23450), "₹234.50")

    def test_groups_thousands(self):
        self.assertEqual(rupees(123450), "₹1,234.50")

    def test_unknown_is_a_question_mark(self):
        self.assertEqual(rupees(None), "?")

    def test_round_trip(self):
        for paise in (0, 1, 99, 100, 23450, 99900):
            self.assertEqual(parse_paise(rupees(paise)), paise, paise)


if __name__ == "__main__":
    unittest.main()
