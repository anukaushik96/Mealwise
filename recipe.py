"""Turn a cooking intent into a grocery shopping list, via Gemini.

    "I want to make a mango smoothie"
        -> ["2 pieces mango", "500 ml milk", "200 g curd", "100 g honey"]

Those lines are deliberately in the same shape parse_order already reads, so
this module ends where the existing pipeline begins: nothing downstream knows
a model was involved. The suggestion is then shown on the confirmation screen
before a single search runs, which is what keeps a hallucinated ingredient
from reaching a cart.

Stdlib only, over urllib - the same choice swiggy_mcp.py makes for MCP. The
Google SDK would be the project's first dependency, for one POST.

WHAT LEAVES THIS MACHINE: the intent phrase, and nothing else. Not the
address, not the cart, not the account id, not the order history. Read
_build_request before adding a field.
"""

import json
import os
import re
import urllib.error
import urllib.request

import swiggy_auth

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"

# gemini-3.6-flash with thinking off. Measured 2026-08-25 on this task:
# thinking on burned 400-700 reasoning tokens and ~9s to list four fruits;
# "minimal" produced identical lists with 0 reasoning tokens in ~4.5s.
# gemini-2.5-flash and -flash-lite are retired for new keys; 3.7-flash does
# not accept "minimal".
MODEL = "gemini-3.6-flash"
THINKING = "minimal"

KEY_ENV = "GEMINI_API_KEY"
KEY_FILE = os.path.join(swiggy_auth.TOKEN_DIR, "gemini_key")

MAX_INTENT_CHARS = 300
MAX_ITEMS = 12
TIMEOUT = 40

# The prompt is the whole engineering problem here. A model asked plainly for
# a recipe returns "1 cup milk, 1 tbsp honey, ice cubes" - correct cooking,
# useless shopping: a shop does not sell a cup of milk, and nobody needs to
# buy ice. Every rule below was added in response to something a real reply
# got wrong, so re-test before trimming any of them.
PROMPT = """You turn a cooking or shopping intent into a grocery shopping list for an Indian quick-commerce app (Swiggy Instamart).

Rules, all mandatory:
- Output only things a shop sells, in the pack sizes a shop sells them in.
- Use shop units only: g, kg, ml, litre, or a count of pieces. NEVER recipe units like cup, tablespoon, teaspoon, pinch or handful.
- One line per item, formatted exactly: "<number> <unit> <product>"  (e.g. "500 ml milk", "250 g curd", "4 pieces banana").
- Plain generic product names. No brands.
- Skip anything a kitchen already has: water, ice, salt, sugar, common spices, oil.
- Skip equipment and cooking steps.
- 3 to 7 items.

Intent: %s"""

# Phrases that mean "work out what I need", as opposed to a list of products.
_INTENT = re.compile(
    r"\b(make|making|cook|cooking|prepare|preparing|recipe|ingredients?\s+for"
    r"|how do i|what do i need|feeling like|craving)\b", re.I)

# A quantity written the way a recipe writes it, which a shop cannot fill.
_RECIPE_UNIT = re.compile(
    r"^\s*[\d./]+\s*(cups?|tablespoons?|teaspoons?|tbsps?|tsps?|pinch(es)?"
    r"|handfuls?|dashes|dash|sprigs?|cloves?)\s+", re.I)


class RecipeError(Exception):
    """The suggestion could not be produced. Never fatal - fall back to the
    user's own words."""


def api_key():
    """The key, from the environment or the user's own home directory.

    Never from the project folder: a key is a live billable credential and
    this repo gets cloned, zipped and shared - the same reasoning
    swiggy_auth.py applies to Swiggy tokens.
    """
    key = (os.environ.get(KEY_ENV) or "").strip()
    if key:
        return key
    try:
        with open(KEY_FILE) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def available():
    return bool(api_key())


def is_recipe_request(text):
    """Does this ask what to buy, rather than say what to buy?

    Two conditions, because either alone is wrong. Intent words alone would
    send "I want 2 kg atta" to a model that has nothing to add. Failing to
    parse alone would send every typo. So: it reads like a request AND
    nothing in it already looks like a sized product.
    """
    from parse_order import parse_order      # local: avoids an import cycle
    if not _INTENT.search(text or ""):
        return False
    return not any(request.size for request in parse_order(text))


def _build_request(intent):
    """The entire payload. The intent phrase is the only thing user-derived."""
    return {
        "model": MODEL,
        "input": PROMPT % intent[:MAX_INTENT_CHARS],
        "generation_config": {"thinking_level": THINKING},
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": {
                "type": "object",
                "properties": {"items": {"type": "array", "items": {"type": "string"}}},
                "required": ["items"],
            },
        },
    }


def _extract_text(payload):
    """Pull the model's text out of an interactions response.

    The docs describe the result as `interaction.output_text`, but that is a
    convenience property the Google SDKs synthesise - verified 2026-08-25:
    the REST body has no output_text field at all, and the text sits in
    steps[].content[].text. Take the last one; earlier steps can be reasoning.
    """
    text = None
    for step in payload.get("steps") or []:
        for block in step.get("content") or []:
            if isinstance(block, dict) and block.get("text"):
                text = block["text"]
    return text or payload.get("output_text")


def _clean(line):
    """Make one suggested line safe to search for, or drop it.

    A recipe unit that slips past the prompt would otherwise be searched
    literally ("cup milk"), so the quantity is stripped and the bare product
    kept - it then has no size, which sends it to the chooser rather than
    silently into the cart.
    """
    line = " ".join(str(line or "").split())
    if not line:
        return None
    line = _RECIPE_UNIT.sub("", line)
    return line[:80] or None


def expand(intent, timeout=TIMEOUT):
    """Ask for a shopping list. Returns [] rather than raising on a bad reply."""
    key = api_key()
    if not key:
        raise RecipeError("no Gemini API key configured")

    body = json.dumps(_build_request(intent)).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT, data=body, method="POST",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            detail = detail[:200]
        raise RecipeError("Gemini refused the request: %s" % detail)
    except urllib.error.URLError as exc:
        raise RecipeError("could not reach Gemini: %s" % exc.reason)
    except ValueError:
        raise RecipeError("Gemini returned something that was not JSON")

    text = _extract_text(payload)
    if not text:
        raise RecipeError("Gemini returned no suggestion")
    try:
        items = json.loads(text).get("items")
    except (ValueError, AttributeError):
        raise RecipeError("Gemini's suggestion was not the shape we asked for")
    if not isinstance(items, list):
        raise RecipeError("Gemini's suggestion was not a list")

    cleaned = []
    for line in items[:MAX_ITEMS]:
        line = _clean(line)
        if line and line.lower() not in [c.lower() for c in cleaned]:
            cleaned.append(line)
    return cleaned
