#!/usr/bin/env bash
# OPN-10 release-time containment check. Run before each release:
#
#     bash scripts/verify_containment.sh [python-interpreter]
#
# Proves that real model-touching jobs write NOTHING outside the app's own
# folders, by running them with $HOME pointed at an empty throwaway directory
# and asserting it stays empty. See scripts/verify_containment_driver.py for
# the mechanism, job list, and exit codes (0 pass / 1 FAIL / 2 inconclusive).
#
# OPENNOLAN_HOME is PERSISTENT across runs (default ~/.opennolan-verify): the
# routed caches keep the tiny whisper model etc. so re-runs don't re-download.
# A throwaway one would force a multi-GB provision per run.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="${1:-${OPENNOLAN_VERIFY_PYTHON:-python3}}"
VERIFY_HOME="${OPENNOLAN_VERIFY_HOME:-$HOME/.opennolan-verify}"
FAKE_HOME="$(mktemp -d)"
trap 'rm -rf "$FAKE_HOME"' EXIT

mkdir -p "$VERIFY_HOME"
echo "driver:      $PY"
echo "repo:        $REPO"
echo "verify home: $VERIFY_HOME (persistent)"
echo "tripwire:    $FAKE_HOME (throwaway \$HOME)"
echo

env HOME="$FAKE_HOME" \
    OPENNOLAN_HOME="$VERIFY_HOME" \
    OPENNOLAN_CODE_ROOT="$REPO" \
    OPENNOLAN_PACKAGED=1 \
    "$PY" "$REPO/scripts/verify_containment_driver.py"
