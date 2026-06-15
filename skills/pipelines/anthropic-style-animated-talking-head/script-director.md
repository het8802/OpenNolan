# Script Director — anthropic-style-animated-talking-head

## When to Use
You have an approved `brief` and the talking-head footage. Produce the `transcript` (word-level)
and a `script` that segments the VO into beats — **without changing a single word of the VO.**

## Prerequisites
| Layer | Resource |
|-------|----------|
| Brief | `artifacts/brief.json` |
| Footage | the TH video (audio = the fixed VO) |
| Tool | `transcriber` (whisperx, word-level) |

## Do this
1. **Transcribe** the TH with `transcriber` (whisperx, model `small` or better, word-level
   timestamps). Save `transcript.json` (segments + words[] with start/end). This is the source
   of truth for all timing.
2. **Segment into beats.** Group the transcript into narrative beats (hook, each point, each
   claim, transitions, CTA). Each beat: `{id, text, start, end (from words[]), intent}`.
   `intent` ∈ hook | point | claim | explanation | transition | cta.
3. **Extract claims verbatim.** For every beat where the creator states a verifiable
   fact/number/quote ("50 million lines of Ruby", "beat Pokémon FireRed", "until June 22"),
   add a `claims[]` entry: `{spoken_phrase (verbatim), word_span: [start,end], beat_id}`.
   These feed the research stage. Be generous — anything checkable is a claim.
4. **Provisional shot_mode hint** per beat (the scene-director finalizes): personal/opinion/
   transition/CTA → `talking_head_full`; verifiable claim → `claim_proof`; dense data/diagram →
   `animation_full`; sustained explanation needing one supporting animation → `split_5050`;
   annotatable line (logo/term/list) → `talking_head_overlay`.

## Hard rule
The VO is **fixed**. Do not rewrite, trim, reorder, or re-time the creator's words. You are
segmenting and annotating an existing recording, not writing a script.

## Output: `script` (+ `transcript`)
- sections[]: {id, text, start, end, intent, shot_mode_hint}
- claims[]: {spoken_phrase, word_span, beat_id}
- total_duration_s (== footage duration)

## Self-evaluate
- Word-level transcript saved; beats have start/end from words[].
- claims[] captures every verifiable statement verbatim with its word span.
- No VO words altered. Every section has a shot_mode hint.
