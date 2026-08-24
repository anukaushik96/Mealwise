"""Minimal MCP client for Swiggy MCP over streamable HTTP.

Stdlib only - the official `mcp` SDK needs Python 3.10+ and this box has 3.9.
Speaks raw JSON-RPC 2.0, handles both JSON and SSE response bodies, and
carries the Mcp-Session-Id the server hands back at initialize.
"""

import json
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2025-06-18"

SERVERS = {
    "instamart": "https://mcp.swiggy.com/im",
    "food": "https://mcp.swiggy.com/food",
    "dineout": "https://mcp.swiggy.com/dineout",
}


class McpError(Exception):
    """A JSON-RPC error, an HTTP error, or a transport-level failure."""

    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload


def _parse_body(raw, content_type):
    """Return the JSON-RPC message from a JSON or text/event-stream body."""
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return None
    if "text/event-stream" in (content_type or ""):
        messages = []
        for line in text.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk:
                    try:
                        messages.append(json.loads(chunk))
                    except json.JSONDecodeError:
                        pass
        if not messages:
            raise McpError("no JSON-RPC data frames in SSE body", payload=text[:500])
        # Prefer a frame carrying a result or error over pure notifications.
        for msg in messages:
            if "result" in msg or "error" in msg:
                return msg
        return messages[-1]
    return json.loads(text)


def split_prose_and_json(text):
    """Split "some prose ... {json}" into (prose, parsed_json_or_None).

    Scans for a top-level JSON object, respecting strings and escapes so a
    brace inside a product name cannot throw off the match.
    """
    for start, char in enumerate(text):
        if char != "{":
            continue
        depth, in_string, escaped = 0, False, False
        for end in range(start, len(text)):
            ch = text[end]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start:end + 1])
                    except json.JSONDecodeError:
                        break  # not real JSON; try the next "{"
                    return text[:start].strip(), parsed
        # fall through and look for a later "{"
    return text.strip(), None


class SwiggyMcp:
    def __init__(self, token, server="instamart", timeout=60):
        self.url = SERVERS[server] if server in SERVERS else server
        self.token = token
        self.timeout = timeout
        self.session_id = None
        self._next_id = 0

    def _headers(self):
        headers = {
            "Authorization": "Bearer %s" % self.token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _post(self, payload):
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                # initialize returns the session id we must echo on every later call.
                sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
                if sid:
                    self.session_id = sid
                return resp.status, _parse_body(resp.read(), resp.headers.get("Content-Type"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise McpError(
                "HTTP %s from %s: %s" % (exc.code, self.url, detail[:400]),
                status=exc.code,
                payload=detail,
            )
        except urllib.error.URLError as exc:
            raise McpError("cannot reach %s: %s" % (self.url, exc.reason))

    def _rpc(self, method, params=None):
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            payload["params"] = params
        _, message = self._post(payload)
        if message is None:
            raise McpError("empty response to %s" % method)
        if "error" in message:
            err = message["error"]
            raise McpError(
                "%s failed: %s (code %s)" % (method, err.get("message"), err.get("code")),
                payload=err,
            )
        return message.get("result")

    def _notify(self, method, params=None):
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._post(payload)

    def initialize(self, client_name="swiggy-mcp-budget", client_version="0.1.0"):
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": client_name, "version": client_version},
            },
        )
        self._notify("notifications/initialized")
        return result

    def list_tools(self):
        return self._rpc("tools/list").get("tools", [])

    def call_tool(self, name, arguments=None):
        """Call a tool and split its reply into prose and structured data.

        Swiggy tools do not return clean JSON. They return agent-facing prose
        (display instructions, widget warnings) with a JSON object appended -
        and some tools, like get_addresses, return prose only. So we peel the
        JSON object off the end and keep the prose under "_text".
        """
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        texts = [
            block.get("text", "")
            for block in result.get("content", [])
            if block.get("type") == "text"
        ]
        joined = "\n".join(t for t in texts if t)
        if not joined:
            return result

        prose, data = split_prose_and_json(joined)
        payload = {"_text": prose, "_isError": bool(result.get("isError"))}
        if isinstance(data, dict):
            payload.update(data)
        elif data is not None:
            payload["_json"] = data
        return payload
