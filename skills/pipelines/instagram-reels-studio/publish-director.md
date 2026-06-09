# Publish Director — instagram-reels-studio

Package the finished reel for posting and produce the `publish_log`.

## Steps

1. **Export the watermark-free file** to the project's export directory.
2. **Write posting metadata**: caption, hashtags, suggested audio credit, and the target
   platform(s). Keep the caption hook-forward.
3. **Advisory virality score (never a gate).** Run `content_signal` on the finished reel for an
   advisory 0–100 score + weak-moment timeline. It is ADVISORY ONLY and short-form-only — it
   must NOT block publishing. Surface the score and any weak-moment hint; the human decides.
4. **Direct IG/FB publish is DEFERRED (Wave 7).** OpenNolan does not yet post to the Meta Graph
   API (needs a token + app review; `tools/publishers/` is empty). Export the file + metadata for
   the user to post manually, and say so explicitly — do not imply it auto-published.

## Quality bar
Schema-valid publish_log; export directory contains the reel + caption/hashtag metadata;
content_signal score recorded as advisory; manual-post note included. Checkpoint for human
approval.
