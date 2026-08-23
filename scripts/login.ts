/**
 * OAuth 2.1 + PKCE login against Swiggy MCP.
 *
 * Implements docs/start/authenticate.md:
 *   1. Dynamic Client Registration (RFC 7591) at POST /auth/register
 *   2. PKCE S256 verifier/challenge
 *   3. Browser consent at GET /auth/authorize (phone + OTP, handled by Swiggy's UI)
 *   4. Code exchange at POST /auth/token
 *
 * Prints the access token. Tokens are ~5 days (`expires_in: 432000`).
 *
 *   npm run login
 */

import { createHash, randomBytes } from "node:crypto";
import { createServer } from "node:http";
import { spawn } from "node:child_process";

const BASE = "https://mcp.swiggy.com";
const PORT = Number(process.env.SWIGGY_CALLBACK_PORT ?? 8765);
const REDIRECT_URI = `http://127.0.0.1:${PORT}/callback`;
const SCOPE = "mcp:tools mcp:resources mcp:prompts";

const b64url = (b: Buffer) => b.toString("base64url");

/**
 * Register a client. The docs confirm DCR is supported but do not enumerate the body,
 * so these are the RFC 7591 standard fields.
 */
async function register(): Promise<string> {
  const res = await fetch(`${BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      client_name: "swiggy-mcp-budget",
      redirect_uris: [REDIRECT_URI],
      grant_types: ["authorization_code"],
      response_types: ["code"],
      token_endpoint_auth_method: "none",
    }),
  });
  if (!res.ok) throw new Error(`DCR failed (${res.status}): ${await res.text()}`);
  const body = (await res.json()) as { client_id?: string };
  if (!body.client_id) throw new Error(`DCR returned no client_id: ${JSON.stringify(body)}`);
  return body.client_id;
}

/**
 * Serve the loopback callback once and resolve with the authorization code.
 *
 * `onReady` fires only once the socket is actually listening — opening the browser
 * before that races the consent redirect against server startup.
 */
function awaitCallback(expectedState: string, onReady: () => void): Promise<string> {
  return new Promise((resolve, reject) => {
    const server = createServer((req, res) => {
      const url = new URL(req.url ?? "/", `http://127.0.0.1:${PORT}`);
      if (url.pathname !== "/callback") {
        res.writeHead(404).end();
        return;
      }

      const code = url.searchParams.get("code");
      const state = url.searchParams.get("state");
      const error = url.searchParams.get("error");

      res.writeHead(200, { "Content-Type": "text/plain" });
      res.end(error ? `Login failed: ${error}` : "Login complete — you can close this tab.");
      server.close();

      if (error) reject(new Error(`authorize returned ${error}`));
      // CSRF guard: the state we get back must be the one we sent.
      else if (state !== expectedState) reject(new Error("state mismatch — possible CSRF"));
      else if (!code) reject(new Error("no authorization code in callback"));
      else resolve(code);
    });

    server.on("error", reject);
    server.listen(PORT, "127.0.0.1", onReady);
  });
}

function openBrowser(url: string): void {
  const cmd = process.platform === "darwin" ? "open" : process.platform === "win32" ? "start" : "xdg-open";
  spawn(cmd, [url], { stdio: "ignore", detached: true, shell: process.platform === "win32" }).unref();
}

const verifier = b64url(randomBytes(32));
const challenge = b64url(createHash("sha256").update(verifier).digest());
const state = b64url(randomBytes(16));

const clientId = await register();

const authorizeUrl = new URL(`${BASE}/auth/authorize`);
authorizeUrl.search = new URLSearchParams({
  response_type: "code",
  client_id: clientId,
  redirect_uri: REDIRECT_URI,
  code_challenge: challenge,
  code_challenge_method: "S256",
  state,
  scope: SCOPE,
}).toString();

console.log(`\nOpening Swiggy consent (phone + OTP):\n${authorizeUrl}\n`);
const code = await awaitCallback(state, () => openBrowser(authorizeUrl.toString()));

const tokenRes = await fetch(`${BASE}/auth/token`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    grant_type: "authorization_code",
    code,
    code_verifier: verifier,
    redirect_uri: REDIRECT_URI,
    client_id: clientId,
  }),
});

if (!tokenRes.ok) throw new Error(`token exchange failed (${tokenRes.status}): ${await tokenRes.text()}`);

const token = (await tokenRes.json()) as { access_token: string; expires_in?: number };
console.log(`\nAccess token (expires in ${token.expires_in ?? "?"}s):\n`);
console.log(`export SWIGGY_TOKEN=${token.access_token}\n`);
