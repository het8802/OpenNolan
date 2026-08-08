"""First-run dependency provisioning + the dependency doctor (Lane E).

The bundled python-build-standalone interpreter (Lane D) has NO third-party packages, so on first
run we build a managed virtualenv and install deps into it. Core deps install eagerly (the backend
can't `import fastapi` without them); heavy ML installs LAZILY as on-demand capability packs.

Runs in TWO contexts, so it imports with the STANDARD LIBRARY ONLY (no fastapi/pydantic/etc.):
  1. by the BUNDLED interpreter via scripts/provision.py, BEFORE the venv exists, and
  2. in-process by the backend (running inside the venv) for lazy pack installs + /api/doctor.

Everything writable lives under app_paths.home()/runtime:
    runtime/venv                    the managed virtualenv (core deps + installed packs)
    runtime/bin                     downloaded binaries (ffmpeg, ffprobe — pinned + sha-verified)
    runtime/composition/remotion    Remotion project + node_modules (npm ci'd; OPN-3)
    runtime/composition/hyperframes HyperFrames install + node_modules (OPN-3)
    runtime/composition/browsers    the engines' headless browsers (env-routed cache)
    runtime/manifest.json  {schema, base_python, core_installed, packs:[...],
                            node_version, composition_installed} — staleness + doctor state

Provisioning is ATOMIC: the venv is built in runtime/venv.building and os.replace()'d into place, so a
crash never leaves a half-venv the app then trusts. Staleness: if the manifest's schema or base-python
differs from the current interpreter (e.g. an app auto-update bumped the bundled Python), venv_ok() is
False and the venv is rebuilt. OPENNOLAN_FORCE_PROVISION=1 forces the doctor to report everything missing
(so a dev whose machine already has everything can still watch the full provision flow).
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from typing import Callable, Optional

from lib import app_paths

MANIFEST_SCHEMA = 1

# Core requirement files (relative to code_root) installed eagerly so the backend + agent can run.
CORE_REQUIREMENTS = ("requirements-ui.txt", "requirements.txt")

ProgressCb = Callable[[str], None]

# How much of a failed command's output rides along on the RuntimeError, and the hard cap on the
# whole message — it ends up in a native dialog and a mailto body, neither of which tolerates a
# multi-kilobyte traceback line.
_RUN_TAIL_LINES = 15
_RUN_ERR_CHARS = 2000

# Per-socket-operation timeout for the ffmpeg downloads — the ONE network hop left on the path to a
# usable editor. `urlopen()` with no timeout blocks forever, so a host that accepts the connection and
# then goes quiet (a captive portal, a dead mirror, a corporate proxy that swallows the response) hangs
# first launch with a progress bar that never moves and nothing to report. Bounded, it fails in 30s with
# a message, and provision_core's best-effort catch degrades to "ffmpeg skipped — retry from Settings".
_DOWNLOAD_TIMEOUT = 30

# Determinate setup progress: (pct, end_pct, label). `pct` is where this step STARTS on the
# 0-100 scale of the CURRENT provision run; `end_pct` is where it will land when the step
# finishes, so the UI may creep the bar toward it while a long subprocess (uv/npm) is silent.
# Long downloads (ffmpeg) emit many frames with a REAL byte-derived pct. Consumers that only
# want text (the /api/provision endpoints) simply don't pass a StepCb.
StepCb = Callable[[float, float, str], None]


def _step(step: Optional[StepCb], pct: float, end: float, label: str) -> None:
    if step:
        step(pct, end, label)


# ── composition tier (OPN-3: Node + Remotion + HyperFrames) ───────────────────
# The two JS composition engines the agent renders with, plus their prerequisite Node runtime.
# Node ships as a bundled, signed binary (like the interpreter — main.js sets OPENNOLAN_NODE); the
# npm packages install at FIRST RUN into the WRITABLE runtime dir (the bundle is read-only), mirroring
# how the Python venv is populated. Installed EAGERLY in the setup window (AGENT_GUIDE requires the
# agent to offer BOTH runtimes), but BEST-EFFORT: a failure degrades to the ffmpeg-only path and NEVER
# blocks the editor from opening (Linear OPN-3).
NODE_FLOOR_MAJOR = 22  # HyperFrames' floor; Remotion 4.x is also happy here.


# ── capability packs (lazy) ───────────────────────────────────────────────────
# name -> pip packages + an import that proves it loaded + a human label + rough disk cost.
# The heavy ML that would bloat the download to multi-GB; installed only when a feature first needs it.
PACKS: dict[str, dict] = {
    "transcription": {
        "label": "Video understanding & captions",
        "pip": ["torch", "torchaudio", "faster-whisper", "whisperx"],
        "check": "faster_whisper",
        "size_mb": 2600,
    },
    "vision": {
        "label": "Auto-reframe & face tracking",
        "pip": ["mediapipe", "opencv-python-headless"],
        "check": "mediapipe",
        "size_mb": 400,
    },
    "bg-removal": {
        "label": "Background removal",
        "pip": ["rembg", "onnxruntime"],
        "check": "rembg",
        "size_mb": 300,
    },
    "beat-sync": {
        "label": "Music beat detection",
        "pip": ["librosa"],
        "check": "librosa",
        "size_mb": 250,
    },
    "tts": {
        "label": "Local text-to-speech",
        "pip": ["piper-tts"],
        "check": "piper",
        "size_mb": 120,
    },
}


# ── paths ─────────────────────────────────────────────────────────────────────


def venv_dir() -> Path:
    return app_paths.runtime_dir() / "venv"


def venv_python() -> Path:
    return venv_dir() / "bin" / "python"


def bin_dir() -> Path:
    return app_paths.runtime_dir() / "bin"


def manifest_path() -> Path:
    return app_paths.runtime_dir() / "manifest.json"


# ── composition paths ──────────────────────────────────────────────────────────
# The npm engines install into the writable runtime (the app bundle is read-only). We COPY the
# read-only composer/package sources out of code_root() into these dirs, then `npm ci` there, so
# source + node_modules stay colocated (Remotion resolves its own project) and everything is writable.


def composition_dir() -> Path:
    return app_paths.runtime_dir() / "composition"


def remotion_root() -> Path:
    """Writable Remotion project (copied from code_root/remotion-composer + npm ci'd here)."""
    return composition_dir() / "remotion"


def hyperframes_root() -> Path:
    """Writable HyperFrames install (pinned package.json from code_root/composition/hyperframes)."""
    return composition_dir() / "hyperframes"


def browsers_dir() -> Path:
    """Cache the engines' headless browsers here (env-routed) so nothing scatters into the user's ~."""
    return composition_dir() / "browsers"


def node_bin() -> Optional[str]:
    """The Node binary. Packaged: the bundled, signed node (OPENNOLAN_NODE, set by main.js). Dev: PATH."""
    env = os.environ.get("OPENNOLAN_NODE")
    if env and Path(env).exists():
        return env
    return shutil.which("node")


def _tool_beside_node(name: str, env_var: str) -> Optional[str]:
    """Locate npm/npx: explicit env override, then a sibling of the (bundled) node, then PATH."""
    env = os.environ.get(env_var)
    if env and Path(env).exists():
        return env
    node = node_bin()
    if node:
        sibling = Path(node).parent / name
        if sibling.exists():
            return str(sibling)
    return shutil.which(name)


def npm_bin() -> Optional[str]:
    return _tool_beside_node("npm", "OPENNOLAN_NPM")


def npx_bin() -> Optional[str]:
    return _tool_beside_node("npx", "OPENNOLAN_NPX")


def base_python() -> str:
    """The interpreter used to BUILD the venv. When run by scripts/provision.py this is the bundled
    interpreter (sys.executable); override with OPENNOLAN_PYTHON. Used for the base and for staleness."""
    return os.environ.get("OPENNOLAN_PYTHON") or sys.executable


def _base_python_id() -> str:
    """A stable id for the base interpreter so a version bump on app-update invalidates the venv."""
    try:
        out = subprocess.run([base_python(), "--version"], capture_output=True, text=True, timeout=15)
        return (out.stdout or out.stderr).strip() or platform.python_version()
    except Exception:
        return platform.python_version()


# ── manifest (atomic) ──────────────────────────────────────────────────────────


def _read_manifest() -> dict:
    p = manifest_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_manifest(data: dict) -> None:
    p = manifest_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".manifest.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── status / doctor ─────────────────────────────────────────────────────────────


def forced() -> bool:
    return os.environ.get("OPENNOLAN_FORCE_PROVISION", "").lower() in ("1", "true", "yes")


def venv_ok() -> bool:
    """True when a usable, current venv exists. False when missing, stale (schema/python drift), or forced."""
    if forced():
        return False
    if not venv_python().exists():
        return False
    m = _read_manifest()
    return m.get("schema") == MANIFEST_SCHEMA and m.get("base_python") == _base_python_id()


def core_ok() -> bool:
    return venv_ok() and bool(_read_manifest().get("core_installed"))


def ffmpeg_ok() -> bool:
    # BOTH binaries are required: probe_output/HDR-detection shell out to ffprobe, so an ffmpeg-only
    # environment still 503s on every probe. Report ready only when both resolve.
    if forced():
        return False

    def _have(name: str) -> bool:
        return shutil.which(name) is not None or (bin_dir() / name).exists()

    return _have("ffmpeg") and _have("ffprobe")


# ── composition status (OPN-3) ──────────────────────────────────────────────────


def _node_id() -> str:
    """A stable id for the Node runtime so a version bump on app-update invalidates the install."""
    node = node_bin()
    if not node:
        return ""
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=15)
        return (out.stdout or out.stderr).strip()
    except Exception:
        return ""


