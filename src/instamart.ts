import type { SwiggySession } from "./session.js";
import { SwiggyIndeterminateOrderError, SwiggyToolError } from "./envelope.js";
import {
  type Address,
  INSTAMART_MINIMUM_INR,
  assertWithinCeiling,
  normaliseAddresses,
} from "./common.js";
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
 * Swiggy Instamart ordering — POST mcp.swiggy.com/im
 *
 * Journey: get_addresses → search_products / your_go_to_items → update_cart
 *          → get_cart → get_payment_options → checkout
 *          → check_payment_status → confirm_order → track_order
 *
 * Parameters come from the per-tool reference pages. The order-groceries recipe is
 * wrong about three of them (see README, "Documentation discrepancies").
 */

export interface ProductVariation {
  /** SKU-level id. You add VARIATIONS to the cart, never the parent product. */
  spinId: string;
  [k: string]: unknown;
}

export interface SearchProduct {
  name?: string;
  brand?: string;
  /**
   * Note the spelling: the response field is `variations`.
   * The recipe's snippet reads `.variants[0].spinId`, which does not exist.
   */
  variations?: ProductVariation[];
  [k: string]: unknown;
}

export interface ProductSearchResult {
  products?: SearchProduct[];
  /** Only present when the catalogue found related items; render separately. */
  similarProducts?: SearchProduct[];
  /** "0" when there are no more pages. */
  nextOffset?: string;
  [k: string]: unknown;
}

/**
 * A cart line for `update_cart`.
 *
 * The tool's own example passes `{ spinId, skuId, quantity }`. `spinId` is documented
 * as the SKU-level identifier from a product's `variations`; `skuId` appears only in
 * that example and is not described in the parameter table, so it is optional here.
 */
export interface InstamartCartItem {
  spinId: string;
  quantity: number;
  skuId?: string;
  [k: string]: unknown;
}

export interface InstamartCheckoutInput {
  address: Address;
  payment: PaymentChoice;
  /**
   * Explicit user confirmation — same contract as Food. The docs require you to show
   * the cart, the available payment methods and the full delivery address, and get a
   * clear yes, before this call.
   */
  userConfirmed: true;
  /** Client-side guards. Pass the total you showed the user. */
  cartTotal?: number;
}

export class InstamartOrdering {
  constructor(private readonly session: SwiggySession) {}

  // ----- Discover ---------------------------------------------------------

  async addresses(): Promise<Address[]> {
    return normaliseAddresses(await this.session.call<unknown>("get_addresses"));
  }

  async searchProducts(input: {
    addressId: string;
    query: string;
    offset?: number;
  }): Promise<ProductSearchResult> {
    return this.session.call<ProductSearchResult>("search_products", { ...input });
  }

  /** Frequently/recently ordered items — a one-tap reorder path that skips search. */
  async goToItems(addressId: string): Promise<unknown> {
    return this.session.call("your_go_to_items", { addressId });
  }

  // ----- Cart -------------------------------------------------------------

  /**
   * Replace the cart contents.
   *
   * This REPLACES the entire cart with `items` — it is not an incremental add. And
   * `selectedAddressId` is required, despite the recipe omitting it.
   *
   * Do not swap address mid-cart: clear first, or you get cross-address SKU mismatches.
   */
  async updateCart(input: {
    selectedAddressId: string;
    items: InstamartCartItem[];
  }): Promise<unknown> {
    return this.session.call("update_cart", { ...input });
  }

  async getCart(): Promise<Record<string, unknown>> {
    return this.session.call<Record<string, unknown>>("get_cart");
  }

  async clearCart(): Promise<unknown> {
    return this.session.call("clear_cart");
  }

  // ----- Payment + order --------------------------------------------------

  /**
   * Instamart's `get_payment_options` takes NO arguments — cart context is resolved
   * server-side. This is the only source of UPI methods; `get_cart` does not carry them.
   */
  async paymentOptions(): Promise<PaymentOptions> {
    return this.session.call<PaymentOptions>("get_payment_options");
  }

  /**
   * Place the Instamart order.
   *
   * `addressId` is required. `paymentMethod` is "UPI" or "Cash" — there is no "COD"
   * value, despite what the recipe shows; Cash auto-defaults if omitted.
   */
  async checkout(
    input: InstamartCheckoutInput,
  ): Promise<
    | { kind: "placed"; order: PlacedOrder }
    | { kind: "awaiting-payment"; pending: PendingPayment; settle: (o?: SettleOptions) => Promise<PaymentOutcome> }
  > {
    if (input.userConfirmed !== true) {
      throw new Error("checkout requires explicit user confirmation");
    }
    if (typeof input.cartTotal === "number") {
      assertWithinCeiling(input.cartTotal, "Instamart");
      if (input.cartTotal < INSTAMART_MINIMUM_INR) {
        throw new Error(
          `Instamart has a ₹${INSTAMART_MINIMUM_INR} minimum; cart is ₹${input.cartTotal}. ` +
            `Ask the user to add more items.`,
        );
      }
    }

    const args: Record<string, unknown> = {
      addressId: input.address.id,
      ...paymentArgs(input.payment),
    };

    let order: OrderResponse;
    try {
      order = await this.session.call<OrderResponse>("checkout", args);
    } catch (err) {
      // Same non-idempotency rule as Food: reconcile before retrying.
      if (err instanceof SwiggyToolError) throw err;
      throw new SwiggyIndeterminateOrderError("checkout", "get_orders", err);
    }

    if (!isPendingPayment(order)) {
      return { kind: "placed", order: order as PlacedOrder };
    }

    return {
      kind: "awaiting-payment",
      pending: order,
      settle: (o?: SettleOptions) =>
        settlePayment(this.session, order as PendingPayment, INSTAMART_PAYMENT_BINDING, o),
    };
  }

  // ----- Track ------------------------------------------------------------

  async track(orderId: string): Promise<unknown> {
    return this.session.call("track_order", { orderId });
  }

  /** Use this to reconcile after an indeterminate `checkout` failure. */
  async orders(): Promise<unknown> {
    return this.session.call("get_orders");
  }

  async orderDetails(orderId: string): Promise<unknown> {
    return this.session.call("get_order_details", { orderId });
  }
}

/**
 * Instamart's payment binding: `orderId` + `paasId` to confirm, and no geo echo on the
 * status read. This differs from Food — do not share one binding between them.
 */
export const INSTAMART_PAYMENT_BINDING: PaymentBinding = {
  statusArgs: (p) => ({ paasId: p.paasId, orderId: p.orderId }),
  confirmArgs: (p) => ({ orderId: p.orderId, paasId: p.paasId }),
};
