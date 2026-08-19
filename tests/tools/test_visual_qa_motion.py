"""Tests for the visual_qa motion ops (lib/video_motion.py, lib/qa_plan_diff.py).

Analysis is pure, so nearly everything here runs with no ffmpeg: curve parsing,
run detection, the static/frozen accounting (a freeze must be counted ONCE, not
as both frozen and static), tile-grid capacity, the declared-but-not-rendered
lint, and the cut-time derivation on both runtimes.

Behind an ffmpeg skipif, one end-to-end test builds a clip whose second half is
a deliberate freeze and asserts the tool actually finds it — the smallest thing
that fails if the ffmpeg chain, the metadata parsing or the thresholds break.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from lib import qa_plan_diff, video_motion
from tools.analysis.visual_qa import VisualQA

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


# --- curve parsing ----------------------------------------------------------


def test_parse_metadata_print_pairs_frames_with_values():
    text = (
        "frame:0    pts:0       pts_time:0\n"
        "lavfi.signalstats.YAVG=218.61\n"
        "frame:1    pts:512     pts_time:0.0333333\n"
        "lavfi.signalstats.YAVG=1.2947\n"
    )
    assert video_motion.parse_metadata_print(text) == [
        (0.0, 218.61),
        (0.0333333, 1.2947),
    ]


def test_parse_metadata_print_drops_unusable_rows_rather_than_guessing():
    """A fabricated 0.0 would read as 'frozen' — the exact wrong verdict."""
    text = (
        "frame:0    pts:0       pts_time:N/A\n"  # no usable timestamp
        "lavfi.signalstats.YAVG=5\n"
        "frame:1    pts:512     pts_time:0.1\n"  # value missing entirely
        "frame:2    pts:1024    pts_time:0.2\n"
        "lavfi.signalstats.YAVG=nan\n"  # non-finite
        "frame:3    pts:1536    pts_time:0.3\n"
        "lavfi.signalstats.YAVG=7.5\n"
    )
    assert video_motion.parse_metadata_print(text) == [(0.3, 7.5)]


def test_parse_metadata_print_tolerates_trailing_comma():
    text = "frame:0 pts:0 pts_time:0\nlavfi.scd.score=12.5,\n"
    assert video_motion.parse_metadata_print(text) == [(0.0, 12.5)]


# --- bucketing and runs -----------------------------------------------------


def test_bucket_series_groups_by_time_not_frame_index():
    series = [(0.0, 1.0), (0.4, 3.0), (0.5, 10.0), (1.2, 2.0)]
    buckets = video_motion.bucket_series(series, 0.5)
    assert [(b.start, b.end, b.frames) for b in buckets] == [
        (0.0, 0.5, 2),
        (0.5, 1.0, 1),
        (1.0, 1.5, 1),
    ]
    assert buckets[0].mean == pytest.approx(2.0)
    assert buckets[0].peak == pytest.approx(3.0)


def test_bucket_series_omits_empty_buckets():
    """A decode gap must not surface as mean 0.0, which reads as frozen."""
    buckets = video_motion.bucket_series([(0.0, 5.0), (2.2, 5.0)], 0.5)
    assert [b.start for b in buckets] == [0.0, 2.0]


def test_find_runs_respects_min_seconds_and_contiguity():
    buckets = video_motion.bucket_series([(t / 10, 0.01) for t in range(0, 20)], 0.5)  # 2.0s all quiet
    runs = video_motion.find_runs(buckets, below=0.25, min_seconds=0.75, kind="static")
    assert len(runs) == 1
    assert (runs[0].start, runs[0].end) == (0.0, 2.0)

    short = video_motion.bucket_series([(0.0, 0.01), (0.1, 0.01)], 0.5)
    assert video_motion.find_runs(short, below=0.25, min_seconds=0.75, kind="s") == []


def test_find_runs_does_not_bridge_a_time_gap():
    quiet = [(t / 10, 0.01) for t in range(0, 10)]  # 0.0 - 1.0s
    later = [(5.0 + t / 10, 0.01) for t in range(0, 10)]  # 5.0 - 6.0s
    runs = video_motion.find_runs(
        video_motion.bucket_series(quiet + later, 0.5),
        below=0.25,
        min_seconds=0.5,
        kind="static",
    )
    assert [(r.start, r.end) for r in runs] == [(0.0, 1.0), (5.0, 6.0)]


def test_peaks_above_reports_one_timestamp_per_cluster():
    series = [(0.0, 1.0), (1.0, 30.0), (1.03, 45.0), (1.06, 20.0), (5.0, 60.0)]
    assert video_motion.peaks_above(series, threshold=10.0, merge_seconds=0.2) == [1.03, 5.0]


def test_window_stats_reports_zero_frames_rather_than_zero_motion():
    series = [(0.0, 1.0), (5.0, 2.0)]
    assert video_motion.window_stats(series, 1.0, 2.0) == {
        "mean": None,
        "peak": None,
        "frames": 0,
    }


# --- the accounting bug this suite exists to pin down -----------------------


def _flat_then_moving(freeze_seconds: float, move_seconds: float) -> video_motion.Series:
    frozen = [(t / 30, 0.01) for t in range(0, int(freeze_seconds * 30))]
    moving = [(freeze_seconds + t / 30, 4.0) for t in range(0, int(move_seconds * 30))]
    return frozen + moving


def test_frozen_time_is_counted_once_not_twice():
    """frozen is a SUBSET of static; summing both inflates static_fraction.

    A 2s freeze inside a 4s clip is 50% static, never 100%.
    """
    motion = _flat_then_moving(2.0, 2.0)
    luma = [(t, 128.0) for t, _ in motion]
    out = video_motion.summarize(motion=motion, luma=luma, scd=[], duration=4.0)
    assert out["static_seconds"] == pytest.approx(2.0)
    assert out["frozen_seconds"] == pytest.approx(2.0)
    assert out["static_fraction"] == pytest.approx(0.5)
    # ...and it is reported as ONE finding, upgraded to FROZEN, not two.
    assert len(out["findings"]) == 1
    assert out["findings"][0].startswith("FROZEN 0.00-2.00s")


def test_summarize_flags_dark_runs_from_luma_not_motion():
    motion = [(t / 30, 4.0) for t in range(0, 60)]  # moving throughout
    luma = [(t / 30, 2.0) for t in range(0, 60)]  # but black
    out = video_motion.summarize(motion=motion, luma=luma, scd=[], duration=2.0)
    assert out["static_seconds"] == 0
    assert any(f.startswith("DARK") for f in out["findings"])


def test_table_stays_bounded_for_a_long_video():
    assert video_motion.table_bucket_seconds(33.0) == 1.0
    assert video_motion.table_bucket_seconds(600.0) == 10.0
    motion = [(t / 2, 1.0) for t in range(0, 1200)]  # 600s
    out = video_motion.summarize(motion=motion, luma=[], scd=[], duration=600.0)
    assert len(out["table"]) - 1 <= video_motion.MAX_TABLE_ROWS


# --- tile geometry ----------------------------------------------------------


def test_grid_always_has_capacity_for_every_tile():
    """ffmpeg's tile filter silently drops frames past cols*rows; with
    `-frames:v 1` that shows the start of the video and reports success."""
    for n in (1, 2, 7, 9, 10, 33, 61, 120):
        for aspect in (9 / 16, 16 / 9, 1.0):
            grid = video_motion.grid_for(n, tile_width=200, aspect=aspect)
            assert grid["capacity"] >= n, (n, aspect, grid)
            assert grid["empty_cells"] == grid["capacity"] - n


def test_grid_shrinks_tiles_to_meet_the_pixel_budget():
    grid = video_motion.grid_for(30, tile_width=800, aspect=16 / 9, max_pixels=4_000_000)
    assert grid["pixels"] <= 4_000_000
    assert grid["over_budget"] is False
    assert grid["tile_width"] < 800
    assert grid["tile_width"] % 2 == 0 and grid["tile_height"] % 2 == 0


def test_grid_reports_over_budget_rather_than_dropping_tiles():
    """Holding every tile beats the pixel budget: shrinking past the readability
    floor is useless, so the bust is REPORTED and the caller samples less often."""
    grid = video_motion.grid_for(200, tile_width=400, aspect=16 / 9, max_pixels=2_000_000, min_tile_width=80)
    assert grid["capacity"] >= 200  # never silently drops tiles
    assert grid["tile_width"] == 80  # pinned at the floor
    assert grid["over_budget"] is True  # and says so
    assert grid["pixels"] > 2_000_000


def test_max_tiles_for_is_the_inverse_of_the_budget():
    cap = video_motion.max_tiles_for(16 / 9, tile_width=120, max_pixels=4_000_000)
    grid = video_motion.grid_for(cap, tile_width=120, aspect=16 / 9, min_tile_width=120)
    assert grid["over_budget"] is False


def test_grid_single_row_for_a_strip():
    grid = video_motion.grid_for(18, tile_width=190, aspect=9 / 16, rows=1)
    assert (grid["rows"], grid["cols"], grid["empty_cells"]) == (1, 18, 0)


def test_sample_count_matches_fps_over_duration():
    assert video_motion.sample_count(1.5, 12) == 18
    assert video_motion.sample_count(33.0, 1) == 33
    assert video_motion.sample_count(0.0, 12) == 1  # never zero tiles


def test_sample_count_never_undercounts_what_the_fps_filter_emits():
    """The dangerous direction. Measured against real clips: ffmpeg's `fps` filter
    emits 5 frames for a 4.5s clip at fps=1 and 33 for 2.708333s at fps=12, so
    floor() (and even floor(x+0.5)) undersize the grid and `tile` silently drops
    the tail while reporting empty_cells: 0."""
    for duration, fps, at_least in ((4.5, 1, 5), (8.6, 1, 9), (2.708333, 12, 33), (0.9, 12, 11)):
        assert video_motion.sample_count(duration, fps) >= at_least, (duration, fps)
    # Exact multiples must not gain a spurious extra tile.
    assert video_motion.sample_count(1.5, 12) == 18
    assert video_motion.sample_count(2.0, 30) == 60


def test_static_runs_never_extend_past_the_end_of_the_video():
    """Buckets end on whole bucket_seconds boundaries, which overshoot any duration
    that isn't a multiple of one — reporting a freeze in time that doesn't exist and
    pushing static_fraction over the schema's maximum of 1."""
    motion = [(t / 30, 0.01) for t in range(0, 258)]  # 8.6s, entirely static
    out = video_motion.summarize(motion=motion, luma=[], scd=[], duration=8.6)
    assert out["static_seconds"] <= 8.6 + 1e-9, out["static_seconds"]
    assert out["static_fraction"] <= 1.0
    assert all(r["end"] <= 8.6 + 1e-9 for r in out["static_runs"]), out["static_runs"]
    assert all(r["end"] <= 8.6 + 1e-9 for r in out["frozen_runs"])


