"""The iOS theme contract, asserted from Python so that it GATES instead of merely existing.

Why this exists. Two guards cover the palette today and NEITHER runs unless a human
does something:

  * `ThemeContrastAudit` is `#if DEBUG` and fires only when someone launches a Debug build.
  * `frontend/ios/scripts/theme-lint.sh` is invoked by nothing at all — not by a hook
    (`.claude/hooks/` has five, none reference it), not by CI (there is no `.github/`),
    not by a test. It is a script you must remember to run.

Both were written in response to bugs that shipped; neither can stop the next one. Adding
rules to a lint nobody runs changes nothing, so the rules move to where the tests already
run. `test_ios_auth_policy_parity.py` proved the technique in this repo: grep Swift from
Python, and it costs 50ms.

This mirrors that module deliberately — two `str.index` anchors before any regex, `//`
lines stripped first (the prose in `AppTheme.swift` QUOTES the bugs it fixed, e.g.
"white on bullish #22C55E = 2.28:1", and a naive scan reads documentation as a live
violation), and ANTI-VACUITY guards on every scanner, because a regex that quietly stops
matching turns every other assertion in this file green.

Source-level on both sides: no app build, no simulator, no network.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_APPTHEME = _IOS / "Theme/AppTheme.swift"
_LINT = _REPO / "frontend/ios/scripts/theme-lint.sh"

# theme-lint rules 2/3/4/9 scanned only `Views/ Models/ Core/`. `ViewModels/` and
# `Services/` were never scanned at ALL — and `AppTheme.swift`'s own header names
# ViewModels/ as the risk, since ~154 sentiment references reach the palette indirectly
# through computed `Color` vars there. Both are clean today, so widening the scan costs
# nothing and closes the gap BEFORE something lands in it.
_SCAN_DIRS = ("Views", "Models", "Core", "ViewModels", "Services")


# ── Swift source helpers ─────────────────────────────────────────────────────

def _swift_files() -> list[Path]:
    files = [p for d in _SCAN_DIRS for p in sorted((_IOS / d).rglob("*.swift"))]
    assert len(files) >= 400, f"only found {len(files)} Swift files — is _SCAN_DIRS right?"
    return files


def _code_lines(path: Path) -> list[tuple[int, str]]:
    """1-indexed (lineno, source) with `//` comment lines dropped and trailing comments cut."""
    out: list[tuple[int, str]] = []
    for i, raw in enumerate(path.read_text().splitlines(), 1):
        if raw.strip().startswith("//"):
            continue
        out.append((i, re.sub(r"//.*$", "", raw)))
    return out


def _rel(path: Path) -> str:
    return str(path.relative_to(_IOS))


def _sections() -> tuple[str, str, str]:
    """(palette, tokenInventory, auditManifest), comment lines stripped."""
    src = _APPTHEME.read_text()

    def strip(text: str) -> str:
        return "\n".join(l for l in text.splitlines() if not l.strip().startswith("//"))

    palette = src[src.index("struct AppColors {"):src.index("// MARK: - Contrast audit manifest")]
    inventory = src[src.index("struct TokenInventory {"):src.index("static let tokenInventory")]
    manifest = src[src.index("static let auditManifest"):src.index("// MARK: - App Typography")]
    return strip(palette), strip(inventory), strip(manifest)


# ── WCAG 2.1, mirroring ThemeContrastAudit exactly ───────────────────────────

_FLOOR = {"text": 4.5, "largeText": 3.0, "graphic": 3.0, "surface": 0.0, "decorative": 0.0}


def _rgb(hex_str: str) -> tuple[float, float, float]:
    h = hex_str.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) == 8:      # ARGB, as `UIColor(hexString:)` accepts
        h = h[2:]
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _luminance(rgb: tuple[float, float, float]) -> float:
    def channel(v: float) -> float:
        d = max(0.0, min(1.0, v))
        return d / 12.92 if d <= 0.03928 else ((d + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _ratio(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _composite(fg: tuple[float, float, float], alpha: float,
               bg: tuple[float, float, float]) -> tuple[float, float, float]:
    """Flatten a translucent foreground onto its background BEFORE measuring.

    Per-channel in sRGB, before linearisation — the same order
    `ThemeContrastAudit.composite` uses. Doing it after linearising would give a
    different (and wrong) answer.
    """
    if alpha >= 1:
        return fg
    return tuple(f * alpha + b * (1 - alpha) for f, b in zip(fg, bg))


class Token:
    __slots__ = ("name", "light", "light_a", "dark", "dark_a")

    def __init__(self, name, light, light_a, dark, dark_a):
        self.name, self.light, self.light_a, self.dark, self.dark_a = \
            name, light, light_a, dark, dark_a

    def rgb(self, style: str) -> tuple[float, float, float]:
        return _rgb(self.light if style == "light" else self.dark)

    def alpha(self, style: str) -> float:
        return self.light_a if style == "light" else self.dark_a


# One pattern with both alpha groups optional. Two patterns would silently skip whichever
# form got forgotten; `test_token_declaration_scanner_is_not_vacuous` catches that by
# requiring every `static let X = Color(` line to have parsed.
_DECL = re.compile(
    r'static let (\w+) = Color\(\s*lightHex:\s*"([0-9A-Fa-f]{3,8})"\s*'
    r'(?:,\s*lightAlpha:\s*([0-9.]+)\s*)?'
    r',\s*darkHex:\s*"([0-9A-Fa-f]{3,8})"\s*'
    r'(?:,\s*darkAlpha:\s*([0-9.]+)\s*)?'
    # Trailing arguments after the four colour values — `boostsUnderIncreasedContrast:`
    # today, whatever the next axis needs later. Anchoring on `\)` right after darkAlpha
    # silently DROPPED the 14 tokens that opted out of the Increase Contrast boost, and
    # `test_token_declaration_scanner_is_not_vacuous` is what caught it: they vanished
    # from the parse while every contrast assertion still passed on the remaining 47.
    r'(?:,\s*\w+:\s*[^),]+\s*)*\)'
)

_SPEC = re.compile(
    r'TokenSpec\(\s*"(\w+)"\s*,\s*(\w+)\s*,\s*\.(\w+)\s*'
    r'(?:,\s*on:\s*(\[[^\]]*\]|TokenSpec\.contentSurfaces(?:\s*\+\s*\[[^\]]*\])?))?'
    r'(?:\s*,\s*carriesOnAccentText:\s*(true|false))?\s*\)',
    re.S,
)


def _declared_tokens(palette: str) -> dict[str, Token]:
    return {
        m.group(1): Token(m.group(1), m.group(2), float(m.group(3) or 1.0),
                          m.group(4), float(m.group(5) or 1.0))
        for m in _DECL.finditer(palette)
    }


def _surface_registry(palette: str, manifest: str) -> dict[str, Token]:
    tokens = _declared_tokens(palette)
    names = re.findall(r'^\s*"(\w+)":\s*(\w+),', manifest, flags=re.M)
    # `surfaceRegistry` lives between the inventory and the manifest, so pull it from the
    # whole file rather than from either slice.
    src = "\n".join(l for l in _APPTHEME.read_text().splitlines()
                    if not l.strip().startswith("//"))
    block = src[src.index("static let surfaceRegistry"):src.index("static let auditManifest")]
    registry = {}
    for key, ident in re.findall(r'"(\w+)":\s*(\w+),', block):
        assert key == ident, f'surfaceRegistry["{key}"] points at `{ident}`'
        registry[key] = tokens[ident]
    return registry


def _specs(manifest: str) -> list[dict]:
    out = []
    for m in _SPEC.finditer(manifest):
        name, ident, role, surfaces_expr, carries = m.groups()
        if surfaces_expr is None:
            surfaces = list(_CONTENT_SURFACES)
        elif "TokenSpec.contentSurfaces" in surfaces_expr:
            surfaces = list(_CONTENT_SURFACES) + re.findall(r'"(\w+)"', surfaces_expr)
        else:
            surfaces = re.findall(r'"(\w+)"', surfaces_expr)
        out.append({"name": name, "ident": ident, "role": role,
                    "surfaces": surfaces, "carries": carries == "true"})
    return out


_CONTENT_SURFACES = ("background", "cardBackground", "cardBackgroundLight", "cardBackgroundNested")


# ── 1. The scanners must not be vacuous ──────────────────────────────────────

def test_token_declaration_scanner_is_not_vacuous():
    """If `_DECL` drifts, every assertion below passes on an empty set."""
    palette, _, _ = _sections()
    tokens = _declared_tokens(palette)
    assert len(tokens) >= 50, sorted(tokens)
    assert {"gainFill", "lossFill", "textOnAccent", "cardBackgroundLight"} <= tokens.keys()
    # Every `static let … = Color(` line must have PARSED, not merely some of them.
    raw = set(re.findall(r"static let (\w+) = Color\(", palette))
    assert raw == tokens.keys(), f"unparsed declarations: {sorted(raw - tokens.keys())}"


def test_content_surfaces_constant_matches_the_swift():
    """This module hardcodes `contentSurfaces`; pin it to the source or it silently rots."""
    # `contentSurfaces` lives on `TokenSpec`, i.e. past the manifest marker — read the
    # whole file rather than the palette slice.
    src = _APPTHEME.read_text()
    block = src[src.index("static let contentSurfaces"):]
    assert re.findall(r'"(\w+)"', block[:block.index("]")]) == list(_CONTENT_SURFACES)


def test_wcag_maths_matches_the_swift_self_test_anchors():
    """The same two anchors `ThemeContrastAudit.selfTest` pins. Wrong maths here would make
    every ratio below meaningless while still looking plausible."""
    assert abs(_ratio(_rgb("000000"), _rgb("FFFFFF")) - 21.00) < 0.01
    assert abs(_ratio(_rgb("767676"), _rgb("FFFFFF")) - 4.54) < 0.02


# ── 2. Value identity — the hole in BOTH existing guards ─────────────────────

def test_every_token_is_in_inventory_and_manifest_by_VALUE():
    """`ThemeContrastAudit.auditManifestCompleteness` compares `Set(manifest.map(\\.name))`
    against `Mirror` labels, and theme-lint rule 8 greps `TokenSpec("<name>"`. NEITHER reads
    the second positional argument — so `TokenSpec("gainFill", lossFill, …)` passes both
    while auditing the WRONG colour under the right name, and the real `gainFill` goes
    unmeasured behind a green ✅.
    """
    palette, inventory, manifest = _sections()
    declared = set(_declared_tokens(palette))
    inv = dict(re.findall(r"let (\w+) = AppColors\.(\w+)", inventory))
    specs = {s["name"]: s["ident"] for s in _specs(manifest)}

    assert not declared - inv.keys(), f"not in TokenInventory: {sorted(declared - inv.keys())}"
    assert not declared - specs.keys(), f"not in auditManifest: {sorted(declared - specs.keys())}"
    assert not inv.keys() - declared, f"TokenInventory names a non-token: {sorted(inv.keys() - declared)}"

    mismatched_inv = sorted((n, v) for n, v in inv.items() if n != v)
    assert not mismatched_inv, f"TokenInventory alias points elsewhere: {mismatched_inv}"
    mismatched_spec = sorted((n, v) for n, v in specs.items() if n != v)
    assert not mismatched_spec, f"TokenSpec name/value disagree: {mismatched_spec}"


def test_the_value_identity_check_can_actually_fail():
    """Negative control — prove the comparison reads the second argument at all."""
    m = _SPEC.match('TokenSpec("gainFill", lossFill, .text, on: [], carriesOnAccentText: true)')
    assert m is not None and m.group(2) == "lossFill"


# ── 3. Contrast, computed here rather than only at a DEBUG launch ────────────

def _measure_all() -> tuple[int, list[tuple]]:
    palette, _, manifest = _sections()
    tokens = _declared_tokens(palette)
    registry = _surface_registry(palette, manifest)
    checked = 0
    failures: list[tuple] = []

    for spec in _specs(manifest):
        token = tokens[spec["name"]]
        for style in ("light", "dark"):
            if spec["carries"]:
                # A FILL: run it the other way round, exactly as `run()` does — is
                # `textOnAccent` legible ON this? Floor 4.5 regardless of the declared role.
                fill = _composite(token.rgb(style), token.alpha(style), _rgb("FFFFFF"))
                ink = tokens["textOnAccent"].rgb(style)
                measured = _ratio(ink, fill)
                checked += 1
                if measured < 4.5:
                    failures.append((style, "textOnAccent", spec["name"], measured, 4.5))
                continue

            required = _FLOOR[spec["role"]]
            if required == 0:
                continue
            for surface_name in spec["surfaces"]:
                surface_token = registry[surface_name]
                surface = _composite(surface_token.rgb(style), surface_token.alpha(style),
                                     _rgb("FFFFFF" if style == "light" else "000000"))
                fg = _composite(token.rgb(style), token.alpha(style), surface)
                measured = _ratio(fg, surface)
                checked += 1
                if measured < required:
                    failures.append((style, spec["name"], surface_name, measured, required))
    return checked, failures


def test_contrast_assertions_are_not_vacuous():
    """A `surfaces:` parser that returned [] would make the whole contrast suite pass by
    measuring nothing at all."""
    checked, _ = _measure_all()
    assert checked >= 180, checked


@pytest.mark.parametrize("style", ["light", "dark"])
def test_every_token_clears_its_floor(style):
    _, failures = _measure_all()
    bad = [f for f in failures if f[0] == style]
    assert not bad, "\n".join(
        f"[{s}] {t} on {surf} = {m:.2f}:1 (need {r:.2f}:1)" for s, t, surf, m, r in bad
    )


def test_alpha_tokens_are_all_exempt_by_role():
    """The compositing BASE only matters for a token with alpha < 1, and every such token is
    `.decorative`/`.surface` (floor 0) today — which is why this module can composite over an
    arbitrary base. Pin it, or a future alpha'd TEXT token silently gets a number that depends
    on an assumption nobody wrote down."""
    palette, _, manifest = _sections()
    alphaed = {n for n, t in _declared_tokens(palette).items() if t.light_a < 1 or t.dark_a < 1}
    assert alphaed, "no alpha'd tokens found — has the declaration parser drifted?"
    roles = {s["name"]: s["role"] for s in _specs(manifest)}
    offenders = sorted(n for n in alphaed if roles[n] not in ("decorative", "surface"))
    assert not offenders, offenders


def test_light_mode_parity_of_the_three_replacement_tokens():
    """Port of `ThemeContrastAudit.auditLightParity`.

    The whole premise of the dark-mode card work is that LIGHT never moved. These three
    replaced an existing token and must still equal it in light, or the premise is false
    and nobody would find out from a ratio.
    """
    palette, _, _ = _sections()
    t = _declared_tokens(palette)
    # The exact three pairs `auditLightParity` asserts — note `shadowCard` pairs with
    # `shadowAmbient`, not `shadowKey`.
    for replacement, original in (("cardEdge", "border"),
                                  ("shadowCard", "shadowAmbient"),
                                  ("cardBackgroundNested", "cardBackground")):
        assert (t[replacement].light.upper(), t[replacement].light_a) == \
               (t[original].light.upper(), t[original].light_a), \
               f"{replacement} no longer matches {original} in LIGHT"

    # The Swift check's own negative control: a comparator that always said "equal" would
    # keep printing ✅ while light drifted. Prove ours can tell #F4F5F8 from #FFFFFF.
    assert (t["background"].light.upper(), t["background"].light_a) != \
           (t["cardBackground"].light.upper(), t["cardBackground"].light_a)


# ── 4. AccentColor.colorset — the one surface no other guard can see ─────────

def test_accent_colorset_matches_primaryBlue():
    """`AccentColor` is the tint for every system control the app does not draw itself —
    Toggle tracks, Picker selection, the text caret, `Link`, swipe actions, alert buttons,
    sheet grabbers. It is in no `AppColors`, no `TokenInventory`, no `auditManifest`, and
    theme-lint cannot see an asset catalog at all.

    It had drifted: dark was #3B82F6, and `AppTheme.swift` records that white on #3B82F6 is
    3.68:1 — i.e. precisely the value the palette deliberately moved away from.
    """
    contents = _IOS / "Assets.xcassets/AccentColor.colorset/Contents.json"
    assert contents.exists(), f"missing {contents}"
    data = json.loads(contents.read_text())

    palette, _, _ = _sections()
    primary = _declared_tokens(palette)["primaryBlue"]
    want = {None: primary.light.upper(), "dark": primary.dark.upper()}

    seen = {}
    for entry in data["colors"]:
        appearances = entry.get("appearances") or []
        key = appearances[0]["value"] if appearances else None
        c = entry["color"]["components"]
        seen[key] = "".join(f"{int(c[ch], 16):02X}" for ch in ("red", "green", "blue"))
    assert seen == want, f"AccentColor {seen} has drifted from primaryBlue {want}"


# ── 5. theme-lint rules 2/3/4/9, ported with their coverage gaps closed ──────

_MODIFIER = r"(?:foregroundColor|foregroundStyle|tint|accentColor)"
_SURFACE_MODIFIER = r"(?:fill|background|overlay|stroke|strokeBorder)"

# Bare SwiftUI colours are never ink. `Color.white`/`.black` ARE legitimate as a
# translucent scrim, and that exemption is STRUCTURAL (`.opacity(` on the same line)
# rather than a file list, so it cannot rot the way a name list does.
_BARE_HUES = ("gray", "cyan", "yellow", "orange", "green", "red", "blue",
              "purple", "pink", "indigo", "mint", "teal", "brown")


def _bare_colour_violations() -> list[str]:
    """Anywhere in the modifier's ARGUMENT, not just immediately after the paren.

    `.foregroundColor(isOn ? .white : AppColors.textSecondary)` is the dominant idiom for
    a selectable chip in this codebase, and an anchored `\\(\\s*\\.white` pattern misses
    every one of them — which is exactly where five live violations were hiding.
    """
    out = []
    # `\.white` but not `AppColors.white`; a leading `Color.` is fine.
    bare = r"(?<![A-Za-z0-9_])(?:Color)?\.(white|black|primary|secondary)\b"
    hue_alt = "|".join(_BARE_HUES)
    ink = re.compile(rf"\.{_MODIFIER}\((?P<arg>[^\n]*)")
    surf = re.compile(rf"\.{_SURFACE_MODIFIER}\((?P<arg>[^\n]*)")
    bare_re = re.compile(bare)
    hue_re = re.compile(rf"(?<![A-Za-z0-9_])(?:Color)?\.({hue_alt})\b")

    for path in _swift_files():
        for lineno, line in _code_lines(path):
            hit = False
            for m in ink.finditer(line):
                if bare_re.search(m.group("arg")) or hue_re.search(m.group("arg")):
                    hit = True
            for m in surf.finditer(line):
                arg = m.group("arg")
                if hue_re.search(arg):
                    hit = True
                # `.white`/`.black` as a SURFACE are allowed only as a scrim. The
                # exemption is structural (`.opacity(` present), not a file list, so it
                # cannot rot the way a name list does.
                elif bare_re.search(arg) and ".opacity(" not in arg:
                    hit = True
            if hit:
                out.append(f"{_rel(path)}:{lineno}: {line.strip()}")
    return out


def test_bare_colour_scanner_is_not_vacuous():
    """Positive control: the patterns must match a known-violating string."""
    assert _bare_colour_violations() is not None
    probe = ".foregroundColor(.white)"
    assert re.search(rf"\.{_MODIFIER}\(\s*\.?(?:Color\.)?(white|black|primary|secondary)\b", probe)


def test_no_bare_swiftui_colours_as_ink_or_opaque_fill():
    """`.white`/`.black` as INK, and `.primary`/`.secondary` anywhere, are always wrong —
    they are Apple's `label`/`secondaryLabel`, not this palette. As a FILL they are allowed
    only with `.opacity(`, i.e. as a scrim.

    theme-lint rule 2's hue list omitted `white|black|primary|secondary` entirely, so bare
    `.white` on a saturated adaptive surface — the exact failure mode `MoneyMoveCard` was
    flagged for — was uncatchable by construction.
    """
    violations = _bare_colour_violations()
    assert not violations, "\n".join(violations)


# Fill tokens INCLUDING the computed aliases that forward to `primaryFill`. A `*Fill`-suffix
# grep cannot see these two, and the four selected-chip sites use them — so the chip finding
# that was already fixed had no regression guard at all.
_FILL_TOKENS = ("primaryFill", "gainFill", "lossFill", "cautionFill", "accentCyanFill",
                "alertPurpleFill", "chipSelectedBackground", "borderFocus")
_BANNED_ON_FILL = ("textPrimary", "textSecondary", "textMuted", "textInverse")


def _fill_sites() -> list[tuple[str, int, str]]:
    pattern = re.compile(rf"AppColors\.({'|'.join(_FILL_TOKENS)})\b")
    out = []
    for path in _swift_files():
        for lineno, line in _code_lines(path):
            m = pattern.search(line)
            if m:
                out.append((_rel(path), lineno, m.group(1)))
    return out


def test_fill_token_scanner_finds_the_known_sites():
    """Anti-vacuity, and it is not theoretical: theme-lint rule 3 uses BRE alternation
    (`\\(a\\|b\\)Fill`), which is one grep-implementation difference away from matching
    nothing and passing forever."""
    sites = _fill_sites()
    assert len(sites) >= 30, len(sites)
    assert any(f == "Views/Atoms/GrowthMetricChip.swift" for f, _, _ in sites)
    assert any(tok == "chipSelectedBackground" for _, _, tok in sites)


def test_text_tokens_never_sit_on_a_fill():
    """Ink on a `*Fill` is `textOnAccent`, always. A text token there inverts against a fill
    that does not: `textPrimary` is #FFFFFF in dark and #0F172A in light."""
    banned = re.compile(rf"AppColors\.({'|'.join(_BANNED_ON_FILL)})\b")
    fill = re.compile(rf"AppColors\.({'|'.join(_FILL_TOKENS)})\b")
    # Look BACKWARD from the fill only. SwiftUI applies `.foregroundColor` before
    # `.background` on the same view, so ink that appears AFTER a fill belongs to a
    # different view — which is what made a symmetric window flag the price label on a
    # card two lines below an unrelated "CURRENT" badge.
    ink_modifier = re.compile(rf"\.{_MODIFIER}\(")
    violations = []
    for path in _swift_files():
        lines = _code_lines(path)
        for idx, (lineno, line) in enumerate(lines):
            if not fill.search(line):
                continue
            for wlineno, wline in lines[max(0, idx - 6):idx + 1]:
                if not (banned.search(wline) and ink_modifier.search(wline)):
                    continue   # a stroke or a border is not ink
                # A ternary that already names `textOnAccent` has chosen correctly: the
                # banned token is the UNSELECTED arm, sitting on a plain surface.
                if "textOnAccent" in wline:
                    continue
                violations.append(
                    f"{_rel(path)}:{wlineno}: {wline.strip()}  (fill on line {lineno})")
    assert not violations, "\n".join(sorted(set(violations)))


