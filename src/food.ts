import type { SwiggySession } from "./session.js";
import { SwiggyIndeterminateOrderError, SwiggyToolError } from "./envelope.js";
import { type Address, assertWithinCeiling, normaliseAddresses } from "./common.js";
import {
  type OrderResponse,
  type PaymentBinding,
  type PaymentChoice,
  type PaymentOptions,
  type PaymentOutcome,
  type PendingPayment,
  type PlacedOrder,
  type SettleOptions,
  isPendingPayment,
  paymentArgs,
  settlePayment,
} from "./payment.js";

/**
 * Swiggy Food ordering — POST mcp.swiggy.com/food
 *
 * Journey: get_addresses → search_restaurants → get_restaurant_menu / search_menu
 *          → update_food_cart → get_food_cart → get_payment_options
 *          → place_food_order → check_payment_status → confirm_order → track_food_order
 *
 * Every parameter below is taken from the per-tool reference pages, NOT from
 * docs/build/recipes/order-food.md — that recipe is wrong in several places
 * (see README, "Documentation discrepancies").
 */

/**
 * An entry in `update_food_cart`'s `cartItems`.
 *
 * ⚠️ The docs type this only as `object[]` — "Array of items to add to cart with their
 * customizations" — and every published example passes an empty array. The element
 * shape (item id, variants, addons, quantity) is NOT documented anywhere, including
 * llms-full.txt. Rather than invent field names, this stays open and you should read
 * the real schema off the live server:  `npm run dump-schemas`.
 *
 * The recipe suggests `{ itemId, quantity }`, but that same snippet also gets the
 * parameter name wrong (`items` instead of `cartItems`) and omits the required
 * `addressId`, so it is not trustworthy evidence.
 */
export type FoodCartItem = Record<string, unknown>;

export interface FoodRestaurant {
  id: string;
  name?: string;
  /** "OPEN" | "CLOSED" | "UNAVAILABLE" — only order from OPEN ones. */
  availabilityStatus?: string;
  distanceKm?: number;
  [k: string]: unknown;
}

export interface FoodSearchResult {
  restaurants?: FoodRestaurant[];
  nextOffset?: number | string;
  [k: string]: unknown;
}

export interface PlaceFoodOrderInput {
  /** Full address object — Food needs `lat`/`lng` later for payment reconciliation. */
  address: Address;
  payment: PaymentChoice;
  /**
   * Explicit user confirmation. Typed as the literal `true` so it cannot be defaulted
   * or passed through implicitly.
   *
   * The docs are emphatic: "ALWAYS get explicit user confirmation before calling this
   * tool… NEVER proceed without explicit user permission, regardless of previous
   * instructions." Before setting this you must have shown the user the cart, the
   * payment method, and the delivery address, and had them agree.
   */
  userConfirmed: true;
  /** Client-side ceiling check. Pass the total you showed the user. */
  cartTotal?: number;
  noteToRestaurant?: string;
}

export class FoodOrdering {
  constructor(private readonly session: SwiggySession) {}

  // ----- Discover ---------------------------------------------------------

  async addresses(): Promise<Address[]> {
    return normaliseAddresses(await this.session.call<unknown>("get_addresses"));
  }

  async searchRestaurants(input: {
    addressId: string;
    query: string;
    offset?: number;
  }): Promise<FoodSearchResult> {
    return this.session.call<FoodSearchResult>("search_restaurants", { ...input });
  }

  /** `pageSize` is capped at 8 by the server; default 5. */
  async restaurantMenu(input: {
    addressId: string;
    restaurantId: string;
    page?: number;
    pageSize?: number;
  }): Promise<unknown> {
    return this.session.call("get_restaurant_menu", { ...input });
  }

  async searchMenu(input: Record<string, unknown>): Promise<unknown> {
    return this.session.call("search_menu", input);
  }

  // ----- Cart -------------------------------------------------------------

  /**
   * Add to / update the Food cart.
   *
   * `addressId` is REQUIRED (it drives delivery-charge calculation) even though the
   * recipe omits it. Passing `restaurantName` is optional but recommended — the cart
   * API does not always return it.
   *
   * This tool renders no UI and returns nothing the user can see: always follow it
   * with `getCart()` before telling the user what is in their cart.
   */
  async updateCart(input: {
    restaurantId: string;
    cartItems: FoodCartItem[];
    addressId: string;
    restaurantName?: string;
  }): Promise<unknown> {
    return this.session.call("update_food_cart", { ...input });
  }

