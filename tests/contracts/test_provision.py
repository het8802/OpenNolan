"""Contract tests for lib.provision (first-run bootstrapper) + the /api/doctor + /api/provision endpoints.

We do NOT run a real venv build or a 2.6GB pack install here — those are exercised end-to-end by hand
against the bundled interpreter. These tests lock the pure logic: status/doctor, staleness, the pack
registry, atomic manifest writes, and the endpoint contracts (mocking the heavy install).
"""

from __future__ import annotations

import json
import sys

import pytest

from lib import provision


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENNOLAN_HOME", str(tmp_path))
    monkeypatch.delenv("OPENNOLAN_FORCE_PROVISION", raising=False)
    yield


# ── status / doctor ──────────────────────────────────────────────────────────

def test_fresh_home_reports_unprovisioned():
    assert provision.venv_ok() is False
    assert provision.core_ok() is False
    d = provision.doctor()
    assert d["venv_ok"] is False and d["core_ok"] is False
    assert set(d["packs"]) == set(provision.PACKS)
    assert all(v is False for v in d["packs"].values())


def test_force_provision_flag_reports_everything_missing(monkeypatch):
    monkeypatch.setenv("OPENNOLAN_FORCE_PROVISION", "1")
    assert provision.forced() is True
    assert provision.venv_ok() is False
    assert provision.ffmpeg_ok() is False  # forced hides even a PATH ffmpeg
    assert provision.pack_installed("transcription") is False


def test_pack_registry_wellformed():
    for name, p in provision.PACKS.items():
        assert p["pip"] and isinstance(p["pip"], list)
        assert isinstance(p["check"], str) and p["check"]
        assert p["label"] and p["size_mb"] > 0


def test_manifest_atomic_roundtrip(tmp_path):
    provision._write_manifest({"schema": provision.MANIFEST_SCHEMA, "base_python": "Python 3.12.13",
                               "core_installed": True, "packs": ["beat-sync"]})
    assert provision._read_manifest()["packs"] == ["beat-sync"]
    assert list((tmp_path / "runtime").glob(".manifest.*")) == []  # no temp files left


def test_venv_ok_true_only_when_manifest_matches(monkeypatch, tmp_path):
    # simulate a provisioned venv: create the venv python + a matching manifest
    vp = provision.venv_python()
    vp.parent.mkdir(parents=True, exist_ok=True)
    vp.write_text("")  # placeholder file at the venv python path
    monkeypatch.setattr(provision, "_base_python_id", lambda: "Python 3.12.13")
    provision._write_manifest({"schema": provision.MANIFEST_SCHEMA, "base_python": "Python 3.12.13",
                               "core_installed": True, "packs": []})
    assert provision.venv_ok() is True and provision.core_ok() is True
    # a base-python drift (e.g. app update bumped Python) invalidates it -> rebuild
    monkeypatch.setattr(provision, "_base_python_id", lambda: "Python 3.13.0")
    assert provision.venv_ok() is False


def test_provision_pack_rejects_unknown():
    with pytest.raises(ValueError):
        provision.provision_pack("nope")


# ── failure legibility (_run) ─────────────────────────────────────────────────

def test_run_attaches_command_output_to_the_error():
    # A beta tester's first launch died with a bare "command failed (2)" and we could not diagnose it:
    # uv exits 2 for EVERY failure mode, so the cause has to ride along on the exception itself.
    with pytest.raises(RuntimeError) as ei:
        provision._run([sys.executable, "-c", "import sys; print('the real reason'); sys.exit(2)"], None)
    msg = str(ei.value)
    assert "command failed (2)" in msg
    assert "the real reason" in msg


def test_run_error_is_capped_but_keeps_the_tail():
    # The message lands in a native dialog and a mailto body; one traceback line can be kilobytes.
    with pytest.raises(RuntimeError) as ei:
        provision._run([sys.executable, "-c",
                        "import sys; print('x' * 9000); print('LAST LINE'); sys.exit(1)"], None)
    msg = str(ei.value)
    assert len(msg) <= provision._RUN_ERR_CHARS
    assert "LAST LINE" in msg  # the END of the output survives — that's where the cause is


