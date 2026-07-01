# Script Director — instagram-fast-reel

This stage does double duty (there's no separate `scene_plan` stage): transcribe the talking
head AND produce the **cut plan + annotation plan**. This is where you decide what to cut, and
where the animations / meme GIFs / caption hits go. The `edit` stage just executes this plan.

## 1. Transcribe (required)
Run `transcriber` (whisperx) on the source talking head → word-level `transcript` with per-word
start/end timestamps. Everything downstream (cut spans, reveal timing, caption chunks) is keyed
to these word timestamps. Record avg confidence; flag low-confidence spans.

## 2. Cut plan (the fast pacing)
Reading the transcript, mark spans as **keep** or **drop**:
- dead air / long pauses (candidates for `silence_cutter` at the pacing-energy threshold from
  the brief),
- filler ("um", "uh", "like", "you know"), false starts, restarts, and rambling tangents,
- reorder if the strongest line should open — the **hook must land in the first ~2s**.
Emit the kept spans as an ordered list of `{start, end}` in transcript time. You can run
`silence_cutter` here in a dry/plan mode to get suggested cut points, but the actual cutting
happens in `edit`.

## 3. Annotation plan (animations · GIFs · captions)
Walk the KEPT timeline beat by beat. For each beat decide whether it gets:
- **a keyframe animation** — an emphasis word or key phrase that should pop (a text card that
  scales/slides in). Note the exact trigger word + its timestamp.
- **a meme GIF** — a reaction/punchline moment where a GIPHY/Tenor GIF lands. Note the search
  query + the trigger word.
- **a caption emphasis** — beyond the always-on captions, a word to enlarge/color/animate.
- **an energy op** — a punch-in, speed-up (for a slow ramble), or freeze (on a punchline).

**NARRATION SYNC (hard rule):** every reveal/GIF/caption hit is tied to a trigger word and lands
**ON** it, never before. Store the trigger word + `words[]` timestamp with each annotation.

Don't over-decorate — a fast reel is carried by the cuts and captions. Aim for a few
well-placed animation/GIF hits, not one per sentence.

## Output
- `transcript` (word-level).
- `script` (schema-valid): the ordered kept-spans cut plan + the per-beat annotation plan (each
  annotation carries type, trigger word, timestamp, and a source hint the asset stage will build).

## Quality bar
Word-level transcript; hook opens; cut plan removes the dead air/filler; annotation plan pins
every animation/GIF/caption hit to a trigger-word timestamp. Human approval before `assets`.
