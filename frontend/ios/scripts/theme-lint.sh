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
# WHAT LIVES HERE, AND WHAT DOES NOT
# ----------------------------------
# Only the five FILE-SHAPE rules — 1, 5, 6, 7, 8 — the ones a grep pipeline expresses
# as well as anything could. Rules 2, 3, 4 and 9 needed per-entry reasoning and
# anti-vacuity controls that a shell pipeline cannot express, so they now live in
# backend/tests/test_ios_theme_parity.py, which a PostToolUse hook runs on every themed
# edit. See the stubs below for where each one went; the numbers are left as gaps on
# purpose. One rule, one home.
#
# That module also SHELLS OUT to this script, so running the pytest file runs all nine.
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
  "$(grep -rn --include='*.swift' -E 'Color\(hex:|UIColor\(hexString:' . \
      | grep -v '^./Theme/' \
      | grep -vE '(SearchResultRow|EducationBookCard|SearchBookCard|AIVoiceOrb|PlayAudioButton|CompanyLogoView|BookLibraryView|HomeDashboardModels)\.swift' \
      | sed 's://.*$::' \
      | grep -E 'Color\(hex:|UIColor\(hexString:' \
      | grep -v -i 'gradientcolors\|artworkcolors\|covergradient\|herogradient' \
      || true)"

# ── 2. MOVED → test_ios_theme_parity.py::test_no_bare_swiftui_colours_as_ink_or_opaque_fill
#      The Python port scans the whole argument rather than only the token immediately
#      after the paren, so it catches `.foregroundColor(isOn ? .white : …)`; it adds
#      white/black/primary/secondary and the ViewModels/ + Services/ directories.
#
# ── 3. MOVED → test_ios_theme_parity.py::test_text_tokens_never_sit_on_a_fill
#      Adds the `chipSelectedBackground` / `borderFocus` aliases a `*Fill`-suffix grep
#      cannot see, four ink tokens instead of one, and four directories instead of one.
#
# ── 4. RETIRED, not ported → test_graphic_tokens_never_colour_text
#      This rule asked "is the token outside the chart layer", which was only ever a
#      PROXY for "is it inking text" — and the proxy is what hid nine real violations
#      inside the chart layer, since its own filename exemption list swallowed them.
#      Role is now asserted directly, in both directions: a 3:1 token may not ink a
#      Text/Image/Label (that test), and may not be a surface text sits on (the five
#      *Graphic names are in `_FILL_TOKENS`). A `*Graphic` as a raw `.fill()` anywhere
#      is correct — that IS the graphic role — so location never belonged in the rule.
#
# ── 9. MOVED → test_ios_theme_parity.py::test_cards_with_a_fill_and_a_radius_carry_an_edge
#      Plus a companion the shell could not express at all,
#      `test_a_card_surface_reached_through_a_property_still_carries_an_edge`, for a card
#      fill reached through a `var` rather than a token literal.
#
# The numbers above are deliberately LEFT AS GAPS. Renumbering 5→2, 6→3 … would silently
# invalidate three correct source comments that cite rule numbers (PDFKitView.swift,
# PersonaIcon.swift, ThemeContrastAudit.swift).

# ── 5. drawingGroup rasterisation ─────────────────────────────────────────────
# A .drawingGroup() flattens to a Metal texture that is NOT re-rendered on a
# trait change, so it keeps stale colours across a live appearance flip.
#
# The key may be COMPOSITE. A raster whose colours also depend on Differentiate
# Without Color needs `.id("\(colorScheme)-\(differentiate)")`, and an exact match on
# `.id(colorScheme)` would reject the more-correct key — failing the build for doing
# the right thing. Requiring only that `colorScheme` participates keeps the invariant
# (the raster is re-created on an appearance flip) without dictating the key's shape.
DG_BAD=""
while IFS=: read -r file line _; do
  [ -z "$file" ] && continue
  if ! sed -n "$((line)),$((line + 2))p" "$file" | grep -qE '\.id\([^)]*colorScheme'; then
    DG_BAD="${DG_BAD}${file}:${line}: .drawingGroup() without a colorScheme-keyed .id()"$'\n'
  fi
done < <(grep -rnE "\\.drawingGroup\\(\\)" --include='*.swift' . 2>/dev/null \
           | sed 's://.*$::' | grep '\\.drawingGroup()' || true)
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