# ── offline pip (ensurepip, not `uv venv --seed`) ─────────────────────────────

def test_core_venv_seeds_pip_from_the_bundled_wheel(monkeypatch, tmp_path):
    """`uv venv --seed` RESOLVES PIP FROM PYPI (it installs 26.2.1 while the bundled interpreter
    carries ensurepip/_bundled/pip-25.0.1) — a network hop on the critical path of first launch, and
    the exact call that failed for a beta tester. ensurepip unzips the bundled wheel instead."""
    code = tmp_path / "code"
    code.mkdir()
    (code / "requirements.txt").write_text("fastapi\n")
    monkeypatch.setenv("OPENNOLAN_CODE_ROOT", str(code))
    monkeypatch.setattr(provision, "_uv", lambda: "/fake/uv")
    monkeypatch.setattr(provision, "_pip_install", lambda *a, **k: None)
    monkeypatch.setattr(provision, "provision_ffmpeg", lambda *a, **k: None)
    cmds: list[list[str]] = []

    def fake_run(cmd, progress=None, env=None):
        cmds.append(cmd)
        if "venv" in cmd:  # the real uv would create it; os.replace() below needs it to exist
            (provision.app_paths.runtime_dir() / "venv.building" / "bin").mkdir(parents=True)

    monkeypatch.setattr(provision, "_run", fake_run)
    provision.provision_core()
    venv_cmd = next(c for c in cmds if "venv" in c)
    assert "--seed" not in venv_cmd  # --seed = a PyPI round-trip we no longer make
    assert any(c[-2:] == ["-m", "ensurepip"] for c in cmds)


def test_core_install_uses_the_bundled_wheels_offline(monkeypatch, tmp_path):
    """First launch must not reach pypi.org: scripts/vendor-wheels.mjs ships the core wheels inside the
    app and main.js points OPENNOLAN_WHEELS at them. `--no-cache` is LOAD-BEARING — `uv --offline`
    disables the network but NOT ~/.cache/uv, so without it a wheel we forgot to vendor still installs
    on a warm cache (the developer's) and the omission first surfaces on a stranger's Mac."""
    code = tmp_path / "code"
    code.mkdir()
    (code / "requirements.txt").write_text("fastapi\n")
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    monkeypatch.setenv("OPENNOLAN_CODE_ROOT", str(code))
    monkeypatch.setenv("OPENNOLAN_WHEELS", str(wheels))
    monkeypatch.setattr(provision, "_uv", lambda: "/fake/uv")
    monkeypatch.setattr(provision, "provision_ffmpeg", lambda *a, **k: None)
    cmds: list[list[str]] = []

    def fake_run(cmd, progress=None, env=None):
        cmds.append(cmd)
        if "venv" in cmd:
            (provision.app_paths.runtime_dir() / "venv.building" / "bin").mkdir(parents=True)

    monkeypatch.setattr(provision, "_run", fake_run)
    provision.provision_core()
    install = next(c for c in cmds if "install" in c)
    assert "--offline" in install and "--no-cache" in install
    assert install[install.index("--find-links") + 1] == str(wheels)


def test_capability_packs_stay_online_even_with_bundled_wheels(monkeypatch, tmp_path):
    """The packs (torch/onnxruntime, GBs) are deliberately NOT vendored, and they share _pip_install —
    so the offline flags are opt-in per call. Applying them unconditionally would make every pack
    install resolve against the core wheels dir and fail."""
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    monkeypatch.setenv("OPENNOLAN_WHEELS", str(wheels))
    monkeypatch.setattr(provision, "_uv", lambda: "/fake/uv")
    cmds: list[list[str]] = []
    monkeypatch.setattr(provision, "_run", lambda cmd, progress=None, env=None: cmds.append(cmd))
    provision._pip_install(provision.venv_python(), ["torch"], None)
    assert not any(f in cmds[0] for f in ("--offline", "--no-cache", "--find-links"))


