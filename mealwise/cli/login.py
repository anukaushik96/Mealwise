"""OAuth 2.1 + PKCE login against Swiggy MCP.

Implements docs/start/authenticate.md:

1. Dynamic Client Registration (RFC 7591) at ``POST /auth/register``
2. PKCE S256 verifier/challenge
3. Browser consent at ``GET /auth/authorize`` (phone + OTP, Swiggy's UI)
4. Code exchange at ``POST /auth/token``

Prints the access token. Tokens last ~5 days (``expires_in: 432000``).

    python -m swiggy.cli.login
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import subprocess
import sys
import threading
import urllib.parse
import urllib.request

BASE = "https://mcp.swiggy.com"
PORT = int(os.environ.get("SWIGGY_CALLBACK_PORT", "8765"))
REDIRECT_URI = f"http://127.0.0.1:{PORT}/callback"
SCOPE = "mcp:tools mcp:resources mcp:prompts"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _post_json(url: str, body: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as err:  # type: ignore[attr-defined]
        raise SystemExit(f"{url} failed ({err.code}): {err.read().decode()[:400]}") from None


def register() -> str:
    """DCR is supported but the docs do not enumerate the body; these are RFC 7591."""
    body = _post_json(
        f"{BASE}/auth/register",
        {
            "client_name": "swiggy-mcp-budget",
            "redirect_uris": [REDIRECT_URI],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    client_id = body.get("client_id")
    if not isinstance(client_id, str):
        raise SystemExit(f"DCR returned no client_id: {body}")
    return client_id


class _Callback(http.server.BaseHTTPRequestHandler):
    result: dict[str, str] = {}
    done = threading.Event()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        _Callback.result = params
        message = (
            f"Login failed: {params['error']}"
            if "error" in params
            else "Login complete — you can close this tab."
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(message.encode())
        _Callback.done.set()

    def log_message(self, *_: object) -> None:
        pass  # keep the console clean


def open_browser(url: str) -> None:
    opener = {"darwin": "open", "win32": "start"}.get(sys.platform, "xdg-open")
    try:
        subprocess.Popen(
            [opener, url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=sys.platform == "win32",
        )
    except OSError:
        pass  # the URL is printed anyway


def main() -> None:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = _b64url(secrets.token_bytes(16))

    client_id = register()
    authorize_url = f"{BASE}/auth/authorize?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "scope": SCOPE,
        }
    )

    # Bind BEFORE opening the browser: otherwise the consent redirect races startup.
    server = http.server.HTTPServer(("127.0.0.1", PORT), _Callback)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print(f"\nOpening Swiggy consent (phone + OTP):\n{authorize_url}\n")
    open_browser(authorize_url)

    _Callback.done.wait()
    server.shutdown()
    params = _Callback.result

    if "error" in params:
        raise SystemExit(f"authorize returned {params['error']}")
    # CSRF guard: the state we get back must be the one we sent.
    if params.get("state") != state:
        raise SystemExit("state mismatch — possible CSRF")
    code = params.get("code")
    if not code:
        raise SystemExit("no authorization code in callback")

    token = _post_json(
        f"{BASE}/auth/token",
        {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
        },
    )
    print(f"\nAccess token (expires in {token.get('expires_in', '?')}s):\n")
    print(f"export SWIGGY_TOKEN={token['access_token']}\n")


if __name__ == "__main__":
    main()
