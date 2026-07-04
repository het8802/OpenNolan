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
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from lib import app_paths

MANIFEST_SCHEMA = 1

# Core requirement files (relative to code_root) installed eagerly so the backend + agent can run.
CORE_REQUIREMENTS = ("requirements-ui.txt", "requirements.txt")

ProgressCb = Callable[[str], None]

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
    if forced():
        return False
    return shutil.which("ffmpeg") is not None or (bin_dir() / "ffmpeg").exists()


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


def _run(cmd: list[str], progress: Optional[ProgressCb], env: Optional[dict] = None) -> None:
    """Run a subprocess, streaming stdout+stderr line-by-line to `progress`. Raises on non-zero exit."""
    if progress:
        progress(f"$ {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env={**os.environ, **(env or {})},
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        if progress:
            progress(line.rstrip())
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"command failed ({code}): {' '.join(cmd)}")


def _pip_install(target_python: Path, args: list[str], progress: Optional[ProgressCb]) -> None:
    """Install into `target_python`'s environment. Prefer uv (fast); fall back to that python's pip.
    Wheels ONLY (--only-binary=:all:) — a user has no compiler, so a source build = a dead first-run."""
    uv = _uv()
    if uv:
        _run([uv, "pip", "install", "--python", str(target_python), "--only-binary=:all:", *args], progress)
    else:
        _run([str(target_python), "-m", "pip", "install", "--only-binary=:all:", *args], progress)


# ── core provisioning ──────────────────────────────────────────────────────────

def provision_core(progress: Optional[ProgressCb] = None) -> None:
    """Build the managed venv and install core deps + ffmpeg. Atomic + idempotent."""
    rt = app_paths.runtime_dir()
    rt.mkdir(parents=True, exist_ok=True)
    building = rt / "venv.building"
    final = venv_dir()

    if progress:
        progress(f"Setting up OpenNolan runtime in {rt} …")

    # 1) fresh venv in a temp location (so a crash never leaves a half-venv at the real path)
    shutil.rmtree(building, ignore_errors=True)
    uv = _uv()
    if uv:
        _run([uv, "venv", "--python", base_python(), str(building)], progress)
    else:
        _run([base_python(), "-m", "venv", str(building)], progress)
    building_python = building / "bin" / "python"

    # 2) install the core requirement files (wheels only)
    req_args: list[str] = []
    for req in CORE_REQUIREMENTS:
        rp = app_paths.code_root() / req
        if rp.exists():
            req_args += ["-r", str(rp)]
    if not req_args:
        raise RuntimeError("no core requirement files found under code_root()")
    _pip_install(building_python, req_args, progress)

    # 3) verify the backend's hard deps actually import before we trust this venv
    _run([str(building_python), "-c", "import fastapi, uvicorn, pydantic"], progress)

    # 4) atomically swap the built venv into place
    shutil.rmtree(final, ignore_errors=True)
    os.replace(building, final)

    # 5) ffmpeg (best-effort; the editor degrades to 503 on scrub/export if it's absent)
    try:
        provision_ffmpeg(progress)
    except Exception as exc:  # don't fail core provisioning on an ffmpeg hiccup
        if progress:
            progress(f"[warn] ffmpeg provisioning failed: {exc} (you can retry from Settings)")

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
    _write_manifest(new_manifest)
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
        "OPENNOLAN_FFMPEG_URL", "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffmpeg.zip"),
    "ffprobe": os.environ.get(
        "OPENNOLAN_FFPROBE_URL", "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffprobe.zip"),
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


def provision_ffmpeg(progress: Optional[ProgressCb] = None) -> None:
    """Ensure ffmpeg + ffprobe are available. Dev: a PATH ffmpeg is used as-is. Packaged clean Mac:
    download a static arm64 build into runtime/bin, then dequarantine + ad-hoc sign so a notarized,
    hardened app can spawn it. main.js prepends runtime/bin to the child PATH so shutil.which finds it."""
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        if progress:
            progress("ffmpeg found on PATH — using it.")
        return
    bd = bin_dir()
    bd.mkdir(parents=True, exist_ok=True)
    for name, url in FFMPEG_URLS.items():
        dest = bd / name
        if dest.exists():
            continue
        expected = (FFMPEG_SHA256.get(name) or "").strip().lower()
        attempts = 2 if expected else 1  # a pin lets us retry a corrupt/partial download once
        for attempt in range(1, attempts + 1):
            if progress:
                progress(f"Downloading {name}…" + (f" (attempt {attempt})" if attempt > 1 else ""))
            _download_binary(url, dest)
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
                    f"{name} sha256 mismatch: expected {expected[:12]}…, got {actual[:12]}… (pin/url out of sync?)")
            if progress:
                progress(f"[warn] {name} sha mismatch — re-downloading")
        dest.chmod(0o755)
        # A binary WE downloaded isn't quarantined (Electron isn't sandboxed), but strip it anyway,
        # then ad-hoc sign so Gatekeeper never blocks the spawn under a hardened parent.
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(dest)], capture_output=True)
        subprocess.run(["codesign", "--force", "--sign", "-", str(dest)], capture_output=True)
    # verify
    _run([str(bd / "ffmpeg"), "-version"], progress)


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
    _run([npm, "ci", "--no-audit", "--no-fund", "--prefix", str(project)], progress,
         env={"npm_config_update_notifier": "false", "CI": "1"})


def _install_engine(name: str, src: Path, progress: Optional[ProgressCb]) -> Path:
    """Copy a READ-ONLY engine source out of the bundle into the WRITABLE runtime, then npm ci. Atomic:
    build in <name>.building and os.replace() into place, so a crash never leaves a half-install."""
    comp = composition_dir()
    comp.mkdir(parents=True, exist_ok=True)
    final = comp / name
    building = comp / f"{name}.building"
    shutil.rmtree(building, ignore_errors=True)
    # copy source only (any bundled/dev node_modules is stale for this machine — reinstall fresh)
    shutil.copytree(src, building, ignore=shutil.ignore_patterns("node_modules", ".git", "out", "dist"))
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


def provision_composition(progress: Optional[ProgressCb] = None) -> None:
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
    _install_engine("remotion", remo_src, progress)

    hf_src = app_paths.code_root() / "composition" / "hyperframes"
    if not (hf_src / "package.json").exists():
        raise RuntimeError(f"hyperframes pin not found at {hf_src} (ship a pinned package.json + lockfile)")
    _install_engine("hyperframes", hf_src, progress)

    _ensure_browsers(progress)

    m = _read_manifest()
    m["node_version"] = _node_id()
    m["composition_installed"] = True
    _write_manifest(m)
    if progress:
        progress("Video engines ready.")


def _download_binary(url: str, dest: Path) -> None:
    """Download url to dest. Handles a .zip (extract the single binary) or a raw binary."""
    tmp = dest.with_suffix(".download")
    with urllib.request.urlopen(url) as resp, open(tmp, "wb") as out:  # noqa: S310 (pinned/config'd URL)
        shutil.copyfileobj(resp, out)
    # zip? extract the member matching the target name; else it's the raw binary
    import zipfile
    if zipfile.is_zipfile(tmp):
        with zipfile.ZipFile(tmp) as zf:
            member = next((n for n in zf.namelist() if n.rstrip("/").endswith(dest.name)), zf.namelist()[0])
            with zf.open(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
        tmp.unlink(missing_ok=True)
    else:
        os.replace(tmp, dest)
