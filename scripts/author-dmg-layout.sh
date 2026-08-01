#!/usr/bin/env bash
#
# author-dmg-layout.sh — regenerate the OpenNolan install-DMG look.
#
# Run this ONLY when you want to change the DMG window design (background art,
# headline, icon positions, window size). It writes two committed assets that
# scripts/build-dmg.sh then bakes into every .dmg:
#
#   desktop/build/dmg-background.tiff   the window background artwork (HiDPI)
#   desktop/build/dmg.DS_Store          the Finder window layout (bg + positions)
#
# Why this is a SEPARATE, manual step (not part of build-dmg.sh):
#   • The background is set by Finder itself (AppleScript). A *programmatically*
#     written background alias does NOT render on recent macOS (26 / Darwin 25) —
#     the window comes up white. A Finder-authored reference renders, and it keeps
#     rendering when copied into the final image (verified). But it needs a GUI
#     Finder session + Automation permission, which a build box / CI may not have —
#     so we author once here and commit the result, keeping the build headless.
#   • The volume name below MUST match DMG_VOLNAME in build-dmg.sh, because the
#     background reference resolves against a volume of that name.
#
# Requires: macOS with a logged-in GUI session, Python with Pillow (repo .venv).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD="$REPO_ROOT/desktop/build"
VOL="OpenNolan"   # keep in sync with DMG_VOLNAME in build-dmg.sh
PY="$REPO_ROOT/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"

echo "==> Rendering background artwork (1x + @2x → combined HiDPI tiff)"
tmp="$(mktemp -d)"
"$PY" - "$tmp" <<'PYEOF'
import sys
from PIL import Image, ImageDraw, ImageFont
out = sys.argv[1]
CREAM, CREAM_EDGE = (249, 244, 236), (243, 237, 226)
TERRACOTTA, INK, SUBTLE = (217, 105, 74), (43, 42, 40), (150, 143, 133)
W1, H1, S = 660, 400, 2
W, H = W1 * S, H1 * S
APP_X, APP_Y, APPS_X, ICON_R = 180 * S, 195 * S, 480 * S, 64 * S
HN = "/System/Library/Fonts/HelveticaNeue.ttc"
def f(sz, i=0): return ImageFont.truetype(HN, sz, index=i)
im = Image.new("RGB", (W, H), CREAM); d = ImageDraw.Draw(im)
for y in range(H):
    m = 1 - 0.05 * (y / H)
    d.line([(0, y), (W, y)], fill=tuple(int(CREAM[i] * m + CREAM_EDGE[i] * (1 - m)) for i in range(3)))
def tw(s, fo): b = d.textbbox((0, 0), s, font=fo); return b[2] - b[0]
hl, hf = "Move OpenNolan to Applications", f(26 * S, 10)
d.text(((W - tw(hl, hf)) / 2, 52 * S), hl, font=hf, fill=INK)
d.rectangle([(W/2 - 20*S, 90*S), (W/2 + 20*S, 90*S + 3*S)], fill=TERRACOTTA)
sl, sf = "Drag the icon into the folder to install", f(13 * S)
d.text(((W - tw(sl, sf)) / 2, 102 * S), sl, font=sf, fill=SUBTLE)
ax0, ax1, ay = APP_X + ICON_R + 18*S, APPS_X - ICON_R - 18*S, APP_Y
d.line([(ax0, ay), (ax1 - 14*S, ay)], fill=TERRACOTTA, width=3*S)
d.polygon([(ax1, ay), (ax1 - 16*S, ay - 10*S), (ax1 - 16*S, ay + 10*S)], fill=TERRACOTTA)
d.text((28 * S, H - 34 * S), "OPENNOLAN", font=f(12 * S, 10), fill=TERRACOTTA)
im.resize((W1, H1), Image.LANCZOS).save(out + "/bg.tiff")
im.save(out + "/bg@2x.tiff")
PYEOF
tiffutil -cathidpicheck "$tmp/bg.tiff" "$tmp/bg@2x.tiff" -out "$BUILD/dmg-background.tiff" >/dev/null
echo "    wrote $BUILD/dmg-background.tiff"

echo "==> Authoring .DS_Store via Finder (a Finder window will briefly open)"
# Finder bakes the scratch image's absolute path into the .DS_Store's background
# alias. Use a GENERIC /private/tmp path (not mktemp's per-user $TMPDIR) so the
# committed .DS_Store carries no user-specific temp-dir container hash — this is a
# public repo. (Rendering uses the relative ".background:background.tiff" ref, so
# the baked path is irrelevant at runtime; we just don't want the junk committed.)
authdir="$(mktemp -d /private/tmp/opennolan-dmg.XXXXXX)"
for v in "/Volumes/$VOL"*; do [[ -e "$v" ]] && hdiutil detach "$v" -force >/dev/null 2>&1 || true; done
hdiutil create -size 20m -fs HFS+ -volname "$VOL" -o "$authdir/auth.dmg" -quiet
mnt="$(hdiutil attach "$authdir/auth.dmg" -readwrite -noautoopen | awk -F'\t' '/\/Volumes\// {print $NF}' | tail -1)"
[[ "$mnt" == "/Volumes/$VOL" ]] || { echo "authoring volume mounted as '$mnt' (stale?)"; exit 1; }
mkdir -p "$mnt/.background"; cp "$BUILD/dmg-background.tiff" "$mnt/.background/background.tiff"
mkdir -p "$mnt/OpenNolan.app"; : > "$mnt/OpenNolan.app/placeholder"
ln -s /Applications "$mnt/Applications"
osascript <<APPLESCRIPT
tell application "Finder"
  tell disk "$VOL"
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {400, 120, 1060, 520}
    set vo to the icon view options of container window
    set arrangement of vo to not arranged
    set icon size of vo to 128
    set background picture of vo to file ".background:background.tiff"
    set position of item "OpenNolan.app" of container window to {180, 195}
    set position of item "Applications" of container window to {480, 195}
    update without registering applications
    delay 1
    close
  end tell
end tell
APPLESCRIPT
sync; sleep 1
cp "$mnt/.DS_Store" "$BUILD/dmg.DS_Store"
echo "    wrote $BUILD/dmg.DS_Store"
hdiutil detach "$mnt" -force >/dev/null 2>&1 || true
rm -rf "$tmp" "$authdir"
echo "==> Done. Commit desktop/build/dmg-background.tiff + desktop/build/dmg.DS_Store"
