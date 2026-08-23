import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

import { SwiggyToolError, unwrap } from "./envelope.js";

export type SwiggyServer = "food" | "instamart";

/** Endpoints per docs/reference/{food,instamart}/index.md. Note Instamart is `/im`, not `/instamart`. */
const ENDPOINT: Record<SwiggyServer, string> = {
  food: "https://mcp.swiggy.com/food",
  instamart: "https://mcp.swiggy.com/im",
};

export interface SessionOptions {
  /**
   * Bearer token from the OAuth 2.1 + PKCE flow (docs/start/authenticate.md).
   * Session credentials are supplied by the authenticated MCP session — you never
   * pass user identity or tokens as tool arguments.
   */
  accessToken: string;
  clientName?: string;
  clientVersion?: string;
}

/**
 * A connected MCP session against one Swiggy server, with envelope unwrapping.
 *
 * One session per server. Food and Instamart are separate MCP endpoints and do not
 * share a cart, so ordering from both means two sessions.
 */
export class SwiggySession {
  private constructor(
    readonly server: SwiggyServer,
    private readonly client: Client,
  ) {}

  static async connect(server: SwiggyServer, opts: SessionOptions): Promise<SwiggySession> {
    const transport = new StreamableHTTPClientTransport(new URL(ENDPOINT[server]), {
      requestInit: {
        headers: { Authorization: `Bearer ${opts.accessToken}` },
      },
    });

    const client = new Client(
      {
        name: opts.clientName ?? "swiggy-mcp-budget",
        version: opts.clientVersion ?? "0.1.0",
      },
      { capabilities: {} },
    );

    await client.connect(transport);
    return new SwiggySession(server, client);
  }

  /** Call a tool and return its unwrapped `data`, throwing `SwiggyToolError` on `success: false`. */
  async call<T>(tool: string, args: Record<string, unknown> = {}): Promise<T> {
    const result = await this.client.callTool({ name: tool, arguments: args });
    return unwrap<T>(tool, SwiggySession.parse(tool, result));
  }

  /**
   * Call a tool and return the full envelope without throwing on `success: false`.
   *
   * Needed for `check_payment_status`, where a terminal payment *failure* still comes
   * back as `success: true` and the real outcome lives on `data.status`.
   */
  async callEnvelope<T>(tool: string, args: Record<string, unknown> = {}): Promise<T> {
    const result = await this.client.callTool({ name: tool, arguments: args });
    return SwiggySession.parse(tool, result) as T;
  }

  /** Live `inputSchema` for every tool this server exposes — the authoritative shape. */
  async listTools() {
    return this.client.listTools();
  }

  /** Closes the underlying transport too. */
  async close(): Promise<void> {
    await this.client.close();
  }

  /** MCP returns content blocks; Swiggy puts its JSON envelope in a text block. */
  private static parse(tool: string, result: unknown): unknown {
    const res = result as {
      structuredContent?: unknown;
      content?: Array<{ type?: string; text?: string }>;
    };

    // Prefer structuredContent when the server provides it — no re-parsing needed.
    if (res?.structuredContent !== undefined) return res.structuredContent;

    const text = (res?.content ?? [])
      .filter((b) => b?.type === "text" && typeof b.text === "string")
      .map((b) => b.text)
      .join("");

    if (!text) throw new SwiggyToolError(tool, "empty response — no text content block");

    try {
      return JSON.parse(text);
    } catch {
      throw new SwiggyToolError(tool, `response was not JSON: ${text.slice(0, 200)}`);
    }
  }
}
