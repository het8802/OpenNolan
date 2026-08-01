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

import base64
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
# app ships no key. This is the delivery path for the distributed app. FEEDBACK_RELAY_URL overrides it.
# Points at the canonical www host directly (not the apex) so a POST isn't bounced through a redirect.
_DEFAULT_RELAY_URL = "https://www.opennolan.com/api/feedback"


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


def _debug_attachment(session: Optional[str]) -> Optional[dict[str, str]]:
    """Build a Resend email attachment {filename, content(base64)} from a recorded session's raw
    NDJSON log, so the developer gets the log as a FILE (not pasted into the email body). Best-effort:
    a missing/bad/unreadable session returns None — the feedback must still send. Never raises."""
    if not session:
        return None
    try:
        from server import debug_log  # local import: only the debug-report path pays for it

        raw = debug_log.read_session_bytes(session)
        return {
            "filename": f"debug-session-{session}.ndjson",
            "content": base64.b64encode(raw).decode("ascii"),
        }
    except Exception:  # the attachment is diagnostic, never load-bearing — don't lose the feedback
        return None


def _append_record(record: dict[str, Any]) -> None:
    """Append one JSON line. O_APPEND makes a single small line write atomic on local fs, so a
    concurrent reader never sees a torn line."""
    path = _feedback_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _maybe_email(
    kind: str, message: str, email: Optional[str], diagnostics: Optional[str],
    attachments: Optional[list[dict[str, str]]] = None,
) -> bool:
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
                **({"attachments": attachments} if attachments else {}),
            },
            timeout=15,
        )
        return resp.status_code < 300
    except requests.RequestException:
        return False


def _maybe_relay(
    kind: str, message: str, email: Optional[str], diagnostics: Optional[str],
    attachments: Optional[list[dict[str, str]]] = None,
) -> bool:
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
    payload: dict[str, Any] = {"kind": kind, "message": message, "email": email, "diagnostics": diagnostics}
    if attachments:
        payload["attachments"] = attachments
    try:
        resp = requests.post(
            url, headers=headers,
            json=payload,
            timeout=15,
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
    debug_session: Optional[str] = None,
) -> dict[str, Any]:
    """Validate + record + report a feedback submission. Raises FeedbackError on bad input.

    When `debug_session` is given (the editor's "Send debug report" flow), that recorded session's raw
    NDJSON log is emailed to the developer as a FILE ATTACHMENT (not pasted into the body)."""
    _validate(kind, message)
    attachment = _debug_attachment(debug_session)
    attachments = [attachment] if attachment else None
    record = {
        "ts": _now_iso(),
        "kind": kind,
        "message": message,
        "email": email or None,
        "diagnostics": diagnostics or None,
        "debug_session": debug_session or None,
    }
    _append_record(record)  # durable first — never lose feedback (the raw log also stays on disk)
    # Analytics gets metadata only; _scrub turns "message" into "message_len" and never sends the body.
    analytics.capture(
        "feedback_submitted",
        {
            "kind": kind, "message": message, "has_email": bool(email),
            "has_diagnostics": bool(diagnostics), "has_debug_session": bool(debug_session),
            "has_attachment": bool(attachment),
        },
    )
    # Public relay first (the distributed-app path, no shipped secret); direct Resend is the
    # dev / self-host fallback when a local RESEND_API_KEY is configured instead.
    emailed = (
        _maybe_relay(kind, message, email, diagnostics, attachments)
        or _maybe_email(kind, message, email, diagnostics, attachments)
    )
    return {"stored": True, "emailed": emailed}
