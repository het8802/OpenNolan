"""Diff an edit_decisions plan against what the render measurably did.

The failure this exists for: the plan reads beautifully, every stage passes, and
the video is wrong. Nothing in the pipeline ever compared the two — `final_review`
inspects the output, `slideshow_risk` scores the *plan* (its own docstring says
so, and its `edit_decisions` argument is dead), and no check spans both.

Two independent halves, deliberately separate because they have very different
confidence:

  STATIC   Declared-but-not-rendered. Pure lint of the doc against what the
           FFmpeg path actually reads. Needs no video and cannot be wrong: if
           `cuts[].transform.animation` has no reader, that ken-burns move was
           never going to happen. This catches the single most common
           disappointment ("my pan and zoom didn't come out") for free.

  MEASURED Declared-and-should-have-moved-but-didn't. Compares declared motion
           windows against the measured motion curve. Weaker evidence — frame
           energy is averaged over the whole frame, so a small moving badge gets
           diluted — so it only speaks up when the frame is essentially
           identical, and it always reports the number it used.

Everything is ADVISORY. Findings are returned worst-first for a reader who will
stop after the first few; nothing here decides pass/fail.

Renderer scope: the STATIC half describes the **ffmpeg** path only. On the
remotion/hyperframes paths `in_seconds`/`out_seconds` mean timeline position
rather than source offsets, and a composition's motion lives in GSAP/TSX that no
schema describes — so the lint is skipped rather than guessed at.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from lib.video_motion import FROZEN_DIVISOR, STATIC_THRESHOLD, Series, window_stats

#: Severity ordering for worst-first output.
_RANK = {"high": 0, "medium": 1, "low": 2, "info": 3}

#: Keyframe channels each overlay type actually renders. Everything else is
#: warned-and-dropped by video_compose (`_build_drawtext_filter`,
#: `_keyframe_overlay`).
_TEXT_CHANNELS = frozenset({"x", "y", "opacity"})
_LAYER_CHANNELS = frozenset({"x", "y", "scale", "opacity"})

#: subtitles.* keys the renderer never reads. NOTE: subtitles.style is a
#: display-mode STRING per the schema ("sentence"/"word-by-word"/...), not a style
#: dict — so it is not the place to move these to.
_DEAD_SUBTITLE_KEYS = (
    "color",
    "outline_color",
    "background",
    "position",
    "max_words_per_line",
    "font",
    "font_size",
)

#: Transition names the renderer treats as a hard cut, i.e. nothing declared
#: (mirrors VideoCompose._HARD_CUT_NAMES, video_compose.py:962).
_HARD_CUT_NAMES = frozenset({"", "cut", "none", "hard", "hard_cut"})

#: Cut-boundary detection tolerance, seconds.
CUT_TOLERANCE_SECONDS = 0.35


def _finding(
    severity: str,
    kind: str,
    where: str,
    message: str,
    *,
    declared: Any = None,
    measured: Any = None,
    fix: Optional[str] = None,
) -> dict[str, Any]:
    out = {
        "severity": severity,
        "kind": kind,
        "where": where,
        "message": message,
    }
    if declared is not None:
        out["declared"] = declared
    if measured is not None:
        out["measured"] = measured
    if fix:
        out["fix"] = fix
    return out


def _runtime(doc: dict[str, Any]) -> str:
    return str(doc.get("render_runtime") or "ffmpeg").strip().lower()


def _base_cuts(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Cuts that occupy the main timeline. `layer='overlay'` cuts are PiP."""
    return [c for c in (doc.get("cuts") or []) if (c.get("layer") or "primary") != "overlay"]