# --- declared-but-not-rendered lint ----------------------------------------


def _doc(**over):
    doc = {"version": "1.0", "render_runtime": "ffmpeg", "cuts": []}
    doc.update(over)
    return doc


def _kinds(findings):
    return {(f["kind"], f["where"]) for f in findings}


def test_lint_catches_clip_animation_dropped_on_the_ffmpeg_path():
    doc = _doc(
        cuts=[
            {
                "id": "a",
                "source": "x.mp4",
                "in_seconds": 0,
                "out_seconds": 3,
                "transform": {"animation": "ken_burns_in"},
            }
        ]
    )
    found = qa_plan_diff.static_findings(doc)
    hit = [f for f in found if f["where"] == "cuts[0].transform.animation"]
    assert hit and hit[0]["severity"] == "high"
    assert hit[0]["kind"] == "not-rendered"
    assert "motion_ops" in hit[0]["fix"]


def test_lint_catches_transform_without_a_background():
    cut = {
        "id": "a",
        "source": "x.mp4",
        "in_seconds": 0,
        "out_seconds": 3,
        "transform": {"scale": 0.5, "position": {"x": 10, "y": 10}},
    }
    dropped = _kinds(qa_plan_diff.static_findings(_doc(cuts=[cut])))
    assert ("not-rendered", "cuts[0].transform.scale") in dropped
    assert ("not-rendered", "cuts[0].transform.position") in dropped
    # With metadata.background set, the transform IS applied — no finding.
    with_bg = _kinds(
        qa_plan_diff.static_findings(_doc(cuts=[cut], metadata={"background": {"type": "color", "color": "#000"}}))
    )
    assert ("not-rendered", "cuts[0].transform.scale") not in with_bg


