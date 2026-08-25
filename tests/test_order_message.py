"""Three things in confirm_order's prose do not belong on a page.

confirm_order's success line is written for an LLM agent composing a chat
reply, and part of it tells the renderer what to do. The order confirmation
page was printing that at the reader.
"""

import unittest

from mealwise.web_app import human_message

LIVE = ("\U0001F389 Instamart order placed successfully! Sit back and enjoy! "
        "Order ID: 246629482120009\n"
        "[IMPORTANT: Display the above message exactly as-is to the user. "
        "Do not rephrase or summarize it.]\n"
        "⚠️ A rich UI widget may be shown to the user with this "
        "data. Avoid restating everything the widget already displays")


class TheNote(unittest.TestCase):
    def test_it_is_removed(self):
        out = human_message(LIVE)
        self.assertNotIn("IMPORTANT", out)
        self.assertNotIn("exactly as-is", out)
        self.assertNotIn("rephrase", out)

    def test_the_blank_line_it_leaves_is_collapsed(self):
        self.assertNotIn("\n\n", human_message(LIVE))

    def test_it_is_case_insensitive(self):
        self.assertEqual(human_message("Done. [important: do x]"), "Done.")

    def test_a_multi_line_note_goes_too(self):
        self.assertEqual(
            human_message("Done.\n[IMPORTANT: do x\nand y]"), "Done.")


class WhatSurvives(unittest.TestCase):
    """Only the three asides go. Swiggy's wording is not ours to edit."""

    def test_the_greeting_survives_verbatim(self):
        out = human_message(LIVE)
        self.assertIn("\U0001F389 Instamart order placed successfully! "
                      "Sit back and enjoy!", out)

    def test_the_widget_warning_is_removed(self):
        out = human_message(LIVE)
        self.assertNotIn("rich UI widget", out)
        self.assertNotIn("\u26a0", out)

    def test_the_duplicated_order_id_is_removed(self):
        # The table underneath already shows it.
        self.assertNotIn("246629482120009", human_message(LIVE))

    def test_prose_without_a_note_is_untouched(self):
        for text in ("Your order is on its way.", "\U0001F389 All done!"):
            self.assertEqual(human_message(text), text, text)

    def test_missing_prose_is_harmless(self):
        for value in (None, "", "   ", "\n\n"):
            self.assertEqual(human_message(value), "", repr(value))


class ThePage(unittest.TestCase):
    def test_the_note_does_not_reach_the_rendered_page(self):
        from mealwise import web_ui
        ctx = {"nonce": "N", "address_label": "Home", "flash": [],
               "dry_run": False, "signed_in": True, "display_name": "A",
               "payment": None}
        html = web_ui.placed_page(ctx, {
            "headline": "Instamart order placed successfully",
            "detail": human_message(LIVE)[:300],
            "order_id": "246629482120009", "status": "CONFIRMED",
            "total": 48400,
            "track": "track_order(orderId=246629482120009)"})
        self.assertNotIn("IMPORTANT", html)
        self.assertIn("Sit back and enjoy!", html)


if __name__ == "__main__":
    unittest.main()
