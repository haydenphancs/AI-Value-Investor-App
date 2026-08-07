#!/usr/bin/env bash
#
# a11y-sim.sh — flip a Simulator accessibility setting and PROVE the app saw it.
#
# Three axes, one UDID pin, one reboot helper. Companion to type-sweep.sh (which owns the
# content_size sweep and shares these traps).
#
#   ./frontend/ios/scripts/a11y-sim.sh dwc on|off        Differentiate Without Color
#   ./frontend/ios/scripts/a11y-sim.sh contrast on|off   Increase Contrast
#   ./frontend/ios/scripts/a11y-sim.sh appearance light|dark
#   ./frontend/ios/scripts/a11y-sim.sh status            just read the app back
#   SHOT=path.png ./frontend/ios/scripts/a11y-sim.sh dwc on
#
# WHY `defaults write` FOR DWC AND NOT simctl
# -------------------------------------------
# `xcrun simctl ui` supports exactly three things — appearance, increase_contrast,
# content_size. Differentiate Without Color is NOT one of them, so it has to be written
# into the simulator's own preference domain. It must go through `simctl spawn`: editing
# ~/Library/Developer/CoreSimulator/Devices/<udid>/data/.../com.apple.Accessibility.plist
# from the host gets overwritten by the running cfprefsd. The key does not exist until it
# is first toggled.
#
# THE TRAP THIS SCRIPT EXISTS TO PREVENT
# --------------------------------------
# A setting that does not take LOOKS EXACTLY LIKE an app that ignores the setting. Both
# give you an unchanged screenshot. So every set here is followed by a READ-BACK of what
# the APP reports — `TypographyProbe` already prints `differentiateWithoutColor=`,
# `accessibilityContrast=` and `reduceTransparency=` on every DEBUG launch — and the
# script exits non-zero if the app disagrees with what was asked for. Never trust a
# screenshot taken before that line has been checked.
#
# And the one that cost a full day: NEVER `killall cfprefsd` to force a re-read. It works
# for that and silently breaks the simulator's OS-appearance propagation, after which
# `simctl ui appearance` reads back correctly while new scenes resolve the other way.
# Use shutdown + boot.

set -uo pipefail

BUNDLE_ID="com.phan.caydex"
UDID="${CAYDEX_SIM_UDID:-57C9097B-08F1-4CB1-BF9A-035876F3604F}"
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"

AXIS="${1:-status}"
VALUE="${2:-}"

if ! xcrun simctl list devices | grep -q "$UDID.*Booted"; then
  echo "✗ $UDID is not Booted. Candidates:" >&2
  xcrun simctl list devices available | grep -i "iPhone 17 Pro" >&2
  echo "  Three simulators share that NAME — pin one: CAYDEX_SIM_UDID=<udid> $0 ..." >&2
  exit 1
fi

reboot_sim() {
  xcrun simctl shutdown "$UDID" >/dev/null 2>&1
  xcrun simctl boot "$UDID" >/dev/null 2>&1
  xcrun simctl bootstatus "$UDID" -b >/dev/null 2>&1
}

# Launch, capture the probe's accessibility line, terminate. This is the ONLY source of
# truth about what the app actually resolved.
read_back() {
  local log
  log="$(mktemp)"
  xcrun simctl terminate "$UDID" "$BUNDLE_ID" >/dev/null 2>&1
  ( xcrun simctl launch --console-pty "$UDID" "$BUNDLE_ID" >"$log" 2>&1 & echo $! >"$log.pid" )
  local i
  for i in $(seq 1 40); do
    grep -aq 'accessibilityContrast=' "$log" 2>/dev/null && break
    sleep 0.5
  done
  kill "$(cat "$log.pid")" 2>/dev/null
  grep -a 'accessibilityContrast=' "$log" | head -1
}

case "$AXIS" in
  dwc)
    case "$VALUE" in on) B=YES ;; off) B=NO ;; *) echo "usage: $0 dwc on|off" >&2; exit 1 ;; esac
    xcrun simctl spawn "$UDID" defaults write com.apple.Accessibility \
      DifferentiateWithoutColor -bool "$B" >/dev/null 2>&1
    reboot_sim
    EXPECT="differentiateWithoutColor=$([ "$B" = YES ] && echo true || echo false)"
    ;;
  contrast)
    case "$VALUE" in on|off) ;; *) echo "usage: $0 contrast on|off" >&2; exit 1 ;; esac
    xcrun simctl ui "$UDID" increase_contrast "$VALUE" >/dev/null 2>&1
    EXPECT="accessibilityContrast=$([ "$VALUE" = on ] && echo 1 || echo 0)"
    ;;
  appearance)
    case "$VALUE" in light|dark) ;; *) echo "usage: $0 appearance light|dark" >&2; exit 1 ;; esac
    xcrun simctl ui "$UDID" appearance "$VALUE" >/dev/null 2>&1
    sleep 5   # simctl needs a beat to settle before a launch sees it
    EXPECT=""
    ;;
  status) EXPECT="" ;;
  *) echo "usage: $0 {dwc|contrast|appearance|status} [value]" >&2; exit 1 ;;
esac

line="$(read_back)"
if [ -z "$line" ]; then
  echo "✗ no probe output — is a DEBUG build installed on $UDID?" >&2
  exit 1
fi
echo "$line"

if [ -n "$EXPECT" ]; then
  if printf '%s' "$line" | grep -q "$EXPECT"; then
    printf '\033[32m✓ app reports %s\033[0m\n' "$EXPECT"
  else
    printf '\033[31m✗ app does NOT report %s — the setting did not reach it.\033[0m\n' "$EXPECT" >&2
    echo "  Do not screenshot this state; it is indistinguishable from 'the app ignores the setting'." >&2
    exit 1
  fi
fi

if [ -n "${SHOT:-}" ]; then
  # Detached: --console-pty takes the app down with the pipe.
  xcrun simctl launch "$UDID" "$BUNDLE_ID" >/dev/null 2>&1
  sleep 6
  xcrun simctl io "$UDID" screenshot "$SHOT" >/dev/null 2>&1
  echo "shot: $SHOT"
fi
