# Mealwise — Instamart ordering over Swiggy MCP

A grocery ordering tool built on the Swiggy Instamart MCP server. Type what
you want as a list, pick your items, see exactly what you will be charged,
and place the order.

It comes as **a web UI** and **a command line**, over one shared engine — the
same cart planner, the same ₹1000 gate, the same order parser.

```bash
python3 web_app.py            # a browser UI on http://127.0.0.1:8765
python3 order_instamart.py    # the same flow in the terminal
```

```
Your order: 1 litre amul milk, 500 gms paneer and a dozen eggs
```

## Requirements

Python **3.9 or newer**. Nothing to install — the standard library only.
Verified on macOS with the system Python 3.9.6.

## Getting started

```bash
python3 web_app.py
```

Your browser opens on the app. Sign in with **your own phone number and OTP**
— nothing else is needed: Swiggy issues no API keys, and the app registers
itself. The session lasts 5 days, so you are not asked again until it expires.

| Command | What it does |
|---|---|
| `python3 web_app.py` | Order groceries in a browser |
| `python3 order_instamart.py` | Order groceries in the terminal |
| `python3 order_instamart.py --dry-run` | Walk the whole flow without writing the cart or ordering |
| `python3 order_instamart.py --budget 500` | Cap the order at your own limit |
| `python3 order_instamart.py --whoami` | Show which Swiggy account is signed in |
| `python3 order_instamart.py --login` | Sign in as a different number |
| `python3 order_instamart.py --logout` | Sign out |
| `python3 probe_instamart.py` | Read-only diagnostics: dump every tool's raw response |

`--dry-run` and `--budget` work the same way on `web_app.py`.

## The web UI

```bash
python3 web_app.py                 # opens your browser at 127.0.0.1:8765
python3 web_app.py --dry-run       # walk everything; never writes, never orders
python3 web_app.py --budget 500    # your own ceiling, under the ₹999 max
python3 web_app.py --port 9000      # if 8765 is taken
```

Sign in once with your phone number and OTP, then:

1. **Your address sits in the top-left.** Click it for the full list of saved
   addresses, or to add one.
2. **Type your list** — one item per line, or comma separated:
   `1 litre amul milk, 500 gms paneer, 2 maggi`. Or **describe a dish**
   (`I want to make a mango smoothie`) and the ingredients are worked out
   for you — see below.
3. **Confirm what was understood.** Every name, size and quantity is shown
   back before anything is searched.
4. **Items that match the size you asked for are added for you.** Anything
   else — a size Instamart doesn't stock, or a request with no size — asks you
   to choose from the variants in stock.
5. **The cart shows Swiggy's own bill**, priced there and then, with the fee
   lines named as Swiggy names them. Adjust quantities or remove lines.
   Product photos and the per-unit price (`47.5/100 g`) come along, which is
   what actually makes two pack sizes comparable.
6. **Pick a payment method** from a modal listing only what Swiggy offers for
   that cart.
7. **Checkout asks once, in plain words**, showing the exact amount, and
   re-reads the total from Swiggy immediately beforehand.

### Describing a dish instead of listing items

Say `I want to make a mango smoothie` and you get back
`2 pieces mango, 500 ml milk, 400 g curd, 250 g honey` — shop pack sizes, not
recipe amounts — on the same confirmation screen as anything you type
yourself. Nothing is searched until you say yes.

