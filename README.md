# swiggy-mcp-budget

Ordering capability for **Swiggy Food** and **Swiggy Instamart** over MCP.

Both journeys are implemented against the authoritative per-tool reference pages at
`mcp.swiggy.com/builders`, including the shared UPI payment stage driven headlessly
(no widget host, so this client owns the poll loop and the finalisation).

## Layout

| File | Purpose |
| --- | --- |
| `src/session.ts` | MCP session per server (`/food`, `/im`) + envelope unwrapping |
| `src/envelope.ts` | Shared `{success, data, message}` envelope and typed errors |
| `src/payment.ts` | Shared UPI stage: options → place → poll → confirm |
| `src/food.ts` | Food ordering flow |
| `src/instamart.ts` | Instamart ordering flow |
| `src/common.ts` | Address handling, ₹1000 ceiling, ₹99 Instamart minimum |
| `scripts/login.ts` | OAuth 2.1 + PKCE login (DCR → consent → token) |
| `scripts/dump-schemas.ts` | Dump live `inputSchema` for every tool |

## Usage

```bash
npm install
npm run login              # phone + OTP in the browser, prints a token
export SWIGGY_TOKEN=...
```

```ts
import { connectSwiggy } from "./src/index.js";

const swiggy = await connectSwiggy(process.env.SWIGGY_TOKEN!);

// --- Instamart -----------------------------------------------------------
const [home] = await swiggy.instamart.addresses();
const found = await swiggy.instamart.searchProducts({ addressId: home.id, query: "bananas" });

// You add VARIATIONS to the cart, not the parent product.
const spinId = found.products?.[0]?.variations?.[0]?.spinId;

await swiggy.instamart.updateCart({
  selectedAddressId: home.id,
  items: [{ spinId: spinId!, quantity: 2 }],
});

const cart = await swiggy.instamart.getCart();
// → show the cart, the address and the payment methods, and get a real "yes"

const options = await swiggy.instamart.paymentOptions();
const result = await swiggy.instamart.checkout({
  address: home,
  payment: { kind: "cash" },
  userConfirmed: true,
});

if (result.kind === "awaiting-payment") {
  console.log(`Pay here: ${result.pending.bridgeUrl}`);
  const outcome = await result.settle();          // capped polling + confirm
  console.log(outcome.outcome);                    // "placed" | "failed" | "timeout"
}

await swiggy.close();
```

Food is the same shape: `searchRestaurants` → `restaurantMenu` → `updateCart`
→ `getCart` → `paymentOptions(addressId)` → `placeOrder` → `track`.

## Design notes

**Explicit confirmation is enforced by the type system.** `placeOrder` and `checkout`
both require `userConfirmed: true` as a literal, so neither can be reached by
defaulting. The docs are emphatic that these are never called without the user having
seen the cart, the payment method and the delivery address, and agreed.

**The two servers confirm payments differently.** This is the sharpest edge in the API:

| | `check_payment_status` | `confirm_order` |
| --- | --- | --- |
| Food | `paasId` + `orderId` + `addressId` + `lat` + `lng` | `orderId` + `addressId` + `lat` + `lng` |
| Instamart | `paasId` + `orderId` | `orderId` + `paasId` |

Food *requires* the geo echo — omit it and the server cannot reconcile the order, so it
stays stuck pending. `src/payment.ts` models this as a `PaymentBinding` so the two can
never be crossed.

**Payment outcome is read off `data.status`, not `success`.** A terminal payment
*failure* is still a successful status read and returns `success: true`. Only a
transport error (bad `paasId`) uses the failure envelope.

**Polling is capped and gentle.** `check_payment_status` is a ~19s long-poll; the loop
honours `pollingIntervalInMs` / `maxTimeToPollForInMs` from the place-order response and
never tight-loops. On reaching the cap while still pending it calls `confirm_order`
exactly once, which is the documented way to finalise.

**Place-order is not idempotent.** A `success: false` envelope is deterministic and
rethrown as-is. Any other failure (5xx, socket) raises
`SwiggyIndeterminateOrderError`, which names the tool to reconcile with
(`get_food_orders` / `get_orders`) instead of inviting a blind retry that could
double-order.

## Documentation discrepancies

The build recipes contradict the per-tool reference pages in several places. **This code
follows the reference pages**, which match the tool descriptions the servers themselves
return. Following the recipes verbatim produces calls that fail.

| Recipe says | Reference page says |
| --- | --- |
| `order-groceries`: `results.data.products[0].variants[0].spinId` | the field is **`variations`** |
| `order-groceries`: `update_cart({ items })` | **`selectedAddressId` is required** |
| `order-groceries`: `checkout({ paymentMethod: "COD" })` | **`addressId` required**; values are **`"UPI"` / `"Cash"`** — there is no `"COD"` |
| `order-food`: `update_food_cart({ items: [...] })` | the parameter is **`cartItems`**, and **`addressId` is required** |
| `order-food`: `get_restaurant_menu({ restaurantId })` | **`addressId` is required** too |

Also worth knowing: the Food index page reports "17 tools" while 18 tool pages exist
under `/docs/reference/food/`. The extra one is `get_food_delivery_status`, which is
widget-only and omitted from the stage tables.

### Known gap: `update_food_cart`'s `cartItems`

The element shape is **not documented anywhere**, including `llms-full.txt`. The
parameter table types it as bare `object[]`, and every published example — TypeScript,
Python and curl — passes an empty array. `FoodCartItem` is therefore left open rather
than invented.

Resolve it against the live server before building the Food cart step:

```bash
SWIGGY_TOKEN=... npm run dump-schemas -- update_food_cart
```

`tools/list` returns each tool's real JSON Schema, which is authoritative.

## Status

⚠️ **Unverified.** This was written against the docs but never compiled or executed:
the machine it was authored on has no Node installed and its system Python is 3.9.6
(below the MCP Python SDK's 3.10 floor). Before trusting it, run `npm run typecheck`,
then exercise the flows against a real token — starting with `dump-schemas` to confirm
the argument shapes.
