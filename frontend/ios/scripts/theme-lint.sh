#!/usr/bin/env bash
#
# theme-lint.sh — enforce the theming rules that ThemeContrastAudit cannot.
#
# The runtime audit proves the PALETTE is sound: every token clears its WCAG
# floor in both appearances. It says so in its own header, and it is right —
# it cannot see USAGE. It has no idea that some view renders white text on an
# adaptive card, or hardcodes a hex, because those facts live in source, not in
# a resolved UIColor.
#
# This closes that half. Every rule below is one that was violated in the real
# codebase and shipped, and every one is documented in
# .claude/rules/ios-swiftui.md.
#
# Usage:  ./frontend/ios/scripts/theme-lint.sh
# Exit:   0 clean, 1 violations found.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../ios" && pwd)"
cd "$ROOT" || exit 1

FAILED=0
report() {                       # report <title> <explanation> <matches>
  local title="$1" why="$2" matches="$3"
  local n
  n=$(printf '%s' "$matches" | grep -c . || true)
  if [ "$n" -gt 0 ]; then
    printf '\n\033[31m✗ %s (%s)\033[0m\n  %s\n' "$title" "$n" "$why"
    printf '%s\n' "$matches" | sed 's/^/    /'
    FAILED=1
  else
    printf '\033[32m✓\033[0m %s\n' "$title"
  fi
}

# ── 1. Raw hex outside the palette ────────────────────────────────────────────
# A Color(hex:) is frozen: it cannot adapt, so it is wrong in one mode by
# construction. Brand trade dress is the one legitimate exception.
report "no Color(hex:) outside Theme/" \
  "frozen colours can't adapt — declare a token in AppTheme.swift instead" \
  "$(grep -rn --include='*.swift' 'Color(hex:' . \
      | grep -v '^./Theme/' \
      | grep -vE '(SearchResultRow|EducationBookCard|SearchBookCard|AIVoiceOrb|PlayAudioButton|CompanyLogoView|BookLibraryView|AudioPlayerModels|AudioManager|HomeDashboardModels)\.swift' \
      | grep -v -i 'gradientcolors\|artworkcolors\|covergradient\|herogradient\|// ' \
      || true)"

# ── 2. SwiftUI system colours ─────────────────────────────────────────────────
# Apple's tints, not this palette. Measured on white: .cyan 2.54, .orange 2.20,
# .green 2.22, .yellow 1.51 — all fail. They look adaptive and are not ours.
report "no SwiftUI system colours in Views/ or Models/" \
  "Apple's tints fail this app's contrast floors (.yellow is 1.51:1 on white)" \
  "$(grep -rnE '(foregroundColor|foregroundStyle|\.fill|\.stroke|\.tint)\(\s*(Color\.)?\.(gray|cyan|yellow|orange|green|red|blue|purple|pink|indigo|mint|teal|brown)\b' \
      --include='*.swift' Views/ Models/ Core/ 2>/dev/null || true)"

# ── 3. On-accent ink ──────────────────────────────────────────────────────────
# `.white` on a *Fill is correct behaviour with the wrong token; textPrimary on
# a *Fill is an actual bug — it inverts with the appearance and fails in one.
report "textPrimary never sits on a *Fill token" \
  "textPrimary inverts per mode; on a saturated fill use AppColors.textOnAccent" \
  "$(grep -rn --include='*.swift' -B6 'AppColors\.\(primary\|gain\|loss\|caution\|accentCyan\)Fill' Views/ 2>/dev/null \
      | grep 'foregroundColor(AppColors.textPrimary)\|foregroundStyle(AppColors.textPrimary)' || true)"

# ── 4. Graphic tokens must stay in the chart layer ────────────────────────────
# *Graphic clears 3:1, not 4.5:1. Correct for a stroke, illegal for text.
report "*Graphic tokens confined to the chart layer" \
  "graphic tokens clear 3:1, not the 4.5:1 text floor" \
  "$(grep -rn --include='*.swift' 'AppColors\.\(gain\|loss\|caution\|accent\|primary\)Graphic' Views/ Models/ Core/ 2>/dev/null \
      | grep -v 'Views/Molecules/Chart/' \
      | grep -v 'Models/ChartModels.swift' \
      | grep -vE '(Sparkline|ChartView|Chart\.swift|MiniStockChart|RadarChart|Meter|Gauge|FlowChart|TimelineChart|MiniChart|BreakdownChart|LevelIndicator|ThresholdZones|SemiCircleGauge|StarRatingView|MetricCard)' \
      || true)"

# ── 5. drawingGroup rasterisation ─────────────────────────────────────────────
# A .drawingGroup() flattens to a Metal texture that is NOT re-rendered on a
# trait change, so it keeps stale colours across a live appearance flip.
DG_BAD=""
while IFS=: read -r file line _; do
  [ -z "$file" ] && continue
  if ! sed -n "$((line)),$((line + 2))p" "$file" | grep -q '\.id(colorScheme)'; then
    DG_BAD="${DG_BAD}${file}:${line}: .drawingGroup() without .id(colorScheme)"$'\n'
  fi
done < <(grep -rnE "^[^/]*[^/]\\.drawingGroup\\(\\)" --include='*.swift' . 2>/dev/null | grep -v '//' || true)
report "every .drawingGroup() is keyed on colorScheme" \
  "a Metal raster keeps stale colours across a LIVE appearance flip" \
  "$(printf '%s' "$DG_BAD")"

# ── 6. Divider tinting is inert ───────────────────────────────────────────────
# Divider draws UIColor.separator OVER its background, so .background() on it
# does nothing. Someone "fixing" a divider there changes nothing and concludes
# the token is broken.
report "no inert Divider().background()" \
  "Divider draws UIColor.separator over it — use .overlay(AppColors.divider)" \
  "$(grep -rn --include='*.swift' -A1 'Divider()' . 2>/dev/null \
      | grep -v '^./Theme/' \
      | grep '\.background(' || true)"

# ── 7. Opaque brand assets need a clipped mark ────────────────────────────────
# CaydexLogo.png is an opaque #171B26 tile; drawn bare on a light page it is a
# dark square. CaydexLogoMark exists to clip it.
report "CaydexLogo rendered only via CaydexLogoMark" \
  "the PNG is an opaque #171B26 tile — bare, it is a dark square on a light page" \
  "$(grep -rn --include='*.swift' 'Image("CaydexLogo")' . 2>/dev/null \
      | grep -v 'Views/Atoms/CaydexLogoMark.swift' || true)"

echo
if [ "$FAILED" -eq 0 ]; then
  printf '\033[32mtheme-lint: clean\033[0m\n'
else
  printf '\033[31mtheme-lint: violations found\033[0m\n'
fi
exit "$FAILED"
