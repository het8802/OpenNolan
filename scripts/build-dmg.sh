#!/usr/bin/env bash
#
# build-dmg.sh — OpenNolan Mac deployment: build the Apple-Silicon .dmg.
#
# The canonical entry point for producing a distributable (or locally testable) OpenNolan.app + .dmg.
# It wraps `npm --prefix desktop run dist` with a few guardrails:
#
#   • Detaches stale OpenNolan disk-image mounts left behind by a failed prior run.
#   • Auto-detects a Developer ID cert: present -> signed + notarized; absent -> unsigned local build.
#   • Verifies the finished .dmg (hdiutil verify) and prints exactly where it landed + how to test it.
#
# Usage:
#   scripts/build-dmg.sh              build the .dmg (signed if a cert is present, else unsigned)
#   scripts/build-dmg.sh --unsigned   force an unsigned local build even if a cert exists
#   scripts/build-dmg.sh --dir        fast path: unpacked .app only, skip the .dmg (uses dist:dir)
#   scripts/build-dmg.sh --publish     signed build + upload to the GitHub Releases auto-update feed
#
# Signing / notarization env (release machine only) — see docs/RELEASE-mac.md for the full list:
#   CSC_NAME, or CSC_LINK + CSC_KEY_PASSWORD          (Developer ID identity)
#   APPLE_API_KEY + APPLE_API_KEY_ID + APPLE_API_ISSUER   (App Store Connect key — recommended)
#   or APPLE_ID + APPLE_APP_SPECIFIC_PASSWORD + APPLE_TEAM_ID
#
set -euo pipefail

# --- locate the repo root from this script's location (works when run from anywhere) ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DESKTOP="$REPO_ROOT/desktop"

# --- tiny logging helpers ---
if [[ -t 1 ]]; then BOLD=$'\033[1m'; RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; BLU=$'\033[34m'; RST=$'\033[0m'
else BOLD=""; RED=""; GRN=""; YEL=""; BLU=""; RST=""; fi
step() { printf '%s\n' "${BLU}${BOLD}==>${RST} ${BOLD}$*${RST}"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '%s\n' "${YEL}warning:${RST} $*" >&2; }
die()  { printf '%s\n' "${RED}error:${RST} $*" >&2; exit 1; }

# --- parse flags ---
DIR_ONLY=0; PUBLISH=0; FORCE_UNSIGNED=0
for arg in "$@"; do
  case "$arg" in
    --dir)      DIR_ONLY=1 ;;
    --publish)  PUBLISH=1 ;;
    --unsigned) FORCE_UNSIGNED=1 ;;
    -h|--help)  awk 'NR>1 && /^#/{sub(/^# ?/,""); print; next} NR>1{exit}' "$0"; exit 0 ;;
    *) die "unknown flag: $arg (see --help)" ;;
  esac
done
[[ $DIR_ONLY == 1 && $PUBLISH == 1 ]] && die "--dir and --publish are mutually exclusive"

# --- preflight ---
step "Preflight"
[[ "$(uname -s)" == "Darwin" ]] || die "macOS only (this builds a signed Mac .app/.dmg)"
[[ "$(uname -m)" == "arm64" ]]  || warn "not on arm64 — the build targets arm64 only; cross-building is unsupported"
command -v node >/dev/null || die "node not found"
command -v npm  >/dev/null || die "npm not found"
[[ -d "$DESKTOP" ]] || die "desktop/ not found at $DESKTOP"
if [[ ! -x "$DESKTOP/node_modules/.bin/electron-builder" ]]; then
  info "installing desktop deps (first run)…"
  npm --prefix "$DESKTOP" install
fi
info "repo:    $REPO_ROOT"
info "node:    $(node -v)   npm: $(npm -v)"

# --- detach stale OpenNolan mounts from a failed prior run ---
shopt -s nullglob
for vol in /Volumes/OpenNolan*; do
  warn "detaching stale mount: $vol"
  hdiutil detach "$vol" >/dev/null 2>&1 || true
done
shopt -u nullglob

# --- decide signed vs unsigned ---
step "Signing mode"
have_identity() { security find-identity -v -p codesigning 2>/dev/null | grep -q "Developer ID Application"; }
SIGNED=0
if [[ $FORCE_UNSIGNED == 1 ]]; then
  info "forced UNSIGNED build (--unsigned)"
elif [[ -n "${CSC_NAME:-}${CSC_LINK:-}" ]] || have_identity; then
  SIGNED=1
  info "${GRN}Developer ID present → signed + notarized build${RST}"
else
  info "${YEL}no Developer ID cert found → UNSIGNED build${RST} (fine for local testing; not distributable)"
fi
if [[ $SIGNED == 0 ]]; then
  export CSC_IDENTITY_AUTO_DISCOVERY=false   # tell electron-builder to skip signing + notarization
fi

# --- publish preflight (auto-update needs a signed build + real repo in build.publish) ---
if [[ $PUBLISH == 1 ]]; then
  [[ $SIGNED == 1 ]] || die "--publish requires a signed build (electron-updater verifies signatures on macOS)"
  if grep -q "OWNER_PLACEHOLDER\|REPO_PLACEHOLDER" "$DESKTOP/package.json"; then
    die "build.publish in desktop/package.json still has OWNER_PLACEHOLDER/REPO_PLACEHOLDER — set the real GitHub owner/repo first"
  fi
  [[ -n "${GH_TOKEN:-}${GITHUB_TOKEN:-}" ]] || warn "no GH_TOKEN/GITHUB_TOKEN set — electron-builder may not be able to upload the release"
fi

