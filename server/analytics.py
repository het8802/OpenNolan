"""Product analytics + error tracking via PostHog — for the packaged desktop app.

Design contract (from docs/plans/publish-mac-app.md, must-fix gaps):
  * OPT-OUT IS HONORED AT INIT. If the user opted out, we do NOT construct a PostHog client
    at all — not "construct then drop events." No client => nothing can leak, ever.
  * GRACEFUL: a missing `posthog` package, a missing key, or an init error degrades to a silent
    no-op. Analytics must never break the backend.
  * ANONYMOUS: identity is a stable per-install device id (server.settings.device_id()), NO PII.
  * SCRUBBED: event properties are stripped of absolute file paths, secret-looking values, and
    free-text prompt bodies before send (a local creative app's props are full of these).
  * NEVER during tests: if pytest is loaded, analytics is hard-disabled regardless of settings.

    is_enabled() gates everything:
        opted-out? ─┐
        no posthog? ├─► disabled → capture()/capture_exception() are no-ops
        under test? │
        no key?    ─┘
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

from server import settings

# The project's PUBLIC ingestion key (write-only, safe to embed in a client, like any web SDK key).
# Overridable via env for self-hosting / a separate prod project.
_DEFAULT_KEY = "phc_s9P9JiTbBgmzqYGwug8ciiLnWsCSJF62Vz5UGRJsPGBE"
_DEFAULT_HOST = "https://us.i.posthog.com"

# Redact values that look like an absolute POSIX path or a secret; drop free-text bodies entirely.
_PATH_RE = re.compile(r"(/Users/|/home/|/var/|/private/|/tmp/)[^\s]*")
_SECRET_HINT = ("key", "token", "secret", "password", "authorization", "cookie")
_FREETEXT_KEYS = ("prompt", "message", "text", "transcript", "caption", "content", "body")

_client: Optional[Any] = None
_initialized = False


def _under_pytest() -> bool:
    return "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ


_INTERNAL_SENTINEL = ".opennolan-internal"


def _is_internal() -> bool:
    """True on the developer's OWN machines, so their events can be filtered out of product analytics.
    `env` (packaged vs dev) can't do this: the developer running the downloaded .app looks identical to
    a real user. Marked two ways, either wins:
      * OPENNOLAN_INTERNAL set truthy — easy in a dev shell.
      * a sentinel file ~/.opennolan-internal — works for the Finder-launched packaged app (no env to
        set); set once with `touch ~/.opennolan-internal`, survives reinstalls (lives in the home dir,
        not app data)."""
    val = os.environ.get("OPENNOLAN_INTERNAL", "").strip().lower()
    if val and val not in ("0", "false", "no"):
        return True
    try:
        return (Path.home() / _INTERNAL_SENTINEL).exists()
    except Exception:
        return False


def _env_props() -> dict[str, Any]:
    """Base properties attached to EVERY event: which build fired it (`env`) and whether it came from
    an internal/developer machine (`internal`). This is what lets the dashboards separate real users
    from our own use — filter `internal != true`."""
    from lib import app_paths

    return {
        "env": "packaged" if app_paths.is_packaged() else "dev",
        "internal": _is_internal(),
    }


def is_enabled() -> bool:
    """True only when analytics may run: not opted out, not under test, key + package present."""
    if _under_pytest():
        return False
    if settings.get("analytics_disabled", False):
        return False
    if not (os.environ.get("POSTHOG_KEY") or _DEFAULT_KEY):
        return False
    try:
        import posthog  # noqa: F401
    except Exception:
        return False
    return True


def _redact_paths(obj: Any) -> Any:
    """Recursively path-redact every string in a nested structure. Used by _before_send to scrub the
    SDK-built exception frames (abs_path/filename/value), which embed the OS username and which _scrub
    never sees (the SDK adds `$exception_list` AFTER our properties are scrubbed)."""
    if isinstance(obj, str):
        return _PATH_RE.sub("[path]", obj)
    if isinstance(obj, list):
        return [_redact_paths(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _redact_paths(v) for k, v in obj.items()}
    return obj


def _before_send(event: Any) -> Any:
    """Last gate before an event leaves the machine: strip absolute paths from event properties,
    closing the PII leak in SDK-serialized `$exception_list` stack frames (each frame's abs_path is
    /Users/<username>/… — the module's 'NO PII / paths stripped' contract). MUST NOT raise: the SDK
    falls back to the UN-redacted event if this throws, so any failure re-opens the leak."""
    try:
        if isinstance(event, dict) and isinstance(event.get("properties"), dict):
            event["properties"] = _redact_paths(event["properties"])
    except Exception:
        pass
    return event


def _get_client() -> Optional[Any]:
    """Lazily construct the PostHog client the FIRST time it's needed — and ONLY if enabled.
    Returns None (and stays None) whenever analytics is disabled/unavailable."""
    global _client, _initialized
    if _initialized:
        return _client
    _initialized = True
    if not is_enabled():
        _client = None  # opt-out honored AT INIT: no client is ever built
        return None
    try:
        from posthog import Posthog

        _client = Posthog(
            project_api_key=os.environ.get("POSTHOG_KEY", _DEFAULT_KEY),
            host=os.environ.get("POSTHOG_HOST", _DEFAULT_HOST),
            enable_exception_autocapture=True,
            before_send=_before_send,
        )
    except Exception:
        _client = None  # any init failure -> silent no-op
    return _client


def _scrub(props: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Strip PII/secrets from event properties before they leave the machine."""
    if not props:
        return {}
    clean: dict[str, Any] = {}
    for k, v in props.items():
        kl = str(k).lower()
        if any(h in kl for h in _SECRET_HINT):
            clean[k] = "[redacted]"
            continue
        if any(t in kl for t in _FREETEXT_KEYS):
            # keep only a length signal, never the body
            clean[f"{k}_len"] = len(v) if isinstance(v, (str, bytes, list, dict)) else None
            continue
        if isinstance(v, str):
            clean[k] = _PATH_RE.sub("[path]", v)
        else:
            clean[k] = v
    return clean


def capture(event: str, properties: Optional[dict[str, Any]] = None) -> None:
    """Record a product event (no-op when disabled). Identity = anonymous device id."""
    client = _get_client()
    if client is None:
        return
    try:
        props = _scrub(properties)
        props.update(_env_props())
        client.capture(distinct_id=settings.device_id(), event=event, properties=props)
    except Exception:
        pass  # analytics never raises into the caller


def capture_exception(exc: BaseException, properties: Optional[dict[str, Any]] = None) -> None:
    """Record a backend exception (no-op when disabled)."""
    client = _get_client()
    if client is None:
        return
    try:
        props = _scrub(properties)
        props.update(_env_props())
        client.capture_exception(exc, distinct_id=settings.device_id(), properties=props)
    except Exception:
        pass


class _ClientError(Exception):
    """A JS (React) or Electron error re-homed into PostHog Error Tracking next to backend
    exceptions, so there's ONE crash inbox. ponytail: the Python traceback is this one-frame shim;
    the REAL client stack rides along in the `client_stack` property."""


def capture_client_error(
    source: str,
    message: str,
    stack: Optional[str] = None,
    context: Optional[dict[str, Any]] = None,
) -> None:
    """Report a frontend/Electron error to PostHog (no-op when disabled). Paths are redacted from
    the message + stack; free-text context values are scrubbed like any event props."""
    client = _get_client()
    if client is None:
        return
    props = _scrub(dict(context or {}))
    props.update(_env_props())
    props["source"] = str(source)[:80]
    props["platform"] = "client"
    if stack:
        props["client_stack"] = _PATH_RE.sub("[path]", str(stack))[:8000]
    safe = _PATH_RE.sub("[path]", str(message))[:300]
    try:
        # Raise + catch so the exception carries a valid (if shim-only) traceback for the SDK.
        raise _ClientError(f"[{source}] {safe}")
    except _ClientError as exc:
        try:
            client.capture_exception(exc, distinct_id=settings.device_id(), properties=props)
        except Exception:
            pass


def shutdown() -> None:
    """Flush queued events on backend shutdown (no-op when disabled)."""
    client = _get_client()
    if client is None:
        return
    try:
        client.shutdown()
    except Exception:
        pass


def reset() -> None:
    """Drop the memoized client so is_enabled() is re-evaluated under new settings/env. Called when
    the user flips the opt-out (so it takes effect immediately) and by tests."""
    global _client, _initialized
    _client = None
    _initialized = False