def _node_major() -> Optional[int]:
    ver = _node_id().lstrip("v")
    if not ver or "." not in ver:
        return None
    head = ver.split(".", 1)[0]
    return int(head) if head.isdigit() else None


def node_ok() -> bool:
    """True when a bundled/PATH Node >= NODE_FLOOR_MAJOR is available."""
    if forced():
        return False
    major = _node_major()
    return major is not None and major >= NODE_FLOOR_MAJOR


def remotion_ok() -> bool:
    """True when the writable Remotion project exists WITH node_modules installed."""
    if forced():
        return False
    root = remotion_root()
    return (root / "package.json").exists() and (root / "node_modules").exists()


def hyperframes_ok() -> bool:
    """True when the writable HyperFrames install has its local CLI binary (no live npm/npx needed)."""
    if forced():
        return False
    return (hyperframes_root() / "node_modules" / ".bin" / "hyperframes").exists()


def composition_ok() -> bool:
    """True when the WHOLE composition tier is present + current. False on Node-version drift (rebuild),
    mirroring venv_ok()'s base_python staleness check."""
    if forced():
        return False
    if not (node_ok() and remotion_ok() and hyperframes_ok()):
        return False
    m = _read_manifest()
    return bool(m.get("composition_installed")) and m.get("node_version") == _node_id()