# --- DMG builder --------------------------------------------------------------
# We build the .dmg with `hdiutil create -srcfolder` instead of electron-builder's
# bundled dmgbuild. dmgbuild's final step is `hdiutil convert`, which on managed
# Macs (endpoint-security agents like Kandji/Crowdstrike intercepting disk-image
# reads) reliably fails on large images with "Resource temporarily unavailable"
# — while `create -srcfolder`, which reads plain files, always works. This path is
# also universal: it produces the same DMG on managed and unmanaged machines.
#
# The install-window look (cream background art with a "Move OpenNolan to
# Applications" headline + arrow, and the two icons positioned) comes from two
# committed assets baked into the staging folder:
#   desktop/build/dmg-background.tiff   the artwork (in the image as .background/)
#   desktop/build/dmg.DS_Store          the Finder window layout (bg + positions)
# Both are (re)generated by scripts/author-dmg-layout.sh — see that file for why
# the background must be authored by Finder (a programmatic alias renders white on
# recent macOS) and why it's a separate manual step. Here the build is fully
# headless: it just copies files and runs create -srcfolder.
#
# Note: the .background/ folder + .background.tiff are dot-hidden, so a normal user
# never sees them; a user browsing with "Show hidden files" (⌘⇧.) ON will — that
# toggle overrides all hiding and can't be beaten while an image background (which
# needs a file) exists. .DS_Store is always hidden by Finder regardless.
# DMG_VOLNAME must match the volume name author-dmg-layout.sh used (the background
# reference resolves against a volume of that name).
DMG_VOLNAME="OpenNolan"
build_dmg() {
  local app="$1" out="$2" stage id
  local bg="$DESKTOP/build/dmg-background.tiff" ds="$DESKTOP/build/dmg.DS_Store"
  [[ -f "$bg" && -f "$ds" ]] || die "missing DMG layout assets ($bg / $ds) — run scripts/author-dmg-layout.sh"

  # staging folder = app + Applications symlink + hidden .background/art + .DS_Store.
  # Plain files only (no mounted disk image), so nothing goes through the read path
  # that the endpoint-security agent blocks.
  stage="$(mktemp -d)/stage"
  mkdir -p "$stage/.background"
  ditto "$app" "$stage/OpenNolan.app"
  ln -s /Applications "$stage/Applications"
  cp "$bg" "$stage/.background/background.tiff"
  cp "$ds" "$stage/.DS_Store"
  chflags hidden "$stage/.background"

  # one-shot create: reads plain files, writes compressed UDZO — no convert.
  rm -f "$out"
  hdiutil create -srcfolder "$stage" -volname "$DMG_VOLNAME" -format UDZO -o "$out" -quiet \
    || die "hdiutil create -srcfolder failed (see df -h for disk space)"

  # sign the finished .dmg when a Developer ID is available. NOTE: notarizing the
  # DMG itself is a separate release-machine step (xcrun notarytool submit + staple);
  # electron-builder's old dmg target did that automatically — the new path does not,
  # so a public release must notarize+staple the .dmg. See docs/RELEASE-mac.md.
  if [[ $SIGNED == 1 ]]; then
    id="$(security find-identity -v -p codesigning 2>/dev/null | grep 'Developer ID Application' | head -1 | sed -E 's/.*"(.*)"/\1/')"
    if [[ -n "$id" ]]; then codesign --sign "$id" "$out" || warn "could not codesign the .dmg"; fi
  fi

  rm -rf "$(dirname "$stage")"
}

# --- build ---
step "Building (this bundles Python + web/dist; ~950 MB unpacked, takes a few minutes)"
cd "$DESKTOP"
if [[ $DIR_ONLY == 1 ]]; then
  npm run dist:dir
elif [[ $PUBLISH == 1 ]]; then
  npm run dist -- --publish always
else
  npm run dist
fi

# --- report + verify ---
step "Result"
if [[ $DIR_ONLY == 1 ]]; then
  APP="$DESKTOP/dist/mac-arm64/OpenNolan.app"
  [[ -d "$APP" ]] || die "expected app not found at $APP"
  info "${GRN}✅ built:${RST} $APP  ($(du -sh "$APP" | cut -f1))"
  info "test it:  open \"$APP\""
  exit 0
fi

APP="$DESKTOP/dist/mac-arm64/OpenNolan.app"
[[ -d "$APP" ]] || die "expected app not found at $APP (electron-builder builds the zip + unpacked app)"
VERSION="$(node -p "require('$DESKTOP/package.json').version")"
DMG="$DESKTOP/dist/OpenNolan-${VERSION}-arm64.dmg"
step "Packaging DMG (hdiutil create -srcfolder — bypasses the convert step that fails on managed Macs)"
build_dmg "$APP" "$DMG"
info "verifying image integrity…"
hdiutil verify "$DMG" >/dev/null && info "checksum valid"
printf '\n%s\n' "${GRN}${BOLD}✅ DMG ready:${RST} $DMG  ($(du -sh "$DMG" | cut -f1))"
if [[ $SIGNED == 1 ]]; then
  info "the app inside is signed; the .dmg is signed too."
  warn "the .dmg is NOT yet notarized/stapled — for a public release run: xcrun notarytool submit \"$DMG\" ... && xcrun stapler staple \"$DMG\" (see docs/RELEASE-mac.md)"
  if [[ $PUBLISH == 1 ]]; then
    warn "electron-builder uploaded the zip + latest-mac.yml (auto-update feed), but NOT this .dmg — upload it to the release yourself: gh release upload v$VERSION \"$DMG\""
  fi
else
  cat <<EOF
    ${YEL}unsigned${RST} — for local testing only. To install:
      open "$DMG"                          # then drag OpenNolan → Applications
      xattr -cr /Applications/OpenNolan.app   # strip quarantine so Gatekeeper allows an unsigned app
EOF
fi
