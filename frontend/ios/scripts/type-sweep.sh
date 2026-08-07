#!/usr/bin/env bash
#
# type-sweep.sh — drive the Simulator through every Dynamic Type content-size category
# and read back what `AppTypography` actually resolved to.
#
# WHY THIS EXISTS
# ---------------
# `AppTypography`'s 40 tokens scale via `UIFontMetrics` under the `readingCap` (1.4) and
# `dataCap` (1.25) clamps in Theme/AppTheme.swift. `UIFontMetrics` reads a PROCESS-level
# UIKit trait, so the only way to exercise the shipping mechanism is to change the
# simulator's content size and relaunch. Before this script there was no way to do that
# in this repo: `xcrun simctl` appeared in zero .sh and zero .md files, and the only
# mention of `content_size` was a bare comment in TypographyProbe.swift.
#
# Companion to theme-lint.sh (usage rules) and ThemeContrastAudit (palette). This one
# covers the third axis: RESOLUTION — what the app ended up rendering at.
#
# Usage:
#   ./frontend/ios/scripts/type-sweep.sh                 # sweep all 12 categories
#   ./frontend/ios/scripts/type-sweep.sh large AX5       # only these
#   SHOT=1 ./frontend/ios/scripts/type-sweep.sh AX3      # also capture screenshots
#   CAYDEX_SIM_UDID=<udid> ./frontend/ios/scripts/type-sweep.sh
#
# Exit: 0 all categories reported, 1 a set failed / no probe output / wrong build.
#
# ─────────────────────────────────────────────────────────────────────────────────────
# FOUR TRAPS, ENCODED BELOW RATHER THAN LEFT IN A COMMENT. Each one cost real time and
# each one FAILS SILENTLY — they produce plausible numbers, not errors.
#
#  1. PIN THE UDID. Three simulators on this machine are named "iPhone 17 Pro". A
#     name-based `-destination` picks an arbitrary one, so you can set content_size on a
#     device the app is not running on and measure a stale category forever. This script
#     refuses to run against a UDID that is not Booted, and prints the device list.
#
#  2. NEVER `killall cfprefsd`. The old recipe for forcing a preference re-read also
#     silently breaks the simulator's OS-appearance propagation: `simctl ui appearance`
#     keeps reading back correctly while newly-created scenes resolve the other way and
#     live flips deliver ZERO trait changes. Use shutdown + boot, which is what
#     `--reboot` does here.
#
#  3. READ BACK EVERY SET. `simctl ui <udid> content_size <x>` can no-op, and it does not
#     say so. Every set is followed by a get and compared.
#
#  4. TRUST ONLY TWO LINES OF PROBE OUTPUT. TypographyProbe prints rendered heights using
#     SwiftUI's `.dynamicTypeSize()` override, which `UIFontMetrics` CANNOT see — so
#     those rows print "FIXED" for AppTypography even when it is working perfectly. They
#     are a measurement of SwiftUI, not of this app. The only trustworthy signal is the
#     `SYSTEM category=` / `AppTypography resolved:` pair. This script greps for exactly
#     those two and FAILS if it found only the height rows, so the harness can never
#     "confirm" the bug it exists to disprove.
#
# Plus one that is merely annoying: `simctl launch --console-pty` kills the app when the
# pipe dies, so the screenshot pass has to launch detached.

set -uo pipefail

BUNDLE_ID="com.phan.caydex"
UDID="${CAYDEX_SIM_UDID:-57C9097B-08F1-4CB1-BF9A-035876F3604F}"
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"

# The twelve valid arguments to `simctl ui <udid> content_size`, smallest to largest.
# Recorded HERE because they are documented nowhere else in this repo and simctl rejects
# anything else without explaining what it wanted. `increment` / `decrement` are also
# accepted (relative to current) but are useless for a reproducible sweep.
ALL_CATEGORIES=(
  extra-small small medium large extra-large extra-extra-large extra-extra-extra-large
  accessibility-medium accessibility-large accessibility-extra-large
  accessibility-extra-extra-large accessibility-extra-extra-extra-large
)

# Convenience aliases so "AX5" works as well as the mouthful.
# A `case`, not an associative array: macOS ships bash 3.2, where `declare -A` is not a
# thing and silently degrades to an INDEXED array — `${ALIAS[AX5]}` then evaluates the
# subscript arithmetically, which under `set -u` aborts with "unbound variable". Every
# script in this repo has to stay 3.2-clean for the same reason.
expand_alias() {
  case "$1" in
    default) echo large ;;
    AX1) echo accessibility-medium ;;
    AX2) echo accessibility-large ;;
    AX3) echo accessibility-extra-large ;;
    AX4) echo accessibility-extra-extra-large ;;
    AX5) echo accessibility-extra-extra-extra-large ;;
    *)   echo "$1" ;;
  esac
}

if [ "$#" -gt 0 ]; then
  CATEGORIES=()
  for a in "$@"; do CATEGORIES+=("$(expand_alias "$a")"); done
else
  CATEGORIES=("${ALL_CATEGORIES[@]}")
fi

# Guard the alias expansion against a typo: every category must be one simctl accepts,
# or the run measures nothing and the `SET FAILED` line below is the only clue.
for c in "${CATEGORIES[@]}"; do
  ok=0
  for v in "${ALL_CATEGORIES[@]}"; do [ "$c" = "$v" ] && ok=1; done
  if [ "$ok" -eq 0 ]; then
    echo "✗ '$c' is not a content_size category. Valid values:" >&2
    printf '    %s\n' "${ALL_CATEGORIES[@]}" >&2
    echo "    (aliases: default AX1 AX2 AX3 AX4 AX5)" >&2
    exit 1
  fi
