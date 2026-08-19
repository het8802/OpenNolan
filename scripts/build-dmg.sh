#!/usr/bin/env bash
#
# build-dmg.sh — OpenNolan Mac deployment: build the Apple-Silicon .dmg.
#
# The canonical entry point for producing a distributable (or locally testable) OpenNolan.app + .dmg.
# It wraps `npm --prefix desktop run dist` with a few guardrails:
#
#   • Detaches stale OpenNolan disk-image mounts left behind by a failed prior run.
#   • Auto-detects a Developer ID cert: present -> signed + notarized; absent -> unsigned local build.
#   • Notarizes + staples the .dmg itself (electron-builder only does the .app — see notarize_dmg).
#   • Gates on the real Gatekeeper checks (spctl + stapler validate), not just an image checksum.
#   • Prints exactly where it landed + how to test it.
#
# Usage:
#   scripts/build-dmg.sh              build the .dmg (signed if a cert is present, else unsigned)
#   scripts/build-dmg.sh --unsigned   force an unsigned local build even if a cert exists
#   scripts/build-dmg.sh --dir        fast path: unpacked .app only, skip the .dmg (uses dist:dir)
#   scripts/build-dmg.sh --publish    the full release: signed + notarized, then uploads the .dmg
#                                     (human download) alongside electron-builder's .zip +
#                                     latest-mac.yml (the auto-update feed). Needs GH_TOKEN.
#   scripts/build-dmg.sh --bump-version         bump desktop/package.json patch, commit, tag, push
#   scripts/build-dmg.sh --bump-version=minor   same, but minor / major / an explicit 1.2.3
#
# --bump-version runs BEFORE the build, so the .app and the .dmg filename carry the new number.
# It pushes the tag, which triggers .github/workflows/release-mac.yml — CI then does its own
# signed build and Release upload. Combining it with --publish means two builds of the same tag.
#
# A signed build always talks to Apple twice (once for the .app, once for the .dmg) and uploads
# ~1 GB each time. For a quick local check use --dir or --unsigned; neither contacts Apple.
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
DIR_ONLY=0; PUBLISH=0; FORCE_UNSIGNED=0; BUMP=""
for arg in "$@"; do
  case "$arg" in
    --dir)      DIR_ONLY=1 ;;
    --publish)  PUBLISH=1 ;;
    --unsigned) FORCE_UNSIGNED=1 ;;
    --bump-version)   BUMP=patch ;;
    --bump-version=*) BUMP="${arg#*=}" ;;   # minor | major | an explicit 1.2.3 — npm takes them all
    -h|--help)  awk 'NR>1 && /^#/{sub(/^# ?/,""); print; next} NR>1{exit}' "$0"; exit 0 ;;
    *) die "unknown flag: $arg (see --help)" ;;
  esac
done
[[ $DIR_ONLY == 1 && $PUBLISH == 1 ]] && die "--dir and --publish are mutually exclusive"
[[ -n "$BUMP" && $DIR_ONLY == 1 ]] && die "--bump-version with --dir makes no sense: --dir is a local
    smoke build, so this would tag + push a release for a .dmg you never packaged"

