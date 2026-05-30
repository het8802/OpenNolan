# Instagram Reel link-list extraction

Use when Het shares an Instagram Reel that names a stack/list of tools, skills, repos, or resources and asks for the links.

## Workflow proven on Reel `DXt6opNiAN1` — “50 Best Claude Code Skills”

1. Capture public metadata first:
   ```bash
   mkdir -p /tmp/iglinks && cd /tmp/iglinks
   yt-dlp --write-info-json --write-thumbnail --skip-download --no-warnings 'https://www.instagram.com/reel/<id>/'
   ```
2. If the Reel is a fast slideshow, download the media and make analysis frames/contact sheets:
   ```bash
   yt-dlp --no-warnings -f 'bv*+ba/b' -o 'reel.%(ext)s' 'https://www.instagram.com/reel/<id>/'
   ffmpeg -y -i reel.mp4 -vf "fps=1,scale=540:-1,tile=6x10" -frames:v 1 contact.jpg
   ffmpeg -y -i reel.mp4 -vf fps=2 frames/frame_%03d.jpg
   ```
3. Use the contact sheet to identify the complete list and use individual full-size frames for ambiguous items. In the Claude Code skills Reel, the list slide gave all 50 names; individual repo frames confirmed owners/repositories.
4. Search the web for an authoritative companion page when the Reel caption promises “comment for links” or the topic title is distinctive. For `DXt6opNiAN1`, searching `"50 Best Claude Code Skills" links` found an AIFLOXIUM blog with the same list and install commands.
5. If a companion page contains `/plugin marketplace add owner/repo` commands, parse those commands to map item names to GitHub links. Verify unique repos with `git ls-remote https://github.com/<owner>/<repo>.git HEAD` before returning links.
6. Return a compact table: number, visible Reel name, and canonical link. Note when multiple visible skills live inside the same repository.
7. Clean up temporary Reel media unless Het asks to keep it.

## Pitfalls

- Do not rely on the first search result alone; cross-check with visible Reel frames so a SEO recap page does not silently alter the list.
- GitHub API unauthenticated search can rate-limit quickly. Prefer page parsing plus `git ls-remote` verification for many repos.
- Some visible labels are umbrella names while the repo contains multiple sub-skills, e.g. `voice-builder`, `reels-scripting`, `post-scorer`, `youtube-thumbnail`, and `hook-generator` all map to `charlie947/social-media-skills`.
- Frame OCR can miss duplicate/blank transitions; sample at 1–2 fps and inspect full-size frames for blurred entries.
