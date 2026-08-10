"""Receive assets from a phone (or any device with a browser) over the local network.

WHY THIS EXISTS AND NOT AIRDROP
    macOS gives an app no way to *receive* an AirDrop. Receiving is Finder's job, the files
    always land in ~/Downloads, and the only public API (NSSharingServiceNameSendViaAirDrop)
    sends. So the closest thing to "airdrop straight into the project" is: the Mac shows a QR
    code, the phone opens it, and the phone POSTs the file into the project. Same outcome
    (pick on phone -> file in project), no Apple API, and it works from Android too.

WHY A SEPARATE HTTP SERVER AND NOT A ROUTE ON THE MAIN APP
    The main API binds 127.0.0.1 and serves /api/env (the user's BYOK keys), the agent chat
    and the whole project tree. Re-binding *that* to 0.0.0.0 so a phone could reach it would
    hand the machine to everyone on the cafe wifi. This server binds 0.0.0.0 but answers
    exactly two paths, both behind an unguessable token, and it shuts itself off after
    TTL_SECONDS. One session at a time, in-memory only, nothing survives a restart.

WHY RAW BODIES AND NOT MULTIPART
    We own both ends, so the phone page POSTs the file as the raw request body with the name
    in the query string. That skips multipart parsing entirely (the `cgi` module is gone in
    3.13) and streams to disk in chunks, so a 2 GB phone video never sits in memory.
"""

from __future__ import annotations

import html
import json
import os
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

from lib.project import sanitize_filename

TTL_SECONDS = 15 * 60
MAX_BYTES = 4 * 1024 * 1024 * 1024  # one 4 GiB phone video is already absurd
_CHUNK = 1 << 20

# Budgets for ONE window. A phone sending a shoot is a handful of clips; anything past these
# is either a bug or someone who got hold of the link, and neither should be able to fill the
# user's disk. Enforced across the whole session, not per request.
MAX_SESSION_BYTES = 16 * 1024 * 1024 * 1024
MAX_SESSION_FILES = 200

# ThreadingHTTPServer spawns one thread per ACCEPTED connection, before any token is checked,
# so without a cap a stranger on the wifi gets a thread per socket for free (measured on this
# app: 200 half-open connections took the backend from 12 threads to 212). One phone needs one
# connection; 24 is generous for a browser that opens a couple plus retries.
_MAX_CONNECTIONS = 24
# And a thread that IS admitted must not park forever. Applies per socket read, so a slow but
# progressing upload is fine while a silent socket dies.
_SOCKET_TIMEOUT_S = 30


class LanUnavailable(RuntimeError):
    """This Mac has no usable LAN address, so no phone could reach us anyway."""


@dataclass
class _Session:
    # `id` names the WINDOW so a caller can close the one it opened and nobody else's; `token`
    # is the capability the phone holds. Two ids on purpose — a client that had to hand back the
    # token to close a window would be carrying the LAN credential around for no reason.
    id: str
    token: str
    project_id: str
    projects_dir: Path
    expires_at: float
    # Analytics is passed IN rather than imported: server.app imports this module, and the
    # ingest hooks it owns (_capture_asset_ingest / _asset_failed) live over there.
    on_saved: Optional[Callable[[str, Path, str, str], None]] = None
    on_failed: Optional[Callable[[str, str], None]] = None  # (kind, failure_class)
    on_closed: Optional[Callable[[dict[str, Any]], None]] = None  # one rollup per window
    opened_at: float = 0.0
    url: str = ""
    origin: str = ""  # scheme://host:port we advertised — the only Host/Origin we answer to
    received: list[dict[str, Any]] = field(default_factory=list)
    bytes_written: int = 0
    httpd: Any = None
    # Set by stop(). shutdown() only stops ACCEPTING; a transfer already in flight keeps its
    # thread, so an upload started a moment before Done would otherwise stream on unchecked.
    cancelled: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def dead(self) -> bool:
        return self.cancelled.is_set() or time.time() >= self.expires_at


