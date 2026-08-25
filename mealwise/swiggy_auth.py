"""OAuth 2.1 + PKCE login for Swiggy MCP.

Swiggy issues no static API keys: every caller registers itself via Dynamic
Client Registration, then runs the authorization-code flow with PKCE. Consent
happens in a browser (phone + OTP), so first login is always interactive.

Refresh tokens are not wired in v1.0, so the 5-day access token IS the session:
on 401 we simply re-run the whole flow.
"""

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

DISCOVERY_URL = "https://mcp.swiggy.com/.well-known/oauth-authorization-server"
SCOPES = "mcp:tools mcp:resources"
REDIRECT_HOST = "127.0.0.1"
REDIRECT_PORT = 8765
REDIRECT_URI = "http://%s:%d/callback" % (REDIRECT_HOST, REDIRECT_PORT)

# A token is a live bearer credential for a real, payment-capable account.
# None of it may live beside the source: this project is shared, and
# .gitignore does nothing for a zip, an AirDrop or a shared drive. Everything
# personal goes under the operating-system user's own home directory, where
# the kernel keeps one user's data away from another's.
#
#   ~/.swiggy_mcp/                 0700
#     current                      user id of the active account
#     sessions/<user_id>.json      0600, one per Swiggy account
#     prefs/<user_id>.json         0600, UI preferences (last address used)
#     probe/                       diagnostic dumps (addresses, orders)
#
# Sessions are kept per account so two people alternating on one machine do
# not evict each other and re-do OTP on every switch. This is safe *because*
# each person has their own OS login; it would not be on a shared one.
TOKEN_DIR = os.environ.get("SWIGGY_MCP_HOME") or os.path.join(
    os.path.expanduser("~"), ".swiggy_mcp")
SESSIONS_DIR = os.path.join(TOKEN_DIR, "sessions")
CURRENT_FILE = os.path.join(TOKEN_DIR, "current")
PROBE_DIR = os.path.join(TOKEN_DIR, "probe")
PREFS_DIR = os.path.join(TOKEN_DIR, "prefs")

_LEGACY_SINGLE_FILE = os.path.join(TOKEN_DIR, "token.json")
# Written by a much older version that kept the token beside the code. The
# modules moved into mealwise/ since, so this points a directory up - at the
# project root, where such a file would actually be.
_LEGACY_REPO_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".swiggy_token.json")


class AuthError(Exception):
    pass


def _post_json(url, payload, form=False):
    if form:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        content_type = "application/x-www-form-urlencoded"
    else:
        data = json.dumps(payload).encode("utf-8")
        content_type = "application/json"
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": content_type, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise AuthError("HTTP %s from %s: %s" % (exc.code, url, detail[:400]))
    except urllib.error.URLError as exc:
        raise AuthError("cannot reach %s: %s" % (url, exc.reason))


