export * from "./envelope.js";
export * from "./session.js";
export * from "./common.js";
export * from "./payment.js";
export * from "./food.js";
export * from "./instamart.js";

import { FoodOrdering } from "./food.js";
import { InstamartOrdering } from "./instamart.js";
import { SwiggySession } from "./session.js";

/**
 * Connect to both Swiggy MCP servers.
 *
 * Food and Instamart are separate endpoints with separate carts, so ordering across
 * both means two sessions. Close them when you are done.
 */
export async function connectSwiggy(accessToken: string): Promise<{
  food: FoodOrdering;
  instamart: InstamartOrdering;
  close: () => Promise<void>;
}> {
  const [foodSession, imSession] = await Promise.all([
    SwiggySession.connect("food", { accessToken }),
    SwiggySession.connect("instamart", { accessToken }),
  ]);

  return {
    food: new FoodOrdering(foodSession),
    instamart: new InstamartOrdering(imSession),
    close: async () => {
      await Promise.allSettled([foodSession.close(), imSession.close()]);
    },
  };
}