# ── 8. Every stored token is audited ──────────────────────────────────────────
# ThemeContrastAudit's completeness check walks AppColors.tokenInventory, a
# HAND-WRITTEN duplicate of the palette — Mirror cannot see static members. So a
# token added to AppColors and forgotten in TokenInventory is invisible to the
# runtime guard and ships unaudited with a green check. Only the source level can
# see that, which is here.
TOKENS_MISSING=""
while read -r name; do
  [ -z "$name" ] && continue
  if ! grep -q "let ${name} = AppColors\.${name}" Theme/AppTheme.swift; then
    TOKENS_MISSING="${TOKENS_MISSING}AppColors.${name} is not in TokenInventory"$'\n'
  elif ! grep -q "TokenSpec(\"${name}\"" Theme/AppTheme.swift; then
    TOKENS_MISSING="${TOKENS_MISSING}AppColors.${name} is not in auditManifest"$'\n'
  fi
done < <(sed -n '/^struct AppColors {/,/^    static let auditManifest/p' Theme/AppTheme.swift \
           | grep -oE '^    static let [A-Za-z0-9_]+ = Color\(' \
           | sed -E 's/^    static let ([A-Za-z0-9_]+) = Color\($/\1/' || true)
report "every stored colour token is in TokenInventory AND auditManifest" \
  "the runtime audit cannot see a token you forgot to declare — it prints a green check anyway" \
  "$(printf '%s' "$TOKENS_MISSING")"

# ── 9. No bare card fill ──────────────────────────────────────────────────────
# `.background(AppColors.cardBackground)` paints a card's FILL with no shape, no
# `cardEdge` and no shadow. In DARK that separates from the page on fill alone and
# looks perfectly fine, which is why it passes review; in LIGHT it is #FFFFFF on the
# #F4F5F8 page — 1.09:1 with no edge, i.e. an invisible card.
#
# This is exactly what `.cardSurface()` / `.cardFill()` exist to prevent, and the
# notification rows shipped with it because no guard could see it: the runtime
# ThemeContrastAudit resolves DECLARED tokens against DECLARED surfaces and has no
# view of how a token is applied. Only a source scan can.
#
# `cardBackgroundNested` is exempt: it is the correct fill for a card inside a card,
# and its call sites are already inside a shaped container.
# Comment lines are stripped, and everything from the first `#Preview` to EOF is
# skipped: a preview's own backdrop is not a card, and the comment next to this very
# fix names the token it forbids.
BARE_FILL="$(find Views -name '*.swift' -print0 2>/dev/null \
  | xargs -0 awk '
      FNR==1 { inpreview=0 }
      /^[[:space:]]*#Preview/ { inpreview=1 }
      inpreview { next }
      /^[[:space:]]*\/\// { next }
      /\.background\(AppColors\.cardBackground\)/ { print FILENAME ":" FNR ":" $0 }
    ' || true)"
# GATED on the alert surfaces, REPORTED elsewhere.
#
# 24 pre-existing sites live in settings screens and report sections. Some are rows
# inside an already-shaped group and are therefore fine; each needs looking at in
# light mode before it is touched, and turning them all red today would break the
# gate for work nobody has reviewed. So the guard fails for the surfaces that have
# been through it, and prints the rest as a backlog with a count.
ALERT_SURFACES='Views/(Organisms/(Alerts|NotificationInbox)|Molecules/(ActivityRow|AlertCardView|PriceAlertRuleRow))'
BARE_FILL_GATED="$(printf '%s' "$BARE_FILL" | grep -E "$ALERT_SURFACES" || true)"
BARE_FILL_BACKLOG="$(printf '%s' "$BARE_FILL" | grep -vE "$ALERT_SURFACES" || true)"
report "no bare .background(AppColors.cardBackground) on the alert surfaces" \
  "a card fill with no shape or edge is invisible in light mode (1.09:1) — use .cardSurface()" \
  "$BARE_FILL_GATED"
if [ -n "$BARE_FILL_BACKLOG" ]; then
  printf '\033[33m  note\033[0m %s other bare card fill(s) outside the alert surfaces — check each in LIGHT mode\n' \
    "$(printf '%s\n' "$BARE_FILL_BACKLOG" | grep -c ':')"
fi

echo
if [ "$FAILED" -eq 0 ]; then
  printf '\033[32mtheme-lint: clean\033[0m\n'
else
  printf '\033[31mtheme-lint: violations found\033[0m\n'
fi
exit "$FAILED"