# `_lifecycle_lock` serializes the whole of start(): read-current, bind, publish. `_state_lock`
# only guards the module globals and is taken briefly inside. Two locks because start() binds a
# socket while holding the outer one, and stop() (which takes the inner one) is called from
# inside start() — one lock would deadlock, and no lock let two concurrent starts each bind a
# socket, with the loser's listener staying bound until the process exits.
_lifecycle_lock = threading.Lock()
_state_lock = threading.Lock()
_active: Optional[_Session] = None
_timer: Optional[threading.Timer] = None


# ── network ───────────────────────────────────────────────────────────────────────────


def lan_ip() -> Optional[str]:
    """The address a phone on the same wifi should dial, or None if there is no LAN.

    A UDP `connect` sends no packet — it only makes the kernel pick the route it *would*
    use, which is how you learn which local interface faces the network.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(0.3)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()
    # Loopback means no route off this machine: a QR pointing at 127.0.0.1 would open the
    # PHONE's own localhost, which is a confusing failure. Better to say so up front.
    return None if not ip or ip.startswith("127.") else ip


# ── asset kinds ───────────────────────────────────────────────────────────────────────


def kind_for(name: str) -> Optional[str]:
    """assets/<kind> for this filename, or None if the app could not list or preview it.

    Deliberately the SAME classifier the asset browser uses, imported lazily because
    server.app imports this module — one vocabulary, no drift, no import cycle.
    """
    from server.app import _classify

    return _classify((), Path(name).suffix)


# ── lifecycle ─────────────────────────────────────────────────────────────────────────


def start(
    projects_dir: Path,
    project_id: str,
    on_saved: Optional[Callable[[str, Path, str, str], None]] = None,
    on_failed: Optional[Callable[[str, str], None]] = None,
    on_closed: Optional[Callable[[dict[str, Any]], None]] = None,
    ttl_seconds: int = TTL_SECONDS,
) -> dict[str, Any]:
    """Open a receive window for one project. Replaces any window open for a DIFFERENT one.

    Re-opening for the SAME project returns the window that is already up, unchanged. That is
    not just tidiness: minting a fresh token would silently invalidate the QR code a phone has
    already scanned, and it makes the call idempotent, so two racing opens from one UI cannot
    leave a caller holding a session the other one replaced.
    """
    global _active, _timer
    # ONE lock over read-current -> bind -> publish. Without it two concurrent starts each bind
    # a socket and only the last one is tracked, leaving the other listening on the wifi, with
    # no watchdog, until the process exits.
    with _lifecycle_lock:
        with _state_lock:
            current = _active
        if current is not None and current.project_id == project_id and not current.dead():
            return _public(current)

        ip = lan_ip()
        if ip is None:
            raise LanUnavailable(
                "This Mac is not on a network, so a phone has nothing to connect to. "
                "Join the same wifi as your phone and try again."
            )
        stop()

        sess = _Session(
            id=secrets.token_hex(8),
            token=secrets.token_urlsafe(16),
            project_id=project_id,
            projects_dir=Path(projects_dir),
            expires_at=time.time() + ttl_seconds,
            opened_at=time.time(),
            on_saved=on_saved,
            on_failed=on_failed,
            on_closed=on_closed,
        )
        # Port 0 = let the OS pick a free one. macOS prompts once per BINARY to accept incoming
        # connections, not per port, so a stable port would buy nothing.
        httpd = _BoundedServer(("0.0.0.0", 0), _handler_for(sess))
        sess.httpd = httpd
        sess.origin = f"http://{ip}:{httpd.server_address[1]}"
        sess.url = f"{sess.origin}/r/{sess.token}"

        threading.Thread(target=httpd.serve_forever, name="lan-receive", daemon=True).start()

        with _state_lock:
            _active = sess
            # Belt and braces: the handler also refuses an expired token, but a window nobody
            # closes should not keep a socket open on the wifi for the rest of the session.
            _timer = threading.Timer(ttl_seconds, lambda: stop(sess.id))
            _timer.daemon = True
            _timer.start()
        return _public(sess)


def stop(session_id: Optional[str] = None) -> bool:
    """Close the window. Safe to call when nothing is open. True if something was closed.

    `session_id` scopes the close to ONE window: a late "close" from a dialog that has already
    been replaced must not kill the live window. React StrictMode remounts every effect in dev,
    so the open-then-close-then-open race is not hypothetical — it is every single mount.
    """
    global _active, _timer
    with _state_lock:
        if session_id is not None and (_active is None or _active.id != session_id):
            return False
        sess, _active = _active, None
        timer, _timer = _timer, None
    if timer is not None:
        timer.cancel()
    if sess is None:
        return False
    # BEFORE shutdown: shutdown() only stops the accept loop, so a transfer already streaming
    # keeps its thread. _drain watches this and abandons the part file mid-flight.
    sess.cancelled.set()
    # Every teardown funnels through here — Done, the watchdog, a replace, a status() reap —
    # which is why the rollup is emitted HERE and not at four call sites.
    if sess.on_closed is not None:
        try:
            sess.on_closed(_rollup(sess))
        except Exception:
            pass  # telemetry must never keep a socket open
    # Never called from the serving thread (API thread or the watchdog timer), so shutdown()
    # cannot deadlock on itself.
    for close in (sess.httpd.shutdown, sess.httpd.server_close):
        try:
            close()
        except Exception:
            pass
    return True


def status() -> Optional[dict[str, Any]]:
    """The open window, or None. Expiry is settled HERE so a poll is what reaps a dead one."""
    with _state_lock:
        sess = _active
    if sess is None:
        return None
    if sess.dead():
        stop()
        return None
    return _public(sess)


def _rollup(sess: _Session) -> dict[str, Any]:
    """What one receive window achieved, for `phone_receive_finished`.

    `outcome` separates the two ways of ending with nothing, because they mean opposite
    things: `closed_empty` is a user who gave up (wifi, firewall prompt, could not scan),
    `expired_empty` is a window nobody came back to. Collapsing them would hide the first,
    which is the one that says the feature does not work."""
    with sess.lock:
        received = list(sess.received)
        total = sess.bytes_written
    by_kind: dict[str, int] = {}
    for entry in received:
        by_kind[entry["kind"]] = by_kind.get(entry["kind"], 0) + 1
    if received:
        outcome = "received"
    else:
        # The DEADLINE, deliberately not sess.dead(): stop() sets `cancelled` before it builds
        # this rollup, so dead() would report every window as expired.
        outcome = "expired_empty" if time.time() >= sess.expires_at else "closed_empty"
    return {
        "outcome": outcome,
        "files": len(received),
        "bytes": total,
        "seconds_open": max(0.0, time.time() - sess.opened_at),
        "by_kind": by_kind,
    }


def _public(sess: _Session) -> dict[str, Any]:
    with sess.lock:
        received = list(sess.received)
    return {
        "active": True,
        "id": sess.id,
        "project_id": sess.project_id,
        "url": sess.url,
        "expires_at": sess.expires_at,
        "received": received,
    }


# ── the socket ────────────────────────────────────────────────────────────────────────


class _BoundedServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with a hard cap on live handler threads.

    The stock one spawns a thread per accepted connection BEFORE any token is checked, so an
    unauthenticated stranger on the wifi can hold sockets open and get a parked thread each
    (measured: 200 half-open connections -> +200 threads in the process that also runs the
    editor and the agent). Over the cap we hang up immediately: a real phone uses one
    connection, so a client that needs a 25th is not a phone.
    """

    daemon_threads = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._slots = threading.Semaphore(_MAX_CONNECTIONS)
        super().__init__(*args, **kwargs)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)  # spawns the handler thread
        except BaseException:
            self._slots.release()  # the thread never started, so nothing else will release
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


