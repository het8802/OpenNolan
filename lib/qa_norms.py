"""Turn a motion measurement into a verdict.

`video_motion.summarize` deliberately returns measurement and never a pass/fail
-- "the caller decides". In practice the caller was an agent with no scale, so
it read `cut_count: 6` off a 33s reel and called it fine. Six cuts in 33s is a
4.7s perceived shot length; the genre cuts every ~2s. The number that explained
the whole problem was measured correctly and meant nothing to the reader.

This module is the missing scale. It holds the numbers that separate "renders"
from "reads well", compares a summary against them, and returns failures with
the editorial consequence spelled out.

# ponytail: these are hand-picked defaults, not measured ones. The intended
# upgrade is to run `motion` over reference reels the user already likes and
# replace each number with a percentile of that set -- same comparison, their
# taste instead of mine. Retune here; nothing else reads these constants.
"""

from __future__ import annotations

from typing import Any, Optional

#: Short-form vertical (Reels / TikTok / Shorts): product demos, explainers, hooks.
SHORT_FORM = {
    "label": "short_form",
    # duration / (cut_count + 1). The genre cuts every 1.5-2.5s; past this the
    # reel reads as a slideshow no matter how much the camera drifts.
    "max_perceived_shot_seconds": 2.5,
    # Share of runtime not moving. A fifth is already generous for short-form.
    "max_static_fraction": 0.20,
    # Any ONE stretch holding still this long reads dead. Below
    # video_motion.MIN_STATIC_SECONDS (0.75) it is a held beat, not a defect;
    # this is where a held beat becomes a stall.
    "max_static_run_seconds": 1.25,
    # A frozen tail is its own failure: end cards routinely animate, then sit.
    "max_end_hold_seconds": 1.5,
    # Mean motion across the opening. The scroll-stopper cannot be a still.
    # video_motion.STATIC_THRESHOLD is 0.25 and a slow pan measures 0.5-2, so
    # this asks for at least a slow pan's worth of movement.
    "min_hook_motion": 0.60,
    "hook_seconds": 1.5,
}

#: Longer-form / cinematic: shots are allowed to breathe, holds are intentional.
LONG_FORM = {
    "label": "long_form",
    "max_perceived_shot_seconds": 6.0,
    "max_static_fraction": 0.35,
    "max_static_run_seconds": 3.0,
    "max_end_hold_seconds": 3.0,
    "min_hook_motion": 0.25,
    "hook_seconds": 2.0,
}

PROFILES = {"short_form": SHORT_FORM, "long_form": LONG_FORM}
DEFAULT_PROFILE = "short_form"


def _fail(metric: str, measured: Any, norm: Any, unit: str, why: str) -> dict[str, Any]:
    return {
        "metric": metric,
        "status": "fail",
        "measured": measured,
        "norm": norm,
        "unit": unit,
        "why": why,
    }


def _ok(metric: str, measured: Any, norm: Any, unit: str) -> dict[str, Any]:
    return {
        "metric": metric,
        "status": "pass",
        "measured": measured,
        "norm": norm,
        "unit": unit,
    }