def pack_installed(name: str) -> bool:
    if forced():
        return False
    return name in (_read_manifest().get("packs") or [])


def doctor() -> dict:
    """Structured provisioning status for /api/doctor + the setup UI."""
    return {
        "home": str(app_paths.home()),
        "forced": forced(),
        "base_python": _base_python_id(),
        "venv_ok": venv_ok(),
        "core_ok": core_ok(),
        "ffmpeg_ok": ffmpeg_ok(),
        "node_ok": node_ok(),
        "remotion_ok": remotion_ok(),
        "hyperframes_ok": hyperframes_ok(),
        "composition_ok": composition_ok(),
        "packs": {name: pack_installed(name) for name in PACKS},
        "pack_meta": {name: {"label": p["label"], "size_mb": p["size_mb"]} for name, p in PACKS.items()},
    }


# ── installer plumbing ───────────────────────────────────────────────────────────


def _uv() -> Optional[str]:
    """Locate uv: the bundled binary (OPENNOLAN_UV, set by main.js), then PATH. None -> use venv pip."""
    env = os.environ.get("OPENNOLAN_UV")
    if env and Path(env).exists():
        return env
    return shutil.which("uv")


def _wheels_dir() -> Optional[Path]:
    """Locate the CORE wheels vendored inside the app (OPENNOLAN_WHEELS, set by main.js -> the packaged
    Resources/wheels, built by scripts/vendor-wheels.mjs). None when the caller isn't asking for an
    offline install (dev); None while `offline=True` is a hard failure — see _pip_install."""
    env = os.environ.get("OPENNOLAN_WHEELS")
    if env and Path(env).exists():
        return Path(env)
    return None


