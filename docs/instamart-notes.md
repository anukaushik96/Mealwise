# Swiggy Instamart MCP — Field Notes

Verified against a live account on **2026-08-24** (Bengaluru HSR Layout and
Gurugram). One full lifecycle was exercised end to end: OAuth → address →
search → cart → fee gate → payment → `checkout` → `track_order` → DELIVERED.

Read this **with** the Builders Club docs, not instead of them. The docs are
right about structure and wrong about several specifics, all listed below.
Everything here is backed by an observed response, not inference, unless the
line says otherwise.

---

## 0. Read this first

**Section 1 headings state the CORRECTED conclusion.** The disproved belief is
quoted underneath each one as "Belief that broke". Do not read a heading as a
description of Swiggy's behaviour before checking the line below it.

**If you read nothing else:** never trust a number you did not just read from
the server. `get_cart.billBreakdown.toPay` is the only figure that says what a
customer will be charged. Everything else — fee models, thresholds, order
history bills — has already been observed to lie.

### How stale is this?
Dated **2026-08-24**, one account, two addresses, one day. Nothing here is a
contract. Before relying on it, re-verify in this order — cheapest and most
likely to have moved first:

1. `tools/list` — tool inventory and parameter names (a schema change
   invalidates §5 and §7 immediately)
2. Any fee figure or threshold in §1.1, §1.8, §6 — pricing policy, changes
   without notice
3. Auth details in §3 — token lifetime, whether refresh tokens now work
4. Everything else

If a claim here contradicts what the server does today, **the server wins** and
this file is out of date. Fix it in place.

### Where the code lives

| File | What it owns |
|---|---|
| `mealwise/swiggy_auth.py` | OAuth 2.1 + PKCE, dynamic client registration, token cache |
| `mealwise/swiggy_mcp.py` | JSON-RPC transport; splits prose from the appended JSON |
| `mealwise/money.py` | Rupee parsing to integer paise; handles `"FREE"`, `"₹1,234.50"` |
| `mealwise/instamart.py` | Cart planner, the ₹1000 limit maths, cart/fee helpers |
| `mealwise/parse_order.py` | Natural-language order sentence → items, sizes, counts |
| `mealwise/cli.py` | The interactive ordering CLI |
| `mealwise/web_app.py` | The web UI: routes, session state, the split OAuth flow |
| `mealwise/web_ui.py` | HTML for the web UI - no JavaScript, anywhere |
| `mealwise/probe.py` | Read-only prober; dumps every tool's raw response |

The §2 trust hierarchy is enforced in `mealwise/instamart.py` — `cart_item_total()`
reads the server's `Item Total` line, and `fee_overhead()` derives fees by
subtraction from `toPay`. If you rewrite those, re-read §1.4 and §1.6 first.

---

## 1. Assumptions that broke

The point of this file. Each of these was believed, then killed by real data.

### 1.1 Fees are not flat — they ranged 4%–123% of item total
> **Belief that broke:** *Fees are flat, roughly ₹23*
**Wrong, and expensively so.** Measured fee load on the same account, same day:

| Item total | Fees | Ratio | Fee lines present |
|---|---|---|---|
| ₹59 | ₹71 | 120% | Handling, Small Cart, Delivery, GST |
| ₹70 | ₹71 | 101% | Handling, Small Cart, Delivery, GST |
| ₹86 | ₹106 | 123% | Handling, Small Cart, Delivery, **Surge**, GST |
| ₹90 | ₹71 | 79% | Handling, Small Cart, Delivery, GST |
| ₹103 | ₹47 | 46% | Handling, Delivery, GST |
| ₹187 | ₹47 | 25% | Handling, Delivery, GST |
| ₹257 | ₹12 | 5% | Handling only |
| ₹288 | ₹12 | 4% | Handling only |

The *set of fee lines* changes with cart value and time of day. Never model
fees. Measure them.

### 1.2 Surge and late-night fees exist, and come and go within the hour
> **Belief that broke:** *There are no surge fees — I saw no evidence for them*
**Wrong.** `High Demand Surge Fee ₹30` appeared on one cart and was gone from
an identical cart ~20 minutes later. `Late Night Fee ₹9` likewise appears only
at night. Fees are time-varying, so a cached fee profile must expire.

