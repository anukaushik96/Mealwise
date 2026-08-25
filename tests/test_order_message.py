"""Swiggy's success prose is addressed to an agent, not to a person.

The live message on 2026-08-25 carried a bracketed order to the renderer
("Display the above message exactly as-is") and a warning that a rich widget
may repeat the data - both of which were printed on the order page, under a
table that already showed the order id, status and total.
"""

import unittest

from mealwise.web_app import human_message

# Verbatim from a real confirm_order response.
LIVE = ("\U0001F389 Instamart order placed successfully! Sit back and enjoy! "
        "Order ID: 246629482120009\n"
        "[IMPORTANT: Display the above message exactly as-is to the user. "
        "Do not rephrase or summarize it.]\n"
        "⚠️ A rich UI widget may be shown to the user with this "
        "data. Avoid restating everything the widget already displays")


class TheLiveMessage(unittest.TestCase):
    def test_the_greeting_survives_verbatim(self):
        # Swiggy's own wording is fine as it is; only the asides go.
        self.assertEqual(
            human_message(LIVE),
            "\U0001F389 Instamart order placed successfully! "
            "Sit back and enjoy!")

    def test_no_instruction_to_the_renderer_reaches_the_page(self):
        out = human_message(LIVE)
        for leak in ("IMPORTANT", "as-is", "widget", "rephrase", "⚠"):
            self.assertNotIn(leak, out, leak)

    def test_the_order_id_is_not_repeated(self):
        # The table below the headline already shows it.
        self.assertNotIn("246629482120009", human_message(LIVE))


class OtherShapes(unittest.TestCase):
    def test_a_message_with_no_asides_is_untouched(self):
        self.assertEqual(human_message("Your order is on its way."),
                         "Your order is on its way.")

    def test_missing_or_empty_prose_is_harmless(self):
        for value in (None, "", "   "):
            self.assertEqual(human_message(value), "", repr(value))

    def test_either_marker_ends_the_human_part(self):
        self.assertEqual(human_message("All good. ⚠ widget note"), "All good.")
        self.assertEqual(human_message("Done. [IMPORTANT: do x]"), "Done.")


if __name__ == "__main__":
    unittest.main()



class TheTrackFooter(unittest.TestCase):
    """`Track it with track_order(orderId=...)` was an MCP tool signature.

    A person cannot call it - there is no CLI, and the page offers no
    tracking of its own - so it was machine-facing text on a screen meant
    for a human.
    """

    def page(self):
        from mealwise import web_ui
        ctx = {"nonce": "N", "address_label": "Home", "flash": [],
               "dry_run": False, "signed_in": True, "display_name": "A",
               "payment": None}
        return web_ui.placed_page(ctx, {
            "headline": "Instamart order placed successfully",
            "detail": "\U0001F389 Instamart order placed successfully! "
                      "Sit back and enjoy!",
            "order_id": "246629482120009", "status": "CONFIRMED",
            "total": 48400})

    def test_the_signature_is_gone(self):
        html = self.page()
        self.assertNotIn("track_order", html)
        self.assertNotIn("Track it with", html)
        self.assertNotIn("orderId=", html)

    def test_the_rest_of_the_page_is_intact(self):
        html = self.page()
        for kept in ("Instamart order placed successfully", "Sit back and enjoy!",
                     "246629482120009", "CONFIRMED", "Start another order"):
            self.assertIn(kept, html, kept)

    def test_no_format_placeholder_is_left_behind(self):
        body = self.page().split("</style>")[-1]
        self.assertNotIn("%s", body)
        self.assertNotIn("%d", body)