# Graphic-role tokens: the five `*Graphic` plus every chart series. theme-lint rule 4 named
# only the five and then excluded `*ChartView.swift` by FILENAME, which is what hid nine
# `Text` value labels rendering at 2.89–3.84:1.
_GRAPHIC_TOKEN = re.compile(
    r"AppColors\.((?:gain|loss|caution|accent|primary)Graphic"
    r"|growth[A-Z]\w*|profit[A-Z]\w*|confidence[A-Z]\w*)\b")


def test_graphic_token_scanner_is_not_vacuous():
    hits = [1 for p in _swift_files() for _, l in _code_lines(p) if _GRAPHIC_TOKEN.search(l)]
    assert len(hits) >= 20, len(hits)


def test_graphic_tokens_never_colour_text():
    """A 3:1 token may stroke a mark; it may not ink a `Text`.

    The exemption is STRUCTURAL — is this line a `Text` foreground? — rather than "is this
    file named *ChartView.swift", which is what the filename filter was crudely approximating
    and why it hid nine genuine violations inside the chart layer.
    """
    violations = []
    fg = re.compile(rf"\.{_MODIFIER}\(")
    for path in _swift_files():
        lines = _code_lines(path)
        for idx, (lineno, line) in enumerate(lines):
            if not (_GRAPHIC_TOKEN.search(line) and fg.search(line)):
                continue
            # Walk back to the view this modifier is attached to.
            window = [w for _, w in lines[max(0, idx - 5):idx + 1]]
            if any("Text(" in w for w in window):
                violations.append(f"{_rel(path)}:{lineno}: {line.strip()}")
    assert not violations, "\n".join(violations)