### 1.3 Swiggy One PLUS does not buy unconditional free delivery
> **Belief that broke:** *Delivery is free because the account is Swiggy One PLUS*
**Wrong.** `Delivery Partner Fee` was ₹30 on small carts and FREE on larger
ones, on the same One PLUS account at the same address. Membership does not
buy unconditional free delivery — cart value does.

### 1.4 `get_orders.billDetails` holds placeholder numbers — never compute from it
> **Belief that broke:** *The −₹15 gap in billDetails is an unreported discount*
**Wrong. Two of those numbers are fake, so the arithmetic never meant
anything.**

`get_orders` returns a `billDetails` object that *looks* like a bill:

```json
"billDetails": { "itemTotal": 257, "deliveryFee": 16,
                 "packagingFee": 11, "grandTotal": 269 }
```

Add the parts up and you get 284, not 269. I originally concluded Swiggy was
applying a ₹15 discount it wasn't disclosing.

The real explanation is simpler. **`deliveryFee: 16` and `packagingFee: 11`
appear on every order, identical, regardless of what was charged.** For the
order above we watched the actual cart at checkout time:

```
Item Total              ₹257.00
Handling Fee             ₹12.00
Delivery Partner Fee        FREE     <- billDetails claims 16
                                     <- there was NO packaging fee at all
TO PAY                     ₹269
```

Delivery was free and there was no packaging fee, yet `billDetails` reports 16
and 11. They are placeholders. Three orders on the account all carried the same
16/11 while their true fees differed completely.

So the sum can't reconcile, and the "delta" is just `grandTotal` minus two
made-up numbers. Nothing is hidden; the figures simply aren't real.

**Rule: from `get_orders`, only `grandTotal` / `totalAmount` is trustworthy.**
Never show a user a fee breakdown built from `billDetails`, and never compute
anything from it.

### 1.5 `get_orders.paymentMethod` is overwritten at settlement — never trust it
> **Belief that broke:** *get_orders.paymentMethod tells you how the order was paid*
**Wrong.** It is written by the settlement layer *after* the fact, and it
overwrites the customer's choice. Same order, 7 minutes apart:

```
checkout response      paymentMethod = "Cash"
get_orders, CONFIRMED  paymentMethod = "Cash"
get_orders, DELIVERED  paymentMethod = "UPIIntent"   <- overwritten
```

The field also emits **gateway** names (`Juspay`) that no user can select.
Use the `checkout` response if you need to know how an order was placed.

### 1.6 Never compute the item subtotal by summing `items[]`
> **Belief that broke:** *Item subtotal can be computed by summing items[]*
**Wrong, and it produced negative fees (−₹199).** The cart can contain entries
the server excludes from the bill (out of stock, or added by another session).
Read the `Item Total` line out of `billBreakdown` instead; fall back to summing
only `isInStockAndAvailable` items.