def test_lint_stays_silent_on_default_transform_values():
    """scale 1.0 / position 'center' are the schema defaults AND the renderer's own
    no-op classification (video_compose.py:1865) — flagging them is pure noise."""
    doc = _doc(
        cuts=[
            {
                "id": "a",
                "source": "x.mp4",
                "in_seconds": 0,
                "out_seconds": 3,
                "transform": {"scale": 1.0, "position": "center"},
            }
        ]
    )
    assert qa_plan_diff.static_findings(doc) == []


def test_lint_uses_the_renderers_background_predicate_not_truthiness():
    """metadata.background = {"type": "none"} is falsy to the renderer, so the
    transform IS dropped and the lint must still say so."""
    cut = {
        "id": "a",
        "source": "x.mp4",
        "in_seconds": 0,
        "out_seconds": 3,
        "transform": {"scale": 0.5},
    }
    none_bg = _doc(cuts=[cut], metadata={"background": {"type": "none"}})
    assert ("not-rendered", "cuts[0].transform.scale") in _kinds(qa_plan_diff.static_findings(none_bg))
    for bg_type in ("color", "image"):
        real_bg = _doc(cuts=[cut], metadata={"background": {"type": bg_type}})
        assert ("not-rendered", "cuts[0].transform.scale") not in _kinds(qa_plan_diff.static_findings(real_bg))


def test_lint_ignores_a_hard_cut_name_on_a_pip_cut():
    """A hard-cut name declares nothing, so there is nothing to warn about."""
    for name in ("cut", "none", ""):
        doc = _doc(
            cuts=[
                {
                    "id": "p",
                    "source": "p.mp4",
                    "in_seconds": 0,
                    "out_seconds": 1,
                    "layer": "overlay",
                    "transition_in": name,
                }
            ]
        )
        assert qa_plan_diff.static_findings(doc) == [], name
    real = _doc(
        cuts=[
            {
                "id": "p",
                "source": "p.mp4",
                "in_seconds": 0,
                "out_seconds": 1,
                "layer": "overlay",
                "transition_in": "fade",
            }
        ]
    )
    assert any(f["kind"] == "not-rendered" for f in qa_plan_diff.static_findings(real))