def test_offline_install_without_wheels_fails_instead_of_going_online(monkeypatch, tmp_path):
    """A missing wheels dir used to turn the offline core install back into an ONLINE one: the flags
    are added only when a dir resolves, so `offline=True` + no dir called uv with no --offline at all.
    That is green on the developer's Mac (network + warm cache) and a mystery everywhere else — the
    same bug class the vendoring exists to kill. It must fail BEFORE any subprocess runs."""
    monkeypatch.setenv("OPENNOLAN_WHEELS", str(tmp_path / "definitely" / "missing"))
    monkeypatch.setattr(provision, "_uv", lambda: "/fake/uv")
    cmds: list[list[str]] = []
    monkeypatch.setattr(provision, "_run", lambda cmd, progress=None, env=None: cmds.append(cmd))
    with pytest.raises(RuntimeError, match="wheels are missing"):
        provision._pip_install(provision.venv_python(), ["-r", "requirements.txt"], None, offline=True)
    assert cmds == []  # nothing was spawned — no online install slipped through


def test_dev_core_install_asks_for_online_explicitly(monkeypatch, tmp_path):
    """Dev (unpackaged, no vendored wheels) still installs from pypi.org — but because provision_core
    PASSES offline=False, not because the offline path quietly degraded when the dir was absent."""
    code = tmp_path / "code"
    code.mkdir()
    (code / "requirements.txt").write_text("fastapi\n")
    monkeypatch.delenv("OPENNOLAN_CODE_ROOT", raising=False)  # unpackaged
    monkeypatch.delenv("OPENNOLAN_WHEELS", raising=False)
    monkeypatch.setattr(provision.app_paths, "code_root", lambda: code)
    monkeypatch.setattr(provision, "_uv", lambda: "/fake/uv")
    monkeypatch.setattr(provision, "provision_ffmpeg", lambda *a, **k: None)
    cmds: list[list[str]] = []

    def fake_run(cmd, progress=None, env=None):
        cmds.append(cmd)
        if "venv" in cmd:
            (provision.app_paths.runtime_dir() / "venv.building" / "bin").mkdir(parents=True)

    monkeypatch.setattr(provision, "_run", fake_run)
    provision.provision_core()
    install = next(c for c in cmds if "install" in c)
    assert not any(f in install for f in ("--offline", "--no-index", "--find-links"))


def test_wheels_dir_is_none_unless_the_path_really_exists(monkeypatch):
    # Dev (unset) and an older packaged build (var set, dir absent) both fall back to the online install.
    monkeypatch.delenv("OPENNOLAN_WHEELS", raising=False)
    assert provision._wheels_dir() is None
    monkeypatch.setenv("OPENNOLAN_WHEELS", "/nope/wheels")
    assert provision._wheels_dir() is None


# ── composition tier (OPN-3: Node + Remotion + HyperFrames) ────────────────────

def _fake_node(monkeypatch, version="v22.5.0"):
    monkeypatch.setattr(provision, "node_bin", lambda: "/fake/node")
    monkeypatch.setattr(provision, "_node_id", lambda: version)


def _fake_engines_on_disk():
    remo = provision.remotion_root()
    (remo / "node_modules").mkdir(parents=True, exist_ok=True)
    (remo / "package.json").write_text("{}")
    hf_bin = provision.hyperframes_root() / "node_modules" / ".bin"
    hf_bin.mkdir(parents=True, exist_ok=True)
    (hf_bin / "hyperframes").write_text("")


def test_fresh_home_reports_no_composition(monkeypatch):
    monkeypatch.setattr(provision, "_node_id", lambda: "")  # no node on a clean box
    assert provision.node_ok() is False
    assert provision.remotion_ok() is False
    assert provision.hyperframes_ok() is False
    assert provision.composition_ok() is False
    d = provision.doctor()
    for k in ("node_ok", "remotion_ok", "hyperframes_ok", "composition_ok"):
        assert d[k] is False