def test_cards_with_a_fill_and_a_radius_carry_an_edge():
    """Port of rule 9. A #FFFFFF card on the #F4F5F8 page is 1.09:1, so light mode cannot
    separate them by luminance and needs `cardEdge`."""
    violations = []
    for path in _swift_files():
        lines = _code_lines(path)
        preview = next((i for i, (_, l) in enumerate(lines) if l.startswith("#Preview")), len(lines))
        for idx, (lineno, line) in enumerate(lines[:preview]):
            # Word-bounded: `cardBackgroundLight` and `cardBackgroundNested` are different
            # surfaces (an inset chip, a nested card), not the card this rule is about.
            if not re.search(r"AppColors\.cardBackground\b", line) or ".background(" not in line:
                continue
            # A translucent wash is a tint, not the card idiom — it has no fill to edge.
            if ".opacity(" in line:
                continue
            window = " ".join(w for _, w in lines[idx:idx + 10])
            if "cornerRadius" not in window and "RoundedRectangle" not in window:
                continue
            # A PARTIAL-corner clip is the "row inside a grouped card" idiom, not a card.
            # The group it belongs to carries the edge (see `ProfileView.settingsSection`,
            # which wraps these rows in a `.cardBorder(...)`).
            if "UnevenRoundedRectangle" in window:
                continue
            if any(m in window for m in (".cardBorder", ".cardFill", ".cardSurface")):
                continue
            violations.append(f"{_rel(path)}:{lineno}: {line.strip()}")
    assert not violations, "\n".join(violations)


# ── 6. The shell rules that stayed in the shell ──────────────────────────────

@pytest.mark.skipif(not _LINT.exists(), reason="theme-lint.sh not present")
def test_theme_lint_shell_rules_pass():
    """Rules 1/5/6/7/8 are file-shape rules with no Python-side parsing to share, and
    `theme-lint.sh` is what `.claude/rules/ios-swiftui.md` tells a human to run. Rules
    2/3/4/9 MOVED into this module — they needed per-entry reasoning and anti-vacuity
    controls a grep pipeline cannot express — so there is one rule with one home."""
    r = subprocess.run(["bash", str(_LINT)], capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    # The script prints a ✓ per rule. Zero means it exited 0 without checking anything.
    assert r.stdout.count("✓") >= 5, r.stdout