def test_a_clean_plan_produces_no_findings_at_all():
    """The noise test. An advisory tool that cries wolf on a correct plan is worse
    than no tool, because the agent learns to skip it."""
    doc = _doc(
        cuts=[
            {"id": "a", "source": "a.mp4", "in_seconds": 0, "out_seconds": 2, "transition_in": "cut"},
            {"id": "b", "source": "b.mp4", "in_seconds": 1, "out_seconds": 4, "transition_in": "cut"},
        ],
        overlays=[
            {
                "type": "text",
                "text": "rises",
                "start_seconds": 0.2,
                "end_seconds": 1.8,
                "keyframes": [{"t": 0.2, "y": 900}, {"t": 1.8, "y": 400}],
            }
        ],
        audio={"music": {"asset_id": "m1", "volume": 0.3}},
        subtitles={"style": "word-by-word"},
    )
    assert qa_plan_diff.static_findings(doc) == []
    report = qa_plan_diff.diff(
        doc,
        motion=[(t / 30, 3.0) for t in range(0, 150)],
        cut_times=[2.0],
        frozen_runs=[],
        duration=5.0,
    )
    assert report["findings"] == [], report["lines"]


def test_lint_catches_overlay_animation_and_dead_top_level_keys():
    doc = _doc(
        overlays=[
            {"type": "text", "text": "hi", "start_seconds": 0, "end_seconds": 1, "animation": {"type": "slide_up"}}
        ],
        transitions=[{"type": "fade", "at_seconds": 1, "duration_seconds": 0.5}],
        music={"asset_id": "m1"},
        subtitles={"color": "#fff"},
    )
    kinds = _kinds(qa_plan_diff.static_findings(doc))
    assert ("not-rendered", "overlays[0].animation") in kinds
    assert ("not-rendered", "transitions") in kinds
    assert ("not-rendered", "music") in kinds
    assert ("not-rendered", "subtitles") in kinds


def test_lint_knows_text_overlays_ignore_scale_keyframes():
    doc = _doc(
        overlays=[
            {
                "type": "text",
                "text": "hi",
                "start_seconds": 0,
                "end_seconds": 2,
                "keyframes": [{"t": 0, "scale": 1.0}, {"t": 2, "scale": 1.4}],
            }
        ]
    )
    kinds = _kinds(qa_plan_diff.static_findings(doc))
    assert ("not-rendered", "overlays[0].keyframes[].scale") in kinds
    # The same keyframes on an image overlay DO render.
    img = _doc(
        overlays=[
            {
                "type": "image",
                "asset_id": "a",
                "position": {"x": 0, "y": 0},
                "start_seconds": 0,
                "end_seconds": 2,
                "keyframes": [{"t": 0, "scale": 1.0}, {"t": 2, "scale": 1.4}],
            }
        ]
    )
    assert ("not-rendered", "overlays[0].keyframes[].scale") not in _kinds(qa_plan_diff.static_findings(img))


def test_lint_catches_keyframes_that_never_change_value():
    """The real defect found in a shipped project: 30 captions each with two
    keyframes holding the same y, i.e. a constant dressed up as an animation."""
    doc = _doc(
        overlays=[
            {
                "type": "text",
                "text": "You do not need",
                "start_seconds": 0.0,
                "end_seconds": 1.32,
                "keyframes": [{"t": 0.0, "y": 1400}, {"t": 1.32, "y": 1400}],
            }
        ]
    )
    hit = [f for f in qa_plan_diff.static_findings(doc) if f["kind"] == "no-op-keyframes"]
    assert len(hit) == 1
    assert "ever changes value" in hit[0]["message"]
    assert hit[0]["severity"] == "medium"


def test_lint_catches_a_single_keyframe_and_out_of_window_keyframes():
    one = _doc(
        overlays=[
            {
                "type": "text",
                "text": "hi",
                "start_seconds": 0,
                "end_seconds": 2,
                "keyframes": [{"t": 0, "y": 10}],
            }
        ]
    )
    assert any(f["kind"] == "no-op-keyframes" for f in qa_plan_diff.static_findings(one))

    outside = _doc(
        overlays=[
            {
                "type": "text",
                "text": "hi",
                "start_seconds": 5.0,
                "end_seconds": 7.0,
                "keyframes": [{"t": 0.0, "y": 10}, {"t": 1.0, "y": 900}],
            }
        ]
    )
    hit = [f for f in qa_plan_diff.static_findings(outside) if f["kind"] == "keyframes-outside-window"]
    assert hit and hit[0]["severity"] == "high"


def test_lint_is_skipped_on_composition_runtimes():
    doc = _doc(
        render_runtime="hyperframes",
        cuts=[
            {
                "id": "a",
                "source": "s.html",
                "in_seconds": 0,
                "out_seconds": 2,
                "transform": {"animation": "ken_burns_in"},
            }
        ],
    )
    found = qa_plan_diff.static_findings(doc)
    assert [f["kind"] for f in found] == ["lint-skipped"]


# --- cut-time derivation ----------------------------------------------------


def test_cut_windows_concatenate_and_divide_by_speed():
    doc = _doc(
        cuts=[
            {"id": "a", "source": "x", "in_seconds": 0, "out_seconds": 2},
            {"id": "b", "source": "y", "in_seconds": 10, "out_seconds": 13},
            {"id": "c", "source": "z", "in_seconds": 0, "out_seconds": 4, "speed": 2.0},
        ]
    )
    windows, _ = qa_plan_diff.expected_cut_windows(doc)
    assert [(w["start"], w["end"]) for w in windows] == [
        (0.0, 2.0),
        (2.0, 5.0),
        (5.0, 7.0),
    ]


