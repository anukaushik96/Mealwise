# How Mealwise is put together

One engine, two front ends, and a hard rule that the server's number wins.
Read this before changing anything; read [instamart-notes.md](instamart-notes.md)
before changing anything that touches the Instamart API.

## Layers

Nothing in a lower layer imports from a higher one.

```
  interface   web_app.py + web_ui.py          cli.py
              (HTTP routes, HTML)             (terminal prompts)
                        \                      /
  engine       money.py   parse_order.py   instamart.py   recipe.py
               (paise)    (sentence→items) (cart+limit)   (dish→list)
                        \                      /
  transport            swiggy_mcp.py     swiggy_auth.py
                       (JSON-RPC)        (OAuth 2.1 + PKCE)
                                 |
                          Swiggy Instamart MCP
```

- **transport** — `swiggy_mcp.py` speaks JSON-RPC and separates the prose
  Swiggy returns from the JSON appended to it. `swiggy_auth.py` handles
  sign-in (phone + OTP, no API key exists) and caches the 5-day session
  under `~/.swiggy_mcp`, never in the repo.
- **engine** — no I/O of its own beyond the transport layer, which is what
  makes it testable offline. `money.py` keeps every amount in integer paise.
  `parse_order.py` turns "500 gms paneer, 2 kg atta" into structured
  requests. `instamart.py` owns the cart planner and the ₹1000 gate.
  `recipe.py` turns a dish into a shopping list via Gemini, and is optional -
  the app runs without a key.
- **interface** — `web_app.py` is a stdlib `HTTPServer` bound to 127.0.0.1
  with all state in memory; `web_ui.py` renders the HTML (no JavaScript
  anywhere). `cli.py` is the same flow in the terminal.

## What one order looks like

```
  type a list  ──►  parse_order  ──►  review page: every quantity echoed back
                                              │
                                              ▼
                              search_products, per line
                                              │
                    exact size match? ──yes──►  added straight to the basket
                            │
                            no
                            ▼
                    choose page: pick a variant by hand
                                              │
                                              ▼
                    update_cart  ──►  get_cart  ──►  the bill you are shown
                                              │
                                              ▼
                    get_payment_options  ──►  checkout  ──►  track_order
```

## The rules that matter

**The server's figure wins.** A projection is for deciding what to add
next, never for showing someone what they will pay. `push_cart()` always
writes *and* reads back, because the account is live state a phone app can
write to at the same moment.

**Money is integer paise.** Floats drift over repeated adds. Parse at the
boundary, format only for display.

**Both ids travel together.** `spinId` is the catalogue id; `skuId` is that
variant's stock at the one store serving an address. A cart write needs
both, and a `skuId` is worthless at a different address - which is why
changing address clears the basket.

**`update_cart` replaces the whole cart.** There is no add or remove. Send
the complete list every time, and render the cart Swiggy hands back rather
than the one you sent: they are different lists, and the difference is
something the user is being charged for.

**₹1000 is a wall, not a preference.** Swiggy refuses checkout at ₹1000 or
more, so the planner checks every add against the limit before applying it.
Fees cannot be estimated (measured load has ranged from 4% to 123% of the
item total), so the planner starts at zero fees, says "items only", and
switches to the measured figure the moment a real cart exists.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

Offline by design: no MCP call, no network, no live account. The Swiggy
client is stubbed where a test needs one, so the suite is safe to run
anywhere. It covers the money parsing, the order parser, the cart planner
and gate, the web cart actions, and that every page renders.

What it does **not** cover, and cannot: whether Swiggy still behaves the way
`instamart-notes.md` recorded. Use `python3 -m mealwise.probe` for that - it
is read-only and dumps every tool's raw response.
