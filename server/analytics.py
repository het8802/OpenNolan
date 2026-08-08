"""Product analytics + error tracking via PostHog — for the packaged desktop app.

Design contract (from docs/plans/publish-mac-app.md, must-fix gaps):
  * OPT-OUT IS HONORED AT INIT. If the user opted out, we do NOT construct a PostHog client
    at all — not "construct then drop events." No client => nothing can leak, ever.
  * GRACEFUL: a missing `posthog` package, a missing key, or an init error degrades to a silent
    no-op. Analytics must never break the backend.
  * ANONYMOUS: identity is a stable per-install device id (server.settings.device_id()), NO PII.
  * SCRUBBED: event properties are stripped of absolute file paths, secret-looking values, and
    free-text prompt bodies before send (a local creative app's props are full of these).
  * GATED: every event passes the taxonomy in schemas/analytics/*.json, and that gate FAILS
    CLOSED — an unloadable taxonomy drops EVERY event rather than waving them through. The
    "graceful" clause above is about the sink, never about the gate.
  * NEVER during tests: if pytest is loaded, analytics is hard-disabled regardless of settings.

    is_enabled() gates everything:
        opted-out? ─┐
        no posthog? ├─► disabled → capture()/capture_exception() are no-ops
        under test? │
        no key?    ─┘
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import uuid
from collections import OrderedDict
from contextvars import ContextVar
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

# ── taxonomy (schemas/analytics/*.json) ───────────────────────────────────────
# One SOURCE, so Python, React and Electron can never drift into three copies of an enum —
# split across family files only because ~100 events in a single file is unreviewable in a diff.
_taxonomy_cache: Optional[dict[str, Any]] = None
# The split introduces a failure mode the single file lacked: a PARTIAL merge, where valid
# events silently become undeclared and are dropped. So the merge is all-or-nothing (below).
_taxonomy_failed_logged = False

# Local counters for telemetry health (#112). They can only ride OUT on the next event that
# actually sends — a dead sink cannot report its own death. ponytail: in-memory, so they reset
# on backend restart; make them a file under home() if a restart-spanning number is ever needed.
_counters: dict[str, int] = {"dropped_props": 0, "unknown_events": 0, "send_failed": 0, "budget_dropped": 0}

# One pending data-quality violation, reported on the NEXT successful capture. It cannot be
# reported from inside validate_event: that would re-enter the validator on the event that is
# already being rejected. Bounded to one because the question is "is the taxonomy drifting",
# and the first violation answers it — a flood would just spend the session budget saying so.
_pending_violation: Optional[dict[str, Any]] = None
_reported_violations: set[tuple[str, str]] = set()
_reentrant = False

# ── S7: the per-session upload budget, ENFORCED ───────────────────────────────
# The agreed ceiling is ≤40 uploads for an expected productive session, hard cap 100. Until
# now the only 100 in the code was per POST BODY (server/app.py) and per queue
# (web/src/analytics/track.js) — across 5-second flushes a session could upload arbitrarily
# many. "Families default to rollups" is an intention; this is the enforcement.
#
# A single shared limiter is not implementable: three sources emit independently and none of
# them sees the others. Electron main POSTs raw JSON straight to PostHog, which a backend
# counter cannot observe AT ALL. So the budget is PER SOURCE, constrained by one equation:
#
#     backend_noncritical + electron_noncritical + Σ(critical reserves)  ≤ 100
#     55                 + 8                     + (25 + 12)             = 100
#
# The critical reserve is what stops "criticals bypass" from making the hard cap unbounded:
# a crash loop is bounded by 25 here and by 12 in the shell, not by nothing. Criticals are
# §6's never-sampled set — the numerators AND denominators of the low-N rates (activation,
# export, every failure class, unmet capability), where dropping one costs a real observation.
#
# This backend counter covers backend-direct capture() AND the renderer batch, because the
# renderer has no PostHog client of its own and every renderer event arrives through
# POST /api/telemetry/events -> capture(). Electron's half lives in desktop/main.js.
BUDGET_NONCRITICAL = 55
BUDGET_CRITICAL = 25
# Electron's half, stated here so the equation above can be checked in ONE place and asserted
# by a test rather than living as two numbers in two languages that drift apart.
ELECTRON_BUDGET_NONCRITICAL = 8
ELECTRON_BUDGET_CRITICAL = 12
SESSION_HARD_CAP = 100

# A backend outlives many sessions, so the counter map is bounded and evicts oldest-first.
# ponytail: an evicted session's budget resets — it can only ever grant MORE, never fewer,
# and 32 concurrent sessions on one local backend does not happen.
_MAX_TRACKED_SESSIONS = 32
_session_uploads: "OrderedDict[str, dict[str, int]]" = OrderedDict()

# The session id minted by Electron main and carried in on the X-ON-Session header. A ContextVar
# (not a global) so concurrent requests can't read each other's — and anyio's threadpool copies
# the context, so FastAPI's sync `def` routes see it too. Render threads do NOT inherit it; they
# read the session id off the job record instead (it is threaded there at creation).
_session_ctx: ContextVar[Optional[str]] = ContextVar("opennolan_session_id", default=None)


TAXONOMY_DIR = ("schemas", "analytics")
_ENVELOPE_FILE = "_envelope.json"


def _merge_taxonomy(paths: list[Path]) -> dict[str, Any]:
    """Merge schemas/analytics/*.json into one taxonomy, ALL-OR-NOTHING.

    Raises on any defect. A PARTIAL merge is the failure mode the single file did not have:
    valid events would silently become undeclared and be dropped, one family at a time, with
    nothing to notice. A total failure is loud and recoverable; a partial one is neither.

    A duplicate event name across two family files is a defect too — the survivor would depend
    on directory order, so the taxonomy would mean different things on different machines."""
    env_paths = [p for p in paths if p.name == _ENVELOPE_FILE]
    if len(env_paths) != 1:
        raise ValueError(f"expected exactly one {_ENVELOPE_FILE}, found {len(env_paths)}")
    merged = json.loads(env_paths[0].read_text())
    merged.setdefault("events", {})
    for key in ("schema_version", "property_types", "envelope", "reporter_envelope", "reserved_substrings"):
        if key not in merged:
            raise ValueError(f"{_ENVELOPE_FILE} is missing {key!r}")
    for path in sorted(p for p in paths if p.name != _ENVELOPE_FILE):
        family = json.loads(path.read_text())
        for name, entry in (family.get("events") or {}).items():
            if name in merged["events"]:
                raise ValueError(f"event {name!r} is declared twice (second in {path.name})")
            merged["events"][name] = entry
        for section in ("enums", "open_vocabularies"):
            for key, value in (family.get(section) or {}).items():
                if key.startswith("$"):
                    continue
                bucket = merged.setdefault(section, {})
                if key in bucket and bucket[key] != value:
                    raise ValueError(f"{section} key {key!r} conflicts (second in {path.name})")
                bucket[key] = value
    if not merged["events"]:
        raise ValueError("merged taxonomy declares no events")
    return merged


def taxonomy() -> dict[str, Any]:
    """The merged taxonomy. Any defect => empty, which FAILS CLOSED: validate_event then drops
    every event. That is deliberate — the taxonomy is the gate that stops a free-text key
    reaching the wire, so if it cannot load, the security contract cannot be honored.

    Fail-closed means no event can ever ride the `unknown_events` counter out, so the failure
    is otherwise INVISIBLE. Hence the one-time stderr line: it is the only signal there is."""
    global _taxonomy_cache
    if _taxonomy_cache is None:
        try:
            from lib import app_paths

            root = app_paths.code_root().joinpath(*TAXONOMY_DIR)
            _taxonomy_cache = _merge_taxonomy(sorted(root.glob("*.json")))
        except Exception as exc:
            _taxonomy_cache = {}
            _log_taxonomy_failure(exc)
    return _taxonomy_cache


def _log_taxonomy_failure(exc: BaseException) -> None:
    """Announce a fail-closed taxonomy ONCE. Without this the outage is silent everywhere:
    log_destination() carries only key and host, and the counters cannot ride out."""
    global _taxonomy_failed_logged
    if _taxonomy_failed_logged:
        return
    _taxonomy_failed_logged = True
    try:
        print(
            f"[analytics] TAXONOMY FAILED TO LOAD from {'/'.join(TAXONOMY_DIR)} — "
            f"ALL events will be dropped (fail-closed): {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
    except Exception:
        pass


_PROJECT_KEY_FILE = ".analytics_id"


def project_key(projects_dir: Any, project_id: Optional[str]) -> Optional[str]:
    """A random, persisted per-project id — NEVER the directory name.

    `project_id` in this app is a slug of the name the USER typed ("Q4 launch teaser" ->
    "q4-launch-teaser"), so sending it verbatim would put customer and campaign text on the
    wire. §7 of the plan specifies a random uuid4 persisted in the project dir, and refuted
    HMAC(install_id, dir_name) because install_id is uploaded and folder names are guessable
    under it. Returns None when the project dir is not writable/present — an absent join key
    is honest; a leaked name is not."""
    if not project_id:
        return None
    try:
        path = Path(projects_dir) / project_id / _PROJECT_KEY_FILE
        existing = path.read_text().strip()
        if existing:
            return existing
    except OSError:
        pass
    key = uuid.uuid4().hex[:16]
    try:
        path = Path(projects_dir) / project_id / _PROJECT_KEY_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "x") as fh:  # exclusive: two threads must agree on one id
            fh.write(key + "\n")
        return key
    except FileExistsError:
        try:
            return path.read_text().strip() or None
        except OSError:
            return None
    except OSError:
        return None


def set_session_id(session_id: Optional[str]) -> None:
    """Bind the current request's session id (called by the X-ON-Session middleware)."""
    _session_ctx.set((session_id or "").strip() or None)


def current_session_id() -> Optional[str]:
    """The session this event belongs to.

    Two layers, in order: the ContextVar bound from the request's X-ON-Session header, then
    the session that SPAWNED this backend process (Electron sets OPENNOLAN_SESSION_ID on the
    child env). The second is what makes boot-time events like `app_opened` joinable at all —
    they have no request to inherit from. A dev backend started independently of the shell has
    neither, and stays NULL rather than being attributed to a session that did not start it."""
    return _session_ctx.get() or (os.environ.get("OPENNOLAN_SESSION_ID") or "").strip() or None


def validate_event(event: str, props: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Taxonomy gate, run BEFORE _scrub. Returns the surviving properties, or None to drop
    the whole event.

    · unknown event    -> drop the event, count it
    · unknown property -> drop the property, keep the event, count it

    A property whose name contains a reserved substring can never be declared (contract test 3
    forbids it), so it lands in the unknown-property branch and is dropped — which is what stops
    `prompt_len=412` from silently arriving as `prompt_len_len=None`."""
    tax = taxonomy()
    events = tax.get("events") or {}
    if not events:
        # FAIL CLOSED. The taxonomy is the gate that stops a free-text key reaching the wire;
        # if it cannot load, the security contract cannot be honored and nothing may be sent.
        # (It ships in the bundle via extraResources, so this is a packaging failure, not a
        # normal state — and _log_taxonomy_failure() says so out loud, because under fail-closed
        # this counter can never ride out on a later event.)
        _counters["unknown_events"] += 1
        return None
    entry = events.get(event)
    if entry is None:
        _counters["unknown_events"] += 1
        _note_violation("unknown_event", event)
        return None
    declared = entry.get("properties") or {}
    allowed = set(declared) | set(tax.get("envelope") or {})
    clean = {}
    for k, v in props.items():
        if k not in allowed:
            _counters["dropped_props"] += 1
            _note_violation("unknown_property", event)
            continue
        if not _enum_ok(tax, event, k, declared.get(k), v):
            _note_violation("wrong_type", event)
            # A field TYPED as an enum but accepting any string is not a constrained field —
            # it is an unlabelled free-text field, and the entire safety argument for this
            # instrumentation is that free text cannot reach the wire. _scrub cannot help:
            # it blocklists key NAMES, not values.
            _counters["dropped_props"] += 1
            continue
        if not _bounded(v):
            # _scrub only inspects top-level KEYS, so a nested map keyed by free text
            # ({"tools": {"<a user prompt>": {...}}}) would ride straight through it. Every
            # legitimate nested value here is keyed by a closed vocabulary (tool ids,
            # feature ids), so anything outside that shape is dropped rather than guessed at.
            _counters["dropped_props"] += 1
            continue
        clean[k] = v
    return clean


# Tool ids, feature ids and bucket labels — everything this taxonomy nests. Deliberately
# narrow: it is an allowlist for structure, not a sanitizer for content. No whitespace, which
# is what makes prose (a prompt, a project name, a caption) fail it.
_BOUNDED_TOKEN = re.compile(r"^[A-Za-z0-9_.:/+-]{1,64}$")
# "0-1", "10-50", "500+", "0.5-1" — everything render_jobs._bucket can produce, and nothing else.
_BUCKET_LABEL = re.compile(r"^(?:0|\d+(?:\.\d+)?)(?:-\d+(?:\.\d+)?|\+)?$")


def _enum_values(tax: dict[str, Any], event: str, prop: str) -> Optional[list]:
    """The closed vocabulary for a property, if one is declared. `<event>.<prop>` wins over a
    shared `<prop>` — `phase`, `stage` and `failure_class` mean different things per event."""
    enums = tax.get("enums") or {}
    found = enums.get(f"{event}.{prop}")
    if found is None:
        found = enums.get(prop)
    return found if isinstance(found, list) else None


def _enum_ok(tax: dict[str, Any], event: str, prop: str, kind: Optional[str], value: Any) -> bool:
    """Enforce the declared vocabulary for enum/bucket properties. DENY BY DEFAULT.

    · a declared enum        -> membership required
    · listed in `open_vocabularies` -> bounded token (shape only)
    · anything else          -> dropped

    The default is deny because the permissive branch is a door, and the first thing that
    walked through it was a user-created style name. A shape check cannot possibly close it:
    `q4-launch-teaser` — somebody's unreleased campaign — is character-for-character the same
    shape as `instagram-fast-reel`. So a vocabulary this codebase does not define is either
    justified in `open_vocabularies` (with the reason it CANNOT be user-authored) or collapsed
    to a bounded classification at the emit site. A new field gets neither by accident."""
    if kind not in ("E", "B") or not isinstance(value, str):
        return True
    allowed = _enum_values(tax, event, prop)
    if allowed is not None:
        return value in allowed
    if kind == "B":
        # A bucket is arithmetic output, not a vocabulary: `_bucket()` turns a number into
        # "10-50" or "500+". Digits, a dash and a plus cannot carry a name, so this is a
        # content bound rather than the shape bound that E fields cannot rely on.
        return bool(_BUCKET_LABEL.match(value))
    if prop not in (tax.get("open_vocabularies") or {}):
        return False
    return bool(_BOUNDED_TOKEN.match(value))


def _bounded(value: Any, depth: int = 0) -> bool:
    """True when a value is safe to send.

    Depth 0 is a top-level declared property: a plain string there is already governed by the
    declared-name allowlist and by _scrub's path redaction, so it passes. INSIDE a container
    the rules tighten to a closed token, because that is the actual hole — `_scrub` inspects
    only top-level keys, so `{"tools": {"<a user's prompt>": {...}}}` would ride straight
    through it untouched."""
    if value is None or isinstance(value, (bool, int, float)):
        return True
    if isinstance(value, str):
        return depth == 0 or bool(_BOUNDED_TOKEN.match(value))
    if depth >= 3:
        return False
    if isinstance(value, (list, tuple)):
        return len(value) <= 250 and all(_bounded(v, depth + 1) for v in value)
    if isinstance(value, dict):
        return len(value) <= 100 and all(
            isinstance(k, str) and _BOUNDED_TOKEN.match(k) and _bounded(v, depth + 1) for k, v in value.items()
        )
    return False


def _note_violation(kind: str, event: str) -> None:
    """Remember ONE violation for the next successful capture. Names only — the offending
    value is discarded here, which is the point: it is the thing that failed the gate."""
    global _pending_violation
    if _pending_violation is not None or (kind, event) in _reported_violations:
        return
    _reported_violations.add((kind, event))
    _pending_violation = {"class": kind, "event_name": event, "blocked": kind == "unknown_event"}


def _flush_violation() -> None:
    """Report a pending violation. Re-entrancy guarded: this calls capture(), which calls
    validate_event(), which is where violations come from."""
    global _pending_violation, _reentrant
    pending, _pending_violation = _pending_violation, None
    if pending is None or _reentrant:
        return
    _reentrant = True
    try:
        capture("data_quality_violation", pending)
    finally:
        _reentrant = False


def is_critical(event: str) -> bool:
    """Criticals draw on the reserve instead of the ordinary budget. Declared in the taxonomy,
    not hardcoded here, so Python and Electron read ONE list and the reviewer sees it in the
    diff that adds the event."""
    return bool(((taxonomy().get("events") or {}).get(event) or {}).get("critical"))


def _budget_ok(event: str, session_id: Optional[str]) -> bool:
    """Spend one upload from this session's budget. False => dropped and COUNTED.

    A silent drop is worse than no cap: every rate computed from a truncated session is wrong
    in an unknowable direction. `telemetry_budget_dropped` rides out on the next event that
    sends, which under this design is always a critical one — the reserve outlives the
    ordinary budget by construction."""
    # One shared bucket for genuinely session-less work (the detached nightly sweep). It is
    # rare and install-scoped by design, and giving it a per-event bucket would be no cap.
    key = session_id or "<none>"
    counts = _session_uploads.get(key)
    if counts is None:
        counts = {"noncritical": 0, "critical": 0}
        _session_uploads[key] = counts
        while len(_session_uploads) > _MAX_TRACKED_SESSIONS:
            _session_uploads.popitem(last=False)
    else:
        _session_uploads.move_to_end(key)
    bucket = "critical" if is_critical(event) else "noncritical"
    limit = BUDGET_CRITICAL if bucket == "critical" else BUDGET_NONCRITICAL
    if counts[bucket] >= limit:
        _counters["budget_dropped"] += 1
        return False
    counts[bucket] += 1
    return True


# The provider FAMILY, never the variable name. A closed list computed BEFORE capture, because
# _scrub cannot help here: it tests the KEY name, so `var_name='ANTHROPIC_API_KEY'` would ride
# through unredacted (verified).
_PROVIDER_FAMILIES = (
    ("anthropic", ("ANTHROPIC", "CLAUDE")),
    ("generation", ("REPLICATE", "FAL", "RUNWAY", "LUMA", "OPENAI", "GEMINI", "GOOGLE")),
    ("media", ("FIRECRAWL", "APIFY", "BROWSERBASE")),
    ("voice", ("ELEVENLABS", "CARTESIA", "DEEPGRAM", "PIPER")),
    ("stock", ("PEXELS", "UNSPLASH", "STORYBLOCKS", "ENVATO")),
)


def provider_family(var_name: str) -> str:
    upper = str(var_name or "").upper()
    for family, markers in _PROVIDER_FAMILIES:
        if any(m in upper for m in markers):
            return family
    return "other"


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


# Set by any harness that must NEVER be able to reach the production project (scripts/dev's
# scratch test/smoke home sets it). With it on, a missing POSTHOG_KEY DISABLES analytics
# instead of silently falling back to the hardcoded production key.
_NO_DEFAULT_KEY = "OPENNOLAN_ANALYTICS_NO_DEFAULT_KEY"


def _explicit_key() -> Optional[str]:
    return (os.environ.get("POSTHOG_KEY") or "").strip() or None


def is_enabled() -> bool:
    """True only when analytics may run: not opted out, not under test, key + package present."""
    if _under_pytest():
        return False
    if settings.get("analytics_disabled", False):
        return False
    if _explicit_key() is None and (os.environ.get(_NO_DEFAULT_KEY) or "").strip() not in ("", "0", "false"):
        # A scratch/CI home with no key of its own. The fallback below is the PRODUCTION
        # token, so silently using it here would pollute the real project the first time
        # anyone flips analytics on in a harness. Refuse instead.
        return False
    if not (_explicit_key() or _DEFAULT_KEY):
        return False
    try:
        import posthog  # noqa: F401
    except Exception:
        return False
    try:
        settings.device_id()
    except settings.InstallIdUnavailable as exc:
        # NEVER invent an id. install_id is the PostHog distinct_id AND the join key every
        # readback depends on, so a second id for one launch is worse than no analytics: it
        # silently splits one machine into two installs at the moment activation is measured.
        print(f"[analytics] DISABLED — no install id: {exc}", file=sys.stderr)
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
    key = _explicit_key() or _DEFAULT_KEY
    host = os.environ.get("POSTHOG_HOST", _DEFAULT_HOST)
    if not is_enabled():
        _client = None  # opt-out honored AT INIT: no client is ever built
        log_destination(key, host)
        return None
    try:
        from posthog import Posthog

        _client = Posthog(
            project_api_key=key,
            host=host,
            enable_exception_autocapture=True,
            before_send=_before_send,
            # posthog-python defaults this to True, so today NO $geoip_* property is
            # collected at all. That default was inherited, not chosen: country is what
            # tells us whether the deferred EU-compliance work is urgent, and timezone
            # sets digest/support timing. All-or-nothing at the client — enrichment
            # happens server-side at ingestion, AFTER _before_send, so the hook cannot
            # keep country and drop city.
            disable_geoip=False,
        )
    except Exception:
        _client = None  # any init failure -> silent no-op
    log_destination(key, host)
    return _client


def _key_hint(key: str) -> str:
    """A prefix is only safe to print on a well-formed key. Slicing a short/malformed value
    prints the whole thing, which is how a mis-set var becomes a log leak."""
    return f"{key[:12]}…" if len(key) >= 24 else f"<malformed key, {len(key)} chars>"


def log_destination(key: str, host: str) -> None:
    """One boot line answering 'which PostHog project am I writing to'.

    The fallback to _DEFAULT_KEY is SILENT, so a typo'd env var name writes to production
    with no error. Prefix only — never the whole key, even though it is a public write-only
    token, because this line ends up in logs and bug reports."""
    try:
        print(
            f"[analytics] {'ENABLED' if _client is not None else 'DISABLED'} "
            f"key={_key_hint(key)} host={host} "
            f"default_key={key == _DEFAULT_KEY} {_env_props()}",
            file=sys.stderr,
        )
    except Exception:
        pass


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


def _envelope(install_id: str) -> dict[str, Any]:
    """The join keys every event carries. Without them nothing can be joined to anything:
    `distinct_id` alone cannot tell you which session a render belonged to.

    `session_id` comes from the request context when the caller did not supply one — a render
    thread has no request context, which is why it passes the id explicitly off the job record.
    Counters ride out here because a failed sink cannot report its own failure (#112)."""
    env: dict[str, Any] = {
        "schema_version": taxonomy().get("schema_version", 1),
        "event_id": uuid.uuid4().hex,
        "install_id": install_id,
    }
    for name, count in _counters.items():
        if count:
            env[f"telemetry_{name}"] = count
    return env


def capture(event: str, properties: Optional[dict[str, Any]] = None) -> bool:
    """Record a product event (no-op when disabled). Identity = the anonymous install id.

    Returns True iff the event was handed to the PostHog client. That boolean is load-bearing:
    it is what POST /api/telemetry/events reports as `accepted`, and the renderer's session
    announcement clears its pending marker on it. Returning nothing made a taxonomy rejection,
    an exhausted budget and a successful send indistinguishable at the endpoint.

    NOT a delivery receipt — PostHog offers no synchronous one. It means "accepted by this
    process", which is the strongest statement available here.

    Order is load-bearing: validate_event (taxonomy) THEN _scrub (PII) — never instead of."""
    client = _get_client()
    if client is None:
        return False
    try:
        checked = validate_event(event, dict(properties or {}))
        if checked is None:
            return False  # undeclared event; counted in _counters
        session_id = (properties or {}).get("session_id") or current_session_id()
        if not _budget_ok(event, session_id):
            return False  # session upload budget exhausted; counted in _counters
        props = _scrub(checked)
        props.update(_env_props())
        install_id = settings.device_id()
        props.update(_envelope(install_id))
        props.setdefault("session_id", current_session_id())
        client.capture(distinct_id=install_id, event=event, properties=props)
        _counters.update({k: 0 for k in _counters})  # they rode out on this event
        _flush_violation()
        return True
    except Exception:
        _counters["send_failed"] += 1  # never a bare pass: a silent sink makes every number a lie
        return False


def capture_exception(exc: BaseException, properties: Optional[dict[str, Any]] = None) -> None:
    """Record a backend exception (no-op when disabled). NOT taxonomy-validated — `$exception`
    is the SDK's own event and its properties are diagnostic, not product vocabulary."""
    client = _get_client()
    if client is None:
        return
    try:
        props = _scrub(properties)
        props.update(_env_props())
        install_id = settings.device_id()
        props.update(_envelope(install_id))
        props.setdefault("session_id", current_session_id())
        client.capture_exception(exc, distinct_id=install_id, properties=props)
        _report_error("python_api", exc, properties)
    except Exception:
        _counters["send_failed"] += 1


def _report_error(layer: str, exc: BaseException, properties: Optional[dict[str, Any]] = None) -> None:
    """The bounded twin of `$exception`. `fingerprint` groups on the exception CLASS and the
    top frame's basename — never the message, which embeds ids and paths and would give every
    occurrence its own group, which is the opposite of what an inbox is for."""
    props = dict(properties or {})
    try:
        capture(
            "error_reported",
            {
                "layer": layer,
                "fatal": bool(props.get("fatal", False)),
                "handled": bool(props.get("handled", True)),
                "fingerprint": _fingerprint(exc),
            },
        )
    except Exception:
        pass


def _fingerprint(exc: BaseException) -> str:
    """`ExceptionClass:basename:line`, guaranteed to survive our OWN gate.

    Two live defects, both caught by data_quality_violation{class: wrong_type} — which is
    exactly what that event exists for, and the reason it is worth its own upload:

      · it joined with '@', which _BOUNDED_TOKEN does not allow, so the crash inbox's grouping
        key was silently dropped from every single error_reported;
      · a synthetic frame's basename is '<string>' / '<stdin>' / '<frozen importlib._bootstrap>',
        and angle brackets are not in the token set either.

    Widening the safety regex to fit one field would be backwards, so the field is sanitized to
    the charset the gate already enforces — and asserted, because a fingerprint that cannot
    reach the wire is worse than no fingerprint: the inbox looks healthy and groups nothing.
    """
    tb = exc.__traceback__
    frame = "unknown"
    while tb is not None:
        frame = f"{_token_safe(Path(tb.tb_frame.f_code.co_filename).name)}:{tb.tb_lineno}"
        tb = tb.tb_next
    return f"{_token_safe(type(exc).__name__)}:{frame}"[:64]


def _token_safe(value: str) -> str:
    """Collapse anything outside _BOUNDED_TOKEN's charset. Never empty — an empty component
    would make two unrelated crashes share a fingerprint."""
    cleaned = re.sub(r"[^A-Za-z0-9_.:/+-]", "_", str(value))
    return cleaned or "unknown"


_EXC_CLASS_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]{0,63}Error)\b")
# basename:line only. A full frame is `/Users/<name>/…/Studio.jsx:842:11`, i.e. the OS username.
_FRAME_RE = re.compile(r"([^/\\\s()]+):(\d+):\d+\)?\s*$")


def _exception_class(message: Any) -> str:
    """The class name only — `TypeError`, never the message that follows it.

    Run through _token_safe for the reason _fingerprint documents: a value outside
    _BOUNDED_TOKEN's charset is dropped SILENTLY, so an inbox looks healthy and groups
    nothing."""
    m = _EXC_CLASS_RE.match(str(message or "").strip())
    return _token_safe(m.group(1) if m else "Error")


def _top_frame(stack: Any) -> Optional[str]:
    """`basename:line` from the topmost real frame. Mirrors `topFrame` in desktop/main.js."""
    for line in str(stack or "").split("\n"):
        m = _FRAME_RE.search(line.rstrip())
        if m:
            return _token_safe(f"{m.group(1)}:{m.group(2)}")[:120]
    return None


def _stack_hash(stack: Any, message: Any = "") -> str:
    """Group on the SHAPE, not the text. Message bodies embed ids and paths, so hashing them
    would give every occurrence its own group — the opposite of what a crash inbox is for.
    Mirrors `stackHash` in desktop/main.js so one crash groups the same from either process."""
    raw = str(stack or "") or str(message or "")
    shape = "|".join(re.sub(r":\d+:\d+", "", _PATH_RE.sub("[path]", line)) for line in raw.split("\n")[:8])
    return hashlib.sha256(shape.encode("utf-8", "replace")).hexdigest()[:16]


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
    """Report a frontend/Electron error to PostHog (no-op when disabled).

    NOTHING FREE-TEXT LEAVES THIS FUNCTION. An error message is user data: a rejected API
    detail or a promise reason routinely embeds the project name, a filename or something the
    user typed, and path redaction alone does not touch any of that. Only the bounded triple
    goes out — `exception_class`, `top_frame` (basename:line), `stack_hash` (the SHAPE) —
    which is the same contract `desktop_error` uses at `desktop/main.js:361-384`, and the same
    reasoning `error_reported`'s `fingerprint` already documents in the taxonomy.

    The raw message and stack stay LOCAL, on the backend log, where they are as useful for
    debugging as they ever were. The renderer POSTs them to 127.0.0.1; the boundary that
    matters is this one, on the way to PostHog."""
    client = _get_client()
    if client is None:
        return
    props = _scrub(dict(context or {}))
    props.update(_env_props())
    install_id = settings.device_id()
    props.update(_envelope(install_id))
    props.setdefault("session_id", current_session_id())
    props["source"] = str(source)[:80]
    props["platform"] = "client"
    klass = _exception_class(message)
    frame = _top_frame(stack)
    props["exception_class"] = klass
    if frame:
        props["top_frame"] = frame
    props["stack_hash"] = _stack_hash(stack, message)
    # Local only — deliberately NOT a property. This is the copy a developer actually reads,
    # and it never leaves the machine.
    print(
        f"[client-error] {source} {klass} @ {frame or '?'} :: {_PATH_RE.sub('[path]', str(message))[:300]}",
        file=sys.stderr,
    )
    try:
        # Raise + catch so the exception carries a valid (if shim-only) traceback for the SDK.
        # The text is the GROUPING KEY, so it is the bounded triple and never the message.
        raise _ClientError(f"[{source}] {klass} @ {frame or '?'}")
    except _ClientError as exc:
        try:
            client.capture_exception(exc, distinct_id=install_id, properties=props)
            _report_error(_client_layer(source), exc, context)
        except Exception:
            _counters["send_failed"] += 1


def _client_layer(source: str) -> str:
    """Which layer crashed. The source string is set by our own reporters, but it is still
    collapsed here so a new reporter cannot invent a layer the dashboards do not know."""
    s = str(source or "").lower()
    if "boundary" in s or "react" in s:
        return "react"
    if "main" in s or "desktop" in s or "fatal" in s:
        return "electron_main"
    return "renderer"


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
    global _client, _initialized, _taxonomy_cache, _taxonomy_failed_logged
    _client = None
    _initialized = False
    _taxonomy_cache = None
    _taxonomy_failed_logged = False
    _counters.update({k: 0 for k in _counters})
    _session_uploads.clear()
    global _pending_violation, _reentrant
    _pending_violation = None
    _reported_violations.clear()
    _reentrant = False