def test_cut_windows_subtract_xfade_overlap_on_the_ffmpeg_path():
    """The renderer overlaps an xfade join, shortening the timeline. The JS
    preview does not — these windows follow the renderer."""
    doc = _doc(
        cuts=[
            {"id": "a", "source": "x", "in_seconds": 0, "out_seconds": 2},
            {"id": "b", "source": "y", "in_seconds": 0, "out_seconds": 2, "transition_in": "fade"},
        ],
        metadata={"default_transition_duration": 0.5},
    )
    windows, _ = qa_plan_diff.expected_cut_windows(doc)
    assert [(w["start"], w["end"]) for w in windows] == [(0.0, 2.0), (1.5, 3.5)]


def test_cut_windows_ignore_pip_cuts_and_skip_overlap_on_compositions():
    doc = _doc(
        cuts=[
            {"id": "a", "source": "x", "in_seconds": 0, "out_seconds": 2},
            {"id": "pip", "source": "p.mp4", "in_seconds": 0, "out_seconds": 1, "layer": "overlay"},
            {"id": "b", "source": "y", "in_seconds": 0, "out_seconds": 3},
        ]
    )
    windows, _ = qa_plan_diff.expected_cut_windows(doc)
    assert [w["id"] for w in windows] == ["a", "b"]

    # Composition runtimes build their own timeline: no overlap subtracted, even
    # with a transition declared. Verified against a real 33.00s screen-demo doc.
    comp = _doc(
        render_runtime="hyperframes",
        cuts=[
            {"id": "a", "source": "a.html", "in_seconds": 0, "out_seconds": 2.0},
            {"id": "b", "source": "b.html", "in_seconds": 0, "out_seconds": 3.0, "transition_in": "fade"},
        ],
    )
    windows, _ = qa_plan_diff.expected_cut_windows(comp)
    assert [(w["start"], w["end"]) for w in windows] == [(0.0, 2.0), (2.0, 5.0)]


def test_expected_motion_windows_only_lists_rendered_changing_channels():
    doc = _doc(
        overlays=[
            {
                "type": "text",
                "text": "moves",
                "start_seconds": 1.0,
                "end_seconds": 3.0,
                "keyframes": [{"t": 1.0, "y": 100}, {"t": 3.0, "y": 400}],
            },
            {
                "type": "text",
                "text": "static",
                "start_seconds": 4.0,
                "end_seconds": 5.0,
                "keyframes": [{"t": 4.0, "y": 100}, {"t": 5.0, "y": 100}],
            },
            {
                "type": "text",
                "text": "scale-only",
                "start_seconds": 6.0,
                "end_seconds": 7.0,
                "keyframes": [{"t": 6.0, "scale": 1.0}, {"t": 7.0, "scale": 2.0}],
            },
        ]
    )
    windows = qa_plan_diff.expected_motion_windows(doc)
    assert [(w["index"], w["channels"], w["start"], w["end"]) for w in windows] == [(0, ["y"], 1.0, 3.0)]


# --- measured half ----------------------------------------------------------


def test_measured_flat_finding_when_a_keyframe_window_is_frozen():
    doc = _doc(
        overlays=[
            {
                "type": "text",
                "text": "slide up",
                "start_seconds": 0.0,
                "end_seconds": 2.0,
                "keyframes": [{"t": 0.0, "y": 900}, {"t": 2.0, "y": 400}],
            }
        ]
    )
    motion = [(t / 30, 0.005) for t in range(0, 60)]
    frozen = [{"start": 0.0, "end": 2.0, "seconds": 2.0, "mean_motion": 0.005}]
    found, _ = qa_plan_diff.measured_findings(doc, motion=motion, cut_times=[], frozen_runs=frozen, duration=2.0)
    flat = [f for f in found if f["kind"] == "flat"]
    assert flat and flat[0]["severity"] == "high"
    assert "did not" in flat[0]["message"] or "nothing animated" in flat[0]["message"]


def test_measured_stays_quiet_when_the_window_actually_moves():
    doc = _doc(
        overlays=[
            {
                "type": "text",
                "text": "slide up",
                "start_seconds": 0.0,
                "end_seconds": 2.0,
                "keyframes": [{"t": 0.0, "y": 900}, {"t": 2.0, "y": 400}],
            }
        ]
    )
    motion = [(t / 30, 3.0) for t in range(0, 60)]
    found, _ = qa_plan_diff.measured_findings(doc, motion=motion, cut_times=[], frozen_runs=[], duration=2.0)
    assert [f for f in found if f["kind"] == "flat"] == []


def test_unmeasured_window_is_reported_as_unknown_not_as_flat():
    doc = _doc(
        overlays=[
            {
                "type": "text",
                "text": "x",
                "start_seconds": 50.0,
                "end_seconds": 52.0,
                "keyframes": [{"t": 50.0, "y": 1}, {"t": 52.0, "y": 9}],
            }
        ]
    )
    found, _ = qa_plan_diff.measured_findings(doc, motion=[(0.0, 5.0)], cut_times=[], frozen_runs=[], duration=1.0)
    kinds = {f["kind"] for f in found}
    assert "unmeasured" in kinds and "flat" not in kinds