def test_node_ok_respects_floor(monkeypatch):
    monkeypatch.setattr(provision, "node_bin", lambda: "/fake/node")
    monkeypatch.setattr(provision, "_node_id", lambda: "v20.11.0")
    assert provision.node_ok() is False  # below the >= 22 floor
    monkeypatch.setattr(provision, "_node_id", lambda: "v22.5.0")
    assert provision.node_ok() is True


def test_composition_ok_requires_all_parts_and_node_match(monkeypatch):
    _fake_node(monkeypatch, "v22.5.0")
    _fake_engines_on_disk()
    assert provision.remotion_ok() and provision.hyperframes_ok()
    assert provision.composition_ok() is False  # engines on disk but manifest not stamped yet
    provision._write_manifest({"schema": provision.MANIFEST_SCHEMA, "composition_installed": True,
                               "node_version": "v22.5.0"})
    assert provision.composition_ok() is True
    # Node-version drift (an app update bumped Node) invalidates -> rebuild, like base_python drift
    monkeypatch.setattr(provision, "_node_id", lambda: "v24.0.0")
    assert provision.composition_ok() is False


def test_force_provision_hides_composition(monkeypatch):
    _fake_node(monkeypatch)
    _fake_engines_on_disk()
    provision._write_manifest({"schema": provision.MANIFEST_SCHEMA, "composition_installed": True,
                               "node_version": "v22.5.0"})
    monkeypatch.setenv("OPENNOLAN_FORCE_PROVISION", "1")
    assert provision.node_ok() is False and provision.composition_ok() is False


def test_provision_composition_requires_node(monkeypatch):
    monkeypatch.setattr(provision, "_node_id", lambda: "")  # no node -> refuse
    with pytest.raises(RuntimeError):
        provision.provision_composition()


def test_provision_composition_records_manifest(monkeypatch, tmp_path):
    _fake_node(monkeypatch, "v22.7.0")
    code = tmp_path / "code"
    (code / "remotion-composer").mkdir(parents=True)
    (code / "remotion-composer" / "package.json").write_text("{}")
    (code / "composition" / "hyperframes").mkdir(parents=True)
    (code / "composition" / "hyperframes" / "package.json").write_text("{}")
    monkeypatch.setenv("OPENNOLAN_CODE_ROOT", str(code))
    installed: list[str] = []
    monkeypatch.setattr(provision, "_install_engine",
                        lambda name, src, prog, **kw: installed.append(name))
    monkeypatch.setattr(provision, "_ensure_browsers", lambda prog: None)
    provision.provision_composition()
    assert installed == ["remotion", "hyperframes"]  # both engines, Remotion first
    m = provision._read_manifest()
    assert m["composition_installed"] is True and m["node_version"] == "v22.7.0"


def test_provision_composition_step_frames_monotonic(monkeypatch, tmp_path):
    # The setup-window progress contract: step(pct, end, label) frames never move backwards,
    # end >= pct, and the run finishes at exactly (100, 100). desktop/setup.html relies on this.
    _fake_node(monkeypatch, "v22.7.0")
    code = tmp_path / "code"
    (code / "remotion-composer").mkdir(parents=True)
    (code / "remotion-composer" / "package.json").write_text("{}")
    (code / "composition" / "hyperframes").mkdir(parents=True)
    (code / "composition" / "hyperframes" / "package.json").write_text("{}")
    monkeypatch.setenv("OPENNOLAN_CODE_ROOT", str(code))
    monkeypatch.setattr(provision, "_npm_ci", lambda project, prog: None)
    monkeypatch.setattr(provision, "_ensure_browsers", lambda prog: None)
    frames: list[tuple[float, float, str]] = []
    provision.provision_composition(None, step=lambda pct, end, label: frames.append((pct, end, label)))
    assert frames and frames[-1] == (100, 100, "Video engines ready.")
    prev = -1.0
    for pct, end, label in frames:
        assert pct >= prev and end >= pct and label
        prev = pct


