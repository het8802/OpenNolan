"""In-app feedback / bug reports (publish-plan ask #3).

A submission always does three things, in resilience order:
  1. Append a durable local record to <app_paths.home()>/feedback.jsonl — so feedback is NEVER
     lost, even with no network and no Resend key.
  2. Emit a PostHog `feedback_submitted` event (metadata only: kind, message length, has-email —
     the body is scrubbed out by analytics._scrub, never sent to analytics).
  3. Best-effort email via Resend when RESEND_API_KEY + FEEDBACK_TO are configured.

The full message body goes ONLY to the local record and the email (where the user intends it to
go), never to analytics.

    submit() ─► append jsonl (durable) ─► capture event (metadata) ─► maybe email (Resend)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from lib import app_paths
from server import analytics

VALID_KINDS = {"bug", "feature", "other"}
MAX_MESSAGE_CHARS = 5000
_RESEND_ENDPOINT = "https://api.resend.com/emails"

# Public feedback relay (website /api/feedback) — holds the Resend secret SERVER-side, so the packaged
# app ships no key. This is the delivery path for the distributed app. Empty until the endpoint is
# deployed; then set FEEDBACK_RELAY_URL in the packaged env (or fill this default with the public URL).
_DEFAULT_RELAY_URL = ""


class FeedbackError(ValueError):
    """Invalid submission (bad kind / empty / too-long message) — surfaced as HTTP 400."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _feedback_path():
    return app_paths.home() / "feedback.jsonl"


def _validate(kind: str, message: str) -> None:
    if kind not in VALID_KINDS:
        raise FeedbackError(f"kind must be one of {sorted(VALID_KINDS)}, got {kind!r}")
    if not message or not message.strip():
        raise FeedbackError("message is required")
    if len(message) > MAX_MESSAGE_CHARS:
        raise FeedbackError(f"message too long (max {MAX_MESSAGE_CHARS} chars)")


def _append_record(record: dict[str, Any]) -> None:
    """Append one JSON line. O_APPEND makes a single small line write atomic on local fs, so a
    concurrent reader never sees a torn line."""
    path = _feedback_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _maybe_email(kind: str, message: str, email: Optional[str], diagnostics: Optional[str]) -> bool:
    """Send the feedback email via Resend if configured. Returns True iff an email was sent.
    Never raises into the caller — a mail failure must not fail the whole submission."""
    api_key = os.environ.get("RESEND_API_KEY")
    to_addr = os.environ.get("FEEDBACK_TO")
    if not api_key or not to_addr:
        return False
    from_addr = os.environ.get("FEEDBACK_FROM", "OpenNolan <onboarding@resend.dev>")
    reply = f"\n\nReply-to: {email}" if email else ""
    diag = f"\n\n--- diagnostics ---\n{diagnostics}" if diagnostics else ""
    body = f"[{kind}] feedback from the OpenNolan app\n\n{message}{reply}{diag}"
    try:
        resp = requests.post(
            _RESEND_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": from_addr,
                "to": [to_addr],
                "subject": f"OpenNolan {kind}: {message.strip().splitlines()[0][:60]}",
                "text": body,
                **({"reply_to": email} if email else {}),
            },
            timeout=10,
        )
        return resp.status_code < 300
    except requests.RequestException:
        return False


def _maybe_relay(kind: str, message: str, email: Optional[str], diagnostics: Optional[str]) -> bool:
    """POST feedback to the developer's public relay (website /api/feedback), which sends the email
    server-side with ITS OWN Resend key — so the distributed app ships no secret. Returns True iff the
    relay accepted and emailed. Best-effort: never raises into the caller (feedback is already stored)."""
    url = os.environ.get("FEEDBACK_RELAY_URL", _DEFAULT_RELAY_URL)
    if not url:
        return False
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("FEEDBACK_RELAY_TOKEN")
    if token:
        headers["X-Feedback-Token"] = token
    try:
        resp = requests.post(
            url, headers=headers,
            json={"kind": kind, "message": message, "email": email, "diagnostics": diagnostics},
            timeout=10,
        )
        if resp.status_code >= 300:
            return False
        try:
            return bool(resp.json().get("emailed"))
        except ValueError:
            return True  # 2xx with a non-JSON body — treat as delivered
    except requests.RequestException:
        return False


def submit(
    kind: str,
    message: str,
    email: Optional[str] = None,
    diagnostics: Optional[str] = None,
) -> dict[str, Any]:
    """Validate + record + report a feedback submission. Raises FeedbackError on bad input."""
    _validate(kind, message)
    record = {
        "ts": _now_iso(),
        "kind": kind,
        "message": message,
        "email": email or None,
        "diagnostics": diagnostics or None,
    }
    _append_record(record)  # durable first — never lose feedback
    # Analytics gets metadata only; _scrub turns "message" into "message_len" and never sends the body.
    analytics.capture(
        "feedback_submitted",
        {"kind": kind, "message": message, "has_email": bool(email), "has_diagnostics": bool(diagnostics)},
    )
    # Public relay first (the distributed-app path, no shipped secret); direct Resend is the
    # dev / self-host fallback when a local RESEND_API_KEY is configured instead.
    emailed = _maybe_relay(kind, message, email, diagnostics) or _maybe_email(kind, message, email, diagnostics)
    return {"stored": True, "emailed": emailed}
