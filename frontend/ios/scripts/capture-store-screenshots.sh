#!/bin/bash
# Capture the App Store screenshot set — with SAMPLE market data on FICTIONAL companies where a
# screen shows prices — on the 6.9" simulator. One launch per shot, no taps.
#
#   ./frontend/ios/scripts/capture-store-screenshots.sh                  # newest Debug sim build
#   ./frontend/ios/scripts/capture-store-screenshots.sh <path/to/ios.app>
#   CAYDEX_SHOT_ONLY=01-home ./frontend/ios/scripts/capture-store-screenshots.sh   # one shot
#
# WHY: the market-data licence permits no public display of prices, % moves or price charts,
# and the App Store listing is public (.claude/rules/marketing.md §1 "Screenshots"). 1.0 shipped
# real prices in three of its five screenshots. Every screen that shows those figures is
# captured in the DEBUG-only StoreScreenshotMode: invented values on FICTIONAL companies, each
# checked against FMP's full stock list — the rule's "or on a fictional ticker" branch, so no
# "Sample data" label (owner, 2026-10-08). The label is opt-in (CAYDEX_STORE_SHOT_LABEL=1) and
# REQUIRED again if a real ticker ever returns to the fixtures. The other screens are captured
# with the mode OFF. See frontend/ios/ios/Core/Utilities/StoreScreenshotMode.swift.
#
# NEEDS
#   • a DEBUG simulator build — Xcode ⌘B or the canonical build in CLAUDE.md. This script never
#     builds (Machine-safety rules: one Swift compile at a time, main session only).
#   • the 6.9" simulator signed in to an account and past the disclaimer + onboarding (the app is
#     account-only; the demo account is fine). A signed-out simulator captures the sign-in page.
#
# OUTPUT: documents/legal/screenshots/6.9-v2/NN-name.png at 1320×2868 (Apple's 6.9" size, scaled
# down to every smaller iPhone). CHECK EVERY FRAME before uploading: no real price, % move or
# chart line; no real investor or politician name; no score / fair value / opinion on a real
# ticker. The Wiser shot carries LIVE article titles (newest first): if one names a real person,
# swap that shot out — re-shooting the same day shows the same titles.
set -euo pipefail

DEVICE="${CAYDEX_SHOT_DEVICE:-3C473C18-1FB5-417F-836B-3D0EFDFB7026}"   # iPhone 17 Pro Max (6.9")
BUNDLE="com.phan.caydex"
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
OUT="${CAYDEX_SHOT_OUT:-$REPO/documents/legal/screenshots/6.9-v2}"
SETTLE="${CAYDEX_SHOT_SETTLE:-12}"     # seconds from launch to capture
ONLY="${CAYDEX_SHOT_ONLY:-}"

WORKSPACE="$REPO/frontend/ios/ios.xcodeproj"

# The newest Debug simulator build of THIS project: the product is Caydex.app (PRODUCT_NAME),
# and only DerivedData trees whose info.plist WorkspacePath is this repo's project count — a
# second checkout or an unrelated project must never be captured by accident.
newest_build() {
  local dd best="" best_mtime=0 ws app mtime
  for dd in "$HOME"/Library/Developer/Xcode/DerivedData/ios-*; do
    [ -f "$dd/info.plist" ] || continue
    ws="$(/usr/libexec/PlistBuddy -c 'Print :WorkspacePath' "$dd/info.plist" 2>/dev/null || true)"
    [ "$ws" = "$WORKSPACE" ] || continue
    app="$dd/Build/Products/Debug-iphonesimulator/Caydex.app"
    [ -d "$app" ] || continue
    mtime="$(stat -f %m "$app")"
    if [ "$mtime" -gt "$best_mtime" ]; then best="$app"; best_mtime="$mtime"; fi
  done
  printf '%s' "$best"
}

APP="${1:-$(newest_build)}"
if [ -z "$APP" ] || [ ! -d "$APP" ]; then
  echo "✗ no Debug simulator build of $WORKSPACE found — build first (Xcode ⌘B or the canonical command)" >&2
  exit 1
fi

# A build without the screenshot mode would ignore CAYDEX_STORE_SHOT and capture LIVE prices in
# the "sample" shots. Debug builds may keep their code in <Exe>.debug.dylib, so search both.
EXE="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Info.plist")"
if ! find "$APP" -maxdepth 1 -type f \( -name "$EXE" -o -name '*.dylib' \) -exec grep -aq "CAYDEX_STORE_SHOT" {} \; -print | grep -q .; then
  echo "✗ $APP does not contain StoreScreenshotMode (a Release, stale or foreign build) — rebuild Debug" >&2
  exit 1
fi

# …and the build must be NEWER than the screenshot sources. A Debug build made before a fixture
# change passes the check above but serves the OLD fixtures — before 2026-10-08 that meant a LIVE
# Updates feed (an AI brief with real index % moves, headlines naming real people) in the Updates shot.
# The code lives in the main executable or, in a Debug build, <Exe>.debug.dylib: take the newer.
BUILT=0
BUILT_BIN=""
for bin in "$APP/$EXE" "$APP/$EXE.debug.dylib"; do
  [ -f "$bin" ] || continue
  mtime="$(stat -f %m "$bin")"
  if [ "$mtime" -gt "$BUILT" ]; then BUILT="$mtime"; BUILT_BIN="$bin"; fi