def _run(cmd: list[str], progress: Optional[ProgressCb], env: Optional[dict] = None) -> None:
    """Run a subprocess, streaming stdout+stderr line-by-line to `progress`. Raises on non-zero exit
    with the tail of that output ATTACHED. uv exits 2 for EVERY failure mode (usage error, unreachable
    index, bad --python, unwritable target), so a bare "command failed (2)" carries no cause — that is
    literally all we got from a beta tester's dead first run. `progress` is not enough: it goes to the
    setup window, while the exception is what reaches the dialog, the email and analytics."""
    if progress:
        progress(f"$ {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, **(env or {})},
    )
    assert proc.stdout is not None
    tail: deque[str] = deque(maxlen=_RUN_TAIL_LINES)
    for line in proc.stdout:
        line = line.rstrip()
        tail.append(line)
        if progress:
            progress(line)
    code = proc.wait()
    if code != 0:
        msg = f"command failed ({code}): {' '.join(cmd)}"
        detail = "\n".join(tail)
        room = _RUN_ERR_CHARS - len(msg)
        if detail and room > 2:
            # Keep the END of the output — the cause is the last line, and one traceback line can be
            # kilobytes on its own. The command always survives; it is the smaller half.
            msg += "\n" + (detail if len(detail) < room else "…" + detail[-(room - 2) :])
        raise RuntimeError(msg)


def _pip_install(target_python: Path, args: list[str], progress: Optional[ProgressCb], offline: bool = False) -> None:
    """Install into `target_python`'s environment. Prefer uv (fast); fall back to that python's pip.
    Wheels ONLY (--only-binary=:all:) — a user has no compiler, so a source build = a dead first-run.

    `offline=True` (the CORE install only) installs from the wheels vendored inside the app instead of
    pypi.org. It is opt-in because the capability packs share this function and are deliberately still
    online — torch is not something we ship. `--no-cache` is LOAD-BEARING: `uv --offline` only disables
    the network, not uv's on-disk cache, so without it a wheel we forgot to vendor would still install
    on any machine with a warm ~/.cache/uv (the developer's) and the omission would first surface on a
    stranger's Mac. That is the exact bug class this exists to kill.

    So `offline=True` with NO wheels directory is a HARD FAILURE, never a quiet online install. The
    flags are added only when a wheels dir resolves, so a bundle missing Resources/wheels used to fall
    through to a normal pypi.org install — green on the developer's Mac (which has a network and a warm
    cache) and a mystery on the machine the redesign exists to protect. Same bug class, different
    missing thing."""
    uv = _uv()
    wheels = _wheels_dir() if offline else None
    if offline and wheels is None:
        raise RuntimeError(
            "offline install requested but the bundled Python wheels are missing "
            f"(OPENNOLAN_WHEELS={os.environ.get('OPENNOLAN_WHEELS') or 'unset'}) — this app bundle is "
            "incomplete. Reinstall OpenNolan, or rebuild it with `node scripts/vendor-wheels.mjs`."
        )
    if uv:
        local = ["--offline", "--no-cache", "--find-links", str(wheels)] if wheels else []
        _run([uv, "pip", "install", "--python", str(target_python), "--only-binary=:all:", *local, *args], progress)
    else:
        # No bundled/PATH uv (dev, or a broken bundle). pip's spelling of the same three flags — a
        # missing wheel must fail here, never silently fall through to the network.
        local = ["--no-index", "--no-cache-dir", "--find-links", str(wheels)] if wheels else []
        _run([str(target_python), "-m", "pip", "install", "--only-binary=:all:", *local, *args], progress)


# ── core provisioning ──────────────────────────────────────────────────────────


