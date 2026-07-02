#!/usr/bin/env python3
"""First-run provisioning CLI — run by the BUNDLED interpreter, BEFORE the venv exists (Lane E).

Usage:
    python scripts/provision.py --doctor        # print provisioning status as JSON
    python scripts/provision.py --core          # build the managed venv + core deps + ffmpeg
    python scripts/provision.py --pack <name>    # install a capability pack into the venv

Streams NDJSON to stdout so desktop/main.js can drive a setup window:
    {"type":"log","line":"..."}   progress line
    {"type":"doctor","doctor":{}} (--doctor only)
    {"type":"done"}               success
    {"type":"error","error":"…"}  failure (exit 1)
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Put the read-only code root on sys.path so `lib.*` imports. Packaged: OPENNOLAN_CODE_ROOT =
# Contents/Resources/backend (set by main.js). Dev: the repo root. lib.provision is stdlib-only,
# so this works under the bare bundled interpreter with no venv/site-packages.
sys.path.insert(0, os.environ.get("OPENNOLAN_CODE_ROOT", str(Path(__file__).resolve().parent.parent)))

from lib import provision  # noqa: E402


def emit(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


def progress(line: str) -> None:
    emit({"type": "log", "line": line})


def main() -> int:
    ap = argparse.ArgumentParser(description="OpenNolan first-run provisioning")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--doctor", action="store_true", help="print status JSON and exit")
    group.add_argument("--core", action="store_true", help="provision the core venv + ffmpeg")
    group.add_argument("--pack", metavar="NAME", help="install a capability pack")
    args = ap.parse_args()

    try:
        if args.doctor:
            emit({"type": "doctor", "doctor": provision.doctor()})
            return 0
        if args.core:
            provision.provision_core(progress)
        else:
            provision.provision_pack(args.pack, progress)
        emit({"type": "done"})
        return 0
    except Exception as exc:  # surface a clean error line for the setup UI
        emit({"type": "error", "error": str(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