This needs a Google Gemini API key ([free from AI
Studio](https://aistudio.google.com/apikey)). Put it in the environment, or in
a file the app reads:

```bash
export GEMINI_API_KEY=your-key           # either this
printf '%s' your-key > ~/.swiggy_mcp/gemini_key && chmod 600 ~/.swiggy_mcp/gemini_key
```

**Never put the key in this folder.** It lives beside your Swiggy session
under your home directory, for the same reason — a repo gets cloned, zipped
and shared, and `.gitignore` does nothing about that.

With no key, the box behaves exactly as it always has: a list box. Nothing
else changes, and nothing is installed — the Gemini call is one `urllib` POST,
so the standard-library-only promise still holds.

Three things worth knowing:

- **The model is only consulted when you phrase it as a request.**
  `1 litre milk` never leaves your machine; `make me a smoothie` does. The
  trigger is the verb — *make*, *cook*, *recipe for*, *what do I need for* —
  so a bare dish name like `bread omelette` is searched as a product instead.
  Name the dish in a sentence to get ingredients. The test suite asserts this
  both ways.
- **Only the dish phrase is sent.** Not your address, cart, order history or
  account number — asserted in the tests, and the whole request payload is
  built in one function in `recipe.py` so you can check it yourself.
- **It takes a few seconds** (typically 4–13s), because a real model is
  thinking about your dinner. Plain lists stay instant.

Note Google's pricing page states free-tier content is used to improve their
products; the paid tier is not. Only dish names go over, but that is the
trade you are making.

### What it deliberately does not do

- **It never treats a suggestion as fact.** An AI-suggested list lands on
  the same confirmation screen as your own typing, labelled as a suggestion,
  and is searched only after you approve it. If the suggestion fails, you get
  your own words back rather than a search for "to make fruit custard".
- **It never invents a name for you.** Swiggy's API never sends one — the
  token carries an account number and nothing else — so the top-right shows
  your account number until you type a label for yourself, and says as much.
- **It never guesses where you are.** Reading a device's location needs
  JavaScript, and the Instamart MCP has no nearest-address tool, so the
  top-left shows the saved address actually in use — the one you last chose,
  or your Swiggy default. Nothing is inferred about your whereabouts.
- **No JavaScript is served at all.** The payment modal is CSS `:target`; the
  address drawer is `<details>`. This keeps the project Python-only, and means
  nothing can happen in the browser that the server did not render.
- **It serves only 127.0.0.1**, and every form carries a per-run token. The
  app holds a live payment-capable session and has no password of its own, so
  it must not be reachable from your network — and a page you have open in
  another tab must not be able to spend your money.
- **One request at a time.** There is exactly one server-side cart per Swiggy
  account, and `update_cart` replaces it, so overlapping requests could each
  write a cart the other thought it owned.

## Sharing this with other people

The app is multi-user by design. Each person runs it, signs in with their own
Swiggy number, and sees only their own account. There is nothing to configure
and no shared state.

**Nothing personal is ever written into this folder.** Everything account-
specific lives under your own home directory:

```
~/.swiggy_mcp/                 (0700, only you can read it)
  current                      which account is active
  sessions/<user_id>.json      (0600) one session per Swiggy account
  probe/                       diagnostic dumps, if you run the prober
```

So you can clone, copy, zip or share this repository freely — your Swiggy
session does not travel with it, and neither do your addresses or orders.

Isolation is enforced by the operating system: each OS user has their own
home directory, and the permissions above stop other users on the same
machine reading it. **This assumes each person has their own OS login.** If
several people share one login, file permissions cannot separate them — sign
out with `--logout` when you finish.

### Switching between accounts

Sessions are kept per Swiggy account, so two people on the same machine do not
evict each other:

```bash
python3 order_instamart.py --accounts        # who is signed in here
python3 order_instamart.py --login           # add another account
python3 order_instamart.py --use <user_id>   # switch back, no OTP needed
python3 order_instamart.py --logout          # sign out of the active account
python3 order_instamart.py --logout --all    # sign out of every account
```

Sessions last **5 days**. Swiggy issues no refresh tokens, so after that you
sign in again with phone and OTP — until then you are never asked twice.

Note `--logout` removes the session from this machine but does not revoke it
at Swiggy's end; the token stays valid until it expires.

## What it does for you

- **Orders in plain language.** `500 gms paneer, 2 kg atta and a dozen eggs`
  is parsed into items, sizes and quantities, then shown back for confirmation.
  Understands g/kg/ml/litre/pieces/dozen, packs, and spelled-out numbers.
- **Matches the size you asked for.** Variants are ranked by how closely they
  match, with the best pre-selected. Exact matches are flagged.
- **Tells you the real price before you commit.** Instamart fees vary a great
  deal with cart value and time of day — a small cart can carry more in fees
  than in groceries. The app measures the real fee on your first item and
  keeps the running total honest.
- **Refuses what Swiggy would refuse.** Instamart rejects orders at ₹1000 or
  above. You are told while shopping, not at checkout, and told exactly how
  much to remove. If a surge fee later pushes the real total over the line,
  the cart says so and the checkout button goes away.
- **Never orders without you.** Placing an order needs the exact amount shown
  and the word `yes` typed in full.

## A word of caution

`checkout` spends real money and there is **no cancellation tool** in the
Swiggy MCP — cancelling an order means contacting Swiggy customer care. The
app asks for explicit confirmation, and re-reads the total from Swiggy
immediately beforehand, but once placed it is placed.

Use `--dry-run` to explore without any risk.

## For developers

`INSTAMART_NOTES.md` records how the Instamart MCP actually behaves, verified
against a live account — including where the official docs are wrong, which
response fields cannot be trusted, and the assumptions that broke. Read it
before changing anything.

| File | Role |
|---|---|
| `web_app.py` | The web UI: routes, session state, split OAuth flow |
| `recipe.py` | Dish → shopping list, via Gemini over stdlib `urllib` |
| `web_ui.py` | HTML and CSS for the web UI; no JavaScript |
| `order_instamart.py` | The interactive CLI |
| `instamart.py` | Cart planning, the ₹1000 limit, fee helpers |
| `parse_order.py` | Order sentence → items, sizes, counts |
| `money.py` | Rupee parsing, in integer paise |
| `swiggy_mcp.py` | JSON-RPC transport for Swiggy MCP |
| `swiggy_auth.py` | OAuth 2.1 + PKCE sign-in and session storage |
| `probe_instamart.py` | Read-only diagnostics |
