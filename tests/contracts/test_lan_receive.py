"""Contract tests for "Receive from phone" (server/lan_receive.py + its three API routes).

The interesting surface is not the happy path, it is what a stranger on the same wifi can do:
the receive server binds 0.0.0.0, so the token gate, the expiry, the filename handling and the
kind whitelist are the whole security story. Everything here talks to a REAL socket — a mocked
HTTP server would not prove the thing that needed proving.
"""

import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from server import lan_receive
from server.app import create_app

PIPELINE = "animated-explainer"
STUB_CAPS = {"composition_runtimes": {}, "capabilities": [], "setup_offers": [], "runtime_warnings": []}


@pytest.fixture(autouse=True)
def _no_leaked_server():
    """A test that leaves a socket bound on 0.0.0.0 would poison every later test."""
    yield
    lan_receive.stop()


@pytest.fixture
def ctx(tmp_path):
    projects = tmp_path / "projects"
    app = create_app(projects_dir=projects, capabilities_provider=lambda: STUB_CAPS)
    # base_url matters: the receive routes require a loopback Host, and TestClient's default
    # is "http://testserver", which is exactly what the guard is there to refuse.
    client = TestClient(app, base_url="http://127.0.0.1")
    client.post("/api/projects", json={"name": "Phone Test", "pipeline_type": PIPELINE})
    return client, projects


def _lan_available():
    return lan_receive.lan_ip() is not None


needs_lan = pytest.mark.skipif(not _lan_available(), reason="no LAN address on this machine")


