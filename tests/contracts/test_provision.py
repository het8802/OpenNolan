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


def test_provision_endpoint_unknown_pack_404(tmp_path):
    r = _client(tmp_path).post("/api/provision/bogus")
    assert r.status_code == 404


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