# --- version bump (--bump-version) --------------------------------------------------------------
# desktop/package.json is the single source of truth for the version: app.getVersion() reads it,
# the DMG filename below is built from it, and release-mac.yml builds whatever v{version} tag it
# was triggered by. This has to run BEFORE the build — electron-builder stamps the version into
# the .app at package time, so bumping afterwards would ship the old number under the new tag.
if [[ -n "$BUMP" ]]; then
  step "Bumping version ($BUMP)"
  git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repo: $REPO_ROOT"
  # `git commit -am` stages every tracked modification, so anything dirty would be swallowed into
  # the version commit. Untracked files are fine — -a does not touch them.
  git -C "$REPO_ROOT" diff --quiet && git -C "$REPO_ROOT" diff --cached --quiet \
    || die "working tree has tracked changes — commit or stash them first, or they land in the version commit"
  BRANCH="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)"
  # Derive the remote from the branch's upstream rather than hardcoding it — this repo's remote is
  # named het8802, not origin.
  UPSTREAM="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)" \
    || die "branch '$BRANCH' has no upstream — set one first: git push -u <remote> $BRANCH"
  REMOTE="${UPSTREAM%%/*}"

  TAG="$(npm --prefix "$DESKTOP" version "$BUMP" --no-git-tag-version)"   # prints e.g. v1.0.2
  if git -C "$REPO_ROOT" rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    git -C "$REPO_ROOT" checkout -- desktop/package.json desktop/package-lock.json
    die "tag $TAG already exists — bump reverted, nothing changed"
  fi
  git -C "$REPO_ROOT" commit -qam "chore(desktop): $TAG"
  git -C "$REPO_ROOT" tag "$TAG"
  git -C "$REPO_ROOT" push "$REMOTE" "$BRANCH" --tags
  info "${GRN}$TAG${RST} committed, tagged, pushed to $REMOTE/$BRANCH"
  warn "$TAG is now pushed: release-mac.yml is building + publishing this tag in CI too"
fi

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
# Vite compiles web/src + web/node_modules into web/dist; the .app only ships
# that dist. Missing or stale web deps fail the compile (and therefore the
# whole package), so always install from the lockfile — do not assume the
# developer already ran npm install in web/.
[[ -f "$REPO_ROOT/web/package-lock.json" ]] || die "web/package-lock.json missing"
info "syncing web deps from lockfile…"
npm --prefix "$REPO_ROOT/web" ci
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

# --- notarization credentials -------------------------------------------------
# BOTH the .app (electron-builder, via mac.notarize) and the .dmg (notarize_dmg below) need
# these. Resolved here, up front, so a missing credential fails in two seconds instead of
# after a 20-minute build. Supports either auth method from docs/RELEASE-mac.md.
NOTARY_ARGS=()
if [[ $SIGNED == 1 ]]; then
  if [[ -n "${APPLE_API_KEY:-}" && -n "${APPLE_API_KEY_ID:-}" && -n "${APPLE_API_ISSUER:-}" ]]; then
    [[ -f "$APPLE_API_KEY" ]] || die "APPLE_API_KEY points at a missing file: $APPLE_API_KEY"
    NOTARY_ARGS=(--key "$APPLE_API_KEY" --key-id "$APPLE_API_KEY_ID" --issuer "$APPLE_API_ISSUER")
    info "notarization: App Store Connect API key ($APPLE_API_KEY_ID)"
  elif [[ -n "${APPLE_ID:-}" && -n "${APPLE_APP_SPECIFIC_PASSWORD:-}" && -n "${APPLE_TEAM_ID:-}" ]]; then
    NOTARY_ARGS=(--apple-id "$APPLE_ID" --password "$APPLE_APP_SPECIFIC_PASSWORD" --team-id "$APPLE_TEAM_ID")
    info "notarization: Apple ID app-specific password ($APPLE_ID)"
  else
    die "signed build, but no notary credentials in the environment.
    Set APPLE_API_KEY + APPLE_API_KEY_ID + APPLE_API_ISSUER (recommended), or
        APPLE_ID + APPLE_APP_SPECIFIC_PASSWORD + APPLE_TEAM_ID.   See docs/RELEASE-mac.md.
    For a local test build that skips Apple entirely, use: $0 --unsigned  (or --dir)"
  fi
fi

# --- publish preflight (auto-update needs a signed build + real repo in build.publish) ---
if [[ $PUBLISH == 1 ]]; then
  [[ $SIGNED == 1 ]] || die "--publish requires a signed build (electron-updater verifies signatures on macOS)"
  if grep -q "OWNER_PLACEHOLDER\|REPO_PLACEHOLDER" "$DESKTOP/package.json"; then
    die "build.publish in desktop/package.json still has OWNER_PLACEHOLDER/REPO_PLACEHOLDER — set the real GitHub owner/repo first"
  fi
  # A die, not a warn: with no token electron-builder silently publishes nothing, and the
  # `gh release upload` at the end has no release to attach the .dmg to.
  [[ -n "${GH_TOKEN:-}${GITHUB_TOKEN:-}" ]] || die "no GH_TOKEN/GITHUB_TOKEN set — nothing would be uploaded. Try: gh auth switch --user <owner> && export GH_TOKEN=\$(gh auth token)"
  command -v gh >/dev/null || die "gh not found — needed to upload the .dmg to the release (brew install gh)"