def provision_core(progress: Optional[ProgressCb] = None, step: Optional[StepCb] = None) -> None:
    """Build the managed venv and install core deps + ffmpeg. Atomic + idempotent."""
    rt = app_paths.runtime_dir()
    rt.mkdir(parents=True, exist_ok=True)
    building = rt / "venv.building"
    final = venv_dir()

    if progress:
        progress(f"Setting up OpenNolan runtime in {rt} …")

    # 1) fresh venv in a temp location (so a crash never leaves a half-venv at the real path).
    #    The venv needs pip: `uv venv` OMITS it, and lazy capability-pack installs shell out to
    #    `python -m pip`. We used to ask uv for it with `--seed`, but that RESOLVES PIP FROM PYPI —
    #    proven by the versions: --seed installs pip 26.2.1 while the bundled interpreter already
    #    carries ensurepip/_bundled/pip-25.0.1-py3-none-any.whl. That was a network round-trip on
    #    the critical path of first launch, and it is the exact call that died for a beta tester.
    #    `ensurepip` just unzips the wheel that ships inside the interpreter — fully offline.
    _step(step, 0, 3, "Creating Python environment…")
    shutil.rmtree(building, ignore_errors=True)
    uv = _uv()
    building_python = building / "bin" / "python"
    if uv:
        _run([uv, "venv", "--python", base_python(), str(building)], progress)
        _run([str(building_python), "-m", "ensurepip"], progress)
    else:
        _run([base_python(), "-m", "venv", str(building)], progress)  # stdlib venv seeds pip itself

    # 2) install the core requirement files — offline from the wheels vendored inside the app (see
    #    _pip_install + scripts/vendor-wheels.mjs). Only the PACKAGED app carries them, so that is the
    #    condition; a dev checkout asks for the online install EXPLICITLY rather than getting it as a
    #    silent fallback. Packaged with no wheels dir now raises instead of reaching pypi.org.
    req_args: list[str] = []
    for req in CORE_REQUIREMENTS:
        rp = app_paths.code_root() / req
        if rp.exists():
            req_args += ["-r", str(rp)]
    if not req_args:
        raise RuntimeError("no core requirement files found under code_root()")
    _step(step, 3, 55, "Installing Python packages…")
    _pip_install(building_python, req_args, progress, offline=app_paths.is_packaged())

    # 3) verify the backend's hard deps actually import before we trust this venv
    _step(step, 55, 58, "Verifying installation…")
    _run([str(building_python), "-c", "import fastapi, uvicorn, pydantic"], progress)

    # 4) atomically swap the built venv into place
    _step(step, 58, 60, "Activating environment…")
    shutil.rmtree(final, ignore_errors=True)
    os.replace(building, final)

    # 5) ffmpeg (best-effort; the editor degrades to 503 on scrub/export if it's absent)
    try:
        provision_ffmpeg(progress, step=step, span=(60.0, 97.0))
    except Exception as exc:  # don't fail core provisioning on an ffmpeg hiccup
        if progress:
            progress(f"[warn] ffmpeg provisioning failed: {exc} (you can retry from Settings)")
        _step(step, 97, 97, "ffmpeg skipped — you can retry from Settings.")

    # 6) record success (preserve composition state across a core rebuild; composition_ok() re-checks
    #    Node drift on its own, so carrying the keys is safe and avoids a needless re-install)
    m = _read_manifest()
    new_manifest = {
        "schema": MANIFEST_SCHEMA,
        "base_python": _base_python_id(),
        "core_installed": True,
        "packs": m.get("packs") or [],
    }
    for k in ("node_version", "composition_installed"):
        if k in m:
            new_manifest[k] = m[k]
    _step(step, 97, 100, "Finishing up…")
    _write_manifest(new_manifest)
    _step(step, 100, 100, "Core setup complete.")
    if progress:
        progress("Core setup complete.")


def provision_pack(name: str, progress: Optional[ProgressCb] = None) -> None:
    """Install a capability pack into the existing venv (lazy, on first use)."""
    if name not in PACKS:
        raise ValueError(f"unknown pack {name!r}; known: {sorted(PACKS)}")
    if not core_ok():
        raise RuntimeError("core runtime is not provisioned yet")
    pack = PACKS[name]
    if progress:
        progress(f"Installing '{pack['label']}' (~{pack['size_mb']} MB, one time)…")
    _pip_install(venv_python(), list(pack["pip"]), progress)
    # verify it actually loads in the venv (torch installing but not importing is the #1 real failure)
    _run([str(venv_python()), "-c", f"import {pack['check']}"], progress)
    m = _read_manifest()
    packs = set(m.get("packs") or [])
    packs.add(name)
    m["packs"] = sorted(packs)
    _write_manifest(m)
    if progress:
        progress(f"'{pack['label']}' ready.")


# ── ffmpeg ───────────────────────────────────────────────────────────────────────

# Static arm64 macOS ffmpeg/ffprobe (OPN-3: pinned + sha-verified, like the bundled Python/Node).
#
# PINNING CONTRACT: for a reproducible, "always the same binary" release, ship a VERSIONED url + the
# sha256 of the extracted binary. When a sha is set (here or via OPENNOLAN_FFMPEG_SHA256 /
# OPENNOLAN_FFPROBE_SHA256), the download is enforced against it and a mismatch re-downloads once then
# fails. When it's blank we fall back to the "latest" redirect UNVERIFIED and warn — acceptable in dev,
# NOT for a public build. Fill FFMPEG_SHA256 by running `python scripts/provision.py --print-ffmpeg-sha`
# against the versioned url you intend to ship, then paste the hashes here (and pin the url).
FFMPEG_URLS = {
    "ffmpeg": os.environ.get(
        "OPENNOLAN_FFMPEG_URL", "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffmpeg.zip"
    ),
    "ffprobe": os.environ.get(
        "OPENNOLAN_FFPROBE_URL", "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffprobe.zip"
    ),
}
# sha256 of the EXTRACTED binary (not the zip). Empty string = unpinned (dev only, warns). Fill before release.
FFMPEG_SHA256 = {
    "ffmpeg": os.environ.get("OPENNOLAN_FFMPEG_SHA256", ""),
    "ffprobe": os.environ.get("OPENNOLAN_FFPROBE_SHA256", ""),
}