def test_install_engine_atomic_on_failure(monkeypatch, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "package.json").write_text("{}")

    def boom(project, progress):
        raise RuntimeError("npm ci failed")

    monkeypatch.setattr(provision, "_npm_ci", boom)
    with pytest.raises(RuntimeError):
        provision._install_engine("remotion", src, None)
    # the app must never see a half-install at the trusted final path
    assert not (provision.composition_dir() / "remotion").exists()


def test_core_rebuild_preserves_composition_keys(monkeypatch, tmp_path):
    # a core rebuild (python drift) must NOT wipe an already-installed composition tier
    provision._write_manifest({"schema": provision.MANIFEST_SCHEMA, "base_python": "Python 3.12.13",
                               "core_installed": True, "packs": ["beat-sync"],
                               "node_version": "v22.5.0", "composition_installed": True})
    # exercise just the manifest-preservation tail of provision_core without a real venv build
    m = provision._read_manifest()
    new_manifest = {"schema": provision.MANIFEST_SCHEMA, "base_python": "Python 3.13.0",
                    "core_installed": True, "packs": m.get("packs") or []}
    for k in ("node_version", "composition_installed"):
        if k in m:
            new_manifest[k] = m[k]
    provision._write_manifest(new_manifest)
    m2 = provision._read_manifest()
    assert m2["composition_installed"] is True and m2["node_version"] == "v22.5.0"


# ── ffmpeg pinning (OPN-3) ──────────────────────────────────────────────────────

def test_ffmpeg_sha_mismatch_never_trusts_binary(monkeypatch):
    monkeypatch.setattr(provision.shutil, "which", lambda _x: None)  # force the download path
    monkeypatch.setitem(provision.FFMPEG_SHA256, "ffmpeg", "de" * 32)  # a pin that won't match
    monkeypatch.setattr(provision, "_download_binary",
                        lambda url, dest, on_bytes=None: dest.write_bytes(b"not-ffmpeg"))
    with pytest.raises(RuntimeError, match="sha256 mismatch"):
        provision.provision_ffmpeg()
    assert not (provision.bin_dir() / "ffmpeg").exists()  # mismatched binary removed, not trusted


def test_ffmpeg_pin_applies_to_a_binary_already_on_disk(monkeypatch):
    # The `dest.exists() -> continue` shortcut used to run BEFORE the sha was read, so a binary
    # downloaded during the unpinned era stayed trusted forever and a later pin never reached it.
    import hashlib
    monkeypatch.setattr(provision.shutil, "which", lambda _x: None)  # force the download path
    provision.bin_dir().mkdir(parents=True, exist_ok=True)
    for name in provision.FFMPEG_URLS:
        (provision.bin_dir() / name).write_bytes(b"stale-unpinned-binary")
    monkeypatch.setitem(provision.FFMPEG_SHA256, "ffmpeg", hashlib.sha256(b"good").hexdigest())
    monkeypatch.setitem(provision.FFMPEG_SHA256, "ffprobe", hashlib.sha256(b"good").hexdigest())
    monkeypatch.setattr(provision, "_download_binary",
                        lambda url, dest, on_bytes=None: dest.write_bytes(b"good"))
    monkeypatch.setattr(provision, "_run", lambda *a, **k: None)  # the `-version` probe can't exec a stub
    provision.provision_ffmpeg()
    for name in provision.FFMPEG_URLS:
        assert (provision.bin_dir() / name).read_bytes() == b"good"  # re-downloaded, not trusted