# ── the two routes the phone talks to ─────────────────────────────────────────────────


def _handler_for(sess: _Session):
    class Handler(BaseHTTPRequestHandler):
        # Keep-alive, so a phone sending five clips reuses one connection. Every reply below
        # therefore MUST carry a Content-Length — _send is the only way out for that reason.
        protocol_version = "HTTP/1.1"
        # socketserver hands this to connection.settimeout(), so a socket that goes silent
        # mid-request stops holding a thread. Per-read, so a slow-but-moving upload survives.
        timeout = _SOCKET_TIMEOUT_S

        def log_message(self, *args: Any) -> None:  # noqa: A003 - stdlib hook
            pass  # the default logs every hit to stderr, which the desktop app tees to disk

        # -- helpers --
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            # The token rides in the URL, so it must never leave in a Referer, and the page
            # must never be reinterpreted as another content type.
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _fail(self, code: int, message: str, failure_class: str = "", kind: str = "other") -> None:
            # `failure_class` is what makes the phone path measurable. Without it the only
            # event this feature emits is a success, so its refusal rate is structurally
            # invisible — the picker path instruments every one of its equivalent branches.
            if failure_class and sess.on_failed is not None:
                try:
                    sess.on_failed(kind, failure_class)
                except Exception:
                    pass  # telemetry never costs the user a reply
            self._send(code, json.dumps({"error": message}).encode(), "application/json")

        def _addressed_to_us(self) -> bool:
            """Host must be the exact ip:port we put in the QR, and any Origin must be ours.

            Host: a DNS-rebinding page resolves its OWN name to this address, so the browser
            treats us as same-origin and can read our replies. It cannot forge Host — that is
            the attacker's hostname — so pinning Host is what breaks the technique.

            Origin: absent on the phone page's own GET and equal to our own on its POST. A
            foreign value means some other site is driving the request, which is never us.
            """
            host = (self.headers.get("Host") or "").strip()
            if host != sess.origin.removeprefix("http://"):
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin == sess.origin

        def _authorized(self) -> bool:
            """Constant-time token check + expiry. Anything else is a flat 404 — an
            unauthenticated stranger on the wifi learns nothing, not even that we exist."""
            if not self._addressed_to_us():
                self._fail(404, "not found")
                return False
            path = urlparse(self.path).path
            prefix = f"/r/{sess.token}"
            ok = len(path) == len(prefix) and secrets.compare_digest(path, prefix)
            if not ok:
                self._fail(404, "not found")
                return False
            if sess.dead():
                # POST only: a GET here is the phone opening a stale QR, which is the same
                # window ending, not a second import failure.
                self._fail(
                    410,
                    "This link expired. Open Receive assets from phone again on the Mac.",
                    "link_expired" if self.command == "POST" else "",
                )
                return False
            return True

        # -- routes --
        def do_GET(self) -> None:
            if not self._authorized():
                return
            page = (
                _PAGE.replace("__BRAND_FONT__", _BRAND_FONT_B64)
                .replace("__BRAND_MARK__", _BRAND_MARK)
                .replace("__PROJECT__", html.escape(sess.project_id))
            )
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

        def do_POST(self) -> None:
            if not self._authorized():
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            # BEFORE the length checks: chunked bodies usually carry no Content-Length at all,
            # so the empty-body branch would answer first and hide the real reason. We read
            # exactly Content-Length, so whatever the chunked framing meant to say would be
            # left on a keep-alive socket and parsed as a second request. The phone page never
            # sends this; refuse rather than guess.
            if self.headers.get("Transfer-Encoding"):
                return self._fail(400, "That upload wasn't understood.", "malformed_request")
            if length <= 0:
                return self._fail(411, "That file came through empty.", "malformed_request")
            if length > MAX_BYTES:
                return self._fail(413, f"Too big — the limit is {MAX_BYTES // (1 << 30)} GB.", "too_large")
            # A whole-session budget. MAX_BYTES only bounds ONE file, so without this a link
            # in the wrong hands can fill the disk one accepted file at a time.
            with sess.lock:
                over = len(sess.received) >= MAX_SESSION_FILES or sess.bytes_written + length > MAX_SESSION_BYTES
            if over:
                return self._fail(507, "That's all this link can take. Open a new one on the Mac.", "session_budget")

            raw = parse_qs(urlparse(self.path).query).get("name", [""])[0]
            try:
                name = sanitize_filename(raw)
            except ValueError:
                return self._fail(400, "That filename can't be used.", "invalid_name")
            kind = kind_for(name)
            if kind is None:
                ext = Path(name).suffix.lower() or "that"
                return self._fail(415, f"OpenNolan can't use {ext} files yet.", "invalid_kind")

            kind_dir = sess.projects_dir / sess.project_id / "assets" / kind
            try:
                kind_dir.mkdir(parents=True, exist_ok=True)
                # A dot-prefixed part file: the asset browser hides dot-entries, so a
                # half-uploaded clip never shows up in the grid (it polls every 4s).
                part = kind_dir / f".{secrets.token_hex(8)}.part"
                written = self._drain(length, part)
            except OSError as exc:
                disk_full = "space" in str(exc).lower()
                return self._fail(
                    507 if disk_full else 500,
                    "Couldn't save that on the Mac.",
                    "disk_full" if disk_full else "copy",
                    kind,
                )
            if written != length:
                part.unlink(missing_ok=True)
                # Told apart so the phone shows the honest reason: the window closing under an
                # in-flight upload is not the same as the phone walking out of range.
                if sess.dead():
                    return self._fail(410, "The link closed while that was sending.", "link_expired", kind)
                return self._fail(400, "Upload was cut short — try again.", "incomplete", kind)

            try:
                # Reserve the final name ATOMICALLY. An exists()-then-rename would let the
                # picker upload (or another phone) take the name in between and get clobbered
                # by our os.replace; O_CREAT|O_EXCL makes the winner unambiguous.
                target = _reserve_name(kind_dir, name)
                # Defense in depth, same as the picker upload: sanitize_filename already
                # stripped every directory part, so a target outside its kind dir means
                # something we did not anticipate.
                if kind_dir.resolve() not in target.resolve().parents:
                    raise ValueError("target escaped its kind dir")
            except (OSError, ValueError):
                part.unlink(missing_ok=True)
                return self._fail(400, "That filename can't be used.", "invalid_name", kind)
            os.replace(part, target)  # onto our own reservation

            # PROJECTS-DIR-relative (leading with the project id) — the same `path` contract
            # the picker upload's response and _capture_asset_ingest both speak.
            rel = str(target.resolve().relative_to(sess.projects_dir.resolve()))
            entry = {"name": target.name, "kind": kind, "path": rel, "bytes": written}
            with sess.lock:
                sess.received.append(entry)
                sess.bytes_written += written
            if sess.on_saved is not None:
                try:
                    sess.on_saved(kind, target, rel, target.name)
                except Exception:
                    pass  # analytics/probe must never cost the user their upload
            self._send(201, json.dumps(entry).encode(), "application/json")

        def _drain(self, length: int, dest: Path) -> int:
            """Stream the body to disk. Returns bytes actually written — short means either the
            phone dropped off (walked out of range, locked the screen) or the window closed
            under us. Checked EVERY chunk: `stop()` only ends the accept loop, so without this
            an upload begun a second before Done would keep streaming to the user's disk."""
            written = 0
            with open(dest, "wb") as out:
                while written < length:
                    if sess.dead():
                        break
                    chunk = self.rfile.read(min(_CHUNK, length - written))
                    if not chunk:
                        break
                    out.write(chunk)
                    written += len(chunk)
            return written

    return Handler


