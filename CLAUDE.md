This project is **Python only**. There is no Node.js, TypeScript or Next.js here —
if you find any, it is a leftover and should be removed, not extended.

# Swiggy MCP docs

Swiggy Builders Club docs are the *published* source for Swiggy MCP (Food, Instamart,
Dineout):

- Index:      https://mcp.swiggy.com/builders/llms.txt
- Full text:  https://mcp.swiggy.com/builders/llms-full.txt
- Per-page:   append `.md` to any https://mcp.swiggy.com/builders/docs/... URL

Tool schemas live under `/docs/reference/{food,instamart,dineout}`. Error codes are at
`/docs/reference/errors`. Auth is at `/docs/start/authenticate`.

## The docs are wrong in specific, load-bearing ways

**`tools/list` on the live server is authoritative — not the docs, and not this file.**
Run `python -m mealwise.cli.dump_schemas` with a token to get the real JSON Schema for
every tool. The README's "Live server vs. the docs" section records what was found by
doing exactly that; read it before trusting any published parameter name.

The short version, because these break code silently:

- **The success envelope has no `success` key.** Success returns the BARE payload;
  only failure returns `{success: false, error: {...}}`. Keying failure off a *falsy*
  `success` rejects every good response.
- **The text content block is human-readable prose, not JSON.** Use
  `structured_content`.
- **Response field names in the docs are frequently wrong**: product titles are
  `displayName` (not `name`), sizes are `quantityDescription`, `price` is an OBJECT,
  stock is `isInStockAndAvailable`, payment labels are `displayName` (not `label`).
- **`get_addresses` paginates (10/page) and returns no `lat`/`lng`**, yet `track_order`
  requires them — they come from `get_cart.selectedAddressDetails`.
- **`get_food_orders` requires `addressId`** and returns `{}` — not an error — without it.

Rules:

1. Before recommending a tool name, parameter, error code, rate limit or auth flow,
   check it against the live server or the README's findings table. Fetching the doc
   page alone is not enough — the docs and the server disagree.
2. Never invent tool names or parameters. If neither the docs nor the live schema cover
   it, say so and ask.
3. Prefer `.md` page fetches over `llms-full.txt` when you know the area — it is cheaper
   on context.

# Working in this repo

- **Run the tests** (`python -m unittest discover -s tests -t .`) after touching
  `history.py`, `budget.py`, `targets.py` or `common.py`. They encode captured live API
  strings; a failure usually means the API moved.
- **Money paths need explicit confirmation.** `checkout` takes `user_confirmed=True` as
  a literal and re-reads the live cart total against an approved ceiling. Do not add a
  code path that orders without both.
- **`checkout` is not idempotent.** On an indeterminate failure, reconcile with
  `get_orders` before any retry.
- **Never log or commit a Swiggy token.** They are live credentials that can spend real
  money, and are stored AES-256-GCM encrypted (`mealwise/crypto.py`).