def _sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def provision_ffmpeg(
    progress: Optional[ProgressCb] = None, step: Optional[StepCb] = None, span: tuple[float, float] = (0.0, 100.0)
) -> None:
    """Ensure ffmpeg + ffprobe are available. Dev: a PATH ffmpeg is used as-is. Packaged clean Mac:
    download a static arm64 build into runtime/bin, then dequarantine + ad-hoc sign so a notarized,
    hardened app can spawn it. main.js prepends runtime/bin to the child PATH so shutil.which finds it.

    `span` is this function's slice of the caller's 0-100 progress scale; each binary gets an equal
    sub-slice and reports REAL byte progress within it (Content-Length is known for these downloads)."""
    s0, s1 = span
    # PACKAGED: ALWAYS provision our own ffmpeg into runtime/bin — do NOT trust a `shutil.which` hit.
    # Provisioning may run with a richer PATH (e.g. a dev shell, or a system/Homebrew ffmpeg) than the
    # packaged BACKEND, which is Finder-launched with a minimal PATH (/usr/bin:/bin) + runtime/bin. A
    # system ffmpeg found here would NOT be on the backend's PATH at runtime → the old early-return
    # left runtime/bin empty and every probe/cut failed with "ffprobe not found". Our own copy in
    # runtime/bin (which main.js prepends) is the only reliable guarantee. DEV: a PATH ffmpeg is fine.
    if not app_paths.is_packaged() and shutil.which("ffmpeg") and shutil.which("ffprobe"):
        if progress:
            progress("ffmpeg found on PATH — using it (dev).")
        _step(step, s1, s1, "ffmpeg ready.")
        return
    bd = bin_dir()
    bd.mkdir(parents=True, exist_ok=True)
    names = list(FFMPEG_URLS.items())
    per = (s1 - s0) / max(len(names), 1)
    for i, (name, url) in enumerate(names):
        f0, f1 = s0 + i * per, s0 + (i + 1) * per
        dest = bd / name
        expected = (FFMPEG_SHA256.get(name) or "").strip().lower()
        if dest.exists():
            # A pin has to apply RETROACTIVELY. This check used to run BEFORE the sha was read, so a
            # binary downloaded during the unpinned era (or a truncated/corrupt write) stayed trusted
            # forever and no later pin could ever reach it. Unpinned: keep today's behaviour.
            if not expected or _sha256_file(dest).lower() == expected:
                _step(step, f1, f1, f"{name} already present.")
                continue
            if progress:
                progress(f"[warn] existing {name} fails the sha256 pin — re-downloading.")
            dest.unlink(missing_ok=True)
        attempts = 2 if expected else 1  # a pin lets us retry a corrupt/partial download once
        for attempt in range(1, attempts + 1):
            if progress:
                progress(f"Downloading {name}…" + (f" (attempt {attempt})" if attempt > 1 else ""))
            _step(step, f0, f1, f"Downloading {name}…")
            # Real byte progress, throttled to ~0.5% increments so we don't flood the NDJSON pipe.
            last_emit = [f0]

            def on_bytes(read: int, total: Optional[int], _f0=f0, _f1=f1, _name=name, _last=last_emit) -> None:
                if not step or not total:
                    return
                pct = _f0 + (read / total) * (_f1 - _f0)
                if pct - _last[0] >= 0.5 or read >= total:
                    _last[0] = pct
                    mb, mb_total = read // (1 << 20), max(1, total // (1 << 20))
                    _step(step, pct, _f1, f"Downloading {_name}… {mb} / {mb_total} MB")

            _download_binary(url, dest, on_bytes)
            if not expected:
                if progress:
                    progress(f"[warn] {name} is UNPINNED (no sha256) — OK in dev, MUST pin before release.")
                break
            actual = _sha256_file(dest).lower()
            if actual == expected:
                if progress:
                    progress(f"{name} sha256 OK ({actual[:12]}…)")
                break
            dest.unlink(missing_ok=True)  # never trust a mismatched binary
            if attempt == attempts:
                raise RuntimeError(
                    f"{name} sha256 mismatch: expected {expected[:12]}…, got {actual[:12]}… (pin/url out of sync?)"
                )
            if progress:
                progress(f"[warn] {name} sha mismatch — re-downloading")
        dest.chmod(0o755)
        # A binary WE downloaded isn't quarantined (Electron isn't sandboxed), but strip it anyway,
        # then ad-hoc sign so Gatekeeper never blocks the spawn under a hardened parent.
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(dest)], capture_output=True)
        subprocess.run(["codesign", "--force", "--sign", "-", str(dest)], capture_output=True)
    # verify BOTH binaries actually execute — ffprobe is downloaded from an independent URL and is
    # what probe_output/HDR-detection call, so a corrupt/incompatible ffprobe that slipped past an
    # ffmpeg-only check would still 503 every probe while provisioning reported success.
    _step(step, s1, s1, "Verifying ffmpeg…")
    _run([str(bd / "ffmpeg"), "-version"], progress)
    _run([str(bd / "ffprobe"), "-version"], progress)