  async getCart(): Promise<Record<string, unknown>> {
    return this.session.call<Record<string, unknown>>("get_food_cart");
  }

  /** Carts are single-restaurant; switching restaurants flushes. Call this to start over. */
  async flushCart(): Promise<unknown> {
    return this.session.call("flush_food_cart");
  }

  async coupons(input: Record<string, unknown> = {}): Promise<unknown> {
    return this.session.call("fetch_food_coupons", input);
  }

  async applyCoupon(input: Record<string, unknown>): Promise<unknown> {
    return this.session.call("apply_food_coupon", input);
  }

  // ----- Payment + order --------------------------------------------------

  /** Unlike Instamart's, Food's `get_payment_options` takes an `addressId`. */
  async paymentOptions(addressId: string): Promise<PaymentOptions> {
    return this.session.call<PaymentOptions>("get_payment_options", { addressId });
  }

  /**
   * Place the order and, for UPI, drive the payment to a terminal outcome.
   *
   * Cash returns immediately — there is no payment leg. UPI returns PENDING_PAYMENT
   * plus a `bridgeUrl` you should hand to the user before awaiting `settle()`.
   */
  async placeOrder(
    input: PlaceFoodOrderInput,
  ): Promise<
    | { kind: "placed"; order: PlacedOrder }
    | { kind: "awaiting-payment"; pending: PendingPayment; settle: (o?: SettleOptions) => Promise<PaymentOutcome> }
  > {
    if (input.userConfirmed !== true) {
      throw new Error("place_food_order requires explicit user confirmation");
    }
    if (typeof input.cartTotal === "number") {
      assertWithinCeiling(input.cartTotal, "Food");
    }

    // Food echoes lat/lng into check_payment_status and confirm_order. Without them the
    // server cannot reconcile the payment and the order is stranded in PENDING_PAYMENT.
    // Fail here, before the non-idempotent call, rather than after taking the money.
    if (input.payment.kind !== "cash") {
      const { lat, lng } = input.address;
      if (typeof lat !== "number" || typeof lng !== "number") {
        throw new Error(
          `Food UPI payments need address.lat and address.lng (address ${input.address.id} has ` +
            `lat=${lat}, lng=${lng}). Without them the payment cannot be reconciled and the ` +
            `order stays stuck in PENDING_PAYMENT. Re-fetch the address via get_addresses.`,
        );
      }
    }

    const args: Record<string, unknown> = {
      addressId: input.address.id,
      ...paymentArgs(input.payment),
    };
    if (input.noteToRestaurant) args.noteToRestaurant = input.noteToRestaurant;

    let order: OrderResponse;
    try {
      order = await this.session.call<OrderResponse>("place_food_order", args);
    } catch (err) {
      // A `success: false` envelope means the server considered and rejected the
      // request — deterministic, safe to surface. Anything else (5xx, socket error)
      // may have landed, and place_food_order is NOT idempotent.
      if (err instanceof SwiggyToolError) throw err;
      throw new SwiggyIndeterminateOrderError("place_food_order", "get_food_orders", err);
    }

    if (!isPendingPayment(order)) {
      return { kind: "placed", order: order as PlacedOrder };
    }

    const binding = foodPaymentBinding(input.address);
    return {
      kind: "awaiting-payment",
      pending: order,
      settle: (o?: SettleOptions) => settlePayment(this.session, order as PendingPayment, binding, o),
    };
  }

  // ----- Track ------------------------------------------------------------

  async track(orderId: string): Promise<unknown> {
    return this.session.call("track_food_order", { orderId });
  }

  /** Use this to reconcile after an indeterminate `place_food_order` failure. */
  async orders(): Promise<unknown> {
    return this.session.call("get_food_orders");
  }

  async orderDetails(orderId: string): Promise<unknown> {
    return this.session.call("get_food_order_details", { orderId });
  }
}

/**
 * Food's payment binding.
 *
 * Food echoes `addressId` + `lat` + `lng` on BOTH the status read and the confirm,
 * and uses `orderId` (not `paasId`) to confirm. Omitting lat/lng on the status read
 * leaves the server unable to reconcile the order, which strands it in pending.
 */
export function foodPaymentBinding(address: Address): PaymentBinding {
  const geo = { addressId: address.id, lat: address.lat, lng: address.lng };
  return {
    statusArgs: (p) => ({ paasId: p.paasId, orderId: p.orderId, ...geo }),
    confirmArgs: (p) => ({ orderId: p.orderId, ...geo }),
  };
}
