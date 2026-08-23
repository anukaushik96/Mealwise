/** Shared between the Food and Instamart ordering flows. */

export interface Address {
  id: string;
  /** Food's payment reconciliation needs these echoed back — see payment.ts. */
  lat?: number;
  lng?: number;
  label?: string;
  [k: string]: unknown;
}

/**
 * Both servers refuse to place orders at or above ₹1000 while MCP is in beta.
 *
 * Food:      "Order placement is NOT allowed for cart values of ₹1000 or more."
 * Instamart: "Checkout is NOT allowed for cart values above the allowed limit"
 *            (its numbered guidance names ₹1000).
 *
 * We check client-side too so we can fail before the mutating call rather than after.
 */
export const ORDER_CEILING_INR = 1000;

/** Instamart has a ₹99 minimum (docs/build/recipes/order-groceries.md, step 4). */
export const INSTAMART_MINIMUM_INR = 99;

export class CartCeilingError extends Error {
  constructor(readonly total: number, readonly service: string) {
    super(
      `${service} cart total ₹${total} is at or above the ₹${ORDER_CEILING_INR} beta ceiling. ` +
        `Tell the user to place this order in the Swiggy app instead.`,
    );
    this.name = "CartCeilingError";
  }
}

export function assertWithinCeiling(total: number, service: string): void {
  if (total >= ORDER_CEILING_INR) throw new CartCeilingError(total, service);
}

/**
 * `get_addresses` is documented to return the address list, but the docs do not pin
 * down whether `data` is the bare array or an object wrapping it. Normalise both
 * rather than betting on one — the recipes and reference pages disagree elsewhere.
 */
export function normaliseAddresses(data: unknown): Address[] {
  if (Array.isArray(data)) return data as Address[];
  const wrapped = (data as { addresses?: unknown })?.addresses;
  if (Array.isArray(wrapped)) return wrapped as Address[];
  return [];
}