def print_ffmpeg_shas(progress: Optional[ProgressCb] = None) -> dict:
    """Download the CONFIGURED ffmpeg/ffprobe to a temp dir and return their sha256 — the fill-in step for
    the FFMPEG_SHA256 pins. Point OPENNOLAN_FFMPEG_URL/OPENNOLAN_FFPROBE_URL at a VERSIONED build first."""
    out: dict[str, str] = {}
    tmpd = Path(tempfile.mkdtemp(prefix="ffmpeg-sha-"))
    try:
        for name, url in FFMPEG_URLS.items():
            dest = tmpd / name
            if progress:
                progress(f"Downloading {name} to hash…")
            _download_binary(url, dest)
            out[name] = _sha256_file(dest)
            if progress:
                progress(f"{name}: {out[name]}")
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    return out


# ── composition provisioning (OPN-3) ────────────────────────────────────────────


def _npm_ci(project: Path, progress: Optional[ProgressCb]) -> None:
    """Deterministic install of a project's committed lockfile into <project>/node_modules. Wheels-equivalent:
    npm ci fails (rather than mutating the lockfile) if package.json and the lock disagree."""
    npm = npm_bin()
    if not npm:
        raise RuntimeError(f"npm not found (bundled Node missing?) — need Node >= {NODE_FLOOR_MAJOR}")
    _run(
        [npm, "ci", "--no-audit", "--no-fund", "--prefix", str(project)],
        progress,
        env={"npm_config_update_notifier": "false", "CI": "1"},
    )


def _install_engine(
    name: str,
    src: Path,
    progress: Optional[ProgressCb],
    step: Optional[StepCb] = None,
    span: tuple[float, float] = (0.0, 100.0),
    label: Optional[str] = None,
) -> Path:
    """Copy a READ-ONLY engine source out of the bundle into the WRITABLE runtime, then npm ci. Atomic:
    build in <name>.building and os.replace() into place, so a crash never leaves a half-install."""
    s0, s1 = span
    what = label or name
    comp = composition_dir()
    comp.mkdir(parents=True, exist_ok=True)
    final = comp / name
    building = comp / f"{name}.building"
    shutil.rmtree(building, ignore_errors=True)
    # copy source only (any bundled/dev node_modules is stale for this machine — reinstall fresh)
    copy_end = s0 + (s1 - s0) * 0.15
    _step(step, s0, copy_end, f"Preparing {what}…")
    shutil.copytree(src, building, ignore=shutil.ignore_patterns("node_modules", ".git", "out", "dist"))
    _step(step, copy_end, s1, f"Installing {what} packages…")
    _npm_ci(building, progress)
    shutil.rmtree(final, ignore_errors=True)
    os.replace(building, final)
    return final