def test_duration_drift_and_plan_self_contradiction():
    doc = _doc(
        cuts=[{"id": "a", "source": "x", "in_seconds": 0, "out_seconds": 5}],
        metadata={"total_duration_seconds": 30.0},
    )
    found, _ = qa_plan_diff.measured_findings(doc, motion=[(0.0, 1.0)], cut_times=[], frozen_runs=[], duration=33.0)
    kinds = {f["kind"] for f in found}
    assert "duration-drift" in kinds
    assert "plan-inconsistent" in kinds


def test_cut_windows_cap_an_xfade_at_the_material_available():
    """The renderer caps each fade at min(cum, seg) - 0.05; without that the
    derivation runs BACKWARDS on a transition longer than its neighbour and blames
    the render for three things at once."""
    doc = _doc(
        cuts=[
            {"id": "a", "source": "x", "in_seconds": 0, "out_seconds": 1.5},
            {
                "id": "b",
                "source": "y",
                "in_seconds": 0,
                "out_seconds": 1.5,
                "transition_in": "fade",
                "transition_duration": 1.4,
            },
        ],
    )
    windows, _ = qa_plan_diff.expected_cut_windows(doc)
    starts = [w["start"] for w in windows]
    assert all(s >= 0 for s in starts), starts
    # 1.4 fits inside the 1.45 available, so it applies in full: 1.5 - 1.4.
    assert starts[1] == pytest.approx(0.1, abs=1e-6)

    # A fade LONGER than its neighbour is capped instead of running the timeline
    # backwards: available = min(1.5, 1.5) - 0.05 = 1.45, so start = 1.5 - 1.45.
    capped = _doc(
        cuts=[
            {"id": "a", "source": "x", "in_seconds": 0, "out_seconds": 1.5},
            {
                "id": "b",
                "source": "y",
                "in_seconds": 0,
                "out_seconds": 1.5,
                "transition_in": "fade",
                "transition_duration": 2.0,
            },
        ]
    )
    capped_starts = [w["start"] for w in qa_plan_diff.expected_cut_windows(capped)[0]]
    assert capped_starts[1] == pytest.approx(0.05, abs=1e-6), capped_starts
    # A transition that fits is untouched.
    fits = _doc(
        cuts=[
            {"id": "a", "source": "x", "in_seconds": 0, "out_seconds": 2},
            {"id": "b", "source": "y", "in_seconds": 0, "out_seconds": 2, "transition_in": "fade"},
        ],
        metadata={"default_transition_duration": 0.5},
    )
    assert [(w["start"], w["end"]) for w in qa_plan_diff.expected_cut_windows(fits)[0]] == [
        (0.0, 2.0),
        (1.5, 3.5),
    ]


def test_soft_transitions_are_excluded_from_cut_detection():
    """A crossfade cannot produce a scene-change spike, so counting it is no
    evidence at all — and it buries the one hard cut that really went missing."""
    all_soft = _doc(
        cuts=[
            {
                "id": f"c{i}",
                "source": "x",
                "in_seconds": 0,
                "out_seconds": 2,
                **({"transition_in": "fade"} if i else {}),
            }
            for i in range(5)
        ]
    )
    found, _ = qa_plan_diff.measured_findings(all_soft, motion=[(0.0, 3.0)], cut_times=[], frozen_runs=[], duration=8.0)
    assert [f for f in found if f["kind"] == "cut-undetected"] == []

    # A hard cut among soft ones is still reported, and the exclusion is disclosed.
    mixed = _doc(
        cuts=[
            {"id": "a", "source": "x", "in_seconds": 0, "out_seconds": 2},
            {"id": "b", "source": "y", "in_seconds": 0, "out_seconds": 2, "transition_in": "fade"},
            {"id": "c", "source": "z", "in_seconds": 0, "out_seconds": 2, "transition_in": "cut"},
        ]
    )
    hit = [
        f
        for f in qa_plan_diff.measured_findings(mixed, motion=[(0.0, 3.0)], cut_times=[], frozen_runs=[], duration=6.0)[
            0
        ]
        if f["kind"] == "cut-undetected"
    ]
    assert hit, "the hard boundary should still be flagged"
    assert "soft transition(s) excluded" in hit[0]["message"]


def test_cut_undetected_is_low_confidence_and_labelled_as_such():
    doc = _doc(
        cuts=[
            {"id": "a", "source": "x", "in_seconds": 0, "out_seconds": 2},
            {"id": "b", "source": "y", "in_seconds": 0, "out_seconds": 2},
        ]
    )
    found, _ = qa_plan_diff.measured_findings(doc, motion=[(0.0, 1.0)], cut_times=[], frozen_runs=[], duration=4.0)
    hit = [f for f in found if f["kind"] == "cut-undetected"]
    assert hit and hit[0]["severity"] == "low"
    assert "WEAK EVIDENCE" in hit[0]["message"]
    # A detected peak near the boundary clears it.
    ok, _ = qa_plan_diff.measured_findings(doc, motion=[(0.0, 1.0)], cut_times=[2.1], frozen_runs=[], duration=4.0)
    assert [f for f in ok if f["kind"] == "cut-undetected"] == []


