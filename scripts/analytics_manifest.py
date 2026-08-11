#!/usr/bin/env python3
"""Generate the BUILD / FOLD / DROP manifest from the catalog and the live taxonomy.

Every total in the plan derives from a SET DIFFERENCE computed here — never a hand-carried
number. rev 1 of the plan said "50 P0 events"; it was 49, because a row carrying two event
names had been parsed as one phantom name. A count nobody can reproduce is a count nobody
should act on.

    python3 scripts/analytics_manifest.py            # human summary
    python3 scripts/analytics_manifest.py --check    # exit 1 if BUILD is non-empty
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "docs" / "plans" / "analytics-maximal-datapoints" / "agreed" / "plan.md"
# Escaped pipes inside enum cells (`E{cold\|activate}`) are NOT column separators.
_SPLIT = re.compile(r"(?<!\\)\|")

# Names that are deliberately NOT emitted, each with the reason. A DROP with no reason is an
# omission; a DROP with one is a decision.
DROPPED = {
    "preview_mode_switched": "FOLDED into editor_session_summary as preview_switches_source/render counters. Six "
    "switches is six uploads and the catalog's own §3 allows only 6 families to upload "
    "per-interaction — this is none of them. The question ('do users keep flipping to "
    "render?') is a counter question, and the counters answer it identically.",
    "survey_shown": "No surface exists. The catalog itself says 'new surface; no anchor invented' — both "
    "phase-1 docs anchored survey rows at unrelated lines. Building a survey UI is a "
    "product feature, not instrumentation of one.",
    "survey_answered": "Same surface as survey_shown.",
}

# Declared names that are not catalog rows, kept deliberately.
KEPT_OUTSIDE_CATALOG = {
    "app_opened": "Predates the catalog. The backend-boot signal; app_launch_started is the shell's.",
    "desktop_error": "The crash inbox entry from Electron main, which posts direct because the "
    "backend may never have started.",
    "phone_receive_finished": "Postdates the catalog — the phone-receive path (server/lan_receive.py) "
    "did not exist when it was written. ONE per-session rollup, not a per-interaction family: "
    "the per-file signal is already asset_import_finished{source:'phone'}, and this answers the "
    "question that one structurally cannot — whether people open the window and get nothing.",
    "content_schedule_created": "Postdates the catalog — the content calendar (server/content_calendar.py) "
    "did not exist when it was written. The feature's ONE success signal, shared by the REST route and "
    "the agent's schedule_content tool; `replaced` is what decides whether reschedule/delete has to "
    "become real UI instead of a re-save.",
    "content_schedule_failed": "The failure half of the pair above, and the half nobody files a bug "
    "about: no final render, a rejected channel, a past time, a bad write. Without it 'nobody schedules' "
    "and 'scheduling is broken' look identical.",
    "content_calendar_viewed": "Postdates the catalog. Deliberately ONE name for both interactions on "
    "the surface, discriminated by action{calendar|video} — the same shape as thread_lifecycle — because "
    "the <=100 cap has room for one calendar view event, not two, and month-opened -> entry-played is a "
    "funnel on one screen. Answers whether the calendar is found at all (the abandonment question) and "
    "whether an entry leads back to the render.",
}

RENAMED = {
    "auth_connected": (
        "auth_connect_finished",
        "Success-only, so setup conversion had a numerator and no denominator. The failure branches did not exist.",
    ),
}


def catalog_rows() -> list[dict]:
    rows = []
    for line in CATALOG.read_text().splitlines():
        if not line.startswith("|"):
            continue
        cells = _SPLIT.split(line)
        num = cells[1].strip()
        if not re.fullmatch(r"\d+[a-z]?", num) or cells[3].strip() not in ("E", "D"):
            continue
        names = re.findall(r"`([a-z0-9_]+)`", cells[2])
        if not names:
            continue
        rows.append({"num": num, "names": names, "kind": cells[3].strip(), "pri": cells[-4].strip()})
    return rows


def declared() -> set[str]:
    sys.path.insert(0, str(ROOT))
    from server import analytics

    return set(analytics._merge_taxonomy(sorted((ROOT / "schemas" / "analytics").glob("*.json")))["events"])


def main(argv: list[str]) -> int:
    rows = catalog_rows()
    have = declared()
    emitted = [r for r in rows if r["kind"] == "E"]
    derived = [r for r in rows if r["kind"] == "D"]
    names = {n: r["pri"] for r in emitted for n in r["names"]}

    build = sorted(set(names) - have - set(DROPPED))
    fold = sorted(set(DROPPED) & set(names))
    extra = sorted(have - set(names))

    print(f"CATALOG   {len(emitted)} EMITTED rows / {len(names)} names / {len(derived)} DERIVED rows")
    for pri in ("P0", "P1", "P2"):
        pn = {n for n, p in names.items() if p == pri}
        print(
            f"  {pri}  names={len(pn):3d}  declared={len(pn & have):3d}  outstanding={len(pn - have - set(DROPPED)):3d}"
        )
    print(f"\nDECLARED  {len(have)} names   (catalog cap is <=100 EMITTED names)")
    print(f"BUILD     {len(build)} outstanding  {build}")
    print(f"DROP      {len(fold)}")
    for name in fold:
        print(f"  · {name}: {DROPPED[name]}")
    print(f"RENAMED   {len(RENAMED)}")
    for old, (new, why) in RENAMED.items():
        print(f"  · {old} -> {new}: {why}")
    print(f"OUTSIDE THE CATALOG  {len(extra)}")
    for name in extra:
        print(f"  · {name}: {KEPT_OUTSIDE_CATALOG.get(name, 'UNJUSTIFIED')}")
    print(
        f"\nDERIVED rows are NOT events — they are query-time metrics and belong in\n"
        f"docs/analytics-dashboard.md as SQL. Emitting one would be the 'derived metrics\n"
        f"masquerading as events' defect the ratified plan already killed."
    )

    if "--check" in argv:
        unjustified = [n for n in extra if n not in KEPT_OUTSIDE_CATALOG]
        if build or unjustified or len(have) > 100:
            print(f"\nFAIL: build={build} unjustified={unjustified} declared={len(have)}", file=sys.stderr)
            return 1
        print("\nOK: every catalog name is declared, dropped with a reason, or renamed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