### 1.7 `update_cart` does not guarantee the cart holds only what you sent
> **Belief that broke:** *update_cart replaces the cart, so it holds exactly what I sent*
**Not guaranteed.** An item nobody in this session added ("nectr Pomegranate
₹211") appeared in the cart mid-test. Root cause: **the account was being used
in the Swiggy app concurrently** — a third order was placed from the app during
the session. The cart is shared live state and your writes can be raced.
Always re-read `get_cart` after writing, and verify the contents are what you
expect before checking out.

**Seen again 2026-08-25, and it survived the replace.** A "NOICE Bombay Laadi
Pav ₹69" was in `get_cart` *after* an `update_cart` that did not include it —
so "replaces the entire cart" did not evict it. `clear_cart` followed by
`update_cart` is the only other lever available.

**The consequence for any UI: render the cart you were given, not the cart you
sent.** They are different lists. A screen built from the client's own basket
shows five rows while the cart holds six, and offers no way to remove the
sixth — the user sees rows that do not reconcile with the total and has no
idea why.

### 1.7.1 `items[]` can hold something the bill excludes, and the stock flag does not explain it
> **Belief that broke:** *an item in `items[]` is an item you are paying for*
**Wrong — and I asserted it in this file for an hour before checking.** The
same Laadi Pav, measured directly:

```
items[]:  NOICE Bombay Laadi Pav   x1  ₹69   isInStockAndAvailable: true   storeId 1403139
          The Health Factory Bread x1  ₹55   isInStockAndAvailable: true   storeId 1403139
sum of items[]                          ₹124
billBreakdown "Item Total"              ₹55        <- the pav is not in it
cartTotalAmount                         ₹126       <- 55 + 12 + 20 + 30 + 9
```

So `items[]` listed it, the stock flag said it was available, both items were
at the **same** `storeId` (so this is not the multi-store split of §8), and the
bill simply did not include it. §1.6 says `items[]` holds entries the server
does not bill and blames out-of-stock or another session; neither applies here.

**Do not label an item billed or unbilled from any per-item field — subtract.**
`sum(items[]) − Item Total` is the only figure that says what is being left
out. Claiming a charge that is not there is as wrong as hiding one: this UI
told a user they were paying ₹69 they were not.

### 1.8 Never hardcode the free-delivery threshold
> **Belief that broke:** *The free-delivery threshold is ₹259*
**Refined by measurement, and then: never hardcode it.** Delivery was FREE at
an item total of **₹257** and charged at ₹187, so the boundary sits somewhere
in `(187, 257]`. The Small Cart Fee boundary is in `(90, 103]`.

> **Do not put either number in code.** These are pricing *policy*, owned by
> Swiggy, changeable without notice or API version bump, and they already vary
> by address and time of day. Every figure in this document is an observation
> from one account on one day, not a constant.
>
> If your product needs a threshold, **discover it at runtime**: binary-search
> cart value with `update_cart` + `get_cart` (both free and reversible), read
> `toPay`, and cache the result against `(addressId, hour)` with a short TTL.
> If you cannot discover it, degrade gracefully — show the user the real
> `toPay` and let them decide, rather than asserting a threshold that may have
> moved.

### 1.9 Gate the ₹1000 cap on `toPay`, not on the item total
> **Belief that broke:** *The ₹1000 checkout cap applies to the item total*
**Probably not — it is fee-inclusive.** Every field Swiggy calls a cart total
(`cartTotalAmount`, `checkout.cartTotal`, `paymentAmount`) is the fee-inclusive
payable. This is inference from naming, never confirmed by docs, and testing it
would require deliberately building a >₹1000 cart. Gate on `toPay` and the
question stops mattering.

### 1.10 Echo every parsed quantity back before it reaches the cart
> **Belief that broke:** *my natural-language parser handled quantities correctly*
A natural-language order of `"1 L amul milk x 2 units"` parsed to **quantity
1**, because the size slot had already been filled and `2 units` was swallowed.
No error anywhere. If you build an NL front end, assert that every parsed
quantity is echoed back to the user before it reaches the cart.

**Second instance of the same bug, found and fixed 2026-08-25.** `"2 maggi"`
parsed to a product literally named *"2 maggi"* at quantity **1**: the digit
leaked into the `search_products` query *and* the count vanished, silently, in
one step. Cause: the quantity regex captured the word after the number
unconditionally, so a following word that was not a unit made the whole match
look like part of the name. `_QTY` now captures the gap too, and treats
`"2 maggi"` (space, therefore a count) differently from `"7up"` (no space,
therefore a brand) - see `parse_order._MAX_BARE_COUNT`.

The lesson stands and is now enforced structurally: the web UI cannot search
anything until the user has confirmed a screen listing every parsed name, size
and quantity. Do not remove that screen to save a click - it is the assertion
this section asks for.

---

## 2. Trust hierarchy

Decide financial questions in this order. Never invert it.

| Question | Authoritative source |
|---|---|
| What will I be charged? | `get_cart` → `billBreakdown.toPay` |
| What was I charged? | `checkout` response → `cartTotal` |
| How did I pay? | `checkout` response → `paymentMethod` |
| Item subtotal | `billBreakdown` line labelled `Item Total` |
| Order status / items / ETA | `get_orders`, `track_order` |
| Anything financial in `get_orders` | **nothing — do not use** |

---

## 3. Transport and protocol

- Endpoint `POST https://mcp.swiggy.com/im`, JSON-RPC 2.0 over streamable HTTP.
- **No `Mcp-Session-Id`.** The server returns none; it is effectively stateless
  per request. Cart state lives server-side against the account, not a session.
- Send `Accept: application/json, text/event-stream` — responses may be either.
- `MCP-Protocol-Version: 2025-06-18` works.
- Unauthenticated calls return `401` with
  `WWW-Authenticate: Bearer realm="mcp"`.

### Response shape (important)
Tools do **not** return clean JSON. They return agent-facing prose — display
instructions, widget warnings — with a JSON object appended. Some tools return
**prose only**: `get_addresses` has no JSON at all. Parse by scanning for a
balanced top-level `{...}` and keep the prose separately.

### Auth
OAuth 2.1 + PKCE (S256 only). Dynamic Client Registration returns the
`client_id` **`swiggy-mcp`**. Access token lives **5 days**; despite discovery
metadata advertising `refresh_token` in `grant_types_supported`, **no refresh
token is issued**. On 401, re-run the whole browser flow (phone + OTP).
Discovery: `/.well-known/oauth-authorization-server` (the
`oauth-protected-resource` path returns the consumer website, not metadata).

---

## 4. Docs vs reality

| Docs say | Reality |
|---|---|
| `intentApp` ids like `com.google.android.apps.nbu.paisa.user` | URI schemes: `gpay://upi/`, `phonepe://`, `paytmmp://`, `bhim://upi/`, `credpay://upi/`, `super://` |
| `get_payment_options` takes no parameters | Optional `cartAmount`, `addressId` |
| 20 Instamart tools | **16 advertised.** Missing: `get_order_details`, `list_coupons`, `apply_coupon` (last two are whitelist-gated) |
| `track_order(orderId)` | `lat` and `lng` are **required** |
| `check_payment_status(paasId, orderId)` | Also `addressId`, `cartId`, `lat`, `lng`, `finalize` |
| ₹99 minimum order | Never observed as a block; a ₹59 cart checked out fine. What actually happens below ~₹100 is a **₹20 Small Cart Fee** |
| `search_products` variations hold `spinId`, `skuId`, `quantityDescription`, `displayName`, `price`, `isInStockAndAvailable`, `rating`, `sla`, `vegClassifier`, `maxQuantity` | Also **`imageUrl`** and **`price.unitLevelPrice`**, neither documented. `imageUrl` was present on **206/206** variations |
| Money is a display string (`"₹234.00"`) | In `search_products` it is a **plain number** (`"mrp": 144`). Both forms are live, so parse both |
| Nothing about who is signed in | There is **no way to learn the user's name.** The token carries `user_id` and `tid` only; `get_addresses` prose has no name; there is no profile tool |

---

## 5. API gotchas that will bite

- **`update_cart` uses `selectedAddressId`.** Every other tool uses
  `addressId`. This inconsistency is real, not a typo in the docs.
- **Cart items need BOTH `spinId` and `skuId`**, plus `quantity`. Both come
  from `variations[]` in a search result. They are not interchangeable and
  they are not redundant — **verified 2026-08-25** by searching the same
  product from a Gurugram and a Bengaluru address:

  | | Gurugram | Bengaluru | |
  |---|---|---|---|
  | `spinId` | `TYF3262KU8` | `TYF3262KU8` | identical, 5/5 variants |
  | `skuId` | `CMOH4YS8J8` | `5CV84KXR5A` | different, 0/5 identical |

  So `spinId` is the **global catalogue id** for a product variant, stable
  everywhere, and `skuId` identifies **that variant's stock at the serving
  store**. `spinId` says what you want; `skuId` says whose shelf it comes off.

  Consequences:
  - Persist `spinId` in your own data (shopping lists, reorder, favourites).
    **Never persist `skuId`** — re-fetch it per address at cart-build time.
  - Changing delivery address invalidates every `skuId` in the cart. This is
    the real reason the docs say to `clear_cart` before switching address.
  - A stale `skuId` is the likely cause of any "item unavailable" that
    contradicts a search result.

- **`addressId` vs `selectedAddressId` is a naming inconsistency, nothing
  more.** The identical address-id string is accepted by both; only the
  parameter name differs (`update_cart` uses `selectedAddressId`, everything
  else uses `addressId`). There is no second kind of address id.
- **`update_cart` replaces the entire cart.** There is no add/remove. Send the
  complete desired item list every time; snapshot before you overwrite.
- **`get_addresses` returns no JSON.** Scrape IDs from prose:
  `\[([^\]]+)\][^\n]*?\(ID:\s*([A-Za-z0-9]+)\)`. Paginated, `pageSize` max 10.
  Two ID formats coexist: opaque (`d0n03p494bshq3uh2hj0`) and legacy numeric.
- **`create_address` DOES return `addressId`** in clean JSON — the one place
  the API hands you an identifier directly. `latitude`/`longitude` are
  optional and resolved from the address text (verified: a new address created
  without coordinates was serviceable).
- **`maxQuantity` is per variant** and often 1–4. Enforce it before adding, or
  the cart write fails.
- **Prices**: `variations[].price.{mrp, offerPrice}`. Totals round **up**
  (₹256.62 → ₹257) — leave a rupee of slack at any boundary.
- **`billBreakdown.lineItems` is an open-ended `{label, value}` list.** Labels
  are server-generated strings; values may be non-numeric (`"FREE"`) and
  inconsistently decimal (`"₹234.00"` vs `"₹257"`). Never index by position or
  assume a label exists.
- **`get_delivery_status` returns no payload** — just
  `"Operation completed successfully."` Use `track_order`.
- **Carts expire when abandoned.** A 4-item cart emptied itself between two
  probes with no call from us.
- **`displayName` is usually already brand-prefixed.** Concatenating
  `brandName + displayName` produces "Amul Amul Fresh Paneer" and "Mother
  Dairy Mother Dairy Fresh Paneer" — but some rows carry a bare name, so the
  brand cannot simply be dropped either. Prefix only when the name does not
  already start with the brand.

### 5.1 A saved address can serve nothing, and says so cheerfully

**Verified 2026-08-25, and it will bite whoever picks a default address.** On a
real account with 24 saved addresses, the **first** one — the one
`get_addresses` returns first, i.e. the account default — returned:

```
Found 0 product(s) matching "milk".      success: true, no error, _isError: false
```

for *every* query tried, while every other address on the same account returned
20 products for the same query at the same minute. The address is saved and
valid; no store serves it.

So **an empty `products` array is not evidence about stock.** It conflates "we
have none of that" with "nobody delivers here", and the API gives you no field
to tell them apart. If your product defaults to an address, probe it with a
staple query before trusting it, and if a whole shopping list comes back empty,
say the address may be unserved rather than "out of stock" — otherwise the app
looks broken when it is working correctly.

### 5.2 The image host resizes, undocumented

`imageUrl` points at `media-assets.swiggy.com`, which is a **Cloudinary**
delivery endpoint, and the catalogue serves full-size shots — one PNG measured
**758 KB**, which is ~6 MB for a page of eight thumbnails. Inserting a
transformation after `/image/upload/` resizes on the fly:

```
.../image/upload/w_96,h_96,c_fit,q_auto,f_auto/NI_CATALOG/IMAGES/...
```

Measured across four real URLs: **2.0 MB became 15.0 KB**, a 135× cut, every
one HTTP 200. The assets are public — no `Authorization` header needed.

**This is a CDN convention, not a Swiggy API.** Use it only where it is
cosmetic. If Cloudinary ever rejects the transform, thumbnails break and
nothing else does; the untransformed URL is what the API actually handed you.

---

## 6. Fee lines observed

`Handling Fee`, `Small Cart Fee`, `Delivery Partner Fee`,
`High Demand Surge Fee`, `Late Night Fee`, `GST and Charges`.

Treat as an open set — this is what one account saw in one day, not a
taxonomy. The docs enumerate none of them (verified by grepping the full
351KB `llms-full.txt`).

GST is levied on the **fees**, not the items, and its effective rate varied
(₹1.62 on ₹21 of fees; ₹14.40 on ₹92) — do not model it either.

### The fee cliff is the single most important commercial fact
Crossing it can make added items **effectively cheaper than free**. Measured:
adding ₹70 of yogurt to a ₹187 cart raised the payable from ₹234 to ₹269 — a
net **₹35** for ₹70 of goods, because ₹35 of fees vanished.

A budget optimiser that minimises item cost without modelling this will
routinely make the user worse off.

**But model the cliff's existence, never its value.** The threshold is Swiggy's
pricing policy and can change at any time. Detect it by probing live totals;
do not ship a constant. The safe framing for a user is always "here is what the
server says you will pay", never "spend ₹X more and delivery becomes free"
unless you have just measured that to be true for their cart.

---

## 7. Ordering flow that works

```
get_addresses                       -> pick addressId (scrape from prose)
search_products(addressId, query)   -> variations[].spinId + .skuId
update_cart(selectedAddressId,      -> REPLACES cart
            items[{spinId, skuId, quantity}])
get_cart()                          -> billBreakdown.toPay = truth
get_payment_options()               -> only source of UPI app ids
checkout(addressId, paymentMethod)  -> orderId, status
track_order(orderId, lat, lng)      -> lat/lng from get_cart
                                       .selectedAddressDetails
```

- `checkout` **rejects a call with no payment method.** Always pass one.
- COD: `paymentMethod: "Cash"` → returns `status: "CONFIRMED"` immediately, no
  settle path, cart auto-empties.
- UPI app: `paymentMethod: "UPI"` + `intentApp: "<id byte-for-byte>"`.
- Desktop QR: `paymentMethod: "UPI"` + `generateUPIQR: true` — but note the
  schemas **disagree**: `get_payment_options` says set it for desktop QR,
  `checkout`'s own schema says leave it blank unless `get_cart` says otherwise.
  Unresolved.
- **There is no cancellation tool.** Docs route cancellations to customer care.
  `checkout` is genuinely irreversible — gate it behind explicit confirmation.

### Measure fees early
Fees cannot be known before a cart exists. Push the **first** item, read
`toPay`, derive `fees = toPay − Item Total`, then project locally for the rest
of the session. Doing this cut a live projection error from **₹56 to ₹0**.
Re-verify against the server before `checkout` regardless.

---

## 8. Still unverified

- **UPI settle path**: `check_payment_status` → `confirm_order`. Never run.
  Note the docs warn the payment widget auto-polls and auto-confirms, and
  explicitly say not to poll in a tight loop — a headless client has no widget,
  so it must poll itself, sparsely.
- `delete_address`, `report_error`.
- Exact fee-cliff boundaries (bracketed only).
- Whether the ₹1000 cap is on item total or payable.
- Coupons: `apply_coupon` / `list_coupons` are whitelist-gated and were not
  advertised to this account at all.
- Multi-store carts. `checkout` documents splitting into separate orders per
  store with partial-success results; never triggered.

---

## 9. Known change required on production access

Everything here was built and verified in **local development mode**, where
the docs say no approval is needed and `http://localhost` is an acceptable
redirect target. The moment you apply at `/access` and get whitelisted for
production, one specific piece stops working and must be rewritten.

### What breaks

`swiggy_auth.authorize_interactive()` — and only that function.

It logs in by opening a browser and running a small HTTP server on
`127.0.0.1:8765`, then waiting for Swiggy to redirect the code back to it.
That works because the program and the browser are on the same machine.

The docs state production redirect URIs must be **HTTPS and exact-match**,
with `http://localhost` allowed for local development only (platform schemes
such as `alexa://` are considered case by case). So a hosted deployment must
nominate a real address it owns — `https://your.app/auth/callback` — and
`127.0.0.1` stops being a legal target.

### What that forces

1. **The flow splits into two HTTP requests.** One to send the user to
   Swiggy, another when Swiggy redirects back. The current blocking wait
   (up to 300s in a loop) cannot exist in a request handler.
2. **The PKCE verifier must be persisted**, keyed by `state`, because the two
   requests do not share memory. Today it is a local variable held across the
   wait.
3. **Token storage must move to a database**, keyed by *your* application's
   user id. The `~/.swiggy_mcp/sessions/<user_id>.json` layout assumes one
   operating-system user per machine, and the file-permission isolation it
   relies on gives you nothing on a server.
4. **The `print()` calls must go.** The authorize URL has to be *returned* to
   the caller so it can issue a redirect, not written to stdout.

### What survives unchanged

`discover()`, `register_client()`, `make_pkce()`, `exchange_code()`, the token
record shape, and the 5-day / no-refresh handling. These are protocol, not
interaction, and they have been verified against the live server.

**So keep them separate.** If `authorize_interactive()` is isolated from the
protocol functions before that day, the migration is one new adapter rather
than a rewrite of code that already works.

### Also read first

For a product that brokers many end users rather than signing in as yourself,
the docs describe a different path entirely: `/docs/start/enterprise/`
`delegated-auth` — OAuth 2.1 on-behalf-of, with a per-user access token held
by your platform. **Not read in detail during this work.** Start there rather
than adapting the CLI flow.

Applying for access requires: integration name, organisation, the exact
redirect URIs, and which servers you need (`food`, `instamart`, `dineout`).
