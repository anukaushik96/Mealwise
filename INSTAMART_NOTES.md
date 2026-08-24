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
| `swiggy_auth.py` | OAuth 2.1 + PKCE, dynamic client registration, token cache |
| `swiggy_mcp.py` | JSON-RPC transport; splits prose from the appended JSON |
| `money.py` | Rupee parsing to integer paise; handles `"FREE"`, `"₹1,234.50"` |
| `instamart.py` | Cart planner, the ₹1000 limit maths, cart/fee helpers |
| `parse_order.py` | Natural-language order sentence → items, sizes, counts |
| `order_instamart.py` | The interactive ordering CLI |
| `probe_instamart.py` | Read-only prober; dumps every tool's raw response |

The §2 trust hierarchy is enforced in `instamart.py` — `cart_item_total()`
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

---

## 5. API gotchas that will bite

- **`update_cart` uses `selectedAddressId`.** Every other tool uses
  `addressId`. This inconsistency is real, not a typo in the docs.
- **Cart items need BOTH `spinId` and `skuId`**, plus `quantity`. Both come
  from `variations[]` in a search result.
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
