# Mealwise

AI food & spending manager on Swiggy MCP. Answers two questions together — *is this good
for me* and *can I afford it* — then orders the answer on Instamart.

**Python throughout**: FastAPI + Jinja2 on Vercel, Postgres (Neon), Claude `claude-opus-5`,
plus a terminal client for the same Swiggy API. Multi-user.

## Layout

| Path | |
| --- | --- |
| `mealwise/swiggy/` | Swiggy MCP client — Food + Instamart, verified against the live server |
| `mealwise/cli/` | Terminal ordering: `login`, `order`, `basket`, `dump_schemas` |
| `mealwise/web/` | The FastAPI app (routes, Jinja2 templates, CSS) |
| `mealwise/ai/` | Nutrition estimation and the recommendation engine |
| `mealwise/{history,budget,targets,common}.py` | Pure logic — all unit-tested |
| `mealwise/{db,store,crypto,session,auth,config}.py` | Persistence, tokens, OAuth |
| `api/index.py` | Vercel entrypoint (ASGI) |
| `schema.sql` | 7 tables |
| `tests/` | 49 tests, no network needed |

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # fill in DATABASE_URL and ANTHROPIC_API_KEY
python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"   # x2
psql "$DATABASE_URL" -f schema.sql
.venv/bin/python -m uvicorn api.index:app --reload      # http://localhost:8000
```

Tests: `python -m unittest discover -s tests -t .`

### Vercel

Import the repo — `vercel.json` routes everything to `api/index.py`. Add the five env
vars, and set `APP_URL` to the production origin so Swiggy redirects back correctly.

## The terminal client

The same verified client, without the web app:

```bash
python -m mealwise.cli.login                          # phone + OTP, prints a token
export SWIGGY_TOKEN=...
python -m mealwise.cli.order  dahi                    # search only
python -m mealwise.cli.order  dahi --pick=1 --qty=2   # one search, one or more lines
python -m mealwise.cli.basket 5V35CLXB8O:1 Q3L8WG2OTP:1        # a real grocery list
python -m mealwise.cli.basket 5V35CLXB8O:1 --place --max-total=258
python -m mealwise.cli.dump_schemas                   # authoritative tool schemas
```

`--max-total` is not decoration: Instamart pricing is live, and a surge fee appeared, was
renamed and lapsed inside a single session. It pins the approval to a number and aborts
above it rather than paying more than was agreed.

## Live server vs. the docs

Everything below was verified by calling `mcp.swiggy.com` with a real token. Where the
docs and the server disagree, **this code follows the server**. Re-verify with
`python -m mealwise.cli.dump_schemas`.

**The envelope is asymmetric, and the docs describe only half of it.** This was the
single blocking bug — every read failed until it was fixed.

| | `structured_content` |
| --- | --- |
| success | the **bare payload** — no `success`, no `data` wrapper |
| failure | the documented wrapper plus a report id: `{success: false, error: {message, reportId, reportHint}}` |

So failure is keyed off `success is False`, never off a falsy `success`. The same trap sat
in the payment poll, where a successful status read would have been raised as an error.

**The text content block is human-readable prose, not JSON** — "Found 23 saved addresses
(page 1 of 3…)". Never parse it.

**Response field names.** The docs name almost none of these, and several published names
are wrong:

| Docs / assumed | Live server |
| --- | --- |
| product `name` | **`displayName`** |
| variation `quantity` / `weight` | **`quantityDescription`** ("500 ml x 4") |
| variation `price` (number) | **object** `{mrp, offerPrice, unitLevelPrice}` |
| variation `inStock` | **`isInStockAndAvailable`** |
| — | **`maxQuantity`** — a server-enforced per-order cap |
| payment method `label` | **`displayName`** (plus `groupName`, `enabled`) |
| `cod: {paymentMethod, label}` | **`{available, id, displayName}`** |
| cart total (unnamed) | **`cartTotalAmount`** (a *string*) and **`billBreakdown.toPay`** |

**Order history is formatted for display.** `orderTotal` is `"₹310"`, `orderedItems` is one
joined string, `orderedTime` is `"August 22, 10:13 PM"` with **no year**. Consequences:
orders store a DATE plus the raw string, and there is **no per-item price** — order-level
totals are the only trustworthy money figure, so per-dish spend attribution is impossible.

**`get_addresses` paginates and has no coordinates.** `pageSize` caps at 10, so an account
with 23 addresses silently returns the first 10 unless you walk `pagination.hasMore`. Each
address carries only `{id, addressLine, phoneNumber, addressCategory, addressTag}` — no
`lat`/`lng` and no `label`. Since `track_order` *requires* lat/lng, they must come from
`get_cart.selectedAddressDetails`.

**`get_food_orders` requires `addressId`** and returns `{}` — not an error — without it.
History is account-wide regardless of which address you pass.

**Instamart advertises 14 tools, not the 16 or 19 the docs imply.** `list_coupons` and
`apply_coupon` are unadvertised but callable, returning "Coupon tools are not enabled for
your account yet" — surfaced as a value, not an exception, so an un-whitelisted account can
still order. `create_address`, `delete_address` and `get_order_details` are neither
advertised nor verified. Food advertises 18, and every tool name the client uses exists.

**Fee thresholds, measured across six basket sizes.** A ₹20 small-cart fee applies below
~₹99 of goods, and delivery is free above ~₹199. A *larger* basket genuinely cost *less*
(₹113 of goods billed ₹160; ₹91 billed ₹162). The recommendation prompt knows about these.

**Other hard limits.** Orders of ₹1000+ are refused while MCP is in beta. `checkout` is not
idempotent — reconcile with `get_orders` rather than retrying. UPI cannot be completed
without a human; Cash-on-delivery can.

## Design notes

**Connecting Swiggy is signing in.** The access token is a JWT whose `sub` is stable per
account, so it doubles as identity — no separate password system, and multi-user from day
one. Tokens are AES-256-GCM encrypted at rest, because a read-only database leak would
otherwise be enough to order on every connected account.

**Money paths are gated twice.** `checkout` takes `user_confirmed=True` as a literal, and
the order route re-reads the live cart total against the ceiling the human approved.

**UPI payment is not polled.** `check_payment_status` runs up to ~18 minutes and a Vercel
function is capped far below that, so the app hands over the payment link and stops. Cash
completes in-request. A durable job or client-side polling would be the way to close this.

**Nutrition is estimated, and says so.** It is inferred by an LLM from item-name strings,
with a per-item confidence that the UI surfaces. Not medical advice.

**The agent cannot invent a product.** It picks only from a candidate list fetched live from
Instamart, and unknown `spinId`s are filtered before the cart call.

## Status

`python -m unittest discover -s tests -t .` — **49 tests pass**, covering the history
parsers against captured live strings, the budget engine, target derivation, and the cart
probes. No network needed.

Verified against a running server: the signed-out page, the PKCE-S256 authorize redirect,
the `/onboarding` guard, static assets, and `/healthz`.

⚠️ **Not yet exercised end to end.** No database has been attached, so nothing past the
login boundary has executed — import, nutrition estimation, recommendation, cart and order
all compile and are typed but have never run. No Claude API call has been made at all.

Two things to test on the first real deploy, either of which could still require rework:

1. Whether Swiggy's `/authorize` honours a hosted `redirect_uri`. DCR accepts one, but it
   echoes whatever you send and always returns `client_id: "swiggy-mcp"`, so enforcement
   may only happen at `/authorize`.
2. Whether a `refresh_token` is actually issued. It is advertised in
   `grant_types_supported` but has never been seen in a response. Without it, users
   reconnect every ~5 days.

**Known gaps.** The delivery address is hardcoded to the first one returned — there is no
picker. Recommendation search terms are a fixed list keyed off which target is short. No
conversational interface, no weekly insights, no restaurant (Food) ordering, and the
behavioural spending insights are limited to a per-source split.
