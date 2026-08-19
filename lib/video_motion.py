"""Measured motion analysis for rendered-video QA.

QA here has always been "extract some stills, look at them, form an opinion".
A still cannot show motion, so a pan that never panned, an animation that never
ran and a frozen tail all read as "fine". This module turns a render into
numbers instead of opinions.

Pure functions only — no ffmpeg, no file I/O. The caller runs ONE ffmpeg pass
that emits per-frame curves via `metadata=print` and hands the text in here, so
the whole analysis is unit-testable without a video.

The three curves (all from one decode — see `VisualQA._measure`):

    luma    signalstats YAVG on the frames               -> brightness (0..255)
    scd     scdet score on the frames                    -> scene-change, 0..100
    motion  signalstats YAVG after tblend=all_mode=difference -> change energy

Thresholds are empirical, calibrated on this repo's 1080p H.264 renders. They
are parameters, not constants: frame-difference energy scales with bitrate and
resolution, so a 4K/high-bitrate source has a higher noise floor and a
low-bitrate one a lower ceiling. Every threshold is a knob on the op.

# ponytail: absolute thresholds, calibrated by hand. If they misfire across
# resolutions, normalize `motion` by the clip's own p95 before comparing —
# do NOT reach for optical flow (cv2 is not installed; see video_analyzer).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

# --- defaults ---------------------------------------------------------------

#: Bucket mean below this = "not moving". Frozen seconds in real renders measure
#: 0.02-0.10; a slow pan measures 0.5-2; a hard cut spikes to 20-80.
STATIC_THRESHOLD = 0.25
#: Below this the frames are effectively identical (a true freeze, not a slow shot).
FROZEN_DIVISOR = 5.0
#: A static stretch shorter than this is a normal held beat, not a defect.
MIN_STATIC_SECONDS = 0.75
#: luma YAVG below this = black/near-black.
DARK_THRESHOLD = 16.0
MIN_DARK_SECONDS = 0.2
#: ffmpeg's own scdet default. Real cuts in this repo's renders score 12-30.
CUT_THRESHOLD = 10.0
#: Two peaks closer than this are one cut.
CUT_MERGE_SECONDS = 0.2
#: A frame carrying less than this share of the surrounding detail has emptied
#: out. Measured on a real dropout: baseline edge energy ~5.0, trough 0.43 (9%).
DETAIL_DROP_RATIO = 0.35
#: Longer than this and the blank is an intentional beat, not a hole in the edit.
DETAIL_MAX_SECONDS = 0.25
#: Window either side used for the local detail baseline. Wide enough that a
#: few-frame hole cannot drag its own baseline down with it.
DETAIL_WINDOW_SECONDS = 1.0
#: Window just either side of a candidate used to read the shot's own detail
#: level. Short, so it stays inside the neighbouring shot rather than the one
#: after it.
EDGE_LEVEL_SECONDS = 0.33
#: How far the level after a candidate may differ from the level before it and
#: still count as "the picture came back". Beyond this it is a cut to different
#: content, not a hole in the middle of the same content.
LEVEL_MATCH_RATIO = 0.5
#: Below this the picture is genuinely flat everywhere (an all-black or plain
#: card), so "share of surrounding detail" stops meaning anything.
DETAIL_MIN_BASELINE = 1.0
#: Mean motion over the opening is reported separately: the hook is the one
#: window where being still is fatal rather than merely slow.
HOOK_SECONDS = 1.5

#: Analysis bucket. 0.5s smooths single-frame compression noise without hiding
#: a half-second freeze.
BUCKET_SECONDS = 0.5
#: The printed table never exceeds this many rows, whatever the duration.
MAX_TABLE_ROWS = 60

_FRAME_RE = re.compile(r"\bpts_time:(-?[0-9]+(?:\.[0-9]+)?|N/A)")

Series = list[tuple[float, float]]


@dataclass(frozen=True)
class Bucket:
    """One time bucket of a per-frame curve."""

    start: float
    end: float
    mean: float
    peak: float
    frames: int


@dataclass(frozen=True)
class Run:
    """A contiguous stretch of buckets that share a property."""

    start: float
    end: float
    mean: float
    kind: str

    @property
    def seconds(self) -> float:
        return round(self.end - self.start, 3)


# --- parsing ----------------------------------------------------------------


def parse_metadata_print(text: str) -> Series:
    """Parse ffmpeg `metadata=print` output into ``[(pts_time, value)]``.

    The format is a frame header line followed by one ``key=value`` line per
    printed key::

        frame:0    pts:0       pts_time:0
        lavfi.signalstats.YAVG=218.61

    Frames whose timestamp is ``N/A`` or whose value is missing/non-finite are
    dropped rather than guessed — a fabricated 0 would read as "frozen", which
    is exactly the verdict this module exists to get right.
    """
    out: Series = []
    pending: Optional[float] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _FRAME_RE.search(line)
        if m:
            stamp = m.group(1)
            pending = None if stamp == "N/A" else float(stamp)
            continue
        if pending is None or "=" not in line:
            continue
        try:
            value = float(line.rsplit("=", 1)[1].strip().rstrip(","))
        except ValueError:
            continue
        if math.isfinite(value):
            out.append((pending, value))
        # One value per frame: the next frame header re-arms `pending`.
        pending = None
    return out


# --- bucketing --------------------------------------------------------------


def bucket_series(series: Series, bucket_seconds: float = BUCKET_SECONDS) -> list[Bucket]:
    """Group a per-frame curve into fixed time buckets.

    Only buckets that actually contain frames are returned. An empty bucket
    would have to be reported as mean 0.0, which reads as "frozen" — a decode
    gap must never masquerade as a defect, so the gap stays visible instead as a
    discontinuity between `end` and the next `start`.
    """
    if bucket_seconds <= 0:
        raise ValueError("bucket_seconds must be > 0")
    grouped: dict[int, list[float]] = {}
    for t, v in series:
        grouped.setdefault(int(t // bucket_seconds), []).append(v)
    buckets: list[Bucket] = []
    for idx in sorted(grouped):
        vals = grouped[idx]
        buckets.append(
            Bucket(
                start=round(idx * bucket_seconds, 4),
                end=round((idx + 1) * bucket_seconds, 4),
                mean=sum(vals) / len(vals),
                peak=max(vals),
                frames=len(vals),
            )
        )
    return buckets


def _contiguous(a: Bucket, b: Bucket) -> bool:
    return abs(b.start - a.end) < 1e-6


def find_runs(
    buckets: Sequence[Bucket],
    *,
    below: float,
    min_seconds: float,
    kind: str,
) -> list[Run]:
    """Contiguous stretches whose bucket mean stays below `below`."""
    runs: list[Run] = []
    current: list[Bucket] = []

    def flush() -> None:
        if not current:
            return
        span = current[-1].end - current[0].start
        if span + 1e-9 >= min_seconds:
            total = sum(b.mean * b.frames for b in current)
            n = sum(b.frames for b in current) or 1
            runs.append(
                Run(
                    start=current[0].start,
                    end=current[-1].end,
                    mean=total / n,
                    kind=kind,
                )
            )
        current.clear()

    for b in buckets:
        if b.mean < below and (not current or _contiguous(current[-1], b)):
            current.append(b)
        else:
            flush()
            if b.mean < below:
                current.append(b)
    flush()
    return runs


def peaks_above(
    series: Series,
    *,
    threshold: float,
    merge_seconds: float = CUT_MERGE_SECONDS,
) -> list[float]:
    """Timestamps where the curve exceeds `threshold`, one per cluster.

    Within a cluster the timestamp of the single highest sample wins, so a
    two-frame transition reports one boundary rather than two.
    """
    peaks: list[float] = []
    cluster: list[tuple[float, float]] = []

    def flush() -> None:
        if cluster:
            peaks.append(round(max(cluster, key=lambda p: p[1])[0], 3))
            cluster.clear()

    for t, v in series:
        if v < threshold:
            continue
        if cluster and t - cluster[-1][0] > merge_seconds:
            flush()
        cluster.append((t, v))
    flush()
    return peaks


def find_detail_dropouts(
    detail: Series,
    *,
    drop_ratio: float = DETAIL_DROP_RATIO,
    max_seconds: float = DETAIL_MAX_SECONDS,
    window_seconds: float = DETAIL_WINDOW_SECONDS,
    min_baseline: float = DETAIL_MIN_BASELINE,
) -> list[Run]:
    """Frames where spatial detail collapses and then comes back.

    This is the hole no other curve sees. An empty frame mid-transition keeps
    normal luma (so no black-frame test fires), keeps its min/max spread (a
    stray cursor or a clipped label is enough), and scores near zero on scene
    change because the blank resembles the pale frames on either side. It is
    also 1-3 frames long, so bucketing averages it away and a contact sheet
    never lands on it.

    What gives it away is that the picture briefly stops containing anything,
    then contains it again. The recovery is the whole discriminator: a cut to a
    genuinely sparse shot drops detail and STAYS down, which is a design
    choice, not a defect.

    Runs at the very start or end are skipped -- with nothing on one side there
    is no baseline to fall from, or no recovery to confirm.
    """
    if len(detail) < 3:
        return []

    times = [t for t, _ in detail]
    vals = [v for _, v in detail]

    # Windows are taken in FRAMES, not by scanning timestamps: per-frame stats
    # off one decode are evenly spaced, and rescanning every timestamp for every
    # frame is quadratic -- 5.6s on a ten-minute video, on a op that advertises
    # about one. Uneven spacing only shifts a median baseline slightly.
    span = times[-1] - times[0]
    fps = (len(times) - 1) / span if span > 0 else 30.0
    half = max(1, round(window_seconds * fps))
    edge = max(1, round(EDGE_LEVEL_SECONDS * fps))

    flagged: list[bool] = []
    for i, v in enumerate(vals):
        base = percentile(vals[max(0, i - half) : i + half + 1], 0.5)
        flagged.append(base >= min_baseline and v < drop_ratio * base)

    def level(lo_idx: int, hi_idx: int) -> Optional[float]:
        """Median detail over a short window, clamped to the series."""
        lo, hi = max(0, lo_idx), min(len(vals), hi_idx)
        return percentile(vals[lo:hi], 0.5) if hi > lo else None

    out: list[Run] = []
    i = 0
    while i < len(flagged):
        if not flagged[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(flagged) and flagged[j + 1]:
            j += 1
        # needs a normal frame on BOTH sides: one to fall from, one to recover to
        if i > 0 and j < len(flagged) - 1:
            start, end = times[i], times[j]
            before = level(i - edge, i)
            after = level(j + 1, j + 1 + edge)
            run_vals = vals[i : j + 1]
            peak = max(run_vals)
            # The symmetric baseline above straddles cuts, so a low-detail shot
            # sitting next to a high-detail one gets its last frames flagged --
            # a title card before busy footage reads as a hole that is not one.
            # A dropout falls from the surrounding content AND RETURNS TO IT;
            # a cut lands somewhere else and stays. Requiring the level either
            # side to match is what separates them.
            returns = (
                before is not None
                and after is not None
                and peak < drop_ratio * before
                and peak < drop_ratio * after
                and abs(after - before) <= LEVEL_MATCH_RATIO * max(before, after)
            )
            if returns and end - start <= max_seconds:
                out.append(
                    Run(
                        start=round(start, 3),
                        end=round(end, 3),
                        mean=round(sum(run_vals) / len(run_vals), 4),
                        kind="detail_dropout",
                    )
                )
        i = j + 1
    return out


def window_stats(series: Series, start: float, end: float) -> dict[str, Any]:
    """mean/peak/frames of a curve inside ``[start, end)``.

    `frames == 0` means the window was never sampled — the caller must treat
    that as "unknown", never as "no motion".
    """
    vals = [v for t, v in series if start <= t < end]
    if not vals:
        return {"mean": None, "median": None, "peak": None, "frames": 0}
    return {
        "mean": round(sum(vals) / len(vals), 4),
        # the median is the honest one for "is this window moving": a single
        # spike at the start of an otherwise dead window drags the mean over
        # any floor you set, and a hook that opens with a lurch and then coasts
        # to a stop is exactly the shape that fools it.
        "median": round(percentile(vals, 0.5), 4),
        "peak": round(max(vals), 4),
        "frames": len(vals),
    }


def percentile(values: Iterable[float], q: float) -> float:
    """Nearest-rank percentile; 0.0 for an empty input."""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


# --- reporting --------------------------------------------------------------


def table_bucket_seconds(duration: float, max_rows: int = MAX_TABLE_ROWS) -> float:
    """Bucket size that keeps the printed table under `max_rows` rows."""
    if duration <= 0:
        return 1.0
    return max(1.0, math.ceil(duration / max_rows))


def timeline_table(
    motion: Sequence[Bucket],
    luma: Sequence[Bucket],
    *,
    static_threshold: float = STATIC_THRESHOLD,
    dark_threshold: float = DARK_THRESHOLD,
    cuts: Sequence[float] = (),
    bar_width: int = 34,
) -> list[str]:
    """A readable per-bucket motion table — the thing the agent actually reads.

    The bar is scaled to the p95 of bucket means, not the max: one hard cut
    spikes 50x the median and would otherwise flatten every real bar to nothing.
    """
    if not motion:
        return ["(no frames measured)"]
    luma_at = {round(b.start, 4): b.mean for b in luma}
    scale = percentile([b.mean for b in motion], 0.95) or max(b.mean for b in motion) or 1.0
    rows = [f"{'start':>7} {'motion':>8} {'luma':>5}  {'bar':<{bar_width}} flags"]
    for b in motion:
        flags = []
        if b.mean < static_threshold / FROZEN_DIVISOR:
            flags.append("FROZEN")
        elif b.mean < static_threshold:
            flags.append("static")
        lum = luma_at.get(round(b.start, 4))
        if lum is not None and lum < dark_threshold:
            flags.append("DARK")
        hits = [c for c in cuts if b.start <= c < b.end]
        if hits:
            flags.append("cut@" + ",".join(f"{c:g}" for c in hits))
        filled = min(bar_width, int(round(bar_width * b.mean / scale)))
        rows.append(
            f"{b.start:7.1f} {b.mean:8.3f} "
            f"{('' if lum is None else f'{lum:5.0f}')} "
            f" {'#' * filled:<{bar_width}} {' '.join(flags)}".rstrip()
        )
    return rows


def summarize(
    *,
    motion: Series,
    luma: Series,
    scd: Series,
    duration: float,
    detail: Optional[Series] = None,
    hook_seconds: float = HOOK_SECONDS,
    static_threshold: float = STATIC_THRESHOLD,
    dark_threshold: float = DARK_THRESHOLD,
    cut_threshold: float = CUT_THRESHOLD,
    min_static_seconds: float = MIN_STATIC_SECONDS,
    bucket_seconds: float = BUCKET_SECONDS,
) -> dict[str, Any]:
    """Reduce three per-frame curves to a compact, agent-readable verdict.

    Returns measurement, never a pass/fail: the caller decides. Every finding
    carries the number it was derived from so it can be argued with.
    """
    mb = bucket_series(motion, bucket_seconds)
    lb = bucket_series(luma, bucket_seconds)

    # frozen is a strict SUBSET of static: the frozen threshold is lower, so every
    # frozen bucket is also a static bucket and every frozen run sits inside a
    # static run. They are reported as one finding each (the static span, upgraded
    # to FROZEN when it contains identical frames) and counted ONCE — summing both
    # lists would inflate static_fraction by the length of every freeze.
    frozen = find_runs(
        mb,
        below=static_threshold / FROZEN_DIVISOR,
        min_seconds=min_static_seconds,
        kind="frozen",
    )
    static = find_runs(mb, below=static_threshold, min_seconds=min_static_seconds, kind="static")
    dark = find_runs(lb, below=dark_threshold, min_seconds=MIN_DARK_SECONDS, kind="dark")
    cuts = peaks_above(scd, threshold=cut_threshold)
    # measured on the RAW per-frame curve, never the buckets: a 3-frame hole is
    # exactly what bucket_seconds is documented to smooth away.
    dropouts = find_detail_dropouts(detail) if detail else []
    hook = window_stats(motion, 0.0, hook_seconds)

    # A run's last bucket ends on a whole bucket_seconds boundary, which sits PAST
    # the end of any clip whose duration isn't a multiple of it. Left alone that
    # reports a freeze in time that does not exist and pushes static_fraction over
    # 1.0 on a fully static clip — over the maximum:1 declared for
    # final_review.checks.motion_check.static_fraction.
    def clip_to_duration(r: Run) -> Run:
        if duration <= 0 or r.end <= duration or r.start >= duration:
            return r
        return Run(start=r.start, end=duration, mean=r.mean, kind=r.kind)

    frozen = [clip_to_duration(r) for r in frozen]
    static = [clip_to_duration(r) for r in static]
    dark = [clip_to_duration(r) for r in dark]

    static_seconds = sum(r.seconds for r in static)
    frozen_seconds = sum(r.seconds for r in frozen)
    values = [v for _, v in motion]

    findings: list[str] = []
    for r in sorted(static, key=lambda r: -r.seconds):
        inner = [f for f in frozen if f.start < r.end - 1e-9 and f.end > r.start + 1e-9]
        if inner:
            span = (
                ""
                if abs(inner[0].start - r.start) < 1e-6 and abs(inner[-1].end - r.end) < 1e-6
                else f" (identical frames {inner[0].start:.2f}-{inner[-1].end:.2f}s)"
            )
            findings.append(
                f"FROZEN {r.start:.2f}-{r.end:.2f}s ({r.seconds:.2f}s) "
                f"mean motion {r.mean:.3f}{span} — consecutive frames are "
                "effectively identical"
            )
        else:
            findings.append(
                f"STATIC {r.start:.2f}-{r.end:.2f}s ({r.seconds:.2f}s) mean motion {r.mean:.3f} < {static_threshold:g}"
            )
    for r in dark:
        findings.append(f"DARK {r.start:.2f}-{r.end:.2f}s ({r.seconds:.2f}s) mean luma {r.mean:.1f}")
    for r in dropouts:
        findings.append(
            f"EMPTY-FRAME {r.start:.2f}-{r.end:.2f}s — spatial detail falls to "
            f"{r.mean:.2f} against a normal-content baseline, then returns. The picture "
            f"briefly contains almost nothing; luma is unchanged, so no black-frame "
            f"check sees this. Confirm with `strip` over the window."
        )

    # A table bucket coarser than the analysis bucket keeps long videos readable.
    tb = table_bucket_seconds(duration)
    table_motion = bucket_series(motion, tb) if tb != bucket_seconds else mb
    table_luma = bucket_series(luma, tb) if tb != bucket_seconds else lb

    return {
        "duration_seconds": round(duration, 3),
        "frames_measured": len(motion),
        "bucket_seconds": bucket_seconds,
        "thresholds": {
            "static": static_threshold,
            "frozen": round(static_threshold / FROZEN_DIVISOR, 4),
            "dark": dark_threshold,
            "cut": cut_threshold,
            "min_static_seconds": min_static_seconds,
        },
        "motion_stats": {
            "median": round(percentile(values, 0.5), 4),
            "mean": round(sum(values) / len(values), 4) if values else 0.0,
            "p95": round(percentile(values, 0.95), 4),
            "peak": round(max(values), 4) if values else 0.0,
        },
        "static_seconds": round(static_seconds, 3),
        # min(1.0, ...): the clamp above lands runs exactly on `duration`, but
        # Run.seconds is rounded to 3dp, so an 8.6s all-static clip still sums to a
        # share like 1.0002 — over the maximum:1 the schema declares.
        "static_fraction": (min(1.0, round(static_seconds / duration, 4)) if duration > 0 else 0.0),
        "frozen_seconds": round(frozen_seconds, 3),
        # frozen_runs ⊆ static_runs by construction; frozen carries the tight
        # windows vs_plan needs, static_runs the spans a reader should look at.
        "frozen_runs": [_run_dict(r) for r in frozen],
        "static_runs": [_run_dict(r) for r in static],
        "dark_runs": [_run_dict(r) for r in dark],
        "cut_times": cuts,
        "cut_count": len(cuts),
        "detail_dropouts": [
            {**{k: v for k, v in _run_dict(r).items() if k != "mean_motion"}, "mean_detail": round(r.mean, 4)}
            for r in dropouts
        ],
        "hook_motion": {**hook, "window_seconds": hook_seconds},
        "findings": findings,
        "table": timeline_table(
            table_motion,
            table_luma,
            static_threshold=static_threshold,
            dark_threshold=dark_threshold,
            cuts=cuts,
        ),
    }


def _run_dict(r: Run) -> dict[str, Any]:
    return {
        "start": round(r.start, 3),
        "end": round(r.end, 3),
        "seconds": r.seconds,
        "mean_motion": round(r.mean, 4),
    }


# --- tile geometry ----------------------------------------------------------


def grid_for(
    n_tiles: int,
    *,
    tile_width: int,
    aspect: float,
    max_pixels: int = 4_000_000,
    rows: Optional[int] = None,
    min_tile_width: int = 80,
) -> dict[str, Any]:
    """Choose a tile grid that HOLDS every frame and fits a pixel budget.

    ffmpeg's `tile` filter silently drops frames past `cols*rows`, and every
    recipe in this repo pairs it with `-frames:v 1` — so an undersized grid
    reports success while showing only the beginning of the video. The capacity
    returned here is always >= n_tiles; `empty_cells` names the padding so the
    reader isn't left wondering about trailing blanks.

    `aspect` is tile height / tile width.

    Holding every tile wins over the pixel budget: shrinking below
    `min_tile_width` makes the sheet unreadable, so past that floor the budget is
    reported as busted (`over_budget`) rather than quietly honored by dropping
    tiles. The caller's fix is to sample fewer frames, not to squint.
    """
    if n_tiles <= 0:
        raise ValueError("n_tiles must be >= 1")
    if rows is None:
        # Roughly square output: cols/rows ~ aspect.
        cols = max(1, int(round(math.sqrt(n_tiles * aspect))))
        rows = math.ceil(n_tiles / cols)
    else:
        rows = max(1, rows)
        cols = math.ceil(n_tiles / rows)
    cols = max(1, cols)

    def dims(width: int) -> tuple[int, int]:
        w = max(2, width - (width % 2))
        h = max(2, int(round(w * aspect)))
        return w, h - (h % 2)

    tw = max(min_tile_width, int(tile_width))
    for _ in range(24):
        w, h = dims(tw)
        if (cols * w) * (rows * h) <= max_pixels or tw <= min_tile_width:
            break
        tw = max(min_tile_width, int(tw * 0.85))
    tw, th = dims(tw)
    pixels = (cols * tw) * (rows * th)

    return {
        "cols": cols,
        "rows": rows,
        "tile_width": tw,
        "tile_height": th,
        "capacity": cols * rows,
        "empty_cells": cols * rows - n_tiles,
        "pixels": pixels,
        "over_budget": pixels > max_pixels,
    }


def max_tiles_for(aspect: float, *, tile_width: int, max_pixels: int) -> int:
    """Largest tile count whose GRID fits the pixel budget at this tile size.

    Not simply `budget / tile_area`: a grid rounds its column and row counts up,
    so the sheet is always a little larger than the tiles it holds. The estimate
    is walked down until the real geometry fits, which takes only a few steps
    because the padding is at most one extra row and column.
    """
    height = max(2, int(round(tile_width * aspect)))
    n = max(1, int(max_pixels // (tile_width * height)))
    for _ in range(64):
        if n <= 1:
            break
        grid = grid_for(
            n,
            tile_width=tile_width,
            aspect=aspect,
            max_pixels=max_pixels,
            min_tile_width=tile_width,  # pin the size so over_budget reflects geometry
        )
        if not grid["over_budget"]:
            break
        n -= max(1, n // 32)
    return max(1, n)


def sample_count(duration: float, fps: float) -> int:
    """Upper bound on the frames `fps=<fps>` yields over `duration` seconds (>= 1).

    Deliberately never UNDER-counts. ffmpeg's `fps` filter assigns each input
    frame to the nearest output slot, so the exact count is not floor(d*fps) —
    measured against real clips at 24/25/30fps, floor undercounts 12 of 25
    (duration, fps) pairs and floor(d*fps+0.5) still undercounts one (a
    2.708333s/24fps clip at fps=12 emits 33, not 32). Ceil never undercounts.

    The asymmetry is the whole point: `tile` silently DROPS frames past
    cols*rows, so an undercount hides the tail of the video while reporting
    `empty_cells: 0`. An overcount costs one visibly blank padding cell.
    """
    if duration <= 0 or fps <= 0:
        return 1
    return max(1, math.ceil(duration * fps - 1e-9))