fi

# --- publish verification -----------------------------------------------------
# Assert what was actually published instead of printing that we published it. Both failures below
# have already shipped once: electron-builder's GitHub publisher raced itself and created TWO drafts
# for v1.0.1 in the same second (drafts create no tag ref, so GitHub does not enforce uniqueness),
# and the assets split across them — .dmg on one, the update .zip on the other. `gh release upload`
# resolves a tag to ONE release, so with two drafts it silently picks. Publishing either would have
# been broken: no .zip means every auto-update 404s, no .dmg means no download button. The whole
# thing was invisible because the line below this used to be an unconditional success message.
verify_release() {
  local tag="$1" ids n
  ids="$(gh api "repos/{owner}/{repo}/releases" --paginate \
          --jq ".[] | select(.tag_name==\"$tag\") | .id" 2>/dev/null)" \
    || die "could not read the releases for $tag — verify by hand before announcing this build"
  n="$(printf '%s\n' "$ids" | grep -c . || true)"
  [[ "$n" == 0 ]] && die "no release exists for $tag — electron-builder's publish step did nothing.
    Check its output above for an upload error, then re-run with --publish."
  [[ "$n" == 1 ]] || die "expected exactly ONE release for $tag, found $n: $(printf '%s' "$ids" | tr '\n' ' ')
    electron-builder's publisher can create duplicate DRAFTS for one tag and split the assets
    across them. Keep the one holding the .dmg, re-upload the missing assets to it, delete the
    other (gh api --method DELETE repos/{owner}/{repo}/releases/<id>), then re-run this step."

  # Every artifact a user or the updater fetches must be present AND byte-complete. Sizes come from
  # the files we just built, so a truncated or half-uploaded asset fails here rather than on someone
  # else's Mac. The blockmap only enables delta updates, so it is checked when built, not required.
  local assets f name local_size remote_size
  assets="$(gh api "repos/{owner}/{repo}/releases/$ids/assets" --paginate \
            --jq '.[] | "\(.name) \(.size) \(.state)"' 2>/dev/null)" \
    || die "could not list the assets on $tag"
  for f in "$DMG" \
           "$DESKTOP/dist/OpenNolan-${VERSION}-arm64-mac.zip" \
           "$DESKTOP/dist/latest-mac.yml" \
           "$DESKTOP/dist/OpenNolan-${VERSION}-arm64-mac.zip.blockmap"; do
    name="$(basename "$f")"
    if [[ ! -f "$f" ]]; then
      case "$name" in
        *.blockmap) continue ;;  # optional: delta updates only
        *) die "$name was never built locally — the release cannot be complete without it" ;;
      esac
    fi
    local_size="$(stat -f%z "$f")"
    remote_size="$(printf '%s\n' "$assets" | awk -v n="$name" '$1==n {print $2}')"
    [[ -n "$remote_size" ]] || die "$name is MISSING from the $tag release.
    Upload it before publishing:  gh release upload $tag \"$f\" --clobber"
    [[ "$remote_size" == "$local_size" ]] || die "$name on $tag is $remote_size bytes, local file is $local_size — re-upload it:
    gh release upload $tag \"$f\" --clobber"
  done
  info "${GRN}verified${RST} $tag: one release, $(printf '%s\n' "$assets" | grep -c . || true) assets, sizes match the local build"
}

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

  # Sign the finished .dmg — a prerequisite for notarizing it (notarize_dmg, below).
  if [[ $SIGNED == 1 ]]; then
    id="$(security find-identity -v -p codesigning 2>/dev/null | grep 'Developer ID Application' | head -1 | sed -E 's/.*"(.*)"/\1/')"
    # A die, not a warn: an unsigned .dmg cannot be notarized, so continuing just moves the
    # failure to a more confusing place. CSC_LINK alone doesn't help HERE — this step signs
    # from the keychain, so import the .p12 (security import) or set CSC_NAME.
    [[ -n "$id" ]] || die "no 'Developer ID Application' identity in the keychain to sign the .dmg with"
    # --timestamp is explicit on purpose: Apple REJECTS notarization of a signature that has
    # no secure timestamp. codesign's default is not guaranteed to include one.
    codesign --sign "$id" --timestamp "$out" || die "could not codesign the .dmg"
  fi

  rm -rf "$(dirname "$stage")"
}