done

OUT_DIR="${TMPDIR:-/tmp}/caydex-type-sweep"
mkdir -p "$OUT_DIR"
FAILED=0

# ── Trap 1: the UDID must be pinned AND booted ────────────────────────────────
if ! xcrun simctl list devices | grep -q "$UDID.*Booted"; then
  echo "✗ $UDID is not Booted. Devices named 'iPhone 17 Pro' on this machine:" >&2
  xcrun simctl list devices available | grep -i "iPhone 17 Pro" >&2
  echo >&2
  echo "  Pin one explicitly:  CAYDEX_SIM_UDID=<udid> $0" >&2
  echo "  A name-based -destination picks an ARBITRARY one of these, which is how you" >&2
  echo "  end up setting content_size on a device the app is not running on." >&2
  exit 1
fi

# ── Trap 2: shutdown+boot, never `killall cfprefsd` ───────────────────────────
reboot_sim() {
  xcrun simctl shutdown "$UDID" >/dev/null 2>&1
  xcrun simctl boot "$UDID" >/dev/null 2>&1
  xcrun simctl bootstatus "$UDID" -b >/dev/null 2>&1
}

printf '\033[1mtype-sweep\033[0m  udid=%s  categories=%d\n\n' "$UDID" "${#CATEGORIES[@]}"
printf '%-38s %s\n' "CATEGORY" "AppTypography resolved (body / captionTiny / dataSmall / iconDefault)"
printf '%-38s %s\n' "--------" "---------------------------------------------------------------------"

for cat in "${CATEGORIES[@]}"; do
  xcrun simctl ui "$UDID" content_size "$cat" >/dev/null 2>&1

  # ── Trap 3: a set can no-op, and says nothing ───────────────────────────────
  got=$(xcrun simctl ui "$UDID" content_size 2>/dev/null | tr -d '[:space:]')
  if [ "$got" != "$cat" ]; then
    printf '\033[31m✗ %-36s SET FAILED — wanted %s, device reports %s\033[0m\n' "$cat" "$cat" "$got"
    FAILED=1
    continue
  fi

  xcrun simctl terminate "$UDID" "$BUNDLE_ID" >/dev/null 2>&1
  log="$OUT_DIR/$cat.log"
  # `--console-pty` dies with the pipe; that is fine here — we only need the probe
  # lines, which print inside the root .task during launch.
  ( xcrun simctl launch --console-pty "$UDID" "$BUNDLE_ID" >"$log" 2>&1 & echo $! >"$OUT_DIR/pid" )
  for _ in $(seq 1 40); do
    grep -aq 'AppTypography resolved:' "$log" 2>/dev/null && break
    sleep 0.5
  done
  kill "$(cat "$OUT_DIR/pid")" 2>/dev/null

  # ── Trap 4: only two lines are trustworthy ──────────────────────────────────
  system=$(grep -a 'SYSTEM category=' "$log" | head -1 | sed 's/.*SYSTEM category=//')
  resolved=$(grep -a 'AppTypography resolved:' "$log" | head -1 | sed 's/.*resolved: *//')

  if [ -z "$resolved" ] || [ -z "$system" ]; then
    if grep -aq 'AppTypography.body' "$log"; then
      printf '\033[31m✗ %-36s found ONLY the rendered-height rows.\033[0m\n' "$cat"
      echo "    Those use SwiftUI .dynamicTypeSize(), which UIFontMetrics cannot see —" >&2
      echo "    they print FIXED even when the tokens work. Not a valid measurement." >&2
    else
      printf '\033[31m✗ %-36s no probe output. Is this a DEBUG build? (%s)\033[0m\n' "$cat" "$log"
    fi
    FAILED=1
    continue
  fi

  printf '%-38s %s\n' "$cat" "$resolved"
  printf '  \033[2m└ system trait: %s\033[0m\n' "$system"

  if [ -n "${SHOT:-}" ]; then
    # Detached, because --console-pty would take the app down with the pipe.
    xcrun simctl launch "$UDID" "$BUNDLE_ID" >/dev/null 2>&1
    sleep 4
    xcrun simctl io "$UDID" screenshot "$OUT_DIR/$cat.png" >/dev/null 2>&1
    printf '  \033[2m└ shot: %s\033[0m\n' "$OUT_DIR/$cat.png"
  fi
done

echo
echo "Logs: $OUT_DIR"
echo
echo "WHAT TO LOOK FOR: 'AppTypography resolved' must GROW as the category grows, and"
echo "must PLATEAU at readingCap×base / dataCap×base (body 15→21.0, captionTiny 9→12.6,"
echo "dataSmall 10→12.5, iconDefault 16→20.0). A flat row across all categories means"
echo "the token layer regressed to fixed sizes. Values above the plateau mean a cap was"
echo "raised without the layout sweep that gates it."
echo
if [ "$FAILED" -eq 0 ]; then
  printf '\033[32mtype-sweep: all categories reported\033[0m\n'
else
  printf '\033[31mtype-sweep: failures above\033[0m\n'
fi

# Leave the simulator at the default so the next person's manual testing is not confused
# by a machine silently stuck at AX5.
xcrun simctl ui "$UDID" content_size large >/dev/null 2>&1

exit "$FAILED"
