import type { SwiggySession } from "./session.js";
import type { SwiggyEnvelope } from "./envelope.js";
import { SwiggyToolError } from "./envelope.js";

/**
 * The shared UPI Payment stage, used identically by Food and Instamart:
 *
 *   get_payment_options → place-order → check_payment_status → confirm_order
 *
 * Source: docs/build/recipes/pay-with-upi.md ("Non-UI (headless) clients") plus the
 * per-tool pages. This module implements the HEADLESS path: nothing auto-polls or
 * auto-confirms for us, so we own the cadence and the finalisation.
 */

// ---------------------------------------------------------------------------
// Payment options (get_payment_options)
// ---------------------------------------------------------------------------

export interface PaymentMethod {
  /** MUST be echoed byte-for-byte into the place-order call's `intentApp`. */
  id: string;
  label: string;
  [k: string]: unknown;
}

export interface PaymentOptions {
  /** Omitted entirely when UPI isn't available for this cart — then only Cash is offered. */
  platforms?: {
    mobile?: { methods?: PaymentMethod[] };
    desktop?: { methods?: PaymentMethod[] };
  };
  cod?: { paymentMethod: string; label: string };
  /** Flat list across surfaces — what a headless client prints as text. */
  allMethods?: PaymentMethod[];
}

/**
 * The user's pick, normalised.
 *
 * NPCI compliance: never collect a VPA / UPI ID from the user, and never ask which
 * device they are on — `get_payment_options` resolves both surfaces server-side.
 */
export type PaymentChoice =
  | { kind: "cash" }
  | { kind: "upi-app"; intentApp: string }
  | { kind: "upi-qr" };

/** Translate a choice into place-order arguments (identical for both servers). */
export function paymentArgs(choice: PaymentChoice): Record<string, unknown> {
  switch (choice.kind) {
    case "cash":
      return { paymentMethod: "Cash" };
    case "upi-app":
      return { paymentMethod: "UPI", intentApp: choice.intentApp };
    case "upi-qr":
      return { paymentMethod: "UPI", generateUPIQR: true };
  }
}

// ---------------------------------------------------------------------------
// Place-order responses
// ---------------------------------------------------------------------------

/** UPI place-order response: the order exists but is NOT yet placed. */
export interface PendingPayment {
  status: "PENDING_PAYMENT";
  orderId: string;
  paasId: string;
  transactionId?: string;
  /**
   * Opaque HTTPS payment link — present for BOTH app-intent and scan-QR picks
   * (they resolve to the same link). Hand this to the user: opening it renders a
   * scan-or-tap page. Always read it from here, never by parsing `message`.
   */
  bridgeUrl?: string;
  upiIntentUrl?: string;
  isQrFlow?: boolean;
  pollingIntervalInMs?: number;
  maxTimeToPollForInMs?: number;
  paymentMethod: "UPI";
  [k: string]: unknown;
}

/** Cash place-order response: no payment leg at all — already placed. */
export interface PlacedOrder {
  orderId: string;
  status: string;
  paymentMethod: string;
  [k: string]: unknown;
}

export type OrderResponse = PendingPayment | PlacedOrder;

export function isPendingPayment(o: OrderResponse): o is PendingPayment {
  return (o as PendingPayment).status === "PENDING_PAYMENT";
}

// ---------------------------------------------------------------------------
// Payment status
// ---------------------------------------------------------------------------

/**
 * Terminal statuses per the check_payment_status table. Note `cart_changed` is a
 * terminal FAILURE that is not a payment failure — the cart moved under us and the
 * order was never placed, so the fix is "review cart and place again", not "retry pay".
 */
export type PaymentStatusCode =
  | "success"
  | "paid"
  | "failed"
  | "refund-initiated"
  | "cancelled"
  | "cart_changed"
  | "pending"
  | "";

export interface PaymentStatus {
  paasId: string;
  status: PaymentStatusCode;
  terminal: boolean;
  isTerminalSuccess?: boolean;
  isTerminalFailure?: boolean;
  /** True when the backend already finalised the order — do NOT call confirm_order. */
  confirmed?: boolean;
  orderStatus?: string;
  [k: string]: unknown;
}

