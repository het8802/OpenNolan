"""Contract tests for the M2 render-once / proxy model.

Covers the content-hash cache, the assemble-EDL builder, and an end-to-end
FFmpeg proxy render -> cache -> assemble. The Remotion/HyperFrames proxy paths
need their Node runtimes and aren't exercised here; the ffmpeg runtime fully
validates the orchestration (slice -> render solo -> cache -> ffmpeg assemble).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from schemas.artifacts import validate_artifact
from tools.video.render_cache import ProxyCache, file_content_hash
from tools.video.video_compose import VideoCompose


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _mk_clip(path, *, color="red", seconds=1, size="320x240", fps=30):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s={size}:r={fps}:d={seconds}",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-shortest", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-c:a", "aac",
            str(path),
        ],
        check=True, capture_output=True,
    )


# ---- ProxyCache ----

def test_cache_key_is_order_independent_and_content_sensitive():
    assert ProxyCache.key({"a": 1, "b": 2}) == ProxyCache.key({"b": 2, "a": 1})
    assert ProxyCache.key({"a": 1}) != ProxyCache.key({"a": 2})


def test_cache_roundtrip_and_orphaned_record_is_a_miss(tmp_path):
    cache = ProxyCache(root=tmp_path)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video-bytes")
    key = ProxyCache.key({"scene": "s1"})

    assert cache.get(key) is None  # nothing stored yet
    cache.put(key, {"proxy_path": str(clip), "duration_seconds": 1.0})
    rec = cache.get(key)
    assert rec and rec["proxy_path"] == str(clip)
    assert "cached_at" in rec  # stamped on put

    clip.unlink()
    assert cache.get(key) is None  # record points at a vanished clip -> miss


def test_file_content_hash_tracks_bytes(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    h1 = file_content_hash(f)
    f.write_bytes(b"world")
    h2 = file_content_hash(f)
    assert h1 and h2 and h1 != h2
    assert file_content_hash(tmp_path / "missing.bin") == ""


# ---- assemble EDL builder ----

def test_build_assemble_edl_carries_transitions_and_passthrough():
    vc = VideoCompose()
    original = {
        "version": "1.0",
        "render_runtime": "remotion",
        "renderer_family": "explainer-data",
        "cuts": [
            {"id": "s1", "source": "a", "in_seconds": 0, "out_seconds": 2,
             "transition_out": "fade", "transition_duration": 0.5},
            {"id": "s2", "source": "b", "in_seconds": 2, "out_seconds": 5},
        ],
        "audio": {"path": "vo.mp3"},
        "overlays": [{"type": "text"}],
    }
    proxies = [
        {"scene_id": "s1", "proxy_path": "/p/s1.mp4", "duration_seconds": 2.0, "cache_hit": False},
        {"scene_id": "s2", "proxy_path": "/p/s2.mp4", "duration_seconds": 3.0, "cache_hit": False},
    ]
    ed = vc._build_assemble_edl(original, proxies)

    assert ed["render_runtime"] == "ffmpeg"                 # assembler, not the locked runtime
    assert ed["renderer_family"] == "explainer-data"        # carried (satisfies the compose gate)
    assert [c["source"] for c in ed["cuts"]] == ["/p/s1.mp4", "/p/s2.mp4"]  # order preserved
    assert ed["cuts"][0]["transition_out"] == "fade"        # cross-scene transition carried
    assert ed["cuts"][0]["out_seconds"] == 2.0 and ed["cuts"][1]["out_seconds"] == 3.0
    assert ed["audio"] == {"path": "vo.mp3"}
    assert ed["overlays"] == [{"type": "text"}]


# ---- end-to-end ffmpeg proxy render + cache + assemble ----

@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg required")
def test_render_proxies_ffmpeg_e2e_with_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENNOLAN_CACHE_DIR", str(tmp_path / "cache"))

    a = tmp_path / "assets" / "video" / "a.mp4"
    b = tmp_path / "assets" / "video" / "b.mp4"
    _mk_clip(a, color="red")
    _mk_clip(b, color="blue")

    edit_decisions = {
        "version": "1.0",
        "render_runtime": "ffmpeg",
        "renderer_family": "explainer-data",
        "metadata": {"compose_target": {"width": 320, "height": 240, "fps": 30}},
        "cuts": [
            {"id": "s1", "source": "a", "in_seconds": 0, "out_seconds": 1},
            {"id": "s2", "source": "b", "in_seconds": 0, "out_seconds": 1},
        ],
    }
    asset_manifest = {"assets": [
        {"id": "a", "type": "video", "path": str(a), "source_tool": "test", "scene_id": "s1"},
        {"id": "b", "type": "video", "path": str(b), "source_tool": "test", "scene_id": "s2"},
    ]}
    proxies_dir = tmp_path / "proxies"
    vc = VideoCompose()

    # First run: both scenes render (cache miss), proxies land on disk.
    res = vc.execute({
        "operation": "render_proxies",
        "edit_decisions": edit_decisions,
        "asset_manifest": asset_manifest,
        "proxies_dir": str(proxies_dir),
    })
    assert res.success, res.error
    proxies = res.data["proxies"]
    assert len(proxies) == 2
    assert all(not p["cache_hit"] for p in proxies)
    assert all(Path(p["proxy_path"]).exists() for p in proxies)
    assert res.data["n_rendered"] == 2 and res.data["n_cached"] == 0

    # Second run: identical inputs -> all cache hits, nothing re-rendered.
    res2 = vc.execute({
        "operation": "render_proxies",
        "edit_decisions": edit_decisions,
        "asset_manifest": asset_manifest,
        "proxies_dir": str(proxies_dir),
    })
    assert res2.success, res2.error
    assert all(p["cache_hit"] for p in res2.data["proxies"])
    assert res2.data["n_rendered"] == 0

    # The assemble EDL concatenates the proxies and is a schema-valid artifact.
    assemble_ed = res.data["assemble_edit_decisions"]
    assert assemble_ed["render_runtime"] == "ffmpeg"
    assert [c["source"] for c in assemble_ed["cuts"]] == [p["proxy_path"] for p in proxies]
    assert assemble_ed["metadata"]["assemble_of_proxies"] is True
    validate_artifact("edit_decisions", assemble_ed)

    # Assemble via operation="render" (the canonical path — applies overlays +
    # bridges audio); operation="compose" would silently drop overlays.
    final = tmp_path / "final.mp4"
    comp = vc.execute({
        "operation": "render",
        "edit_decisions": assemble_ed,
        "asset_manifest": {"assets": []},
        "output_path": str(final),
    })
    assert comp.success, comp.error
    assert final.exists() and final.stat().st_size > 0


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg required")
def test_edit_then_revert_does_not_serve_stale_pixels(tmp_path, monkeypatch):
    """The critical cache invariant: a re-render with new content must not clobber
    the older proxy, so reverting reuses the correct (original) clip."""
    monkeypatch.setenv("OPENNOLAN_CACHE_DIR", str(tmp_path / "cache"))
    src = tmp_path / "assets" / "video" / "a.mp4"
    proxies_dir = tmp_path / "proxies"
    vc = VideoCompose()

    def run_one():
        ed = {
            "version": "1.0", "render_runtime": "ffmpeg", "renderer_family": "explainer-data",
            "metadata": {"compose_target": {"width": 320, "height": 240, "fps": 30}},
            "cuts": [{"id": "s1", "source": "a", "in_seconds": 0, "out_seconds": 1}],
        }
        am = {"assets": [{"id": "a", "type": "video", "path": str(src), "source_tool": "t", "scene_id": "s1"}]}
        r = vc.execute({"operation": "render_proxies", "edit_decisions": ed,
                        "asset_manifest": am, "proxies_dir": str(proxies_dir)})
        assert r.success, r.error
        return r.data["proxies"][0]

    _mk_clip(src, color="red")
    original_bytes = src.read_bytes()
    p1 = run_one()
    assert not p1["cache_hit"]

    _mk_clip(src, color="blue")            # edit: same scene id, different content
    p2 = run_one()
    assert not p2["cache_hit"]
    assert p2["proxy_path"] != p1["proxy_path"]   # new key -> new file, no clobber
    assert Path(p1["proxy_path"]).exists()         # the red proxy survives on disk

    src.write_bytes(original_bytes)         # revert to the exact original content
    p3 = run_one()
    assert p3["cache_hit"]                          # hits the original proxy...
    assert p3["proxy_path"] == p1["proxy_path"]     # ...and it's the correct file


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg required")
def test_distinct_source_windows_get_distinct_proxies(tmp_path, monkeypatch):
    """Two cuts of the SAME source but different trim windows must not collide on
    one key (which would render the wrong, always-from-0 segment for one)."""
    monkeypatch.setenv("OPENNOLAN_CACHE_DIR", str(tmp_path / "cache"))
    src = tmp_path / "assets" / "video" / "a.mp4"
    _mk_clip(src, color="green", seconds=3)
    ed = {
        "version": "1.0", "render_runtime": "ffmpeg", "renderer_family": "explainer-data",
        "metadata": {"compose_target": {"width": 320, "height": 240, "fps": 30}},
        "cuts": [
            {"id": "s1", "source": "a", "in_seconds": 0, "out_seconds": 1},
            {"id": "s2", "source": "a", "in_seconds": 1, "out_seconds": 2},
        ],
    }
    am = {"assets": [{"id": "a", "type": "video", "path": str(src), "source_tool": "t", "scene_id": "s1"}]}
    r = VideoCompose().execute({"operation": "render_proxies", "edit_decisions": ed,
                                "asset_manifest": am, "proxies_dir": str(tmp_path / "proxies")})
    assert r.success, r.error
    paths = [p["proxy_path"] for p in r.data["proxies"]]
    assert len(set(paths)) == 2     # distinct windows -> distinct keys -> distinct files