# --- notarize + staple the DMG -------------------------------------------------
# A notarization ticket is per-ARTIFACT. electron-builder already notarized and stapled the
# .app, but the .dmg wrapping it is a separate file Apple has never seen — and the .dmg is
# what the browser marks with com.apple.quarantine and what Gatekeeper assesses first, at
# mount time, before the app inside is ever reached. So it needs its own ticket.
# Staple BEFORE any upload: stapling embeds the ticket by rewriting the file.
notarize_dmg() {
  local dmg="$1"
  step "Notarizing the DMG with Apple (uploads $(du -sh "$dmg" | cut -f1); usually a few minutes)"
  # --wait blocks until Apple returns a verdict and exits non-zero if it is not "Accepted".
  xcrun notarytool submit "$dmg" "${NOTARY_ARGS[@]}" --wait \
    || die "notarization was rejected. For the per-file reason, re-run with the submission id above:
    xcrun notarytool log <submission-id> <same credential flags>"
  info "stapling the ticket into the .dmg…"
  xcrun stapler staple "$dmg" \
    || die "stapling failed — the ticket exists on Apple's servers but is not embedded in the .dmg"
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

if [[ $SIGNED == 1 ]]; then
  notarize_dmg "$DMG"
  step "Verifying Gatekeeper acceptance"
  # `hdiutil verify` above only checks the image CHECKSUM (are the bits intact) — it says
  # nothing about signing or notarization. These two are the real gate: exactly what macOS
  # does to a freshly-downloaded .dmg. Hard failures — never ship a .dmg that fails here.
  spctl -a -vv --type install "$DMG" \
    || die "Gatekeeper REJECTED the .dmg (expected: 'accepted' / 'source=Notarized Developer ID')"
  xcrun stapler validate "$DMG" || die "the .dmg has no stapled notarization ticket"
fi

printf '\n%s\n' "${GRN}${BOLD}✅ DMG ready:${RST} $DMG  ($(du -sh "$DMG" | cut -f1))"
if [[ $SIGNED == 1 ]]; then
  info "${GRN}signed + notarized + stapled${RST} — the .app and the .dmg both. Gatekeeper-clean."
  if [[ $PUBLISH == 1 ]]; then
    step "Uploading the DMG to the v$VERSION release"
    # electron-builder's publish already pushed the .zip + latest-mac.yml (the auto-update feed
    # the running app reads — users never see those). The .dmg is the human-facing download and
    # is NOT part of its publish set, because we build it ourselves. --clobber makes re-runs safe.
    gh release upload "v$VERSION" "$DMG" --clobber \
      || die "upload failed. If that was a 403, the active gh account cannot write to the repo:
    gh auth switch --user <owner> && export GH_TOKEN=\$(gh auth token)"
    verify_release "v$VERSION"
    info "${GRN}published on v$VERSION:${RST} .dmg (download) + .zip + latest-mac.yml (auto-update)"
  fi
else
  cat <<EOF
    ${YEL}unsigned${RST} — for local testing only. To install:
      open "$DMG"                          # then drag OpenNolan → Applications
      xattr -cr /Applications/OpenNolan.app   # strip quarantine so Gatekeeper allows an unsigned app
EOF
fi
