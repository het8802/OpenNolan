#!/usr/bin/env python3
"""OPN-10 containment checker driver — run via scripts/verify_containment.sh.

Proves the containment invariant empirically: with cache routing active, real
model-touching jobs (rembg background removal + faster-whisper transcription)
write NOTHING under $HOME. The shell wrapper points $HOME at an empty throwaway
directory, so the assertion is simply "that directory is still empty" — no
allowlist, and it generically catches writers nobody enumerated (piper, a new
library, a future tool).

Runs the tools IN-PROCESS (no uvicorn, no agent turn — there is no HTTP
media-op surface, and an agent turn would write $HOME/.claude* CLI state).
This is the same tool-subprocess env path the agent's jobs use.

Expected environment (set by verify_containment.sh):
    HOME                 -> empty throwaway dir (the tripwire)
    OPENNOLAN_HOME       -> persistent verify dir (venv/models live here across runs)
    OPENNOLAN_CODE_ROOT  -> repo/bundle root (forces is_packaged() -> routing ON)

Exit codes:
    0  PASS          at least one job ran and $HOME gained no files
    1  FAIL          $HOME gained files (leaked paths printed)
    2  INCONCLUSIVE  no job produced output (tool errors are never a pass —
                     and never a containment fail either)

Known blind spot (accepted in the design): native code resolving home via
getpwuid/NSHomeDirectory sees the real home, not $HOME. No such writer is known
in the current tool set.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("OPENNOLAN_CODE_ROOT", str(Path(__file__).resolve().parent.parent)))

from lib import app_paths  # noqa: E402

PASS, FAIL, INCONCLUSIVE = 0, 1, 2


def _ffmpeg_fixture(args: list[str], out: Path) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("INCONCLUSIVE: ffmpeg not on PATH — cannot generate fixtures")
        return False
    res = subprocess.run([ffmpeg, "-y", *args, str(out)], capture_output=True, text=True)
    if res.returncode != 0 or not out.is_file():
        print(f"INCONCLUSIVE: fixture generation failed for {out.name}: {res.stderr[-300:]}")
        return False
    return True


def _run_job(label: str, make_tool, inputs: dict) -> bool:
    """Run one tool job. True iff it executed successfully (produced output)."""
    try:
        tool = make_tool()
    except ImportError as exc:
        print(f"{label}: SKIPPED (unavailable — {exc})")
        return False
    from tools.base_tool import ToolStatus

    if tool.get_status() is not ToolStatus.AVAILABLE:
        print(f"{label}: SKIPPED (unavailable — deps not installed: {tool.dependencies})")
        return False
    try:
        result = tool.execute(inputs)
    except Exception as exc:  # a crashed tool is inconclusive, never a verdict
        print(f"{label}: ERROR ({exc}) — inconclusive")
        return False
    if not result.success:
        print(f"{label}: FAILED to run ({result.error}) — inconclusive")
        return False
    print(f"{label}: ran OK")
    return True


def main() -> int:
    fake_home = Path(os.environ.get("HOME", ""))
    if not fake_home.is_dir() or any(fake_home.iterdir()):
        print("INCONCLUSIVE: $HOME must be an EMPTY throwaway directory (use verify_containment.sh)")
        return INCONCLUSIVE

    base = app_paths.route_caches()
    if base is None:
        print("INCONCLUSIVE: cache routing is gated OFF (set OPENNOLAN_CODE_ROOT or OPENNOLAN_ROUTE_CACHES=1)")
        return INCONCLUSIVE
    print(f"routing ON — cache base: {base}")

    # Fixtures live under the PERSISTENT verify home (Transcriber writes its
    # transcript JSON next to the input, so the dir must be writable).
    jobs_dir = app_paths.home() / "verify-jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    png, wav = jobs_dir / "frame.png", jobs_dir / "speech.wav"

    ran = 0
    if _ffmpeg_fixture(["-f", "lavfi", "-i", "testsrc=size=320x240:rate=1", "-frames:v", "1"], png):
        def _bg():
            from tools.enhancement.bg_remove import BgRemove
            return BgRemove()
        ran += _run_job("bg_remove (rembg → U2NET_HOME)", _bg,
                        {"input_path": str(png), "output_path": str(jobs_dir / "frame_nobg.png")})

    if _ffmpeg_fixture(["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-ar", "16000"], wav):
        def _tr():
            from tools.analysis.transcriber import Transcriber
            return Transcriber()
        ran += _run_job("transcriber (faster-whisper → HF_HOME)", _tr,
                        {"input_path": str(wav), "model_size": "tiny"})

    leaked = sorted(p for p in fake_home.rglob("*") if p.is_file() or p.is_symlink())
    if leaked:
        print(f"\nCONTAINMENT FAIL — {len(leaked)} file(s) appeared under the throwaway HOME:")
        for p in leaked:
            print(f"  {p}")
        return FAIL
    if ran == 0:
        print("\nINCONCLUSIVE: no job produced output — nothing was proven either way")
        return INCONCLUSIVE
    print(f"\nCONTAINMENT OK — {ran} job(s) ran, throwaway HOME is still empty")
    return PASS


if __name__ == "__main__":
    sys.exit(main())