def _reserve_name(directory: Path, name: str) -> Path:
    """Atomically claim `clip.mov`, or `clip-1.mov` if taken, and return the claimed path.

    Two phones both sending IMG_0001.JPG is the normal case, not the edge case, and an
    exists()-then-rename leaves a window where another writer takes the name and our
    os.replace silently destroys their file. O_CREAT|O_EXCL has no such window: whoever
    creates it first owns the name, and we then rename our part file onto our own claim.
    """
    stem, suffix = Path(name).stem, Path(name).suffix
    for i in range(0, 1000):
        candidate = directory / (name if i == 0 else f"{stem}-{i}{suffix}")
        try:
            os.close(os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            return candidate
        except FileExistsError:
            continue
    # 1000 files with one name is not a user; give it a unique name rather than loop forever.
    candidate = directory / f"{stem}-{secrets.token_hex(4)}{suffix}"
    os.close(os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    return candidate


# ── the brand lockup ──────────────────────────────────────────────────────────────────
# The mark and the wordmark are COPIED from the website's `.brand` rule, not approximated:
# same geometry (rx=9, that exact play path), same brand terracotta #D9694A on #FBF8F1, same
# Fraunces 600 at 21px / gap 11 / letter-spacing -0.01em. A logo is one artifact across
# products — see RULES.md "Brand assets are copied, never approximated".
#
# Fraunces is a webfont, and this page must never make an external request (a phone on a
# LAN-only network would render the fallback, and fetching fonts.googleapis.com would break
# the "nothing leaves your network" line printed on this very page). So it ships as a
# glyph-SUBSET woff2 — only the letters in "OpenNolan" — inlined as a data URI: 3.3 KB.
# Regenerate with:
#   curl 'https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&text=OpenNoal'
#   then base64 the woff2 it points at.
# Fraunces is SIL OFL 1.1 (https://github.com/undercasetype/Fraunces) — embedding is granted.
_BRAND_FONT_B64 = "".join(
    """
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
skbx4Ta8m0OM/i5ttf+PEEMAAAA=
""".split()
)

_BRAND_MARK = (
    '<svg width="30" height="30" viewBox="0 0 32 32" aria-hidden="true">'
    '<rect width="32" height="32" rx="9" fill="#D9694A"/>'
    '<path d="M13 10.2l9.2 5.8-9.2 5.8z" fill="#FBF8F1"/></svg>'
)


# ── the page the phone opens ──────────────────────────────────────────────────────────
# Self-contained on purpose: the phone is not on the Vite dev server and must not need one
# byte from anywhere but this socket. Colors are the app's own tokens (web/src/styles.css).

_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light">
<title>Send to OpenNolan</title>
<style>
  :root { --bg:#faf7f2; --panel:#fffdf9; --wash:#f2ede4; --wash-strong:#e9e2d6;
          --ink:#2b2722; --ink-dim:#6f665c; --line:#ece5db; --border:#98886f;
          --accent:#c8643c; --accent-ink:#a44a26; --green-ink:#34663d; --red-ink:#96382a;
          --radius:12px; }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  html { -webkit-text-size-adjust: 100%; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:16px/1.5 -apple-system, system-ui, "Segoe UI", Roboto, sans-serif;
         padding: calc(24px + env(safe-area-inset-top)) calc(20px + env(safe-area-inset-right))
                  calc(40px + env(safe-area-inset-bottom)) calc(20px + env(safe-area-inset-left)); }
  .wrap { max-width: 420px; margin: 0 auto; }

  /* Brand lockup — values copied from the website's `.brand`, not eyeballed. */
  @font-face { font-family:'Fraunces'; font-style:normal; font-weight:600;
               src:url(data:font/woff2;base64,__BRAND_FONT__) format('woff2'); }
  .brand { display:inline-flex; align-items:center; gap:11px; margin-bottom:28px;
           font-family:'Fraunces', Georgia, 'Times New Roman', serif;
           font-weight:600; font-size:21px; letter-spacing:-0.01em; }
  .brand svg { display:block; }

  h1 { font-size:24px; line-height:1.25; margin:0 0 6px; letter-spacing:-0.02em; }
  .sub { color:var(--ink-dim); font-size:14px; margin:0 0 20px; }
  .chip { display:inline-block; background:var(--wash); border:1px solid var(--wash-strong);
          border-radius:999px; padding:3px 10px; font-size:13px; color:var(--ink);
          font-weight:600; max-width:100%; overflow:hidden; text-overflow:ellipsis;
          white-space:nowrap; vertical-align:bottom; }

  .card { background:var(--panel); border:1px solid var(--line); border-radius:var(--radius);
          padding:20px; box-shadow:0 1px 2px #2b272212, 0 8px 24px #2b27220f; }
  .pick { display:block; width:100%; text-align:center; padding:16px; border-radius:10px;
          background:var(--accent); color:#fff; font-size:16px; font-weight:600; cursor:pointer;
          border:0; box-shadow:0 1px 2px rgba(43,39,34,.18); }
  .pick:active { background:var(--accent-ink); transform: translateY(1px); }
  .note { margin:14px 0 0; font-size:13px; color:var(--ink-dim); text-align:center; }

  ul { list-style:none; margin:20px 0 0; padding:0; display:flex; flex-direction:column; gap:8px; }
  li { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }
  .row { display:flex; align-items:baseline; justify-content:space-between; gap:12px; }
  .nm { font-size:14px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .st { font-size:12px; color:var(--ink-dim); flex:none; font-variant-numeric:tabular-nums; }
  .st.ok { color:var(--green-ink); font-weight:600; } .st.err { color:var(--red-ink); }
  .bar { height:3px; background:var(--wash-strong); border-radius:2px; margin-top:9px; overflow:hidden; }
  .bar i { display:block; height:100%; width:0; background:var(--accent); transition:width .15s linear; }
  li.done .bar { display:none; }
  .foot { margin-top:24px; font-size:13px; color:var(--ink-dim); text-align:center; }
</style>
</head><body>
  <div class="wrap">
    <div class="brand">__BRAND_MARK__OpenNolan</div>

    <h1>Send to your Mac</h1>
    <p class="sub">Photos and videos you pick land in <span class="chip">__PROJECT__</span></p>

    <div class="card">
      <label class="pick">Choose photos or videos
        <input id="picker" type="file" multiple hidden>
      </label>
      <p class="note">They save straight into the project. Nothing leaves your network.</p>
    </div>

    <ul id="list"></ul>
    <p class="foot">Keep this page open until every file says Added.</p>
  </div>
<script>
(function () {
  var picker = document.getElementById('picker'), list = document.getElementById('list');

  function row(file) {
    var li = document.createElement('li');
    li.innerHTML = '<div class="row"><span class="nm"></span><span class="st">waiting</span></div>'
                 + '<div class="bar"><i></i></div>';
    li.querySelector('.nm').textContent = file.name;
    list.appendChild(li);
    return { st: li.querySelector('.st'), fill: li.querySelector('.bar i'), li: li };
  }

  function send(file, ui) {
    return new Promise(function (resolve) {
      var xhr = new XMLHttpRequest();
      // Same path we were opened on -> the token rides along, nothing to hardcode.
      xhr.open('POST', location.pathname + '?name=' + encodeURIComponent(file.name));
      xhr.upload.onprogress = function (e) {
        if (!e.lengthComputable) return;
        ui.st.textContent = Math.round(e.loaded / e.total * 100) + '%';
        ui.fill.style.width = (e.loaded / e.total * 100) + '%';
      };
      xhr.onload = function () {
        ui.li.classList.add('done');
        var ok = xhr.status >= 200 && xhr.status < 300, msg = '';
        try { msg = JSON.parse(xhr.responseText).error || ''; } catch (e) {}
        ui.st.className = 'st ' + (ok ? 'ok' : 'err');
        ui.st.textContent = ok ? 'Added' : (msg || 'Failed');
        resolve();
      };
      xhr.onerror = function () {
        ui.li.classList.add('done');
        ui.st.className = 'st err';
        ui.st.textContent = 'Lost connection';
        resolve();
      };
      xhr.send(file);
    });
  }

  picker.addEventListener('change', function () {
    var files = Array.prototype.slice.call(picker.files || []);
    picker.value = '';  // so picking the same file twice still fires a change
    // One at a time: phone wifi upstream is the bottleneck, and serial uploads keep the
    // progress numbers honest instead of five bars crawling together.
    files.reduce(function (chain, f) {
      var ui = row(f);
      return chain.then(function () { return send(f, ui); });
    }, Promise.resolve());
  });
})();
</script>
</body></html>
"""
