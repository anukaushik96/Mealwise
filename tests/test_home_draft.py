"""What you typed last should not be sitting in the box when you come back.

The draft exists for one reason: a sentence the app could not use comes back
in the textarea so it can be edited instead of retyped. That is a single
render. Beyond it, showing someone their old words looks like the app is
stuck - which is how this was noticed.
"""

import unittest

from mealwise import web_app, web_ui
from mealwise.instamart import CartPlanner


def handler_with_draft(text):
    app = web_app.App.__new__(web_app.App)
    app.token = "t"
    app.user_id = "1"
    app.display_name = "Anu"
    app.addresses = [{"id": "a", "label": "Home"}]
    app.address_id = "a"
    app.address_label = "Home"
    app.nonce = "TESTNONCE"
    app.dry_run = False
    app.flash = []
    app.planner = CartPlanner("a")
    app.payment = None
    app.draft = text
    app.suggested_from = None
    app.order = None
    app.ensure_address = lambda: True
    handler = web_app.Handler.__new__(web_app.Handler)
    handler.app = app
    return handler, app


class TheDraft(unittest.TestCase):
    def test_a_rejected_sentence_comes_back_once(self):
        handler, _app = handler_with_draft("I want to make avocado toast")
        self.assertIn("avocado toast", handler.view_home({}, {}))

    def test_and_is_gone_on_the_next_load(self):
        handler, app = handler_with_draft("I want to make avocado toast")
        handler.view_home({}, {})
        self.assertEqual(app.draft, "")
        self.assertNotIn("avocado toast", handler.view_home({}, {}))

    def test_the_box_is_empty_not_missing(self):
        handler, _app = handler_with_draft("")
        html = handler.view_home({}, {})
        self.assertIn('name="text"', html)
        self.assertIn("></textarea>", html)


class BrowserRestore(unittest.TestCase):
    def test_autocomplete_is_off(self):
        # Safari and Chrome refill form fields on a soft reload regardless of
        # what the server sent, so clearing the draft alone is not enough.
        ctx = {"nonce": "N", "address_label": "Home", "flash": [],
               "dry_run": False, "signed_in": True, "display_name": "A",
               "payment": None, "draft": "", "lines": [], "status": None,
               "recipes_on": True, "suggested_from": None}
        self.assertIn('autocomplete="off"', web_ui.home_page(ctx))


if __name__ == "__main__":
    unittest.main()
