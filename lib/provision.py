"""First-run dependency provisioning + the dependency doctor (Lane E).

The bundled python-build-standalone interpreter (Lane D) has NO third-party packages, so on first
run we build a managed virtualenv and install deps into it. Core deps install eagerly (the backend
can't `import fastapi` without them); heavy ML installs LAZILY as on-demand capability packs.

Runs in TWO contexts, so it imports with the STANDARD LIBRARY ONLY (no fastapi/pydantic/etc.):
  1. by the BUNDLED interpreter via scripts/provision.py, BEFORE the venv exists, and
  2. in-process by the backend (running inside the venv) for lazy pack installs + /api/doctor.

Everything writable lives under app_paths.home()/runtime:
    runtime/venv           the managed virtualenv (core deps + installed packs)
    runtime/bin            downloaded binaries (ffmpeg, ffprobe)
    runtime/manifest.json  {schema, base_python, core_installed, packs:[...]} — staleness + doctor state

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

    # 6) record success
    m = _read_manifest()
    _write_manifest({
        "schema": MANIFEST_SCHEMA,
        "base_python": _base_python_id(),
        "core_installed": True,
        "packs": m.get("packs") or [],
    })
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

# Static arm64 macOS ffmpeg/ffprobe. Overridable via env; defaults to Martin Riedl's arm64 redirect.
# NOTE: verify/pin these + their sha before a public release (like the publish placeholders).
FFMPEG_URLS = {
    "ffmpeg": os.environ.get(
        "OPENNOLAN_FFMPEG_URL", "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffmpeg.zip"),
    "ffprobe": os.environ.get(
        "OPENNOLAN_FFPROBE_URL", "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffprobe.zip"),
}


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
        if progress:
            progress(f"Downloading {name}…")
        _download_binary(url, dest)
        dest.chmod(0o755)
        # A binary WE downloaded isn't quarantined (Electron isn't sandboxed), but strip it anyway,
        # then ad-hoc sign so Gatekeeper never blocks the spawn under a hardened parent.
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(dest)], capture_output=True)
        subprocess.run(["codesign", "--force", "--sign", "-", str(dest)], capture_output=True)
    # verify
    _run([str(bd / "ffmpeg"), "-version"], progress)


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
