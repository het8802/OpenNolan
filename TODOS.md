# TODOS

## Desktop App

### Legacy cache orphan cleanup

**What:** One-time, user-consented sweep of pre-routing cache orphans (`~/.cache/opennolan`, `~/.opennolan`, `~/.cache/huggingface` app-attributable entries, `~/.u2net`) after OPN-10 cache routing ships.

**Why:** Anyone who ran a pre-routing build (including the dev machine: ~4.4 GB measured across huggingface/whisper/u2net) keeps hidden orphans forever — routing only redirects future writes. Reclaiming gigabytes for early users is real goodwill; leaving them contradicts the "delete the app, everything's gone" story.

**Context:** Designed during the OPN-10 eng review (2026-07-27; design doc lives in the local gstack store, not in this repo). Deliberately kept OUT of OPN-10: deletion UX deserves its own care (consent prompt, size preview, exclusions for caches shared with other apps — `~/.cache/huggingface` may hold models other tools use, so app-attributable filtering is the hard part). Start at: Settings panel (`web/src/`), plus a small backend endpoint that sizes/enumerates candidate paths before offering deletion. Only ever delete with explicit per-run user confirmation.

**Effort:** M
**Priority:** P3
**Depends on:** OPN-10 cache routing shipped