def _ensure_browsers(progress: Optional[ProgressCb]) -> None:
    """Pre-fetch each engine's headless browser into the app cache so the FIRST render needs no download.
    BEST-EFFORT: log + continue on failure — the engines re-fetch on demand, so this never blocks."""
    bdir = browsers_dir()
    bdir.mkdir(parents=True, exist_ok=True)
    env = {
        "REMOTION_BROWSER_CACHE": str(bdir),
        "PUPPETEER_CACHE_DIR": str(bdir),
        "PLAYWRIGHT_BROWSERS_PATH": str(bdir),
    }
    remo = remotion_root() / "node_modules" / ".bin" / "remotion"
    if remo.exists():
        try:
            _run([str(remo), "browser", "ensure"], progress, env=env)
        except Exception as exc:  # non-fatal: Remotion fetches Chrome Headless Shell on first render
            if progress:
                progress(f"[warn] Remotion browser pre-fetch skipped ({exc}); fetched on first render.")
    hf = hyperframes_root() / "node_modules" / ".bin" / "hyperframes"
    if hf.exists():
        try:
            _run([str(hf), "doctor"], progress, env=env)
        except Exception as exc:  # non-fatal
            if progress:
                progress(f"[warn] HyperFrames pre-warm skipped ({exc}); fetched on first render.")


def provision_composition(progress: Optional[ProgressCb] = None, step: Optional[StepCb] = None) -> None:
    """Install the composition tier (Remotion + HyperFrames) into the writable runtime. Requires the
    bundled Node. Atomic + idempotent + manifest-tracked (staleness keyed to the Node version).

    Called EAGERLY in the setup window but BEST-EFFORT by the caller: main.js/ensureProvisioned must NOT
    treat a failure here as fatal — the editor still opens on the ffmpeg-only path (AGENT_GUIDE degraded
    mode). Raising here is fine; the caller catches it. See the OPN-3 plan."""
    if not node_ok():
        raise RuntimeError(f"Node.js >= {NODE_FLOOR_MAJOR} not available (bundled node missing?)")
    if progress:
        progress("Setting up video engines (Remotion + HyperFrames)…")

    remo_src = app_paths.code_root() / "remotion-composer"
    if not (remo_src / "package.json").exists():
        raise RuntimeError(f"remotion-composer source not found at {remo_src}")
    _install_engine("remotion", remo_src, progress, step=step, span=(0.0, 38.0), label="Remotion")

    hf_src = app_paths.code_root() / "composition" / "hyperframes"
    if not (hf_src / "package.json").exists():
        raise RuntimeError(f"hyperframes pin not found at {hf_src} (ship a pinned package.json + lockfile)")
    _install_engine("hyperframes", hf_src, progress, step=step, span=(38.0, 66.0), label="HyperFrames")

    _step(step, 66, 97, "Downloading render browser…")
    _ensure_browsers(progress)

    _step(step, 97, 100, "Finishing up…")
    m = _read_manifest()
    m["node_version"] = _node_id()
    m["composition_installed"] = True
    _write_manifest(m)
    _step(step, 100, 100, "Video engines ready.")
    if progress:
        progress("Video engines ready.")


def _download_binary(url: str, dest: Path, on_bytes: Optional[Callable[[int, Optional[int]], None]] = None) -> None:
    """Download url to dest. Handles a .zip (extract the single binary) or a raw binary.
    `on_bytes(read, total_or_None)` fires per chunk so callers can surface real download progress."""
    tmp = dest.with_suffix(".download")
    try:
        with (
            urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT) as resp,  # noqa: S310 (config'd URL)
            open(tmp, "wb") as out,
        ):
            total: Optional[int] = None
            try:
                cl = resp.headers.get("Content-Length")
                total = int(cl) if cl else None
            except (TypeError, ValueError):
                total = None
            read = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                read += len(chunk)
                if on_bytes:
                    on_bytes(read, total)
    except (TimeoutError, urllib.error.URLError) as exc:
        # A connect-phase timeout arrives WRAPPED in URLError; a read-phase one is raised bare. Either
        # way a raw "timed out" in the failure dialog names neither the file nor the host, which is the
        # same illegible dead end as the bare "command failed (2)" _run() exists to prevent.
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"downloading {dest.name} from {url} failed: {getattr(exc, 'reason', exc)}") from exc
    # zip? extract the member matching the target name; else it's the raw binary. Extraction goes
    # through a temp file + os.replace so a kill mid-extract (setup-window cancel) can never leave
    # a truncated binary at dest — dest.exists() is trusted as "complete" on the next run.
    import zipfile

    if zipfile.is_zipfile(tmp):
        extracted = dest.with_suffix(".extract")
        with zipfile.ZipFile(tmp) as zf:
            member = next((n for n in zf.namelist() if n.rstrip("/").endswith(dest.name)), zf.namelist()[0])
            with zf.open(member) as src, open(extracted, "wb") as out:
                shutil.copyfileobj(src, out)
        os.replace(extracted, dest)
        tmp.unlink(missing_ok=True)
    else:
        os.replace(tmp, dest)