const TERMINAL_SUCCESS: ReadonlySet<string> = new Set(["success", "paid"]);

export type PaymentOutcome =
  | { outcome: "placed"; orderId: string; status: PaymentStatus }
  | { outcome: "failed"; orderId: string; status: PaymentStatus; retryable: boolean }
  | { outcome: "timeout"; orderId: string; status?: PaymentStatus };

/**
 * Per-server argument binding.
 *
 * This exists because the two servers genuinely differ, and getting it wrong strands
 * orders. From the "Per-server contract" table in pay-with-upi.md:
 *
 *   Food       confirm_order → orderId + addressId + lat + lng   (NOT paasId)
 *   Instamart  confirm_order → orderId + paasId
 *
 * Food additionally requires addressId + lat + lng on `check_payment_status`, or the
 * server cannot reconcile the order and it stays stuck pending.
 */
export interface PaymentBinding {
  statusArgs(p: PendingPayment): Record<string, unknown>;
  confirmArgs(p: PendingPayment): Record<string, unknown>;
}

export interface SettleOptions {
  /** Called on each non-terminal read, for progress reporting. */
  onPending?: (status: PaymentStatus) => void;
  /** Injectable for tests. */
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
}

const defaultSleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Fallbacks only for when place-order omits the window; prefer the server's values. */
const FALLBACK_INTERVAL_MS = 5_000;
const FALLBACK_WINDOW_MS = 300_000;

/**
 * Drive the payment to a terminal outcome, then finalise.
 *
 * `check_payment_status` is a ~19s long-poll, so this sleeps between reads rather than
 * tight-looping — hammering it stresses Swiggy's payment cache. The loop is capped by
 * the window the place-order response handed us.
 *
 * Note we read the outcome off `data.status`, NOT the envelope's `success`: a terminal
 * payment failure is still a *successful status read* and comes back `success: true`.
 */
export async function settlePayment(
  session: SwiggySession,
  pending: PendingPayment,
  binding: PaymentBinding,
  opts: SettleOptions = {},
): Promise<PaymentOutcome> {
  const sleep = opts.sleep ?? defaultSleep;
  const now = opts.now ?? Date.now;

  const interval = pending.pollingIntervalInMs ?? FALLBACK_INTERVAL_MS;
  const deadline = now() + (pending.maxTimeToPollForInMs ?? FALLBACK_WINDOW_MS);

  let last: PaymentStatus | undefined;

  while (now() < deadline) {
    const env = await session.callEnvelope<SwiggyEnvelope<PaymentStatus>>(
      "check_payment_status",
      binding.statusArgs(pending),
    );

    // A transport/tool error (bad or missing paasId) DOES use the failure envelope.
    if (!env.success) {
      throw new SwiggyToolError("check_payment_status", env.error?.message ?? "status read failed", env.hint);
    }

    last = env.data;
    if (last.terminal) break;

    opts.onPending?.(last);
    await sleep(interval);
  }

  if (last?.terminal) {
    if (TERMINAL_SUCCESS.has(last.status)) {
      // Normally the backend already confirmed for us. Only confirm if it says it didn't.
      if (!last.confirmed) {
        await session.call("confirm_order", binding.confirmArgs(pending));
      }
      return { outcome: "placed", orderId: pending.orderId, status: last };
    }

    // Terminal failure. Do NOT confirm. Only `failed` and `cart_changed` are worth
    // another attempt; a refund already underway must not be retried.
    const retryable = last.status === "failed" || last.status === "cart_changed";
    return { outcome: "failed", orderId: pending.orderId, status: last, retryable };
  }

  // Hit our cap while still pending → confirm once to finalise. The backend marks the
  // order failed if payment is still non-terminal, and a late success reconciles
  // server-side. This is the one case where we confirm without a success status.
  await session.call("confirm_order", binding.confirmArgs(pending));
  return { outcome: "timeout", orderId: pending.orderId, status: last };
}
