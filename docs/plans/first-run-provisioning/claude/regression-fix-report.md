# is_packaged() regression fix — report

Status: **BUILT** · branch `fix/provisioning-reliability` · commits `a02ffdb` (style), `d68b4b1` (fix)

## What was wrong

`85e55a7` changed `lib/provision.py:489` to `offline=app_paths.is_packaged()`. `is_packaged()`
read `OPENNOLAN_CODE_ROOT`, which is **not** a packaging signal:

```
desktop/main.js provisionEnv()          ->  scripts/provision.py
  OPENNOLAN_CODE_ROOT: codeRoot()   <-- ALWAYS set, dev AND packaged
  (provision.py calls app_paths.code_root() / req to find requirements.txt,
   so it cannot be made conditional)

desktop/main.js startBackend()          ->  uvicorn
  if (app.isPackaged) { OPENNOLAN_CODE_ROOT = ... }   <-- correctly gated, different block
```

So every **dev** provision looked packaged, asked for an offline install, found no vendored
wheels, and hard-failed before pip ran. The docstring's "a dev checkout leaves it unset" had
always been false for that child; `85e55a7` was just the first code to call `is_packaged()`
from it.

## The fix

A dedicated var, set only in the `.app`, in both children:

| where | var | when |
| --- | --- | --- |
| `main.js` `provisionEnv()` | `OPENNOLAN_CODE_ROOT` | always (unchanged) |
| `main.js` `provisionEnv()` | `OPENNOLAN_PACKAGED=1` | `if (app.isPackaged)` |
| `main.js` `startBackend()` | `OPENNOLAN_PACKAGED=1` | inside the existing `if (app.isPackaged)` |
| `lib/app_paths.py` | `is_packaged()` | `env_flag("OPENNOLAN_PACKAGED") is True` |

`env_flag` (already in the module) instead of `bool(...)` so `OPENNOLAN_PACKAGED=0` reads as
off, matching every other gate here.

## Callers audited

| caller | process | verdict |
| --- | --- | --- |
| `lib/provision.py:489` offline core install | provision.py | **fixed** — dev now online |
| `lib/provision.py:599` ffmpeg PATH shortcut | provision.py | **fixed** — dev now reuses a PATH ffmpeg instead of downloading |
| `lib/app_paths.py:149` `route_caches()` | provision.py + backend | backend unchanged; **dev provisioning no longer routes caches** (the documented intent) |
| `server/app.py:531` pin the single pipeline | backend | unchanged |
| `server/agent_runner.py:1220` packaged pipeline | backend | unchanged |
| `styles/playbook_loader.py:99`, `lib/pipeline_loader.py:74`, `lib/playbook_generator.py:58` | backend | unchanged |
| `server/analytics.py:75` env label | backend | unchanged (`dev` in dev, `packaged` packaged) |
| `scripts/verify_containment.sh` | driver | **updated** — it forced routing on via `CODE_ROOT`; now passes `OPENNOLAN_PACKAGED=1` |

`server/agent_runner.py:666` reads `OPENNOLAN_CODE_ROOT` directly (venv-missing warning) and is
not an `is_packaged()` call site — left alone.

## Also: total download deadline (Codex F1, PARTIAL)

`_DOWNLOAD_TIMEOUT=30` bounds connect + each socket read, not the transfer. A host trickling a
byte inside every 30s window never times out and never finishes. Added `_DOWNLOAD_DEADLINE=300`
— one `time.monotonic()` check inside the existing read loop, raising `TimeoutError`, which the
existing `except` already wraps into the legible "downloading ffmpeg from … failed: …".

## Proofs

All three run against the real code; full command output is in the dispatch report.

1. **Dev regression + resolution** — `OPENNOLAN_CODE_ROOT=<repo>`, no `OPENNOLAN_WHEELS`, no
   `OPENNOLAN_PACKAGED`. With the old body re-created verbatim: `is_packaged() -> True`,
   `provision_core()` raises "the bundled Python wheels are missing". With the fix:
   `is_packaged() -> False` and the install command carries no offline flags → ONLINE.
2. **Packaged + missing wheels (Finding 2 holds)** — `OPENNOLAN_PACKAGED=1`,
   `OPENNOLAN_WHEELS=<missing>`: `is_packaged() -> True`, `RuntimeError`, `subprocesses
   spawned: []`.
3. **Packaged + real wheels** — a genuine `provision_core()` against
   `desktop/resources/wheels` with the bundled python + uv: venv built, 40+ packages installed
   with `--offline --no-cache --find-links`, `import fastapi, uvicorn, pydantic` passed,
   `core_ok() -> True`.

## Tests

`scripts/dev test fast`: passed. `pytest tests/contracts -q`: 837 passed, 14 skipped, 1 xfailed.

New/changed tests: `test_code_root_alone_is_not_packaged` (the regression, pinned),
`test_is_packaged_reads_the_packaged_flag` (incl. the `"0"` case),
`test_dev_core_install_asks_for_online_explicitly` now sets `CODE_ROOT` the way `provisionEnv()`
really does, `test_download_has_a_total_deadline_not_just_a_per_read_timeout`.

## Not done, deliberately

- Did not touch `OPENNOLAN_CODE_ROOT`'s own semantics anywhere.
- Did not amend `fb869b0`/`5559839`/`85e55a7`/`621aacf`.
- The format-only churn `scripts/dev test fast` demands of any changed `.py` is isolated in
  `a02ffdb` so the fix diff reads as a fix.
