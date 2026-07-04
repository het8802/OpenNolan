"""Contract tests for lib.provision (first-run bootstrapper) + the /api/doctor + /api/provision endpoints.

We do NOT run a real venv build or a 2.6GB pack install here — those are exercised end-to-end by hand
against the bundled interpreter. These tests lock the pure logic: status/doctor, staleness, the pack
registry, atomic manifest writes, and the endpoint contracts (mocking the heavy install).
"""

from __future__ import annotations

import json

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
