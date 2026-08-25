"""Swiggy's success prose is addressed to an agent, not to a person.

The live message on 2026-08-25 carried a bracketed order to the renderer
("Display the above message exactly as-is") and a warning that a rich widget
may repeat the data - both of which were printed on the order page, under a
table that already showed the order id, status and total.
"""

import unittest

from mealwise.web_app import human_message

HEADLINE = "Instamart order placed successfully"

# Verbatim from a real confirm_order response.
LIVE = ("\U0001F389 Instamart order placed successfully! Sit back and enjoy! "
        "Order ID: 246629482120009\n"
        "[IMPORTANT: Display the above message exactly as-is to the user. "
        "Do not rephrase or summarize it.]\n"
        "⚠️ A rich UI widget may be shown to the user with this "
        "data. Avoid restating everything the widget already displays")


class TheLiveMessage(unittest.TestCase):
    def test_only_the_human_sentence_survives(self):
        self.assertEqual(human_message(LIVE, HEADLINE), "Sit back and enjoy!")

    def test_no_instruction_to_the_renderer_reaches_the_page(self):
        out = human_message(LIVE, HEADLINE)
        for leak in ("IMPORTANT", "as-is", "widget", "rephrase", "⚠"):
            self.assertNotIn(leak, out, leak)

    def test_the_order_id_is_not_repeated(self):
        # The table below the headline already shows it.
        self.assertNotIn("246629482120009", human_message(LIVE, HEADLINE))


class OtherShapes(unittest.TestCase):
    def test_a_message_that_only_restates_the_headline_is_dropped(self):
        self.assertEqual(human_message("Instamart order placed successfully!",
                                       HEADLINE), "")

    def test_a_genuinely_new_sentence_is_kept(self):
        self.assertEqual(human_message("Your order is on its way.", HEADLINE),
                         "Your order is on its way.")

    def test_missing_or_empty_prose_is_harmless(self):
        for value in (None, "", "   ", "\U0001F389"):
            self.assertEqual(human_message(value, HEADLINE), "", repr(value))

    def test_either_marker_ends_the_human_part(self):
        self.assertEqual(human_message("All good. ⚠ widget note", HEADLINE),
                         "All good.")
        self.assertEqual(human_message("Done. [IMPORTANT: do x]", HEADLINE),
                         "Done.")


if __name__ == "__main__":
    unittest.main()