def test_ffmpeg_existing_binary_kept_when_it_matches_or_is_unpinned(monkeypatch):
    import hashlib
    monkeypatch.setattr(provision.shutil, "which", lambda _x: None)
    provision.bin_dir().mkdir(parents=True, exist_ok=True)
    for name in provision.FFMPEG_URLS:
        (provision.bin_dir() / name).write_bytes(b"good")
    monkeypatch.setitem(provision.FFMPEG_SHA256, "ffmpeg", hashlib.sha256(b"good").hexdigest())
    monkeypatch.setitem(provision.FFMPEG_SHA256, "ffprobe", "")  # unpinned -> keep today's behaviour

    def boom(url, dest, on_bytes=None):
        raise AssertionError("must not re-download a binary that already satisfies the pin")

    monkeypatch.setattr(provision, "_download_binary", boom)
    monkeypatch.setattr(provision, "_run", lambda *a, **k: None)  # the `-version` probe can't exec a stub
    provision.provision_ffmpeg()


def test_download_is_bounded_and_a_stall_is_legible(monkeypatch, tmp_path):
    """urlopen() with no timeout waits forever, so a host that accepts the connection and then goes
    quiet hangs first launch on a bar that never moves. Bounded + wrapped: the error names the file
    and the host, so the failure dialog/mailto carries a cause (same reason _run attaches its tail)."""
    seen: dict = {}

    def fake_urlopen(url, timeout=None):
        seen["timeout"] = timeout
        raise TimeoutError("timed out")

    monkeypatch.setattr(provision.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="downloading ffmpeg from") as err:
        provision._download_binary(provision.FFMPEG_URLS["ffmpeg"], tmp_path / "ffmpeg")
    assert seen["timeout"] == provision._DOWNLOAD_TIMEOUT
    assert isinstance(err.value.__cause__, TimeoutError)
    assert not (tmp_path / "ffmpeg.download").exists()  # no partial file left for the next run to trust


def test_print_ffmpeg_shas(monkeypatch):
    import hashlib
    monkeypatch.setattr(provision, "_download_binary", lambda url, dest, on_bytes=None: dest.write_bytes(b"abc"))
    out = provision.print_ffmpeg_shas()
    assert set(out) == {"ffmpeg", "ffprobe"}
    assert out["ffmpeg"] == hashlib.sha256(b"abc").hexdigest()


# ── endpoints ─────────────────────────────────────────────────────────────────

def _client(tmp_path):
    import tempfile

    from fastapi.testclient import TestClient

    from server.app import create_app
    return TestClient(create_app(projects_dir=tempfile.mkdtemp()))


def test_doctor_endpoint(tmp_path):
    r = _client(tmp_path).get("/api/doctor")
    assert r.status_code == 200
    body = r.json()
    assert "venv_ok" in body and "packs" in body and "pack_meta" in body
    # OPN-3: composition-tier status is surfaced too
    for k in ("node_ok", "remotion_ok", "hyperframes_ok", "composition_ok"):
        assert k in body


def test_provision_endpoint_unknown_pack_404(tmp_path):
    r = _client(tmp_path).post("/api/provision/bogus")
    assert r.status_code == 404


def test_provision_composition_endpoint_streams(monkeypatch, tmp_path):
    # 'composition' must hit the dedicated route, NOT be read as an unknown pack (404)
    def fake_composition(progress=None):
        if progress:
            progress("installing video engines…")
            progress("done")
    monkeypatch.setattr(provision, "provision_composition", fake_composition)

    with _client(tmp_path).stream("POST", "/api/provision/composition") as r:
        assert r.status_code == 200
        frames = [json.loads(line) for line in r.iter_lines() if line]
    types = [f["type"] for f in frames]
    assert "log" in types and types[-1] == "done"
    assert frames[-1].get("tier") == "composition"


def test_provision_endpoint_streams(monkeypatch, tmp_path):
    # mock the heavy install: emit two progress lines instead of pip-installing torch
    def fake_pack(name, progress=None):
        if progress:
            progress(f"installing {name}…")
            progress("done")
    monkeypatch.setattr(provision, "provision_pack", fake_pack)

    with _client(tmp_path).stream("POST", "/api/provision/beat-sync") as r:
        assert r.status_code == 200
        frames = [json.loads(line) for line in r.iter_lines() if line]
    types = [f["type"] for f in frames]
    assert "log" in types and types[-1] == "done"