# --- grouping ---------------------------------------------------------------


def test_repeated_findings_collapse_to_one_line_naming_every_index():
    doc = _doc(
        overlays=[
            {
                "type": "text",
                "text": f"c{i}",
                "start_seconds": i,
                "end_seconds": i + 1,
                "keyframes": [{"t": i, "y": 1400}, {"t": i + 1, "y": 1400}],
            }
            for i in range(30)
        ]
    )
    report = qa_plan_diff.diff(doc, motion=[(0.0, 5.0)], cut_times=[], frozen_runs=[], duration=30.0)
    noop = [f for f in report["findings"] if f["kind"] == "no-op-keyframes"]
    assert len(noop) == 1
    assert noop[0]["occurrences"] == 30
    assert noop[0]["where"] == "overlays[0-29].keyframes"
    assert len(noop[0]["locations"]) == 30
    # The count survives grouping so severity totals stay honest.
    assert report["counts"]["medium"] >= 30


def test_collapse_locations_keeps_non_contiguous_indices_readable():
    assert (
        qa_plan_diff._collapse_locations(["overlays[1].keyframes", "overlays[2].keyframes", "overlays[9].keyframes"])
        == "overlays[1-2,9].keyframes"
    )
    assert qa_plan_diff._collapse_locations(["cuts", "music"]) == "cuts, music"


# --- end to end (real ffmpeg) ----------------------------------------------