def _num(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


# --- static half: declared but not rendered ---------------------------------


def static_findings(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Declarations the FFmpeg render path provably ignores.

    No video is read. Every finding here is a certainty, not an inference.
    """
    runtime = _runtime(doc)
    if runtime != "ffmpeg":
        return [
            _finding(
                "info",
                "lint-skipped",
                "render_runtime",
                f"render_runtime={runtime!r}: the declared-vs-rendered lint describes the "
                "ffmpeg path only. A composition's motion is authored as GSAP/TSX inside "
                "its own HTML/TSX, which no schema describes, so nothing is asserted about "
                "it here. Cut boundaries and the measured curves below still apply — to "
                "check a composition's animation, run `strip` over its window.",
                declared=runtime,
            )
        ]

    out: list[dict[str, Any]] = []
    # Mirror the renderer's own predicate (video_compose.py:3183-3202), not mere
    # truthiness: composite_background is set only for a dict whose type is color
    # or image. A background of {"type": "none"} is falsy to the renderer, so a
    # plain bool() test would wrongly stay silent about a dropped transform.
    _bg = (doc.get("metadata") or {}).get("background")
    has_background = isinstance(_bg, dict) and str(_bg.get("type") or "").strip().lower() in (
        "color",
        "image",
    )

    for i, cut in enumerate(doc.get("cuts") or []):
        transform = cut.get("transform") or {}
        is_pip = (cut.get("layer") or "primary") == "overlay"

        if transform.get("animation"):
            out.append(
                _finding(
                    "high",
                    "not-rendered",
                    f"cuts[{i}].transform.animation",
                    "Declared clip animation (ken-burns / pan-zoom) has NO reader on the "
                    "ffmpeg path — the only reader is _needs_remotion, which is skipped "
                    "when render_runtime=='ffmpeg'. It is dropped with no warning, so the "
                    "clip renders static.",
                    declared=transform.get("animation"),
                    fix="Pre-bake the move with tools/video/motion_ops.py (pan_zoom) and "
                    "point cuts[].source at the derived file, or render via remotion.",
                )
            )

        if not is_pip and not has_background:
            # Mirror video_compose.py:1865: the renderer treats scale 1.0 and
            # position "center" as no-ops and routes them to the legacy
            # fit-and-centre path — which renders exactly the declared full-canvas
            # centred box anyway. They are also the schema's documented defaults,
            # so flagging them is pure noise.
            for key, no_ops in (("scale", (1.0, 1)), ("position", ("center",))):
                value = transform.get(key)
                if value is not None and value not in no_ops:
                    out.append(
                        _finding(
                            "high",
                            "not-rendered",
                            f"cuts[{i}].transform.{key}",
                            f"transform.{key} on a main-timeline cut is only applied when "
                            "metadata.background is set; without it the legacy "
                            "scale/pad/center path runs and the declared box is dropped "
                            "silently.",
                            declared=transform.get(key),
                            fix="Set metadata.background, or drop the transform so the plan matches the render.",
                        )
                    )

        declared_transition = cut.get("transition_in") or cut.get("transition_out")
        # A hard-cut name declares nothing, so suppressing it renders identically.
        if is_pip and str(declared_transition or "").strip().lower() not in _HARD_CUT_NAMES:
            out.append(
                _finding(
                    "low",
                    "not-rendered",
                    f"cuts[{i}].transition_in/out",
                    "PiP (layer='overlay') cuts are removed from the base list before "
                    "transitions are resolved, so their transitions never render.",
                    declared=declared_transition,
                )
            )

    for i, ov in enumerate(doc.get("overlays") or []):
        if ov.get("animation"):
            out.append(
                _finding(
                    "high",
                    "not-rendered",
                    f"overlays[{i}].animation",
                    "overlays[].animation has no reader anywhere in the render path. The overlay renders static.",
                    declared=ov.get("animation"),
                    fix="Express the motion as overlays[].keyframes (x/y/scale/opacity).",
                )
            )

        honored = _TEXT_CHANNELS if ov.get("type") == "text" else _LAYER_CHANNELS
        declared_channels = {
            k
            for kf in (ov.get("keyframes") or [])
            for k, v in kf.items()
            if k != "t" and k != "easing" and v is not None
        }
        for dead in sorted(declared_channels - honored):
            kind_label = "text (drawtext)" if ov.get("type") == "text" else "image/video"
            out.append(
                _finding(
                    "medium",
                    "not-rendered",
                    f"overlays[{i}].keyframes[].{dead}",
                    f"The {kind_label} overlay path renders "
                    f"{'/'.join(sorted(honored))} keyframes only — {dead} is warned and "
                    "ignored.",
                    declared=dead,
                )
            )

        out.extend(_keyframe_shape_findings(i, ov, honored))

    if doc.get("transitions"):
        out.append(
            _finding(
                "medium",
                "not-rendered",
                "transitions",
                "The top-level transitions[] list is never read by any renderer — it is "
                "only carried through the assemble EDL.",
                declared=len(doc["transitions"]),
                fix="Declare transitions per cut via transition_in / transition_out.",
            )
        )

    if doc.get("music"):
        out.append(
            _finding(
                "medium",
                "not-rendered",
                "music",
                "The legacy top-level music object is never read on the ffmpeg path.",
                fix="Move it to audio.music.",
            )
        )

    subtitles = doc.get("subtitles") or {}
    dead_subs = [k for k in _DEAD_SUBTITLE_KEYS if subtitles.get(k) is not None]
    if dead_subs:
        out.append(
            _finding(
                "low",
                "not-rendered",
                "subtitles",
                f"subtitles.{{{','.join(dead_subs)}}} are never read by the renderer.",
                declared=dead_subs,
                fix="Set subtitle typography via the style playbook, or via "
                "video_compose's subtitle_style input — NOT subtitles.style, which the "
                "schema declares as a display-mode string, not a style dict.",
            )
        )

    return out


def _keyframe_shape_findings(index: int, ov: dict[str, Any], honored: frozenset[str]) -> list[dict[str, Any]]:
    """Keyframe sets that are structurally incapable of producing motion."""
    kfs = ov.get("keyframes") or []
    if not kfs:
        return []
    out: list[dict[str, Any]] = []

    if len(kfs) == 1:
        out.append(
            _finding(
                "medium",
                "no-op-keyframes",
                f"overlays[{index}].keyframes",
                "A single keyframe cannot animate — the renderer holds the value "
                "constant before the first and after the last keyframe.",
                declared=1,
            )
        )
        return out

    moving = {ch for ch in honored if len({kf[ch] for kf in kfs if kf.get(ch) is not None}) > 1}
    if not moving:
        out.append(
            _finding(
                "medium",
                "no-op-keyframes",
                f"overlays[{index}].keyframes",
                f"{len(kfs)} keyframes declared but no rendered channel "
                f"({'/'.join(sorted(honored))}) ever changes value — the overlay is "
                "static despite the keyframe timeline.",
                declared=sorted({k for kf in kfs for k in kf if k not in ("t", "easing")}),
            )
        )
        return out

    start = _num(ov.get("start_seconds"))
    end = _num(ov.get("end_seconds"))
    times = sorted(_num(kf.get("t")) for kf in kfs)
    if end > start and (times[-1] <= start or times[0] >= end):
        out.append(
            _finding(
                "high",
                "keyframes-outside-window",
                f"overlays[{index}].keyframes",
                f"Every keyframe (t {times[0]:g}..{times[-1]:g}s) lies outside the "
                f"overlay's visible window {start:g}..{end:g}s. Values are held constant "
                "outside the keyframe range, so nothing animates on screen.",
                declared=f"kf {times[0]:g}..{times[-1]:g}s vs visible {start:g}..{end:g}s",
            )
        )
    return out


# --- expected windows -------------------------------------------------------


def expected_cut_windows(doc: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Absolute timeline windows for each main-timeline cut.

    Returns ``(windows, notes)``. Windows carry `index`, `id`, `start`, `end`.

    Start times are DERIVED, never stored: cuts concatenate as
    ``(out_seconds - in_seconds) / speed``. On the ffmpeg path an xfade join also
    OVERLAPS its neighbours and shortens the total; the only Python that does
    that lives inlined in a PiP helper at video_compose.py:3421. Transition
    NAME PRECEDENCE and duration clamping are reused from the renderer's own
    `_resolve_joins` classmethod rather than reimplemented, so those cannot
    drift; if that import fails the boundary checks are skipped and say so — a
    divergent local copy would be worse than no answer. The separate cap on
    available material lives in `_transitions_concat`, not in `_resolve_joins`,
    so it IS mirrored here by hand (see below).

    One residual imprecision is inherent to deriving from a doc: the renderer
    caps against PROBED normalized segment durations while this uses nominal
    ``(out - in) / speed``, so sub-frame drift remains.

    Note the JS preview (`interp.js` cutStarts) does NOT subtract xfade overlap,
    so preview and render genuinely disagree once any transition is set. These
    windows follow the RENDERER.
    """
    cuts = _base_cuts(doc)
    notes: list[str] = []
    if not cuts:
        return [], notes

    runtime = _runtime(doc)
    joins: list[Optional[dict[str, Any]]] = [None] * max(0, len(cuts) - 1)
    if runtime == "ffmpeg":
        try:
            from tools.video.video_compose import VideoCompose

            joins, join_warnings = VideoCompose._resolve_joins(cuts, doc.get("metadata"))
            notes.extend(join_warnings)
        except Exception as exc:  # pragma: no cover - import-environment dependent
            notes.append(
                f"cut-boundary derivation skipped: could not reuse the renderer's "
                f"transition resolution ({type(exc).__name__}: {exc}). Not substituting "
                "a local copy."
            )
            return [], notes
    else:
        # Composition runtimes build their own timeline; xfade overlap is an
        # ffmpeg-path concept, so cuts concatenate with no shortening. Verified
        # against a real screen-demo doc: cumulative (out-in)/speed reproduced the
        # 33.00s render and metadata.total_duration_seconds exactly, while reading
        # out_seconds as an absolute timeline position gave 4.50s.
        notes.append(f"render_runtime={runtime}: cuts concatenate with no transition overlap subtracted.")

    windows: list[dict[str, Any]] = []
    pos = 0.0
    for i, cut in enumerate(cuts):
        speed = _num(cut.get("speed"), 1.0) or 1.0
        span = _num(cut.get("out_seconds")) - _num(cut.get("in_seconds"))
        dur = max(0.0, span) / speed
        join = joins[i - 1] if i > 0 else None
        if join:
            # An xfade can never consume more material than exists on either side:
            # the renderer caps it at min(cum, seg) - 0.05 (video_compose.py
            # _transitions_concat). Without the cap a transition longer than its
            # neighbouring cut runs the derivation backwards and produces three
            # simultaneously-wrong findings that blame the render.
            available = max(0.0, min(pos, dur) - 0.05)
            pos -= min(join["duration"], available)
        windows.append(
            {
                "index": i,
                "id": cut.get("id"),
                "start": round(pos, 4),
                "end": round(pos + dur, 4),
                "join": join,
            }
        )
        pos += dur
    return windows, notes


def expected_motion_windows(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Windows in which a declared, RENDERED keyframe animation should be visible.

    Overlay timing is absolute on every runtime, so this needs no derivation and
    is the highest-confidence measured check available.
    """
    out: list[dict[str, Any]] = []
    for i, ov in enumerate(doc.get("overlays") or []):
        kfs = ov.get("keyframes") or []
        if len(kfs) < 2:
            continue
        honored = _TEXT_CHANNELS if ov.get("type") == "text" else _LAYER_CHANNELS
        moving = sorted(ch for ch in honored if len({kf[ch] for kf in kfs if kf.get(ch) is not None}) > 1)
        if not moving:
            continue
        times = sorted(_num(kf.get("t")) for kf in kfs)
        start = max(_num(ov.get("start_seconds")), times[0])
        end = min(_num(ov.get("end_seconds")), times[-1])
        if end - start <= 1e-6:
            continue
        out.append(
            {
                "index": i,
                "label": ov.get("text") or ov.get("asset_id") or ov.get("type") or "overlay",
                "channels": moving,
                "start": round(start, 4),
                "end": round(end, 4),
            }
        )
    return out


# --- measured half ----------------------------------------------------------


def measured_findings(
    doc: dict[str, Any],
    *,
    motion: Series,
    cut_times: list[float],
    frozen_runs: list[dict[str, Any]],
    duration: float,
    flat_threshold: Optional[float] = None,
    cut_tolerance: float = CUT_TOLERANCE_SECONDS,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Compare declared motion/timing against the measured curves."""
    if flat_threshold is None:
        flat_threshold = STATIC_THRESHOLD / FROZEN_DIVISOR
    out: list[dict[str, Any]] = []

    for w in expected_motion_windows(doc):
        stats = window_stats(motion, w["start"], w["end"])
        chans = "/".join(w["channels"])
        if not stats["frames"]:
            out.append(
                _finding(
                    "info",
                    "unmeasured",
                    f"overlays[{w['index']}].keyframes",
                    f"Declared {chans} animation over {w['start']:g}-{w['end']:g}s but no "
                    "frames were sampled in that window — no verdict.",
                    declared=f"{chans} over {w['start']:g}-{w['end']:g}s",
                )
            )
            continue
        overlapping = [r for r in frozen_runs if r["start"] <= w["start"] + 1e-6 and r["end"] >= w["end"] - 1e-6]
        if overlapping:
            r = overlapping[0]
            out.append(
                _finding(
                    "high",
                    "flat",
                    f"overlays[{w['index']}].keyframes",
                    f"{w['label']!r} declares a {chans} animation over "
                    f"{w['start']:g}-{w['end']:g}s, but that whole window sits inside a "
                    f"frozen stretch ({r['start']:g}-{r['end']:g}s, mean motion "
                    f"{r['mean_motion']:g}) — consecutive frames are effectively "
                    "identical, so the animation likely did not render. Frame energy is "
                    "averaged over the WHOLE frame, so an element under roughly 20% of "
                    "frame width dilutes to nothing here; confirm with a `strip` over "
                    "this window.",
                    declared=f"{chans} over {w['start']:g}-{w['end']:g}s",
                    measured=f"mean motion {stats['mean']:g}, peak {stats['peak']:g}",
                )
            )
        elif stats["mean"] is not None and stats["mean"] < flat_threshold:
            out.append(
                _finding(
                    "medium",
                    "flat",
                    f"overlays[{w['index']}].keyframes",
                    f"{w['label']!r} declares a {chans} animation over "
                    f"{w['start']:g}-{w['end']:g}s, but measured frame-change energy there "
                    f"is {stats['mean']:g} (< {flat_threshold:g}) — the frame is close to "
                    "identical, so the animation likely did not render.",
                    declared=f"{chans} over {w['start']:g}-{w['end']:g}s",
                    measured=f"mean motion {stats['mean']:g} over {stats['frames']} frames",
                )
            )

    windows, notes = expected_cut_windows(doc)
    # Only HARD boundaries can spike. A soft join is EXPECTED to score low on
    # scdet — a 0.15s fade between maximum-contrast shots peaks around 3 against a
    # threshold of 10, a 1.0s fade under 1 — so counting them is no evidence
    # rather than weak evidence, and it buries the one hard cut that really went
    # missing. A dropped xfade still surfaces, as duration-drift.
    boundaries = [w["start"] for w in windows[1:] if not w.get("join")]
    soft = max(0, len(windows) - 1 - len(boundaries))
    undetected = [b for b in boundaries if not any(abs(b - c) <= cut_tolerance for c in cut_times)]
    if boundaries:
        if undetected:
            out.append(
                _finding(
                    "low",
                    "cut-undetected",
                    "cuts",
                    f"{len(undetected)} of {len(boundaries)} derived HARD cut boundaries have "
                    f"no scene-change peak within {cut_tolerance:g}s: "
                    f"{', '.join(f'{b:.2f}s' for b in undetected[:8])}"
                    f"{' …' if len(undetected) > 8 else ''}."
                    + (f" ({soft} soft transition(s) excluded — an xfade cannot spike.)" if soft else "")
                    + " WEAK EVIDENCE — two visually similar shots cut together also score "
                    "low on scene change, so this is a hint to look, not proof the cut is "
                    "missing."
                    + (
                        ""
                        if _runtime(doc) == "ffmpeg"
                        else " Weaker still on this runtime: the composition builds its own "
                        "scene transitions, so a boundary the doc calls hard may render as a "
                        "soft blend that cannot spike."
                    ),
                    declared=f"{len(boundaries)} hard boundaries",
                    measured=f"{len(cut_times)} detected peaks",
                )
            )
    if windows:
        declared_end = windows[-1]["end"]
        drift = duration - declared_end
        tolerance = max(0.5, declared_end * 0.05)
        if declared_end > 0 and abs(drift) > tolerance:
            out.append(
                _finding(
                    "medium",
                    "duration-drift",
                    "cuts",
                    f"Derived timeline ends at {declared_end:.2f}s but the file is "
                    f"{duration:.2f}s ({drift:+.2f}s). Something added or ate time — "
                    "check trims, speed, and transition overlap.",
                    declared=f"{declared_end:.2f}s",
                    measured=f"{duration:.2f}s",
                )
            )
        # A plan that contradicts itself is worth catching before blaming the render.
        stated = (doc.get("metadata") or {}).get("total_duration_seconds")
        if stated is None:
            stated = doc.get("total_duration_seconds")
        if isinstance(stated, (int, float)) and not isinstance(stated, bool):
            if declared_end > 0 and abs(float(stated) - declared_end) > tolerance:
                out.append(
                    _finding(
                        "medium",
                        "plan-inconsistent",
                        "metadata.total_duration_seconds",
                        f"The plan states {float(stated):.2f}s but its own cuts derive to "
                        f"{declared_end:.2f}s. The plan disagrees with itself, so at least "
                        "one of the two is not what got rendered.",
                        declared=f"{float(stated):.2f}s stated",
                        measured=f"{declared_end:.2f}s derived, {duration:.2f}s on disk",
                    )
                )
    return out, notes


# --- public entry point -----------------------------------------------------


_INDEXED_RE = re.compile(r"^(?P<prefix>.*?)\[(?P<index>\d+)\](?P<suffix>.*)$")


def _collapse_locations(locations: list[str]) -> str:
    """``overlays[4].keyframes`` x30 -> ``overlays[4-33].keyframes``.

    A plan authored in a loop produces the same defect on every item. Printing it
    30 times is how advisory output gets ignored, so identical findings collapse
    to one line that still names every index.
    """
    if len(locations) == 1:
        return locations[0]
    parsed = [_INDEXED_RE.match(loc) for loc in locations]
    if not all(parsed):
        return ", ".join(locations[:5]) + (" …" if len(locations) > 5 else "")
    prefixes = {m.group("prefix") for m in parsed}
    suffixes = {m.group("suffix") for m in parsed}
    if len(prefixes) != 1 or len(suffixes) != 1:
        return ", ".join(locations[:5]) + (" …" if len(locations) > 5 else "")
    idx = sorted(int(m.group("index")) for m in parsed)
    spans: list[str] = []
    run_start = prev = idx[0]
    for i in idx[1:] + [None]:
        if i is not None and i == prev + 1:
            prev = i
            continue
        spans.append(str(run_start) if run_start == prev else f"{run_start}-{prev}")
        if i is not None:
            run_start = prev = i
    return f"{prefixes.pop()}[{','.join(spans)}]{suffixes.pop()}"


def group_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge findings that differ only by which item they landed on."""
    order: list[tuple] = []
    bucket: dict[tuple, list[dict[str, Any]]] = {}
    for f in findings:
        key = (f["severity"], f["kind"], f["message"], f.get("fix"))
        if key not in bucket:
            bucket[key] = []
            order.append(key)
        bucket[key].append(f)

    merged: list[dict[str, Any]] = []
    for key in order:
        group = bucket[key]
        if len(group) == 1:
            merged.append(group[0])
            continue
        head = dict(group[0])
        locations = [f["where"] for f in group]
        head["where"] = _collapse_locations(locations)
        head["occurrences"] = len(group)
        head["locations"] = locations
        declared = {str(f.get("declared")) for f in group if "declared" in f}
        if len(declared) == 1:
            head["declared"] = group[0]["declared"]
        elif declared:
            head["declared"] = f"{len(declared)} distinct values"
        measured = {str(f.get("measured")) for f in group if "measured" in f}
        if len(measured) != 1:
            head.pop("measured", None)
        merged.append(head)
    return merged


def diff(
    doc: dict[str, Any],
    *,
    motion: Series,
    cut_times: list[float],
    frozen_runs: list[dict[str, Any]],
    duration: float,
    flat_threshold: Optional[float] = None,
) -> dict[str, Any]:
    """Full advisory plan-vs-render diff, findings worst-first."""
    static = static_findings(doc)
    measured, notes = measured_findings(
        doc,
        motion=motion,
        cut_times=cut_times,
        frozen_runs=frozen_runs,
        duration=duration,
        flat_threshold=flat_threshold,
    )
    ordered = sorted(static + measured, key=lambda f: _RANK.get(f["severity"], 9))
    findings = group_findings(ordered)
    return {
        "render_runtime": _runtime(doc),
        "counts": {
            sev: sum(f.get("occurrences", 1) for f in findings if f["severity"] == sev)
            for sev in ("high", "medium", "low", "info")
        },
        "findings": findings,
        "notes": notes,
        "lines": [format_finding(f) for f in findings],
    }


def format_finding(f: dict[str, Any]) -> str:
    """One-line rendering: verdict, place, declared vs measured, on one line.

    Advisory output only works if it is unskimmable, so the label, the location,
    the declaration and the measurement all land together — nothing for the
    reader to reconstruct.
    """
    count = f" x{f['occurrences']}" if f.get("occurrences") else ""
    parts = [f"{f['severity'].upper():<6} {f['kind']:<24} {f['where']}{count}"]
    if "declared" in f:
        parts.append(f"        declared: {f['declared']}")
    if "measured" in f:
        parts.append(f"        measured: {f['measured']}")
    parts.append(f"        {f['message']}")
    if f.get("fix"):
        parts.append(f"        fix: {f['fix']}")
    return "\n".join(parts)
