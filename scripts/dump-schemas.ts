/**
 * Dump the LIVE `inputSchema` for every tool on both Swiggy MCP servers.
 *
 * Why this exists: the published docs leave some argument shapes unspecified — most
 * importantly `update_food_cart`'s `cartItems`, typed only as `object[]` with an empty
 * array in every example. The MCP `tools/list` response carries the server's real JSON
 * Schema, which is authoritative. Run this once you have a token and read the shape off
 * the output instead of guessing.
 *
 *   SWIGGY_TOKEN=... npm run dump-schemas
 *   SWIGGY_TOKEN=... npm run dump-schemas -- update_food_cart
 */

import { SwiggySession, type SwiggyServer } from "../src/session.js";

const token = process.env.SWIGGY_TOKEN;
if (!token) {
  console.error("SWIGGY_TOKEN is not set. Run `npm run login` first.");
  process.exit(1);
}

const filter = process.argv.slice(2);

async function dump(server: SwiggyServer): Promise<void> {
  const session = await SwiggySession.connect(server, { accessToken: token! });
  try {
    const { tools } = await session.listTools();
    const selected = filter.length ? tools.filter((t) => filter.includes(t.name)) : tools;

    console.log(`\n${"=".repeat(70)}\n${server.toUpperCase()} — ${selected.length}/${tools.length} tools\n${"=".repeat(70)}`);
    for (const tool of selected) {
      console.log(`\n--- ${tool.name} ---`);
      console.log(JSON.stringify(tool.inputSchema, null, 2));
    }
  } finally {
    await session.close();
  }
}

await dump("food");
await dump("instamart");