@pytest.fixture
def frozen_tail_clip(tmp_path):
    """4s clip: 2s of real motion, then 2s of a single frozen frame."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    moving = tmp_path / "moving.mp4"
    still = tmp_path / "still.mp4"
    out = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=s=320x240:d=2:r=30",
            "-pix_fmt",
            "yuv420p",
            str(moving),
        ],
        check=True,
        capture_output=True,
    )
    # A single frame held for 2s -> consecutive frames are identical.
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=320x240:d=2:r=30",
            "-pix_fmt",
            "yuv420p",
            str(still),
        ],
        check=True,
        capture_output=True,
    )
    concat = tmp_path / "list.txt"
    concat.write_text(f"file '{moving}'\nfile '{still}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(out)],
        check=True,
        capture_output=True,
    )
    return out


@needs_ffmpeg
def test_motion_op_finds_a_real_frozen_tail(frozen_tail_clip):
    result = VisualQA().execute({"operation": "motion", "input_path": str(frozen_tail_clip)})
    assert result.success, result.error
    d = result.data
    assert d["frames_measured"] > 50
    assert d["frozen_seconds"] >= 1.0, d["findings"]
    # The freeze is in the SECOND half, and the first half is not flagged.
    assert d["frozen_runs"], d
    assert d["frozen_runs"][-1]["end"] == pytest.approx(4.0, abs=0.6)
    assert d["frozen_runs"][0]["start"] >= 1.5
    assert 0.3 <= d["static_fraction"] <= 0.75, d["static_fraction"]
    assert any("FROZEN" in f for f in d["findings"])


@needs_ffmpeg
def test_sheet_writes_one_image_with_capacity_for_every_tile(frozen_tail_clip):
    result = VisualQA().execute({"operation": "sheet", "input_path": str(frozen_tail_clip), "fps": 2})
    assert result.success, result.error
    d = result.data
    assert d["tiles"] == 8
    cols, rows = (int(x) for x in d["grid"].split("x"))
    assert cols * rows >= d["tiles"]
    assert result.artifacts and result.artifacts[0].endswith(".jpg")
    assert d["bytes"] > 1000


@needs_ffmpeg
def test_sheet_of_a_short_clip_still_reaches_the_recordable_minimum(tmp_path):
    """final_review requires frames_sampled >= 4; a 3s clip at 1fps would give 3."""
    clip = tmp_path / "short.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=s=320x240:d=3:r=30",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        check=True,
        capture_output=True,
    )
    result = VisualQA().execute({"operation": "sheet", "input_path": str(clip)})
    assert result.success, result.error
    assert result.data["tiles"] >= 4
    assert result.data["notes"] and "fps raised" in result.data["notes"][0]


@needs_ffmpeg
def test_sheet_accepts_explicit_timestamps_one_tile_each(frozen_tail_clip):
    result = VisualQA().execute(
        {
            "operation": "sheet",
            "input_path": str(frozen_tail_clip),
            "timestamps": [0.5, 1.5, 3.0],
        }
    )
    assert result.success, result.error
    assert result.data["tiles"] == 3
    assert result.data["sampling"]["mode"] == "timestamps"


@needs_ffmpeg
def test_strip_thins_the_fps_instead_of_truncating_the_window(frozen_tail_clip):
    result = VisualQA().execute(
        {
            "operation": "strip",
            "input_path": str(frozen_tail_clip),
            "window": {"start": 0.0, "duration": 4.0},
            "fps": 30,
            "max_tiles": 10,
        }
    )
    assert result.success, result.error
    d = result.data
    assert d["tiles"] <= 10
    assert d["window"]["duration"] == pytest.approx(4.0)  # window NOT shortened
    assert d["notes"] and "fps reduced" in d["notes"][0]


@needs_ffmpeg
def test_strip_rejects_a_window_past_the_end_rather_than_writing_a_blank(
    frozen_tail_clip,
):
    result = VisualQA().execute(
        {
            "operation": "strip",
            "input_path": str(frozen_tail_clip),
            "window": {"start": 99.0, "duration": 1.0},
        }
    )
    assert not result.success
    assert "past the last decodable frame" in result.error


@needs_ffmpeg
def test_strip_start_just_inside_the_last_frame_is_a_readable_error_not_ffmpeg_22(
    frozen_tail_clip,
):
    """A start between the last frame's pts and the duration decodes 0 frames;
    the bound has to be the last frame, not the container duration."""
    result = VisualQA().execute(
        {
            "operation": "strip",
            "input_path": str(frozen_tail_clip),
            "window": {"start": 3.99, "duration": 1.0},  # < 4.0s duration, > last pts
        }
    )
    assert not result.success
    assert "past the last decodable frame" in result.error
    assert "-22" not in result.error and "exit" not in result.error


@needs_ffmpeg
def test_sheet_drops_out_of_range_timestamps_without_truncating_the_rest(tmp_path):
    """image2 stops at the first gap in t_%04d, so an unusable timestamp must not
    consume an index — otherwise every later tile vanishes silently."""
    clip = tmp_path / "c.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=s=320x240:d=3:r=30",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        check=True,
        capture_output=True,
    )
    result = VisualQA().execute(
        {
            "operation": "sheet",
            "input_path": str(clip),
            "timestamps": [0.5, 99.0, 1.5, 2.5],  # the bad one is in the MIDDLE
        }
    )
    assert result.success, result.error
    assert result.data["tiles"] == 3, result.data
    assert result.data["sampling"]["timestamps"] == [0.5, 1.5, 2.5]
    assert any("99.00s" in n for n in result.data["notes"])


@needs_ffmpeg
def test_sheet_fails_clearly_when_no_timestamp_is_usable(tmp_path):
    clip = tmp_path / "c.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=s=320x240:d=2:r=30",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        check=True,
        capture_output=True,
    )
    result = VisualQA().execute({"operation": "sheet", "input_path": str(clip), "timestamps": [50.0, 60.0]})
    assert not result.success
    assert "none of the 2 timestamps" in result.error


@needs_ffmpeg
def test_labels_survive_a_code_root_containing_an_apostrophe_and_colon(frozen_tail_clip, tmp_path, monkeypatch):
    """A filtergraph is parsed twice, so one level of escaping is not enough —
    /Users/o'brien is a real home directory and broke the drawtext font path."""
    root = tmp_path / "o'brien co:x"
    (root / "assets" / "fonts" / "Inter").mkdir(parents=True)
    src = Path(__file__).resolve().parents[2] / "assets/fonts/Inter/Inter-Variable.ttf"
    if not src.is_file():
        pytest.skip("bundled label font not present")
    (root / "assets/fonts/Inter/Inter-Variable.ttf").write_bytes(src.read_bytes())
    monkeypatch.setenv("OPENNOLAN_CODE_ROOT", str(root))

    strip = VisualQA().execute(
        {
            "operation": "strip",
            "input_path": str(frozen_tail_clip),
            "window": {"start": 0.0, "duration": 0.5},
        }
    )
    assert strip.success, strip.error
    sheet = VisualQA().execute(
        {
            "operation": "sheet",
            "input_path": str(frozen_tail_clip),
            "timestamps": [0.5, 1.5],
        }
    )
    assert sheet.success, sheet.error


@needs_ffmpeg
def test_vs_plan_reports_missing_plan_instead_of_guessing(frozen_tail_clip):
    result = VisualQA().execute({"operation": "vs_plan", "input_path": str(frozen_tail_clip)})
    assert not result.success
    assert "edit_decisions.json" in result.error


@needs_ffmpeg
def test_vs_plan_end_to_end_flags_a_declared_animation_over_a_frozen_window(frozen_tail_clip, tmp_path):
    import json

    plan = tmp_path / "edit_decisions.json"
    plan.write_text(
        json.dumps(
            {
                "version": "1.0",
                "render_runtime": "ffmpeg",
                "cuts": [{"id": "a", "source": str(frozen_tail_clip), "in_seconds": 0, "out_seconds": 4}],
                "overlays": [
                    {
                        "type": "text",
                        "text": "rises",
                        "start_seconds": 2.2,
                        "end_seconds": 3.8,
                        "keyframes": [{"t": 2.2, "y": 900}, {"t": 3.8, "y": 300}],
                    }
                ],
            }
        )
    )
    result = VisualQA().execute(
        {
            "operation": "vs_plan",
            "input_path": str(frozen_tail_clip),
            "plan_path": str(plan),
        }
    )
    assert result.success, result.error
    d = result.data
    assert d["advisory"] is True
    flat = [f for f in d["findings"] if f["kind"] == "flat"]
    assert flat, d["lines"]
    assert "rises" in flat[0]["message"]
