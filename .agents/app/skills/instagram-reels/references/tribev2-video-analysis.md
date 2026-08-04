# TRIBE v2 / social-signal analysis for Reel replication

Use this when the user asks for TRIBE v2, social-signal scoring, or a model-backed critique of a Reel/TikTok/Short.

## Principle
Do **not** present heuristic or LLM-only critique as real TRIBE v2 inference. The user explicitly cares about separating true hosted/API inference from style analysis. If the model did not actually run, say so and still provide a clearly-labeled qualitative creator-pattern analysis if useful.

## Replicate hosted-model pattern
Known hosted model used in a prior session:

- Model slug: `prakhar-bhartiya/meta-tribev2-social-media-content-signal`
- Expected input: a public video URL or a locally downloaded video uploaded/provided as API input, depending on the client path.
- Model/schema limit observed in session: video input must be `≤60s`. If the candidate is longer, create an explicit trimmed copy (e.g. `ffmpeg -i input.mp4 -t 59.8 ... trim60.mp4`) and report both the original and trimmed paths/durations.
- Paid inference input discipline: before running Replicate, state/verify the exact video being scored. If the user supplied a reference Reel for style analysis but asks about “today’s script,” “our created video,” or a generated draft, do **not** score the reference URL. Locate the generated artifact first, confirm its path/topic, and only then run inference. This avoids conflating reference-style analysis with evaluation of the produced video.
- If the Python `replicate.run(...)` call times out locally, check Replicate `/v1/predictions` for the latest prediction ID and poll its `get` URL; the hosted job may still complete successfully.
- If the API returns HTTP `402 Insufficient credit`, stop claiming model scores. Tell the user billing/credits are needed, preserve the local video path if useful, and offer to rerun once funded.
- If credits are fixed but the token is no longer available, state that the video artifact is still prepared and ask only for the missing token; do not restart analysis from scratch.

Suggested result language:

> The video was downloaded and prepared, but TRIBE-v2 inference did not run: Replicate returned `402 Insufficient credit`. I can still analyze the edit qualitatively, but any scores below would be heuristic, not real TRIBE-v2 outputs.

## Hugging Face / Space caveat
Some public Spaces or demos use names like TRIBE/TribeV2 while actually running a small LLM plus heuristic scoring. Before trusting a Space as real inference:

1. Inspect its visible code/model card if accessible.
2. Look for real `facebook/tribev2` loading or a documented TRIBE-v2 checkpoint.
3. If it uses unrelated models such as `microsoft/phi-2`, generic LLMs, or hard-coded scoring rules, label it as heuristic only.
4. If a Space claims `facebook/tribev2` but times out or fails, report the failure rather than inventing scores.

## Output shape when real inference succeeds
Return:

- Overall score and main sub-scores exactly as reported by the model/API.
- Timeline peaks/drops if provided.
- A short interpretation tied to replication: hook timing, VO/text alignment, visual novelty, emotional reward, caption density, background/motion flow.
- Then separate a clearly-labeled **replication recipe**: text animation, background grammar, transition pacing, SFX/VO alignment, and implementation notes for Remotion/OpenNolan/HyperFrames.

## Input verification pitfall
Before reporting scores or qualitative findings, explicitly verify and name the exact media that was scored/analyzed:

1. State whether the input is the **reference Reel supplied by URL** or the **user-created/exported video** being evaluated.
2. Include the local path, duration, and a lightweight evidence handle such as transcript snippet, title/metadata, SHA-256, contact sheet, or `MEDIA:<path>` attachment.
3. If the session includes both a reference video and a generated video, never assume the downloaded Instagram URL is the target for scoring. Ask/confirm or use the latest created/exported MP4 when the user says “the video we created.”
4. In the final answer, separate “reference style analysis” from “our generated video evaluation” so scores are not attributed to the wrong asset.

## Output shape when real inference fails
Return:

1. What was successfully prepared: video URL/local path, duration, transcript/contact sheet if available.
2. The exact blocking error, e.g. `402 Insufficient credit`.
3. A binary distinction:
   - `Real TRIBE-v2 scores: unavailable`
   - `Qualitative creator-pattern analysis: available`
4. Ask for the one missing unblocker only if needed: credits/billing, alternate endpoint, or permission to use heuristic analysis.