def discover():
    with urllib.request.urlopen(DISCOVERY_URL, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def register_client(meta, client_name="swiggy-mcp-budget", redirect_uris=None):
    return _post_json(
        meta["registration_endpoint"],
        {
            "client_name": client_name,
            "redirect_uris": redirect_uris or
                [REDIRECT_URI, "http://localhost:%d/callback" % REDIRECT_PORT],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": SCOPES,
        },
    )


def make_pkce():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


class _CallbackHandler(BaseHTTPRequestHandler):
    captured = {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/callback"):
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        _CallbackHandler.captured = {k: v[0] for k, v in params.items()}
        ok = "code" in _CallbackHandler.captured
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = "Swiggy login complete - you can close this tab." if ok else \
              "Swiggy login failed: %s" % _CallbackHandler.captured.get("error", "no code returned")
        self.wfile.write(("<html><body style='font:16px system-ui;padding:3rem'>%s</body></html>" % msg).encode())

    def log_message(self, *args):
        pass  # keep the console clean for the actual flow output


def authorize_interactive(meta, client_id, wait_seconds=300):
    """Desktop-only login: open a browser, catch the redirect on localhost.

    NOTE - this is the one function that must be replaced once Swiggy
    whitelists this integration for production. Production redirect URIs must
    be HTTPS and exact-match; 127.0.0.1 is accepted for local development
    only. A hosted deployment also cannot block waiting for the redirect, and
    must persist the PKCE verifier between two separate requests.

    Everything else in this module (discover, register_client, make_pkce,
    exchange_code) is protocol, not interaction, and carries over unchanged.
    See section 9 of docs/instamart-notes.md before changing this.
    """
    verifier, challenge = make_pkce()
    state = secrets.token_urlsafe(16)
    query = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    auth_url = "%s?%s" % (meta["authorization_endpoint"], query)

    _CallbackHandler.captured = {}
    server = HTTPServer((REDIRECT_HOST, REDIRECT_PORT), _CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print("\nOpen this URL and sign in with your Swiggy phone number + OTP:\n")
    print(auth_url)
    print("\nWaiting up to %ds for the redirect back to %s ..." % (wait_seconds, REDIRECT_URI))
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    deadline = time.time() + wait_seconds
    while time.time() < deadline and "code" not in _CallbackHandler.captured:
        if _CallbackHandler.captured.get("error"):
            break
        time.sleep(0.5)
    server.shutdown()

    captured = _CallbackHandler.captured
    if captured.get("error"):
        raise AuthError("authorization denied: %s %s" % (
            captured.get("error"), captured.get("error_description", "")))
    if "code" not in captured:
        raise AuthError("timed out waiting for the browser redirect")
    if captured.get("state") != state:
        raise AuthError("state mismatch - possible CSRF, aborting")
    return captured["code"], verifier


def exchange_code(meta, client_id, code, verifier, redirect_uri=None):
    return _post_json(
        meta["token_endpoint"],
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri or REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        form=True,
    )


def _ensure_dirs():
    for path in (TOKEN_DIR, SESSIONS_DIR, PROBE_DIR, PREFS_DIR):
        if not os.path.isdir(path):
            os.makedirs(path, 0o700)
        os.chmod(path, 0o700)


def probe_dir():
    """Where diagnostic dumps go - never the project folder, they hold PII."""
    _ensure_dirs()
    return PROBE_DIR


def _session_path(user_id):
    safe = "".join(ch for ch in str(user_id) if ch.isalnum() or ch in "-_")
    return os.path.join(SESSIONS_DIR, "%s.json" % (safe or "unknown"))


def _read_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (ValueError, OSError):
        return None


def _migrate():
    """Fold older layouts into ~/.swiggy_mcp/sessions/<user_id>.json."""
    for legacy, note in ((_LEGACY_REPO_FILE, "out of the project folder"),
                         (_LEGACY_SINGLE_FILE, "into per-account storage")):
        if not os.path.exists(legacy):
            continue
        record = _read_json(legacy)
        if not record or not record.get("user_id"):
            continue
        _ensure_dirs()
        target = _session_path(record["user_id"])
        if not os.path.exists(target):
            with open(target, "w") as fh:
                json.dump(record, fh, indent=2)
            os.chmod(target, 0o600)
            _set_current(record["user_id"])
            print("Moved your session %s (account %s)" % (note, record["user_id"]))
        try:
            os.remove(legacy)          # move, never leave a copy behind
        except OSError:
            print("Could not remove %s - delete it by hand, it is a live "
                  "credential." % legacy)


def _set_current(user_id):
    _ensure_dirs()
    with open(CURRENT_FILE, "w") as fh:
        fh.write(str(user_id))
    os.chmod(CURRENT_FILE, 0o600)


def _get_current():
    try:
        with open(CURRENT_FILE) as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def _save(record):
    _ensure_dirs()
    path = _session_path(record.get("user_id"))
    with open(path, "w") as fh:
        json.dump(record, fh, indent=2)
    os.chmod(path, 0o600)
    _set_current(record.get("user_id"))


def _load(user_id=None):
    _migrate()
    uid = user_id or _get_current()
    if not uid:
        return None
    return _read_json(_session_path(uid))


def list_sessions():
    """[(user_id, seconds_remaining)] for every account signed in on this machine."""
    _migrate()
    out = []
    if not os.path.isdir(SESSIONS_DIR):
        return out
    for name in sorted(os.listdir(SESSIONS_DIR)):
        if not name.endswith(".json"):
            continue
        record = _read_json(os.path.join(SESSIONS_DIR, name)) or {}
        if record.get("user_id"):
            out.append((str(record["user_id"]),
                        int(record.get("expires_at", 0) - time.time())))
    return out


def use_account(user_id):
    """Switch to an already signed-in account without re-doing OTP."""
    record = _load(user_id)
    if not record or record.get("expires_at", 0) <= time.time():
        return False
    _set_current(user_id)
    return True


def logout(user_id=None, all_accounts=False):
    """Forget a stored session. Next run authenticates from scratch."""
    _migrate()
    targets = []
    if all_accounts:
        targets = [uid for uid, _ in list_sessions()]
    else:
        uid = user_id or _get_current()
        if uid:
            targets = [uid]

    removed = 0
    for uid in targets:
        path = _session_path(uid)
        if os.path.exists(path):
            os.remove(path)
            removed += 1
    current = _get_current()
    if current in targets and os.path.exists(CURRENT_FILE):
        os.remove(CURRENT_FILE)
    print("Signed out of %d account(s)." % removed if removed
          else "No cached session to sign out of.")
    return removed > 0


def cached_user_id():
    """Swiggy account id of the active session, or None."""
    cached = _load()
    return (cached or {}).get("user_id")


def store_token(token, client_id):
    """Persist a token response as the active session; returns the record."""
    record = dict(token)
    record["client_id"] = client_id
    record["obtained_at"] = time.time()
    record["expires_at"] = time.time() + float(token.get("expires_in", 5 * 24 * 3600))
    _save(record)
    return record


# ---------- split (non-blocking) authorization, for a server front end ----------
#
# authorize_interactive() cannot be reused by an HTTP front end: it blocks for
# up to 300s waiting on a redirect that its OWN process must serve, so calling
# it from inside a request handler deadlocks the handler against itself.
#
# So the flow splits in two - begin_login() builds the URL and hands back the
# PKCE verifier, finish_login() completes the exchange when Swiggy redirects
# back. This is the shape section 9 of docs/instamart-notes.md says production
# access will force (two requests, a persisted verifier, no print()); the only
# development-mode part left is that the redirect URI is still a loopback one.
# Nothing below is new protocol - it reuses discover/register_client/make_pkce/
# exchange_code, which are already verified against the live server.


def begin_login(redirect_uri, client_name="swiggy-mcp-budget"):
    """Register, build the authorize URL, and return the state to carry over.

    The caller MUST keep the returned dict (keyed by its "state") until Swiggy
    redirects back, and MUST compare the returned state before exchanging -
    that comparison is the only thing standing between this and CSRF.
    """
    meta = discover()
    registration = register_client(meta, client_name, redirect_uris=[redirect_uri])
    client_id = registration.get("client_id")
    if not client_id:
        raise AuthError("registration returned no client_id: %s" % registration)

    verifier, challenge = make_pkce()
    state = secrets.token_urlsafe(16)
    query = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return {
        "auth_url": "%s?%s" % (meta["authorization_endpoint"], query),
        "state": state,
        "verifier": verifier,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "meta": meta,
        "started_at": time.time(),
    }


def finish_login(pending, code):
    """Exchange the code from the redirect and store the session."""
    token = exchange_code(pending["meta"], pending["client_id"], code,
                          pending["verifier"], redirect_uri=pending["redirect_uri"])
    if not token.get("access_token"):
        raise AuthError("token endpoint returned no access_token: %s" % token)
    return store_token(token, pending["client_id"])


def cached_token(user_id=None):
    """The stored access token if it is still comfortably valid, else None."""
    record = _load(user_id)
    if not record or not record.get("access_token"):
        return None
    if record.get("expires_at", 0) - 120 <= time.time():
        return None
    return record["access_token"]


# ---------- per-account UI preferences ----------
#
# Which address someone last delivered to is personal data, so it lives beside
# the session under the user's own home directory - never in the project
# folder, which gets cloned, zipped and shared.

def _prefs_path(user_id):
    safe = "".join(ch for ch in str(user_id) if ch.isalnum() or ch in "-_")
    return os.path.join(PREFS_DIR, "%s.json" % (safe or "unknown"))


def load_prefs(user_id):
    if not user_id:
        return {}
    return _read_json(_prefs_path(user_id)) or {}


def save_prefs(user_id, prefs):
    if not user_id:
        return
    _ensure_dirs()
    path = _prefs_path(user_id)
    with open(path, "w") as fh:
        json.dump(prefs, fh, indent=2)
    os.chmod(path, 0o600)


def login(force=False, expect_user_id=None):
    """Return a usable access token, reusing the cached one when still valid.

    `expect_user_id` guards against operating on the wrong account. The cache
    is keyed by nothing but the file path, so after signing in with a second
    phone number the old token would otherwise be reused silently - and an
    ordering app would deliver to the previous account's address.
    """
    if not force:
        cached = _load()
        if cached and cached.get("access_token"):
            same_user = (expect_user_id is None
                         or str(cached.get("user_id")) == str(expect_user_id))
            if not same_user:
                print("Cached token belongs to account %s, wanted %s - re-authorizing."
                      % (cached.get("user_id"), expect_user_id))
            elif cached.get("expires_at", 0) - 120 > time.time():
                left = int(cached["expires_at"] - time.time())
                print("Reusing cached token for account %s (%dh %dm left)"
                      % (cached.get("user_id"), left // 3600, (left % 3600) // 60))
                return cached["access_token"]
            else:
                print("Cached token expired - re-running authorization.")

    meta = discover()
    print("Issuer: %s" % meta.get("issuer"))
    registration = register_client(meta)
    client_id = registration.get("client_id")
    if not client_id:
        raise AuthError("registration returned no client_id: %s" % registration)
    print("Registered client_id: %s" % client_id)

    code, verifier = authorize_interactive(meta, client_id)
    print("Got authorization code, exchanging for a token ...")
    token = exchange_code(meta, client_id, code, verifier)
    if not token.get("access_token"):
        raise AuthError("token endpoint returned no access_token: %s" % token)

    record = store_token(token, client_id)
    print("Signed in as account %s; session stored in %s"
          % (record.get("user_id"), _session_path(record.get("user_id"))))
    return record["access_token"]


if __name__ == "__main__":
    import sys
    if "--logout" in sys.argv:
        logout(all_accounts="--all" in sys.argv)
    elif "--accounts" in sys.argv:
        for uid, left in list_sessions():
            state = "expired" if left <= 0 else "%dh %dm left" % (left // 3600, (left % 3600) // 60)
            print("  %s%s  (%s)" % (uid, "  <- active" if str(uid) == str(cached_user_id()) else "", state))
    elif "--whoami" in sys.argv:
        uid = cached_user_id()
        print("Signed in as Swiggy account %s" % uid if uid else "Not signed in.")
    else:
        login(force="--force" in sys.argv)
        print("Signed in as Swiggy account %s" % cached_user_id())