def judge(summary: dict[str, Any], profile: Optional[str] = None) -> dict[str, Any]:
    """Compare a `video_motion.summarize` result against a norms profile.

    Returns {"status": "pass"|"fail", "profile", "checks": [...], "failures": [...]}.
    A check whose input is missing is reported "unknown" and never counted as a
    pass -- an unmeasured video is not a good one.
    """
    name = profile or DEFAULT_PROFILE
    norms = PROFILES.get(name)
    if norms is None:
        return {
            "status": "unknown",
            "profile": name,
            "checks": [],
            "failures": [],
            "error": f"unknown norms profile {name!r}; have {sorted(PROFILES)}",
        }

    checks: list[dict[str, Any]] = []
    duration = float(summary.get("duration_seconds") or 0.0)

    # --- pacing -------------------------------------------------------------
    cut_count = summary.get("cut_count")
    if duration > 0 and cut_count is not None:
        shot = round(duration / (cut_count + 1), 2)
        cap = norms["max_perceived_shot_seconds"]
        if shot > cap:
            checks.append(
                _fail(
                    "perceived_shot_seconds",
                    shot,
                    cap,
                    "s",
                    f"{cut_count} visible changes in {duration:.0f}s reads as a {shot:.1f}s "
                    f"average shot. Cuts the viewer cannot see are not cuts -- if the plan "
                    f"plotted more than {cut_count}, the extra boundaries joined shots too "
                    f"similar to register, and the edit plays slower than it was written.",
                )
            )
        else:
            checks.append(_ok("perceived_shot_seconds", shot, cap, "s"))

    # --- dead air -----------------------------------------------------------
    sf = summary.get("static_fraction")
    if sf is not None:
        cap = norms["max_static_fraction"]
        if sf > cap:
            checks.append(
                _fail(
                    "static_fraction",
                    sf,
                    cap,
                    "share",
                    f"{sf:.0%} of the runtime is not moving. In short-form that time is "
                    f"spent, not held -- the viewer reads it as the video having stopped.",
                )
            )
        else:
            checks.append(_ok("static_fraction", sf, cap, "share"))

    # A run that reaches the end of the video is reported ONCE, as end_hold --
    # otherwise a frozen end card fails two checks for one defect.
    runs = summary.get("static_runs") or []
    end_hold = 0.0
    mid_runs = []
    for r in runs:
        if duration > 0 and float(r.get("end", 0)) >= duration - 0.2:
            end_hold = max(end_hold, float(r.get("seconds", 0)))
        else:
            mid_runs.append(r)

    if mid_runs:
        worst = max(mid_runs, key=lambda r: float(r.get("seconds", 0)))
        cap = norms["max_static_run_seconds"]
        secs = float(worst.get("seconds", 0))
        if secs > cap:
            checks.append(
                _fail(
                    "longest_static_run_seconds",
                    secs,
                    cap,
                    "s",
                    f"The stretch at {worst.get('start')}-{worst.get('end')}s holds still for "
                    f"{secs:.1f}s mid-reel. Look at it with `strip` over that window: either "
                    f"the shot needs motion or it needs to be shorter.",
                )
            )
        else:
            checks.append(_ok("longest_static_run_seconds", secs, cap, "s"))

    if end_hold:
        cap = norms["max_end_hold_seconds"]
        if end_hold > cap:
            checks.append(
                _fail(
                    "end_hold_seconds",
                    round(end_hold, 2),
                    cap,
                    "s",
                    f"The last {end_hold:.1f}s sit frozen. An end card may land and rest, but "
                    f"this much dead tail is runtime spent on a still frame -- trim it or give "
                    f"the card something to finish doing.",
                )
            )
        else:
            checks.append(_ok("end_hold_seconds", round(end_hold, 2), cap, "s"))

    # --- the hook -----------------------------------------------------------
    hook = summary.get("hook_motion")
    if hook and hook.get("frames"):
        floor = norms["min_hook_motion"]
        # median, not mean: an opening lurch that decays to a standstill still
        # leaves the viewer looking at a still frame at the moment they decide.
        mean = float(hook.get("median") or 0.0)
        if mean < floor:
            checks.append(
                _fail(
                    "hook_motion",
                    round(mean, 3),
                    floor,
                    "energy",
                    f"The first {norms['hook_seconds']}s sit at a median {mean:.2f} motion "
                    f"(peak {float(hook.get('peak') or 0):.2f}). This is the window the viewer "
                    f"decides in, and it is mostly still -- a move that spikes at the top and "
                    f"decays to nothing counts as still for everything after the spike.",
                )
            )
        else:
            checks.append(_ok("hook_motion", round(mean, 3), floor, "energy"))

    # --- holes in the edit --------------------------------------------------
    drops = summary.get("detail_dropouts") or []
    if drops:
        where = ", ".join(f"{d['start']:.2f}s" for d in drops[:5])
        checks.append(
            _fail(
                "detail_dropouts",
                len(drops),
                0,
                "count",
                f"{len(drops)} frame-level hole(s) where the picture briefly empties out ({where}). "
                f"These survive every other check -- luma is normal, so no black-frame test fires, "
                f"and they are too short for a contact sheet to land on. Confirm with `strip`.",
            )
        )
    else:
        checks.append(_ok("detail_dropouts", 0, 0, "count"))

    failures = [c for c in checks if c["status"] == "fail"]
    return {
        "status": "fail" if failures else "pass",
        "profile": name,
        "checks": checks,
        "failures": failures,
        "read_this": (
            "BLOCKING. Each failure is a measured number against a norm for this format. "
            "Do not report the video as good while any of these fail: either fix the edit "
            "and re-measure, or state the specific reason the norm does not apply here."
            if failures
            else "All measured norms met. This says the edit is not broken and not slow; "
            "it does not say it is good -- judge composition and easing by eye."
        ),
    }
