# Mealwise — Instamart ordering over Swiggy MCP

An interactive command-line grocery ordering tool built on the Swiggy
Instamart MCP server. Say what you want in a sentence, pick your items,
see exactly what you will be charged, and place the order.

```
$ python3 order_instamart.py

Your saved delivery addresses
   1. Home                         d0n03p4...
   2. Work                         cpgba5l...
   n. Add a new address

Your order: 1 litre amul milk, 500 gms paneer and a dozen eggs
```

## Requirements

Python **3.9 or newer**. Nothing to install — the standard library only.
Verified on macOS with the system Python 3.9.6.

## Getting started

```bash
python3 order_instamart.py
```

On first run a browser opens for Swiggy sign-in with **your own phone number
and OTP**. Nothing else is needed: Swiggy issues no API keys, and the app
registers itself.

| Command | What it does |
|---|---|
| `python3 order_instamart.py` | Order groceries |
| `python3 order_instamart.py --dry-run` | Walk the whole flow without writing the cart or ordering |
| `python3 order_instamart.py --budget 500` | Cap the order at your own limit |
| `python3 order_instamart.py --whoami` | Show which Swiggy account is signed in |
| `python3 order_instamart.py --login` | Sign in as a different number |
| `python3 order_instamart.py --logout` | Sign out |
| `python3 probe_instamart.py` | Read-only diagnostics: dump every tool's raw response |

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
  much to remove.
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
| `order_instamart.py` | The interactive CLI |
| `instamart.py` | Cart planning, the ₹1000 limit, fee helpers |
| `parse_order.py` | Order sentence → items, sizes, counts |
| `money.py` | Rupee parsing, in integer paise |
| `swiggy_mcp.py` | JSON-RPC transport for Swiggy MCP |
| `swiggy_auth.py` | OAuth 2.1 + PKCE sign-in and session storage |
| `probe_instamart.py` | Read-only diagnostics |