def _post(url, body: bytes, name: str, headers=None):
    """POST a raw body the way the phone page does. Returns (status, text)."""
    req = urllib.request.Request(f"{url}?name={name}", data=body, method="POST", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def _get(url, headers=None):
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


# ── kind classification: the same vocabulary the asset browser lists ──────────────────


@pytest.mark.parametrize(
    "name,kind",
    [
        ("clip.MOV", "video"),
        ("photo.jpg", "images"),
        ("bed.mp3", "audio"),
        ("note.txt", None),
        ("thing.heic", None),
        ("payload.exe", None),
        ("no-extension", None),
    ],
)
def test_kind_for_matches_the_browser_vocabulary(name, kind):
    assert lan_receive.kind_for(name) == kind


# ── the API surface (no socket needed) ────────────────────────────────────────────────


def test_status_is_inactive_before_anything_starts(ctx):
    client, _ = ctx
    assert client.get("/api/projects/phone-test/receive").json() == {"active": False, "received": []}


def test_start_404s_for_an_unknown_project(ctx):
    client, _ = ctx
    assert client.post("/api/projects/nope/receive").status_code == 404


def test_stop_is_idempotent(ctx):
    client, _ = ctx
    assert client.delete("/api/projects/phone-test/receive").json() == {"stopped": False}


# ── the loopback API is not an authorization boundary ─────────────────────────────────
# Reproduced against the running app BEFORE these guards existed: a page served from a
# different origin fired POST /receive with mode:"no-cors" and opened a real listening
# socket on the wifi. The browser hid the response; it did not stop the request.


@pytest.mark.parametrize("method", ["post", "get", "delete"])
def test_a_foreign_origin_cannot_drive_the_receive_routes(ctx, method):
    client, _ = ctx
    r = getattr(client, method)("/api/projects/phone-test/receive", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403, r.text
    assert lan_receive.status() is None  # ...and nothing was opened on the way to the refusal


@pytest.mark.parametrize("method", ["post", "get", "delete"])
def test_a_rebound_host_cannot_drive_the_receive_routes(ctx, method):
    """DNS rebinding makes the BROWSER treat us as same-origin, so Origin stops helping — but
    the attacker cannot forge Host, because Host is their own hostname."""
    client, _ = ctx
    r = getattr(client, method)("/api/projects/phone-test/receive", headers={"Host": "evil.example"})
    assert r.status_code == 403, r.text
    assert lan_receive.status() is None


def test_the_app_itself_is_not_caught_by_the_guard(ctx):
    """Both origins the real app speaks from: Electron (127.0.0.1:port) and the Vite dev
    server (localhost:port), which proxies /api through with the original Origin."""
    client, _ = ctx
    for origin in ("http://127.0.0.1:20906", "http://localhost:5173"):
        r = client.get("/api/projects/phone-test/receive", headers={"Origin": origin})
        assert r.status_code == 200, (origin, r.text)


@needs_lan
def test_re_opening_for_the_same_project_returns_the_same_window(ctx):
    """Idempotent on purpose: a second open must not mint a new token, because that would
    silently invalidate a QR code the phone has already scanned."""
    client, _ = ctx
    first = client.post("/api/projects/phone-test/receive").json()
    second = client.post("/api/projects/phone-test/receive").json()
    assert (second["id"], second["url"]) == (first["id"], first["url"])
    assert _get(first["url"])[0] == 200


@needs_lan
def test_a_stale_close_cannot_kill_the_window_that_replaced_it(ctx):
    """A close naming a window that is no longer the open one is a no-op — otherwise a
    late close from a replaced dialog would shut down the window in front of the user."""
    client, _ = ctx
    client.post("/api/projects", json={"name": "Other", "pipeline_type": PIPELINE})
    stale_id = client.post("/api/projects/other/receive").json()["id"]
    live = client.post("/api/projects/phone-test/receive").json()  # replaces "other"

    assert client.delete(f"/api/projects/other/receive?session_id={stale_id}").json() == {"stopped": False}
    assert client.get("/api/projects/phone-test/receive").json()["id"] == live["id"]
    assert _get(live["url"])[0] == 200

    assert client.delete(f"/api/projects/phone-test/receive?session_id={live['id']}").json() == {"stopped": True}


@needs_lan
def test_start_returns_a_reachable_url_and_stop_closes_it(ctx):
    client, _ = ctx
    started = client.post("/api/projects/phone-test/receive")
    assert started.status_code == 201
    url = started.json()["url"]
    assert url.startswith("http://") and "/r/" in url
    assert "127.0.0.1" not in url  # a phone dialing its own loopback is the failure we avoid

    status, page = _get(url)
    assert status == 200 and "Send to OpenNolan" in page and "phone-test" in page

    assert client.delete("/api/projects/phone-test/receive").json() == {"stopped": True}
    with pytest.raises(Exception):
        _get(url)


@needs_lan
def test_a_wrong_token_is_a_flat_404(ctx):
    client, _ = ctx
    url = client.post("/api/projects/phone-test/receive").json()["url"]
    base = url.rsplit("/r/", 1)[0]
    assert _get(f"{base}/r/guessed-it")[0] == 404
    assert _get(f"{base}/")[0] == 404
    assert _post(f"{base}/r/guessed-it", b"x" * 10, "sneak.mp4")[0] == 404
    # ...and nothing reached the disk under the wrong token
    assert client.get("/api/projects/phone-test/receive").json()["received"] == []


@needs_lan
def test_upload_lands_in_the_project_and_shows_in_status(ctx):
    client, projects = ctx
    url = client.post("/api/projects/phone-test/receive").json()["url"]

    status, body = _post(url, b"\x00fake-mp4-bytes" * 100, "IMG_0001.mp4")
    assert status == 201, body

    saved = projects / "phone-test" / "assets" / "video" / "IMG_0001.mp4"
    assert saved.is_file() and saved.stat().st_size == len(b"\x00fake-mp4-bytes" * 100)

    received = client.get("/api/projects/phone-test/receive").json()["received"]
    assert [(r["name"], r["kind"]) for r in received] == [("IMG_0001.mp4", "video")]
    # The path is PROJECTS-DIR-relative, like every other asset path the API hands out.
    assert received[0]["path"] == "phone-test/assets/video/IMG_0001.mp4"

    # ...and the editor's own listing sees it, which is the whole point.
    listed = client.get("/api/projects/phone-test/assets").json()["kinds"]["video"]
    assert [f["name"] for f in listed] == ["IMG_0001.mp4"]


@needs_lan
def test_a_second_file_with_the_same_name_does_not_clobber_the_first(ctx):
    client, projects = ctx
    url = client.post("/api/projects/phone-test/receive").json()["url"]
    assert _post(url, b"first", "IMG_0001.jpg")[0] == 201
    assert _post(url, b"second-file", "IMG_0001.jpg")[0] == 201

    images = projects / "phone-test" / "assets" / "images"
    assert (images / "IMG_0001.jpg").read_bytes() == b"first"
    assert (images / "IMG_0001-1.jpg").read_bytes() == b"second-file"


@needs_lan
def test_a_traversal_filename_cannot_escape_the_kind_dir(ctx):
    client, projects = ctx
    url = client.post("/api/projects/phone-test/receive").json()["url"]
    assert _post(url, b"evil", "..%2F..%2F..%2Fescaped.mp4")[0] == 201
    # sanitize_filename keeps the basename only — nothing above the project was written.
    assert (projects / "phone-test" / "assets" / "video" / "escaped.mp4").is_file()
    assert not (projects / "escaped.mp4").exists()
    assert not (tmp_parent := projects.parent / "escaped.mp4").exists(), tmp_parent


@needs_lan
def test_a_type_the_editor_cannot_show_is_refused(ctx):
    client, projects = ctx
    url = client.post("/api/projects/phone-test/receive").json()["url"]
    status, body = _post(url, b"MZ...", "payload.exe")
    assert status == 415 and ".exe" in body
    assert list((projects / "phone-test" / "assets").rglob("*.exe")) == []


@needs_lan
def test_an_empty_body_is_refused(ctx):
    client, _ = ctx
    url = client.post("/api/projects/phone-test/receive").json()["url"]
    assert _post(url, b"", "empty.mp4")[0] == 411


@needs_lan
def test_a_partial_upload_leaves_no_half_file_behind(ctx):
    """The phone walks out of wifi range mid-clip. A truncated .mp4 in the grid is worse
    than no file: the editor would happily put it on the timeline."""
    client, projects = ctx
    url = client.post("/api/projects/phone-test/receive").json()["url"]
    host, port, path = _split(url)

    sock = socket.create_connection((host, port), timeout=5)
    sock.sendall(
        f"POST {path}?name=cut-short.mp4 HTTP/1.1\r\nHost: {host}\r\nContent-Length: 5000\r\n\r\n".encode()
        + b"only-a-few-bytes"
    )
    sock.close()
    time.sleep(0.4)

    video = projects / "phone-test" / "assets" / "video"
    assert not video.exists() or list(video.iterdir()) == []
    assert client.get("/api/projects/phone-test/receive").json()["received"] == []


@needs_lan
def test_the_watchdog_closes_the_socket_at_the_ttl(ctx):
    """Nobody has to press Done. A window left open must not keep a port on the wifi."""
    _, projects = ctx
    url = lan_receive.start(projects, "phone-test", ttl_seconds=1)["url"]
    assert _get(url)[0] == 200
    time.sleep(1.3)
    assert lan_receive.status() is None
    with pytest.raises(Exception):
        _get(url)


@needs_lan
def test_an_expired_token_is_refused_even_if_the_socket_is_still_up(ctx):
    """Belt to the watchdog's braces: expiry is enforced per-request too, so a delayed or
    cancelled timer can never leave an accepting-forever upload endpoint on the LAN."""
    _, projects = ctx
    url = lan_receive.start(projects, "phone-test", ttl_seconds=600)["url"]
    lan_receive._active.expires_at = time.time() - 1  # timer still pending, socket still up
    assert _get(url)[0] == 410
    assert _post(url, b"late", "late.mp4")[0] == 410
    assert list((projects / "phone-test" / "assets").rglob("late.mp4")) == []
    assert lan_receive.status() is None  # ...and a poll reaps it


@needs_lan
def test_starting_for_another_project_replaces_the_open_window(ctx):
    client, _ = ctx
    client.post("/api/projects", json={"name": "Other", "pipeline_type": PIPELINE})
    first = client.post("/api/projects/phone-test/receive").json()["url"]
    client.post("/api/projects/other/receive")

    with pytest.raises(Exception):
        _get(first)
    # ...and this project's panel must not show the other project's window
    assert client.get("/api/projects/phone-test/receive").json() == {"active": False, "received": []}
    assert client.get("/api/projects/other/receive").json()["active"] is True


def _split(url):
    rest = url.split("http://", 1)[1]
    hostport, _, path = rest.partition("/")
    host, _, port = hostport.partition(":")
    return host, int(port), "/" + path


# ── the LAN listener's own guards ─────────────────────────────────────────────────────


@needs_lan
def test_the_listener_refuses_a_forged_host(ctx):
    """Same rebinding defence one layer down: a page that rebinds its name to our LAN address
    still sends its OWN hostname as Host, and we only answer to the one in the QR."""
    client, _ = ctx
    url = client.post("/api/projects/phone-test/receive").json()["url"]
    assert _get(url, headers={"Host": "evil.example"})[0] == 404
    assert _post(url, b"x" * 8, "sneak.mp4", headers={"Host": "evil.example"})[0] == 404


@needs_lan
def test_the_listener_refuses_a_foreign_origin(ctx):
    """Defence in depth for after the token leaks (screen capture, a copied URL): a webpage
    can still make a simple cross-origin POST, but not with our Origin on it."""
    client, projects = ctx
    url = client.post("/api/projects/phone-test/receive").json()["url"]
    assert _post(url, b"x" * 8, "drive-by.mp4", headers={"Origin": "https://evil.example"})[0] == 404
    assert list((projects / "phone-test" / "assets").rglob("drive-by.mp4")) == []
    # ...while the phone page's own POST, which carries our origin, still works
    own = url.rsplit("/r/", 1)[0]
    assert _post(url, b"x" * 8, "real.mp4", headers={"Origin": own})[0] == 201


@needs_lan
def test_unauthenticated_connections_cannot_exhaust_threads(ctx):
    """Measured before the cap: 200 half-open sockets with no token took the backend from 12
    threads to 212, because a thread is spawned on ACCEPT, long before the token is read."""
    client, _ = ctx
    url = client.post("/api/projects/phone-test/receive").json()["url"]
    host, port, _ = _split(url)

    held = []
    try:
        for _ in range(lan_receive._MAX_CONNECTIONS + 40):
            try:
                s = socket.create_connection((host, port), timeout=3)
                s.sendall(b"G")  # a request that never finishes
                held.append(s)
            except OSError:
                break
        time.sleep(0.8)
        alive = refused = 0
        for s in held:
            s.settimeout(1)
            try:
                alive += 1 if s.recv(1) else 0
            except socket.timeout:
                alive += 1  # parked in a handler thread, waiting for the rest of the request
            except OSError:
                # The server hung up. RST rather than FIN because our unread "G" was still in
                # its receive buffer when it closed — either way, no thread was spent.
                refused += 1
        assert alive <= lan_receive._MAX_CONNECTIONS, f"{alive} threads for a cap of {lan_receive._MAX_CONNECTIONS}"
        assert refused >= 20, f"only {refused} of {len(held)} were refused"
    finally:
        for s in held:
            s.close()


@needs_lan
def test_a_session_wide_budget_bounds_what_one_link_can_write(ctx, monkeypatch):
    client, projects = ctx
    monkeypatch.setattr(lan_receive, "MAX_SESSION_FILES", 2)
    url = client.post("/api/projects/phone-test/receive").json()["url"]
    assert _post(url, b"one", "a.mp4")[0] == 201
    assert _post(url, b"two", "b.mp4")[0] == 201
    status, body = _post(url, b"three", "c.mp4")
    assert status == 507 and "new one" in body
    assert not (projects / "phone-test" / "assets" / "video" / "c.mp4").exists()


@needs_lan
def test_closing_the_window_abandons_an_upload_already_in_flight(ctx):
    """stop() only ends the accept loop — a transfer that already has a thread keeps its
    thread, so without a per-chunk check a click on Done would not stop it writing."""
    client, projects = ctx
    started = client.post("/api/projects/phone-test/receive").json()
    host, port, path = _split(started["url"])

    sock = socket.create_connection((host, port), timeout=5)
    sock.sendall(
        f"POST {path}?name=slow.mp4 HTTP/1.1\r\nHost: {host}:{port}\r\nContent-Length: 4000000\r\n\r\n".encode()
        + b"\x00" * 1024
    )
    time.sleep(0.3)
    client.delete(f"/api/projects/phone-test/receive?session_id={started['id']}")

    sock.settimeout(5)
    try:
        sock.sendall(b"\x00" * 1024)  # the rest never arrives at a live session
        reply = sock.recv(200)
    except OSError:
        reply = b""
    sock.close()
    time.sleep(0.4)

    video = projects / "phone-test" / "assets" / "video"
    assert not video.exists() or list(video.iterdir()) == []
    assert b"200 OK" not in reply and b"201" not in reply


@needs_lan
def test_a_chunked_body_is_refused_rather_than_half_read(ctx):
    """We read exactly Content-Length, so a chunked framing we ignored would leave bytes on a
    keep-alive socket to be parsed as a second request."""
    client, _ = ctx
    url = client.post("/api/projects/phone-test/receive").json()["url"]
    status, _ = _post(url, b"data", "x.mp4", headers={"Transfer-Encoding": "chunked"})
    assert status == 400


def test_reserve_name_is_atomic(tmp_path):
    """The picker route writes into the same folder. exists()-then-rename let it take the name
    in between and be silently destroyed by our os.replace."""
    d = tmp_path / "video"
    d.mkdir()
    first = lan_receive._reserve_name(d, "IMG_1.mov")
    second = lan_receive._reserve_name(d, "IMG_1.mov")
    assert first.name == "IMG_1.mov" and second.name == "IMG_1-1.mov"
    # Both exist ALREADY — the name is claimed at reservation, not at rename time.
    assert first.exists() and second.exists()


# ── analytics: the phone path must be able to report itself FAILING ───────────────────
# RULES.md:93 — "Every new feature and every new failure path ships with analytics." Without
# these the only event the feature emits is a success, so its refusal rate is structurally
# invisible: a HEIC that 415s every iPhone user would show up as a 100% healthy path.


@pytest.fixture
def sink(monkeypatch):
    """Collect what capture() would have sent, with the real validate_event/_scrub in front —
    so a property the taxonomy does not declare is dropped here exactly as it would be live."""
    from server import analytics

    sent: list[tuple[str, dict]] = []

    class FakeClient:
        def capture(self, **kw):
            sent.append((kw["event"], kw["properties"]))

        def capture_exception(self, exc, **kw):
            sent.append(("$exception", kw.get("properties") or {}))

    monkeypatch.setattr(analytics, "_get_client", lambda: FakeClient())
    return sent


def _events(sink, name):
    return [props for event, props in sink if event == name]


@needs_lan
@pytest.mark.parametrize(
    "name,body,headers,failure_class",
    [
        ("payload.exe", b"MZ", None, "invalid_kind"),
        ("clip.mp4", b"", None, "malformed_request"),
        ("clip.mp4", b"x", {"Transfer-Encoding": "chunked"}, "malformed_request"),
    ],
)
def test_each_phone_refusal_reports_its_failure_class(ctx, sink, name, body, headers, failure_class):
    client, _ = ctx
    url = client.post("/api/projects/phone-test/receive").json()["url"]
    sink.clear()
    _post(url, body, name, headers=headers)

    failures = _events(sink, "asset_import_failed")
    assert failures, "the refusal emitted nothing"
    assert failures[-1]["failure_class"] == failure_class
    # `source` is what separates a phone refusal from a picker refusal; without it the whole
    # event is unattributable and the feature cannot be judged.
    assert failures[-1]["source"] == "phone"


@needs_lan
def test_the_window_reports_one_rollup_when_it_closes(ctx, sink):
    client, _ = ctx
    started = client.post("/api/projects/phone-test/receive").json()
    _post(started["url"], b"\x00" * 64, "IMG_1.mp4")
    sink.clear()
    client.delete(f"/api/projects/phone-test/receive?session_id={started['id']}")

    rollups = _events(sink, "phone_receive_finished")
    assert len(rollups) == 1, f"expected exactly one rollup per window, got {len(rollups)}"
    assert rollups[0]["outcome"] == "received"
    assert rollups[0]["files"] == 1
    assert rollups[0]["by_kind"] == {"video": 1}
    # Bucketed, never a raw duration: the property survived the taxonomy gate.
    assert isinstance(rollups[0]["seconds_open"], str)


@needs_lan
def test_a_window_closed_with_nothing_is_told_apart_from_one_that_expired(ctx, sink):
    """The two empty outcomes mean opposite things — gave up vs never came back — and
    collapsing them would hide the one that says the feature does not work."""
    client, projects = ctx

    started = client.post("/api/projects/phone-test/receive").json()
    sink.clear()
    client.delete(f"/api/projects/phone-test/receive?session_id={started['id']}")
    assert _events(sink, "phone_receive_finished")[-1]["outcome"] == "closed_empty"

    # The expiry half, straight off the hook: stop() sets `cancelled` before it builds the
    # rollup, so the outcome must be decided by the DEADLINE and not by dead().
    seen: list[dict] = []
    lan_receive.start(projects, "phone-test", on_closed=seen.append, ttl_seconds=600)
    lan_receive._active.expires_at = time.time() - 1
    assert lan_receive.status() is None  # the poll is what reaps it
    assert [r["outcome"] for r in seen] == ["expired_empty"]


def test_no_lan_still_reports_an_outcome(ctx, sink, monkeypatch):
    """A window that never opened is the outcome most likely to mean 'unusable for this
    person', so it must not be the one that is invisible."""
    client, _ = ctx
    monkeypatch.setattr(lan_receive, "lan_ip", lambda: None)
    assert client.post("/api/projects/phone-test/receive").status_code == 503
    assert _events(sink, "phone_receive_finished")[-1]["outcome"] == "unavailable"
