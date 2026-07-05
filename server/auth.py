"""Anthropic account authentication for the desktop app.

Two ways to connect the AI agent to the user's Anthropic account:

  1. "Sign in with Claude" — the OAuth 2.0 + PKCE flow `claude setup-token` uses. We open
     claude.ai's authorize page in the browser; the user approves and copies back a `code#state`
     string; we exchange it for an OAuth token and store it as CLAUDE_CODE_OAUTH_TOKEN (the same var
     the Agent SDK reads). Uses the user's Claude Pro/Max subscription — no per-token API billing.
  2. API key fallback — the user pastes an Anthropic API key (console.anthropic.com); we VERIFY it
     with a live call before saving it as ANTHROPIC_API_KEY.

Secrets go to the BYOK .env (via env_config); non-secret metadata (method, expiry) goes to
settings.json. A live auth failure mid-run (expired/revoked token, rejected key) is recorded here
(mark_auth_error) so the UI can prompt a reconnect instead of failing silently — see status().

The OAuth constants mirror the public Claude Code CLI client (the manual paste-code variant needs no
loopback server or custom URL scheme). Each is env-overridable (OPENNOLAN_OAUTH_*) so a future
endpoint move doesn't require a code change.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from server import env_config, settings

# ── Claude Code OAuth constants (env-overridable) ──────────────────────────────
CLIENT_ID = os.environ.get("OPENNOLAN_OAUTH_CLIENT_ID", "9d1c250a-e61b-44d9-88ed-5944d1962f5e")
AUTHORIZE_URL = os.environ.get("OPENNOLAN_OAUTH_AUTHORIZE_URL", "https://claude.ai/oauth/authorize")
TOKEN_URL = os.environ.get("OPENNOLAN_OAUTH_TOKEN_URL", "https://console.anthropic.com/v1/oauth/token")
REDIRECT_URI = os.environ.get("OPENNOLAN_OAUTH_REDIRECT_URI", "https://console.anthropic.com/oauth/code/callback")
SCOPES = os.environ.get("OPENNOLAN_OAUTH_SCOPES", "org:create_api_key user:profile user:inference")

# API-key verification: cheapest authenticated GET that returns 200 for a valid key, 401 for a bad one.
MODELS_URL = "https://api.anthropic.com/v1/models"
ANTHROPIC_VERSION = "2023-06-01"

# console.anthropic.com (the OAuth token endpoint) sits behind Cloudflare, which 403s the default
# Python-urllib User-Agent — "Error 1010: browser_signature_banned". A plain app identifier passes,
# so we send one on EVERY outbound request (a missing/urllib UA silently breaks the token exchange).
_USER_AGENT = "OpenNolan/0.1.0"

OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"      # read by the Agent SDK
REFRESH_TOKEN_ENV = "CLAUDE_CODE_REFRESH_TOKEN"  # internal; hidden from the BYOK panel
API_KEY_ENV = "ANTHROPIC_API_KEY"

_HTTP_TIMEOUT = 15
_PENDING_TTL = 600          # a started OAuth flow is valid for 10 minutes
_EXPIRY_SKEW = 60           # treat a token as expired this many seconds early
_REFRESH_THROTTLE = 60      # at most one silent refresh attempt per minute

# state -> {"verifier": str, "ts": float}; in-memory (single-user local app).
_pending: dict[str, dict[str, Any]] = {}
_last_refresh_attempt = 0.0


class AuthError(Exception):
    """A user-facing auth failure (bad code, rejected key, network) — surfaced as HTTP 400."""


# ── small helpers ──────────────────────────────────────────────────────────────

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _cli_available() -> bool:
    """Lazily probe for a logged-in `claude` CLI (dev machines auth from it with no env token)."""
    try:
        from server.agent_runner import claude_cli_available
        return claude_cli_available()
    except Exception:
        return False


def _http_json(url: str, method: str = "GET", *, headers: Optional[dict] = None,
               body: Optional[dict] = None) -> tuple[int, dict]:
    """Minimal stdlib JSON HTTP (no extra deps). Returns (status, parsed_body); never raises on a
    non-2xx — only on a transport/network failure (as AuthError)."""
    hdrs = {"Accept": "application/json", "User-Agent": _USER_AGENT, **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"error": raw[:400]}
        return exc.code, payload if isinstance(payload, dict) else {"error": str(payload)[:400]}
    except urllib.error.URLError as exc:
        raise AuthError(f"Couldn’t reach Anthropic ({exc.reason}). Check your connection and try again.") from exc


def _err_message(payload: dict) -> str:
    """Dig a human message out of an Anthropic/OAuth error body."""
    if not isinstance(payload, dict):
        return ""
    err = payload.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("error_description") or "")
    # error_description/error = OAuth-style; detail/title = Cloudflare-style (so a block isn't opaque).
    return str(payload.get("error_description") or err or payload.get("message")
               or payload.get("detail") or payload.get("title") or "")


def _prune_pending() -> None:
    cutoff = time.time() - _PENDING_TTL
    for st in [k for k, v in _pending.items() if v.get("ts", 0) < cutoff]:
        _pending.pop(st, None)


def _reset_runner(app_state: Any) -> None:
    """Drop the cached agent runner so the next chat turn rebuilds with the new credentials."""
    if app_state is not None and getattr(app_state, "agent_runner", None) is not None:
        app_state.agent_runner = None


def _is_expired(expires_at: Optional[str]) -> bool:
    if not expires_at:
        return False  # unknown expiry -> assume valid; a live 401 is caught at runtime instead
    try:
        dt = datetime.fromisoformat(expires_at)
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return _now() >= (dt - timedelta(seconds=_EXPIRY_SKEW))


# ── runtime auth-error flag (so failures surface instead of dying silently) ──────

def mark_auth_error(detail: str) -> None:
    """Record that a live call failed auth, so status() reports needs_reauth until the next success."""
    settings.set_value("claude_auth_error", {"detail": (detail or "")[:400], "ts": _iso(_now())})


def clear_auth_error() -> None:
    if settings.get("claude_auth_error"):
        settings.set_value("claude_auth_error", None)


# Patterns that mean "the credential itself is the problem" (reconnect helps). Deliberately does NOT
# match generic tool/network errors so the agent hitting an unrelated 403 on the web won't nag the
# user to reconnect. 401/403 are matched as standalone tokens only.
_AUTH_CODE_RE = re.compile(r"\b(401|403)\b")
_AUTH_PHRASES = (
    "authentication_error", "authentication error", "invalid api key", "invalid x-api-key",
    "x-api-key header", "oauth token", "token has expired", "token expired", "token is expired",
    "invalid_grant", "invalid_token", "unauthorized", "please run /login", "run `claude`",
    "credit balance is too low", "insufficient credit", "billing",
)


def classify_auth_error(text: Optional[str]) -> bool:
    t = (text or "").lower()
    if _AUTH_CODE_RE.search(t):
        return True
    return any(p in t for p in _AUTH_PHRASES)


# ── status ───────────────────────────────────────────────────────────────────

def status() -> dict:
    """The single source of truth the UI polls. Attempts a silent refresh first when an OAuth token
    is past its clock expiry and we still hold a refresh token."""
    _maybe_refresh()
    oauth = os.environ.get(OAUTH_TOKEN_ENV)
    api_key = os.environ.get(API_KEY_ENV)
    method: Optional[str] = None
    if oauth:
        method = "oauth"
    elif api_key:
        method = "api_key"
    elif _cli_available():
        method = "cli"

    meta = settings.get("claude_auth") or {}
    err = settings.get("claude_auth_error")
    expires_at = meta.get("expires_at") if method == "oauth" else None
    expired = _is_expired(expires_at) if method == "oauth" else False
    authenticated = method is not None
    needs_reauth = authenticated and (bool(err) or expired)
    return {
        "authenticated": authenticated,
        "method": method,
        "needs_reauth": needs_reauth,
        "expired": expired,
        "expires_at": expires_at,
        "obtained_at": meta.get("obtained_at"),
        "error": (err or {}).get("detail") if err else None,
    }


# ── OAuth: "Sign in with Claude" ───────────────────────────────────────────────

def start_oauth() -> dict:
    """Begin a PKCE flow. Returns the authorize URL to open in the browser + the state to match on
    finish. The verifier is held in memory keyed by state."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = _b64url(secrets.token_bytes(32))
    _prune_pending()
    _pending[state] = {"verifier": verifier, "ts": time.time()}
    params = {
        "code": "true",
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return {"authorize_url": f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}", "state": state}


def finish_oauth(code_input: str, app_state: Any = None) -> dict:
    """Exchange the pasted `code#state` for tokens and persist them."""
    raw = (code_input or "").strip()
    if not raw:
        raise AuthError("Paste the code shown on the Claude sign-in page.")
    code, _, state_frag = raw.partition("#")
    code = code.strip()
    state = state_frag.strip()

    pend = _pending.get(state) if state else None
    if pend is None:
        # Tolerate a pasted code with no #state when exactly one flow is in flight.
        if len(_pending) == 1:
            state, pend = next(iter(_pending.items()))
        else:
            raise AuthError("This sign-in link expired. Click “Sign in with Claude” again.")

    code_status, payload = _http_json(TOKEN_URL, "POST", body={
        "grant_type": "authorization_code",
        "code": code,
        "state": state,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "code_verifier": pend["verifier"],
    })
    _pending.pop(state, None)
    if code_status >= 300 or not payload.get("access_token"):
        msg = _err_message(payload)
        raise AuthError(f"Sign-in failed{': ' + msg if msg else '.'} Please try again.")
    _store_oauth(payload)
    clear_auth_error()
    _reset_runner(app_state)
    return status()


def _store_oauth(payload: dict) -> None:
    access = payload["access_token"]
    refresh = payload.get("refresh_token") or ""
    expires_in = payload.get("expires_in")

    updates: dict[str, str] = {OAUTH_TOKEN_ENV: access}
    if refresh:
        updates[REFRESH_TOKEN_ENV] = refresh
    if os.environ.get(API_KEY_ENV):          # OAuth wins — clear a stale key so it can't take precedence
        updates[API_KEY_ENV] = ""
    env_config.write_env_vars(updates)
    env_config.reload_env()

    # reload_env can't UNSET a var — force the live process env so THIS session uses the OAuth token.
    os.environ[OAUTH_TOKEN_ENV] = access
    if refresh:
        os.environ[REFRESH_TOKEN_ENV] = refresh
    os.environ.pop(API_KEY_ENV, None)

    expires_at = None
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        expires_at = _iso(_now() + timedelta(seconds=int(expires_in)))
    settings.set_value("claude_auth", {
        "method": "oauth", "obtained_at": _iso(_now()), "expires_at": expires_at,
    })


def _maybe_refresh() -> None:
    """Best-effort silent refresh when the OAuth access token is past its clock expiry. Throttled;
    a failure leaves needs_reauth set so the user can reconnect manually."""
    global _last_refresh_attempt
    if os.environ.get(API_KEY_ENV):
        return
    refresh = os.environ.get(REFRESH_TOKEN_ENV)
    if not refresh:
        return
    meta = settings.get("claude_auth") or {}
    if not _is_expired(meta.get("expires_at")):
        return
    now = time.time()
    if now - _last_refresh_attempt < _REFRESH_THROTTLE:
        return
    _last_refresh_attempt = now
    try:
        code_status, payload = _http_json(TOKEN_URL, "POST", body={
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": CLIENT_ID,
            "scope": SCOPES,
        })
    except AuthError:
        return
    if code_status < 300 and payload.get("access_token"):
        _store_oauth(payload)
        clear_auth_error()


# ── API key fallback ───────────────────────────────────────────────────────────

def set_api_key(key: str, app_state: Any = None) -> dict:
    key = (key or "").strip()
    if not key:
        raise AuthError("Enter your Anthropic API key.")
    ok, detail = _validate_api_key(key)
    if not ok:
        raise AuthError(detail)

    updates: dict[str, str] = {API_KEY_ENV: key}
    if os.environ.get(OAUTH_TOKEN_ENV):
        updates[OAUTH_TOKEN_ENV] = ""
    if os.environ.get(REFRESH_TOKEN_ENV):
        updates[REFRESH_TOKEN_ENV] = ""
    env_config.write_env_vars(updates)
    env_config.reload_env()

    os.environ[API_KEY_ENV] = key
    os.environ.pop(OAUTH_TOKEN_ENV, None)
    os.environ.pop(REFRESH_TOKEN_ENV, None)

    settings.set_value("claude_auth", {"method": "api_key", "obtained_at": _iso(_now()), "expires_at": None})
    clear_auth_error()
    _reset_runner(app_state)
    return status()


def _validate_api_key(key: str) -> tuple[bool, str]:
    try:
        code_status, payload = _http_json(
            MODELS_URL, "GET",
            headers={"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION},
        )
    except AuthError as exc:
        return False, str(exc)
    if code_status == 200:
        return True, ""
    if code_status in (401, 403):
        return False, "Anthropic rejected that API key. Double-check it (from console.anthropic.com) and try again."
    msg = _err_message(payload)
    return False, f"Couldn’t verify the key (HTTP {code_status}){'. ' + msg if msg else '.'}"


# ── disconnect ─────────────────────────────────────────────────────────────────

def disconnect(app_state: Any = None) -> dict:
    """Forget the stored Anthropic credential (leaves a logged-in `claude` CLI, if any, intact)."""
    updates: dict[str, str] = {}
    for k in (OAUTH_TOKEN_ENV, REFRESH_TOKEN_ENV, API_KEY_ENV):
        if os.environ.get(k):
            updates[k] = ""
        os.environ.pop(k, None)
    if updates:
        env_config.write_env_vars(updates)
    settings.set_value("claude_auth", None)
    clear_auth_error()
    _reset_runner(app_state)
    return status()
