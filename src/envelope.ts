/**
 * Shared response envelope.
 *
 * Every Swiggy MCP tool — Food and Instamart alike — returns the same shape:
 *   success: { success: true,  data: <payload>, message?: string }
 *   failure: { success: false, error: { message: string }, hint?: string }
 *
 * Verified against the per-tool reference pages under
 * https://mcp.swiggy.com/builders/docs/reference/{food,instamart}/
 */

export interface SwiggySuccess<T> {
  success: true;
  data: T;
  message?: string;
}

export interface SwiggyFailure {
  success: false;
  error: { message: string };
  /** Extra context the docs tell us to surface to the user verbatim. */
  hint?: string;
}

export type SwiggyEnvelope<T> = SwiggySuccess<T> | SwiggyFailure;

/** A tool returned `success: false`, or the transport produced something unparseable. */
export class SwiggyToolError extends Error {
  constructor(
    readonly tool: string,
    readonly detail: string,
    readonly hint?: string,
  ) {
    super(hint ? `${tool}: ${detail} — ${hint}` : `${tool}: ${detail}`);
    this.name = "SwiggyToolError";
  }
}

/**
 * A non-idempotent mutation (`place_food_order`, `checkout`) failed in a way that
 * leaves us unable to tell whether it landed.
 *
 * The docs are explicit that these two tools are NOT idempotent: on a 5xx you must
 * reconcile via `get_food_orders` / `get_orders` before retrying, or you risk a
 * duplicate order. Callers should never blind-retry on this error.
 */
export class SwiggyIndeterminateOrderError extends Error {
  constructor(
    readonly tool: string,
    readonly reconcileWith: string,
    readonly cause_: unknown,
  ) {
    super(
      `${tool} failed indeterminately — it is not idempotent, so check ${reconcileWith} ` +
        `for an order that may already exist before retrying`,
    );
    this.name = "SwiggyIndeterminateOrderError";
  }
}

/** Narrow an envelope to its payload, throwing a typed error on failure. */
export function unwrap<T>(tool: string, raw: unknown): T {
  if (raw === null || typeof raw !== "object" || !("success" in raw)) {
    const preview = JSON.stringify(raw ?? null).slice(0, 200);
    throw new SwiggyToolError(tool, `unrecognised response envelope: ${preview}`);
  }

  const env = raw as SwiggyEnvelope<T>;
  if (!env.success) {
    throw new SwiggyToolError(tool, env.error?.message ?? "unknown error", env.hint);
  }
  return env.data;
}