done
for src in "$REPO"/frontend/ios/ios/Core/Utilities/StoreScreenshot*.swift; do
  if [ "$(stat -f %m "$src")" -gt "$BUILT" ]; then
    echo "✗ $(basename "$src") changed after this build — rebuild Debug first" >&2
    exit 1
  fi
done
# Any other Swift change after the build only warns: the frames would show the OLD screens.
# (No `| head` here: under pipefail its SIGPIPE to find would end the whole run.)
NEWER="$(find "$REPO/frontend/ios/ios" -name '*.swift' -newer "$BUILT_BIN" 2>/dev/null || true)"
if [ -n "$NEWER" ]; then
  echo "⚠ $(printf '%s\n' "$NEWER" | wc -l | tr -d ' ') Swift file(s) changed after this build — the frames show the build, not the source:" >&2
  printf '%s\n' "$NEWER" | sed -n '1,5p' | sed 's/^/    /' >&2
fi
echo "▸ app:    $APP ($(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Info.plist") ($(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$APP/Info.plist")))"
echo "▸ device: $DEVICE"
echo "▸ out:    $OUT"

CURRENT_SHOT="(setup)"
cleanup() {
  local status=$?
  xcrun simctl terminate "$DEVICE" "$BUNDLE" 2>/dev/null || true
  xcrun simctl status_bar "$DEVICE" clear 2>/dev/null || true
  if [ "$status" -ne 0 ]; then
    echo "✗ stopped during $CURRENT_SHOT (exit $status) — status bar override cleared" >&2
  fi
}
trap cleanup EXIT

xcrun simctl boot "$DEVICE" 2>/dev/null || true
xcrun simctl bootstatus "$DEVICE" -b >/dev/null
xcrun simctl status_bar "$DEVICE" override --time "9:41" \
  --dataNetwork wifi --wifiMode active --wifiBars 3 \
  --cellularMode active --cellularBars 4 \
  --batteryState charged --batteryLevel 100
xcrun simctl install "$DEVICE" "$APP"
mkdir -p "$OUT"

# shot <file-name> <tab> <sample-mode 1|0> [app launch arguments…]
shot() {
  local name="$1" tab="$2" sample="$3"
  shift 3
  if [ -n "$ONLY" ] && [ "$ONLY" != "$name" ]; then return 0; fi
  CURRENT_SHOT="$name"
  xcrun simctl terminate "$DEVICE" "$BUNDLE" 2>/dev/null || true
  SIMCTL_CHILD_CAYDEX_STORE_SHOT="$sample" \
  SIMCTL_CHILD_CAYDEX_STORE_SHOT_TAB="$tab" \
  SIMCTL_CHILD_CAYDEX_QUOTE_WEEK=off \
    xcrun simctl launch "$DEVICE" "$BUNDLE" "$@" >/dev/null
  sleep "$SETTLE"
  xcrun simctl io "$DEVICE" screenshot --type=png "$OUT/$name.png" >/dev/null 2>&1
  echo "✓ $name  ($(sips -g pixelWidth -g pixelHeight "$OUT/$name.png" | awk '/pixel/ {printf "%s ", $2}'))"
}

# The listing shows the screenshots in the order they are uploaded, and the files sort by their
# number — so the shots follow the app's own tab bar: Home, Updates, Research, Tracking, Wiser
# (owner, 2026-10-08; `HomeTab`'s case order, pinned by tests/test_ios_store_screenshot_mode_debug_only.py).
shot 01-home      home     1   # sample market strip, holdings, movers; signals locked
# The Updates shot is safe ONLY because every Updates read is canned (2026-10-08): tabs, the feed
# (Insights card + headlines) and the News Tone trend — StoreScreenshotFixtures. Its live Insights
# card is an AI brief seeded with real index % moves and live headlines can name real people
# (adversarial review 2026-10-06), so never drop one of those fixtures while this shot exists
# (tests/test_ios_store_screenshot_mode_debug_only.py pins it).
# The News Tone chart opens on the window last TAPPED on this device (UserDefaults
# `caydex_updates_trend_window`). The launch argument pins 30D for this one process: the argument
# domain outranks the stored value and is never written back, so the device keeps its own pick.
shot 02-updates   updates  1  -caydex_updates_trend_window month   # sample Insights, News Tone 30D, headlines
shot 03-research  research 0   # persona picker — no market data
shot 04-tracking  tracking 1   # sample watchlist rows (prices, % moves, sparklines)
shot 05-wiser     wiser    0   # Investor Journey + Money Moves — check article titles
#
# Only the tab roots above are sample. A screen opened by a TAP (a ticker cover) shows LIVE
# prices, and with the label off nothing on screen marks it — so never tap in a run.

CURRENT_SHOT="(done)"
echo "Done. Review every frame in $OUT before uploading."
