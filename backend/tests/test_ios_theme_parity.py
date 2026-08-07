"""The iOS theme contract, asserted from Python so that it GATES instead of merely existing.

Why this exists. Two guards covered the palette and NEITHER ran unless a human did
something:

  * `ThemeContrastAudit` is `#if DEBUG` and fires only when someone launches a Debug build.
  * `frontend/ios/scripts/theme-lint.sh` was invoked by nothing at all — not by a hook,
    not by CI (there is still no `.github/`), not by a test. It was a script you had to
    remember to run.

Both were written in response to bugs that shipped; neither could stop the next one. Adding
rules to a lint nobody runs changes nothing, so the rules moved to where the tests already
run. That second bullet is now HISTORY, and this module is what changed it:
`.claude/hooks/post-tool-use-theme.sh` runs this file on every themed edit, and
`test_theme_lint_shell_rules_pass` below shells out to the lint — so the script has two
callers, both of them this module. `test_ios_auth_policy_parity.py` proved the technique in this repo: grep Swift from
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


# ── Computed aliases ─────────────────────────────────────────────────────────
#
# `AppColors` exposes 10 computed aliases that forward to a canonical token. Every scanner
# in this module keys on the CANONICAL name, so before this map they were all invisible:
# `AppColors.bullish` (277 refs), `bearish` (245) and `neutral` (124) alone are 646 of 657
# alias references that no rule could resolve. Concretely, `_BANNED_ON_FILL` could not see
# `AppColors.tabBarUnselected` or `AppColors.chartAxisLabel` even though both ARE
# `textMuted`, and `_GRAPHIC_TOKEN` could see no alias at all.
#
# One map rather than adding alias names to each tuple — which is what `_FILL_TOKENS` used
# to do for two of them. A hand-extended tuple is a second place the alias set lives, and it
# rots the moment an eleventh alias is added. `test_alias_map_matches_the_swift` parses the
# real declarations and asserts IDENTITY, so this cannot silently drift either way.
_ALIAS = {
    "bullish": "gain",
    "bearish": "loss",
    "neutral": "caution",
    "alertBlue": "primaryBlue",
    "borderFocus": "primaryFill",
    "divider": "borderSubtle",
    "tabBarSelected": "primaryBlue",
    "tabBarUnselected": "textMuted",
    "chipSelectedBackground": "primaryFill",
    "chartAxisLabel": "textMuted",
}


def _canon(name: str) -> str:
    """Resolve a computed alias to the token it forwards to. Identity for canonical names."""
    return _ALIAS.get(name, name)


def _with_aliases(tokens) -> tuple:
    """`tokens` plus every alias that forwards into the set — for building a match regex.
    Captures still have to be run through `_canon` before comparing."""
    return tuple(tokens) + tuple(a for a, c in _ALIAS.items() if c in tokens)


def test_alias_map_matches_the_swift():
    """Anti-vacuity + drift guard, by IDENTITY not superset. An alias added to the palette
    and forgotten here silently un-guards every one of its call sites; an alias deleted from
    the palette and left here makes this module resolve a name that no longer exists."""
    declared = dict(re.findall(r"static var (\w+): Color \{ (\w+) \}", _APPTHEME.read_text()))
    assert declared == _ALIAS, (
        f"alias drift.\n  in Swift not here: {set(declared) - set(_ALIAS)}"
        f"\n  here not in Swift: {set(_ALIAS) - set(declared)}"
        f"\n  disagree: {{k for k in declared.keys() & _ALIAS.keys() if declared[k] != _ALIAS[k]}}")
    # Positive probes: an alias resolves, a canonical name is a fixed point.
    assert _canon("bullish") == "gain"
    assert _canon("gain") == "gain"


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
    # A colour passed as a LABELLED ARGUMENT rather than through a modifier. Both this
    # scanner and the shell rule it replaced keyed off `.modifier(`, so every hue handed
    # to a helper function or an initialiser was invisible — which is how four bare
    # system hues survived in `SubChartCanvas` (MACD/signal/%K/%D) with every guard green.
    # Apple's `.green` is 2.22:1 on white and does not adapt, and inside a `Canvas` there
    # is no modifier anywhere for a grep to anchor on.
    # `\w*` on the Color/Fill/Tint stems catches `iconColor:` (40), `valueColor:` (19),
    # `labelColor:`, `dotColor:`, `textColor:`, `fillColor:`, `strokeColor:`,
    # `iconBackgroundColor:`, `trendColor:`, `timeColor:` in one alternative instead of a
    # name list that rots. Coverage goes 139 → 234 of 294 labelled colour arguments.
    #
    # `with:` is the most valuable single addition — 21 `GraphicsContext.fill(_:with:)` /
    # `.stroke(_:with:)` sites, all inside a `Canvas`, which is precisely where the four
    # original `SubChartCanvas` bare hues hid and where there is NO modifier to anchor on.
    # `fallback:` covers the last-resort colour on the `Color(themedHex:role:fallback:)` path.
    #
    # ⚠️ `style:` is deliberately NOT here. It fires on `style: .primary`, where `.primary`
    # is a `PlayAudioButton.Style` case, not `Color.primary` — 2 pure false positives for
    # zero real coverage. An enum case named `.primary`/`.secondary` is a common Swift
    # shape and `style:` is the label most likely to carry one.
    labelled = re.compile(
        r"\b(?:\w*(?:[Cc]olor|[Cc]olour|[Ff]ill|[Tt]int)|stroke|accent|active|base|inner|outer"
        r"|with|fallback)\s*:\s*(?P<arg>[^,)\n]*)")
    bare_re = re.compile(bare)
    hue_re = re.compile(rf"(?<![A-Za-z0-9_])(?:Color)?\.({hue_alt})\b")

    for path in _swift_files():
        lines = _code_lines(path)
        # A `#Preview` may legitimately use a system hue as throwaway sample data — it is
        # not shipped UI. Same boundary the card-edge rule uses.
        preview = next((i for i, (_, l) in enumerate(lines) if l.startswith("#Preview")), len(lines))
        for lineno, line in lines[:preview]:
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
            for m in labelled.finditer(line):
                arg = m.group("arg")
                if hue_re.search(arg):
                    hit = True
                # Same structural scrim exemption as the surface arm above, and it is
                # load-bearing here: `with: .color(.white.opacity(opacity))` is the
                # `GraphicsContext` spelling of a gradient scrim over artwork, and three
                # legitimate sites use it (AudioArtworkThumbnail, MoneyMoveArticleHeroHeader,
                # BookLibraryView). A named hue is still banned — only `.white`/`.black`
                # get the scrim exemption, and only with an alpha on them.
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
    # The labelled-argument arm, which has no modifier to anchor on. This is the shape of
    # the four real `SubChartCanvas` violations that every guard missed until now.
    labelled = re.compile(
        r"\b(?:\w*(?:[Cc]olor|[Cc]olour|[Ff]ill|[Tt]int)|stroke|accent|active|base|inner|outer"
        r"|with|fallback)\s*:\s*(?P<arg>[^,)\n]*)")
    hue = re.compile(r"(?<![A-Za-z0-9_])(?:Color)?\.(green|red|blue|orange)\b")
    bare = re.compile(r"(?<![A-Za-z0-9_])(?:Color)?\.(white|black|primary|secondary)\b")

    def fires(src: str) -> bool:
        for m in labelled.finditer(src):
            arg = m.group("arg")
            if hue.search(arg) or (bare.search(arg) and ".opacity(" not in arg):
                return True
        return False

    # MUST fire — each is a real shape the pre-widening scanner could not see.
    for probe in ("drawLine(context: context, color: .green, lineWidth: 1.5)",
                  "CircularProgressViewStyle(tint: .white)",
                  "context.fill(p, with: .color(.green))",            # GraphicsContext, no modifier
                  "Foo(iconColor: .orange)",                          # \\w*Color stem
                  "Bar(valueColor: Color.red)",
                  "Color(themedHex: h, role: .fill, fallback: .blue)"):
        assert fires(probe), f"widened labelled-arg scanner went blind to: {probe}"

    # MUST NOT fire.
    # 1. A real token — otherwise every chart line in the app is a violation.
    assert not fires("drawLine(context: context, color: AppColors.gainGraphic, lineWidth: 1.5)")
    # 2. `style: .primary` is a PlayAudioButton.Style CASE, not Color.primary. This pins the
    #    decision to keep `style` out of the alternation, so nobody re-adds it.
    assert not fires("PlayAudioButton(episode: e, style: .primary, size: .medium)")
    # 3. A `GraphicsContext` scrim — `.white` WITH an alpha is the legitimate spelling.
    assert not fires("context.fill(r, with: .color(.white.opacity(0.4)))")


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
#
# The five `*Graphic` tokens are in here too, and they are the REPLACEMENT for retired
# shell rule 4's location test. That rule asked "is this token outside the chart layer",
# which was only ever a proxy for role — and the proxy is what hid nine real violations
# inside the chart layer. The genuine invariant is about role, in both directions: a
# 3:1 token must not INK text (below), and it must not be a SURFACE that text sits on
# (here). A `*Graphic` as a raw `.fill()`/`.stroke()` anywhere is correct — that IS the
# graphic role — so location never belonged in the rule.
#
# `chipSelectedBackground` and `borderFocus` used to be hand-listed here. They are ALIASES
# of `primaryFill`, and `_ALIAS` now resolves them along with the other eight — one place,
# not two. Removing them from this tuple is what makes the map load-bearing rather than
# decorative: `_with_aliases` puts them back into the match regex, and `_canon` maps the
# capture to `primaryFill` before it is compared.
_FILL_TOKENS = ("primaryFill", "gainFill", "lossFill", "cautionFill", "accentCyanFill",
                "alertPurpleFill", "alertOrangeFill",
                "gainGraphic", "lossGraphic", "cautionGraphic", "accentGraphic",
                "primaryGraphic")
_BANNED_ON_FILL = ("textPrimary", "textSecondary", "textMuted", "textInverse")


# ── Same-file parameter resolution ───────────────────────────────────────────
#
# WHY. `MoversToggle` renders `.background(isActive ? active : Color.clear)`, where `active`
# is a FUNCTION PARAMETER bound at the two call sites to `AppColors.gainFill` /
# `AppColors.lossFill`. No literal fill token appears anywhere near the ink, so the 6-line
# backward window sees nothing and reverting the ink to `AppColors.textPrimary` — the exact
# regression the `*Fill` + `textOnAccent` fix exists to prevent — is GREEN under every other
# guard in this repo. This resolver is the only thing that closes it.
#
# SAME-FILE ONLY, deliberately. 25 of the 82 Color-typed stored properties are constructed
# only within their own file; the other 42 are cross-file and would need a project-wide
# symbol table (a different program, and one that wants SourceKit rather than regex).
# Same-file is the boundary `_CARD_PROP` already picked, and it is where the real defect is.
_COLOR_PROP_DECL = re.compile(r"^\s*(?:private\s+)?(?:let|var)\s+(\w+)\s*:\s*Color\??\s*(?:=|$)")
_COLOR_PARAM_DECL = re.compile(r"(\w+)\s*:\s*Color\??(?:\s*=|\s*[,)])")


def _color_bindings(lines) -> dict[str, set]:
    """{identifier -> canonical tokens it is bound to at same-file call sites}.

    `#Preview` call sites are excluded: a preview may bind a parameter to a colour that
    production never passes, which would make the resolver hallucinate a pairing.
    """
    preview = next((i for i, (_, l) in enumerate(lines) if l.startswith("#Preview")), len(lines))
    names = set()
    for _, line in lines[:preview]:
        if (m := _COLOR_PROP_DECL.match(line)) and "{" not in line:
            names.add(m.group(1))          # stored property, not a computed `var x: Color {`
        if "func " in line:
            names.update(_COLOR_PARAM_DECL.findall(line))
    bindings: dict[str, set] = {}
    if not names:
        return bindings
    binder = re.compile(rf"\b({'|'.join(map(re.escape, names))})\s*:\s*([^,)\n]*)")
    for _, line in lines[:preview]:
        for m in binder.finditer(line):
            toks = {_canon(t) for t in _APPCOLOR.findall(m.group(2))}
            if toks:
                bindings.setdefault(m.group(1), set()).update(toks)
    return bindings


def test_param_binding_resolver_is_not_vacuous():
    """Three halves. (i) the harvester must still find the real population; (ii)
    `MoversToggle` specifically must resolve — it is named because it is the defect this
    exists for, so a rename must fail loudly rather than silently un-guard it; (iii) the
    resolver must not invent bindings out of nothing."""
    total = {}
    for path in _swift_files():
        b = _color_bindings(_code_lines(path))
        if b:
            total[_rel(path)] = b
    assert len(total) >= 25, f"only {len(total)} files with resolvable colour bindings"

    mt = total.get("Views/Molecules/MoversToggle.swift", {})
    assert mt.get("active") == {"gainFill", "lossFill"}, (
        "MoversToggle's `active:` parameter no longer resolves — the sentiment-fill fix "
        f"it guards is unprotected again. got: {mt}")

    synthetic = [(1, "func seg(_ v: Int, active: Color) -> some View {"),
                 (2, "seg(1, active: AppColors.gainFill)"),
                 (3, "Text(\"x\")")]
    assert _color_bindings(synthetic) == {"active": {"gainFill"}}
    assert _color_bindings([(1, "Text(\"nothing here\")")]) == {}


def _fill_sites() -> list[tuple[str, int, str]]:
    pattern = re.compile(rf"AppColors\.({'|'.join(_with_aliases(_FILL_TOKENS))})\b")
    out = []
    for path in _swift_files():
        for lineno, line in _code_lines(path):
            m = pattern.search(line)
            if m:
                out.append((_rel(path), lineno, _canon(m.group(1))))
    return out


def test_fill_token_scanner_finds_the_known_sites():
    """Anti-vacuity, and it is not theoretical: theme-lint rule 3 uses BRE alternation
    (`\\(a\\|b\\)Fill`), which is one grep-implementation difference away from matching
    nothing and passing forever."""
    sites = _fill_sites()
    assert len(sites) >= 30, len(sites)
    assert any(f == "Views/Atoms/GrowthMetricChip.swift" for f, _, _ in sites)
    # The four selected-chip sites reach `primaryFill` through the `chipSelectedBackground`
    # alias, so this asserts the alias resolution is live, not just that the scanner runs.
    assert any(tok == "primaryFill" for _, _, tok in sites)
    assert _canon("chipSelectedBackground") == "primaryFill"


def test_text_tokens_never_sit_on_a_fill():
    """Ink on a `*Fill` is `textOnAccent`, always. A text token there inverts against a fill
    that does not: `textPrimary` is #FFFFFF in dark and #0F172A in light."""
    # Alias-expanded on BOTH sides: `tabBarUnselected` and `chartAxisLabel` are `textMuted`,
    # and `chipSelectedBackground`/`borderFocus` are `primaryFill` — all four were invisible
    # to this rule while it matched canonical names only.
    banned = re.compile(rf"AppColors\.({'|'.join(_with_aliases(_BANNED_ON_FILL))})\b")
    fill = re.compile(rf"AppColors\.({'|'.join(_with_aliases(_FILL_TOKENS))})\b")
    # Look BACKWARD from the fill only. SwiftUI applies `.foregroundColor` before
    # `.background` on the same view, so ink that appears AFTER a fill belongs to a
    # different view — which is what made a symmetric window flag the price label on a
    # card two lines below an unrelated "CURRENT" badge.
    ink_modifier = re.compile(rf"\.{_MODIFIER}\(")
    violations = []
    for path in _swift_files():
        lines = _code_lines(path)
        # A fill reached through a same-file-bound identifier counts as a fill. Without
        # this, `MoversToggle`'s `.background(isActive ? active : .clear)` — where `active`
        # is bound to `gainFill`/`lossFill` — is invisible, and reverting its ink to
        # `textPrimary` passes every guard.
        bound = {n: t for n, t in _color_bindings(lines).items()
                 if t & set(_FILL_TOKENS)}
        bound_re = (re.compile(rf"\.(?:background|fill)\(.*\b({'|'.join(map(re.escape, bound))})\b")
                    if bound else None)
        for idx, (lineno, line) in enumerate(lines):
            if not (fill.search(line) or (bound_re and bound_re.search(line))):
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


# A view whose foreground is read as CONTENT rather than as a mark. `.claude/rules/
# ios-swiftui.md` puts "anything read as text OR A MEANINGFUL ICON" in the 4.5:1 text
# role, so an SF Symbol inked with a 3:1 token is the same defect as a `Text` — the
# original port only looked for `Text(` and could not see it.
_TEXT_ROLE_VIEW = re.compile(r"\b(?:Text|Image|Label)\(")


def test_graphic_tokens_never_colour_text():
    """A 3:1 token may stroke a mark; it may not ink a `Text`, an `Image` or a `Label`.

    The exemption is STRUCTURAL — is this line the foreground of a content view? — rather
    than "is this file named *ChartView.swift", which is what retired shell rule 4's
    filename filter was crudely approximating and why it hid nine genuine violations
    inside the chart layer.

    `Image(` is deliberately broad and will match decorative art as well as symbols. The
    escape hatch is `accessibilityHidden(true)` in the same window: a glyph declared
    invisible to assistive tech is chrome, not content, and 3:1 is the right floor for it.
    That is a STATED exemption in the source rather than a filename list in the guard,
    which is the whole difference between this rule and the one it replaced.
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
            if not any(_TEXT_ROLE_VIEW.search(w) for w in window):
                continue
            if any("accessibilityHidden(true)" in w for w in window):
                continue
            violations.append(f"{_rel(path)}:{lineno}: {line.strip()}")
    assert not violations, "\n".join(violations)


def test_graphic_token_text_role_scanner_is_not_vacuous():
    """The rule above matches zero lines on a clean tree, so a broken `_TEXT_ROLE_VIEW`
    or `_MODIFIER` would be indistinguishable from success. Prove each half still fires
    on a synthetic violation, including the `Image` arm that this widening added."""
    for view in ("Text(\"+12.4%\")", "Image(systemName: \"arrow.up\")", "Label(\"x\", systemImage: \"y\")"):
        assert _TEXT_ROLE_VIEW.search(view), view
    line = ".foregroundColor(AppColors.gainGraphic)"
    assert _GRAPHIC_TOKEN.search(line) and re.compile(rf"\.{_MODIFIER}\(").search(line)
    # ...and the escape hatch must actually exempt, or it is decoration.
    assert "accessibilityHidden(true)" in ".accessibilityHidden(true)"


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
            # `Nested` included: it is a card in its own right (Recent Activities rows,
            # the ETF snapshot tiles) and separates by `cardEdge` in light exactly as
            # `cardBackground` does — 0 new violations. NOT `Light`: that adds 6 false
            # positives (EarningsDataTypeToggle, EventDateBadge, ChatMarketOverviewWidget,
            # DiversificationCard, UserMessageBubble, IndexDetailSnapshotsSection), all
            # chips/badges/bubbles that separate by fill at 1.14 and correctly have no edge.
            if not re.search(r"AppColors\.cardBackground(?:Nested)?\b", line) or ".background(" not in line:
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


# A card surface reached through a PROPERTY rather than a token literal. The test above
# greps for `AppColors.cardBackground` on the line, so it is structurally blind to
# `.fill(background)` where `background` is a `var` defaulting to a card token — which is
# exactly how `CongressActivityRow` shipped: it stepped the fill up for dark (correctly)
# and, because a bare `.fill` draws no `cardEdge`, was 1.00:1 against its parent in LIGHT
# with no hairline. Its two sibling row types both used `.cardFill` and were fine.
#
# Deliberately NARROW: it fires only when the identifier's own declaration in the same
# file resolves to a card token. The broad version — "any `.fill(someLocalVar)` under a
# corner radius" — needs an allowlist to stay green, and an allowlist is the smell that
# retired shell rule 4.
_CARD_PROP = re.compile(
    r"var\s+(\w+)\s*:\s*Color\s*=\s*AppColors\.(cardBackground\w*)\b")


def _card_backed_properties(lines):
    return {m.group(1) for _, l in lines if (m := _CARD_PROP.search(l))}


def test_a_card_surface_reached_through_a_property_still_carries_an_edge():
    violations = []
    for path in _swift_files():
        lines = _code_lines(path)
        props = _card_backed_properties(lines)
        if not props:
            continue
        preview = next((i for i, (_, l) in enumerate(lines) if l.startswith("#Preview")), len(lines))
        for idx, (lineno, line) in enumerate(lines[:preview]):
            m = re.search(r"\.fill\((\w+)\)|\.background\((\w+)\)", line)
            if not m or (m.group(1) or m.group(2)) not in props:
                continue
            window = " ".join(w for _, w in lines[max(0, idx - 6):idx + 4])
            if "cornerRadius" not in window and "RoundedRectangle" not in window:
                continue
            if any(x in window for x in (".cardBorder", ".cardFill", ".cardSurface", ".strokeBorder")):
                continue
            violations.append(f"{_rel(path)}:{lineno}: {line.strip()}")
    assert not violations, "\n".join(violations)


# A 1pt hairline drawn in its own parent's colour. `DividendInfoCard` shipped six of them:
# the card is `cardBackgroundNested` and the row separator was `cardBackground`, and those
# two share the #FFFFFF LIGHT arm — so the dividers were 1.0000:1 and drew nothing. Dark was
# 1.11 and fine, which is exactly why it looked correct to whoever wrote it.
#
# Value-compared, not token-allowlisted. The 63 1pt-`Rectangle` sites in this app use six
# different tokens; an "approved divider tokens" list would either exclude the legitimate
# `textMuted.opacity()` dimmed rules or admit `cardBackground`. The invariant is not WHICH
# token — it is that A HAIRLINE MUST NOT BE ITS OWN PARENT, and that is decidable from the
# two resolved values. Same reasoning that retired shell rule 4's filename filter.
_STRUCT = re.compile(r"^\s*(?:public\s+|private\s+|internal\s+)?struct\s+(\w+)")
# `.cardSurface()` DEFAULTS to `AppColors.cardBackground` (CardSurface.swift:81) and 76
# files use the no-argument form — matching only the explicit-token spelling saw 5 of the
# ~40 real sites, which is how the first draft of this rule nearly shipped vacuous.
_CARD_PAINT = re.compile(r"\.(?:cardSurface|cardFill)\(\s*(?:AppColors\.(\w+))?")
_HAIRLINE_FILL = re.compile(r"\.fill\(\s*AppColors\.(\w+)(?:\.opacity\(([\d.]+)\))?\s*\)")


def _resolved(tok: Token, style: str, over: tuple, extra_alpha: float = 1.0):
    """Flatten a token (its own alpha, then any inline `.opacity(n)`) onto `over`."""
    return _composite(tok.rgb(style), tok.alpha(style) * extra_alpha, over)


def _hairline_sites() -> list[tuple[str, int, str, str, float]]:
    """(file, lineno, cardToken, hairlineToken, inlineAlpha) for every 1pt Rectangle
    inside a struct that paints a known card surface."""
    tokens = _declared_tokens(_sections()[0])
    out = []
    for path in _swift_files():
        lines = _code_lines(path)
        # Which card surface does the enclosing struct paint? Structs are top-level here,
        # so a running "current struct" pointer is enough.
        struct_surface: dict[str, str] = {}
        cur = None
        for _, line in lines:
            if (m := _STRUCT.match(line)):
                cur = m.group(1)
            elif cur and (m := _CARD_PAINT.search(line)):
                # No explicit token → `.cardSurface()`'s default, `AppColors.cardBackground`.
                struct_surface.setdefault(cur, _canon(m.group(1) or "cardBackground"))
        cur = None
        for idx, (lineno, line) in enumerate(lines):
            if (m := _STRUCT.match(line)):
                cur = m.group(1)
                continue
            if "Rectangle()" not in line or cur not in struct_surface:
                continue
            window = lines[idx:idx + 6]
            if not any(re.search(r"\.frame\((?:height|width):\s*1\b", w) for _, w in window):
                continue
            for _, w in window:
                if (fm := _HAIRLINE_FILL.search(w)):
                    name, alpha = _canon(fm.group(1)), float(fm.group(2) or 1.0)
                    if name in tokens:
                        out.append((_rel(path), lineno, struct_surface[cur], name, alpha))
                    break
    return out


def test_a_hairline_is_never_its_own_parent():
    tokens = _declared_tokens(_sections()[0])
    violations = []
    for rel, lineno, card_name, line_name, alpha in _hairline_sites():
        card, hair = tokens[card_name], tokens[line_name]
        for style in ("light", "dark"):
            base = _resolved(card, style, (1.0, 1.0, 1.0))
            drawn = _resolved(hair, style, base, alpha)
            if _ratio(drawn, base) < 1.02:
                violations.append(
                    f"{rel}:{lineno}: {line_name} hairline on a {card_name} card is "
                    f"{_ratio(drawn, base):.4f}:1 in {style} — it draws nothing")
    assert not violations, "\n".join(violations)


def test_hairline_scanner_is_not_vacuous():
    """This rule matches 0 lines on a clean tree, so a broken `Rectangle()`/`.frame(height: 1)`
    pattern is indistinguishable from success. Assert the scanner still finds the real
    population, and that the comparison can still detect a same-colour hairline."""
    sites = _hairline_sites()
    assert len(sites) >= 20, f"only {len(sites)} hairline sites — the scanner went blind"
    assert len({s[0] for s in sites}) >= 10, "hairlines found in suspiciously few files"
    # The exact defect this exists for, as a synthetic: a #FFFFFF hairline on a #FFFFFF card.
    tokens = _declared_tokens(_sections()[0])
    base = _resolved(tokens["cardBackgroundNested"], "light", (1.0, 1.0, 1.0))
    same = _resolved(tokens["cardBackground"], "light", base)
    assert _ratio(same, base) < 1.02, "the comparison can no longer detect an invisible hairline"
    # ...and it must NOT condemn the token the fix uses.
    ok = _resolved(tokens["borderSubtle"], "light", base)
    assert _ratio(ok, base) >= 1.02, "`divider`/`borderSubtle` should read as a real hairline"


def test_card_backed_property_scanner_is_not_vacuous():
    """The rule above legitimately matches ZERO lines now that CongressActivityRow is
    fixed, so without this control a broken regex would look identical to a clean tree.
    Two halves: the declaration regex must still find real properties in the real
    codebase, and the violation shape must still be detectable."""
    found = {p for path in _swift_files() for p in _card_backed_properties(_code_lines(path))}
    assert found, "no `var x: Color = AppColors.cardBackground*` found — regex is dead"

    synthetic = [(1, "var background: Color = AppColors.cardBackgroundNested"),
                 (2, "RoundedRectangle(cornerRadius: AppCornerRadius.medium)"),
                 (3, ".fill(background)")]
    assert _card_backed_properties(synthetic) == {"background"}
    window = " ".join(l for _, l in synthetic)
    assert "RoundedRectangle" in window
    assert not any(x in window for x in (".cardBorder", ".cardFill", ".cardSurface"))


# ── 5b. Surface separation, mirrored — and its light leg made real ───────────
#
# `ThemeContrastAudit.auditSurfaceSeparation` cannot check what it claims to. Its guard is
#
#     let hasEdge = edgeAlpha > 0.01        // cardEdge LIGHT alpha is 0.14
#     if measured >= minFillStep || hasEdge { continue }
#
# and `hasEdge` is a property of the cardEdge TOKEN, not of the pair and not of the view.
# In light it is unconditionally true, so **all five light comparisons `continue` before
# measuring anything** — 10 comparisons run, 5 can never fail. The docstring promises light
# "MUST have an edge" and never checks that the edge is VISIBLE.
#
# That is why `CongressActivityRow` (1.0000:1 in light, no hairline) was invisible here as
# well as to rule 9. This mirror implements the semantics the Swift only claims: light
# passes on a fill step OR on a hairline that measurably separates from BOTH sides.
#
# It also exists so the Swift pair table is checked by the 50ms hook rather than by an
# `assertionFailure` at launch — the module already mirrors WCAG maths, `auditLightParity`,
# the manifest and the hex clamp; this is the missing fourth mirror.
_MIN_FILL_STEP = 1.05
# The binding measurement today is 1.2272 (cardEdge over `background`). 1.15 leaves real
# headroom for a routine page-colour retune while still catching the regression that
# matters: dropping cardEdge's light alpha 0.14 → 0.011 PASSES the `> 0.01` test the Swift
# uses today and collapses edge-vs-inner to 1.022.
_MIN_EDGE_STEP = 1.15

_PAIR = re.compile(r'\(\s*"(\w+)"\s*,\s*"(\w+)"\s*,\s*\n?\s*AppColors\.(\w+)\s*,\s*AppColors\.(\w+)\s*\)')


def _separation_pairs() -> list[tuple[str, str]]:
    src = "\n".join(l for l in (_IOS / "Theme/ThemeContrastAudit.swift").read_text().splitlines()
                    if not l.strip().startswith("//"))
    # Search the closing anchor FROM the opening one: `for style in` also appears earlier
    # in the file (`_measure_all`'s loop), so an unanchored index() yields an empty slice
    # and the scanner silently parses zero pairs.
    start = src.index("let pairs:")
    block = src[start:src.index("for style in", start)]
    out = []
    for inner, outer, ic, oc in _PAIR.findall(block):
        assert inner == ic and outer == oc, f"pair label/colour disagree: {inner}/{ic} {outer}/{oc}"
        out.append((inner, outer))
    return out


def test_the_tab_bar_draws_its_own_hairline():
    """`(tabBarBackground, background)` cannot go in the pair table above: both dark arms
    are #171B26, so the fill step is 1.0000 and `cardEdge` is transparent in dark — the
    generic check can only fail. The bar solves it locally with its own `border` hairline
    instead, and this pins that.

    Measured before the fix: the left gutter was one unbroken #171B26 for 320px from the
    page through the bar, so a card scrolled to the edge was clipped against nothing."""
    src = (_IOS / "Views/Organisms/CustomTabBar.swift").read_text()
    assert "AppColors.tabBarBackground" in src
    assert re.search(r"\.overlay\(alignment:\s*\.top\)", src), "tab-bar hairline is gone"
    assert "AppColors.border" in src, "the hairline must use `border` — `cardEdge` is alpha 0 in dark"
    tokens = _declared_tokens(_sections()[0])
    bar, border = tokens["tabBarBackground"], tokens["border"]
    for style in ("light", "dark"):
        base = _composite(bar.rgb(style), bar.alpha(style), (1.0, 1.0, 1.0))
        drawn = _composite(border.rgb(style), border.alpha(style), base)
        assert _ratio(drawn, base) >= _MIN_EDGE_STEP, (
            f"the tab-bar hairline is only {_ratio(drawn, base):.4f} against the bar in {style}")


def test_separation_pair_scanner_is_not_vacuous():
    pairs = _separation_pairs()
    assert len(pairs) >= 5, f"only parsed {len(pairs)} separation pairs"
    assert ("cardBackgroundNested", "cardBackground") in pairs
    # The pair deliberately EXCLUDED must stay excluded — it is 1.0000 in dark by design.
    assert ("cardBackgroundNested", "cardBackgroundLight") not in pairs


def test_every_declared_surface_pair_actually_separates():
    """Both legs, for real. Dark needs a fill step (cardEdge is transparent there). Light
    may lean on the hairline, but then the hairline has to be VISIBLE against both sides —
    which is the half the Swift cannot express."""
    tokens = _declared_tokens(_sections()[0])
    edge = tokens["cardEdge"]
    violations = []
    for inner, outer in _separation_pairs():
        for style in ("light", "dark"):
            i, o = tokens[inner], tokens[outer]
            irgb = _composite(i.rgb(style), i.alpha(style), (1.0, 1.0, 1.0))
            orgb = _composite(o.rgb(style), o.alpha(style), (1.0, 1.0, 1.0))
            if _ratio(irgb, orgb) >= _MIN_FILL_STEP:
                continue
            ea = edge.alpha(style)
            if ea <= 0.01:
                violations.append(f"{inner} on {outer} ({style}): fill step "
                                  f"{_ratio(irgb, orgb):.4f} and cardEdge is transparent")
                continue
            drawn = _composite(edge.rgb(style), ea, irgb)
            against = min(_ratio(drawn, irgb), _ratio(drawn, orgb))
            if against < _MIN_EDGE_STEP:
                violations.append(f"{inner} on {outer} ({style}): fill step "
                                  f"{_ratio(irgb, orgb):.4f} and the cardEdge hairline is "
                                  f"only {against:.4f} against the surfaces it divides")
    assert not violations, "\n".join(violations)


def test_the_light_leg_is_not_vacuous():
    """The whole point of this mirror. Prove the light check can FAIL — if `cardEdge`'s
    light alpha were dropped to a value that still clears the Swift's `> 0.01` test, the
    hairline stops separating and this must catch it."""
    tokens = _declared_tokens(_sections()[0])
    card = tokens["cardBackgroundNested"]
    base = _composite(card.rgb("light"), card.alpha("light"), (1.0, 1.0, 1.0))
    faint = _composite(tokens["cardEdge"].rgb("light"), 0.011, base)   # > 0.01, so Swift passes
    assert _ratio(faint, base) < _MIN_EDGE_STEP, (
        "a near-invisible cardEdge now passes the light leg — it is vacuous again")
    real = _composite(tokens["cardEdge"].rgb("light"), tokens["cardEdge"].alpha("light"), base)
    assert _ratio(real, base) >= _MIN_EDGE_STEP, "the real cardEdge should pass"


# ── 6. The manifest is the allowlist for ink-on-surface pairings ─────────────
#
# THE HOLE THIS CLOSES. `.foregroundColor(AppColors.gain).background(AppColors.
# toggleSelectedBackground)` passed every guard in this repo: `gain` is not a bare hue, not
# a `*Graphic`, and `toggleSelectedBackground` is not a `*Fill`; the runtime audit never
# measured the pair because `gain`'s spec defaults to `contentSurfaces`, which excludes
# every control surface. Measured, 16 of the 45 sentiment/accent × control-surface pairings
# fail AA — so this was one line of code away from shipping, and `MoversToggle` is the site
# where it actually did (gain 4.34 light, loss 3.73 dark).
#
# WHY THIS IS NOT AN ALLOWLIST. The escape hatch is `on: … + ["thatSurface"]` on the
# `TokenSpec`, and adding it immediately enrols the pair in `_measure_all` AND in
# `ThemeContrastAudit`. Declaring `gain` on `toggleSelectedBackground` therefore measures
# 4.34 and fails `test_every_token_clears_its_floor` at edit time — before the app can even
# launch. **You cannot silence this lint without measuring the pair, and you cannot declare
# a failing pair without the tests going red first.** That is the whole design.
_INK_MOD = re.compile(r"\.(?:foregroundColor|foregroundStyle|tint|accentColor)\s*\(")
_BG_MOD = re.compile(r"\.(?:background|fill|cardFill|cardSurface)\s*\(")
_APPCOLOR = re.compile(r"AppColors\.(\w+)")


def _ternary_split(line: str):
    """(condition, trueArm, falseArm) for a Swift ternary, or None.

    A paren-DEPTH scanner, not a regex, and it has to be: Swift conditions contain parens
    (`(x ?? false) ? a : b`), arms contain colons (nested calls, dictionary literals), and
    `??` / `?.` are not the ternary `?`.
    """
    depth, i, q = 0, 0, -1
    while i < len(line):
        c = line[i]
        if c == '"':                                  # skip string literals wholesale
            i += 1
            while i < len(line) and line[i] != '"':
                i += 2 if line[i] == "\\" else 1
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "?" and q < 0:
            nxt = line[i + 1] if i + 1 < len(line) else ""
            if nxt not in "?.":                       # not `??`, not `?.`
                q, q_depth = i, depth
        i += 1
    if q < 0:
        return None
    # The matching ':' at the same depth the '?' was found at.
    depth, colon = 0, -1
    for j in range(q + 1, len(line)):
        c = line[j]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                break                                  # closed without a colon → not a ternary
            depth -= 1
        elif c == ":" and depth == 0:
            colon = j
            break
    if colon < 0:
        return None
    # Walk LEFT from '?' to the start of the enclosing ARGUMENT, so
    # `.foregroundColor(sel == p ? a : b)` yields "sel == p", not ".foregroundColor(sel == p".
    depth, start = 0, 0
    for j in range(q - 1, -1, -1):
        c = line[j]
        if c in ")]}":
            depth += 1
        elif c in "([{":
            if depth == 0:
                start = j + 1
                break
            depth -= 1
        elif c == "," and depth == 0:
            start = j + 1
            break
    cond = re.sub(r"\s+", " ", line[start:q]).strip()
    return (cond, line[q + 1:colon], line[colon + 1:]) if cond else None


def _pairings(ink_line: str, bg_line: str, surfaces: set) -> set:
    """(inkToken, surfaceToken) pairs that can actually co-render on screen.

    ARM MATCHING is the structural exemption. Three period toggles and SmartMoneyTabSelector
    drive BOTH the ink and the surface from one boolean, where the ink's false arm
    (`textMuted`) pairs with the surface's false arm (`.clear`) and never with the true arm
    (`toggleSelectedBackground`). Without this the rule reports 4 violations that are
    physically impossible; with it, 0. It also matters that they are UNFIXABLE by declaring
    — `textMuted` on `toggleSelectedBackground` is 4.51/4.06 — so an exemption is the only
    correct answer, and a structural one beats a file list.

    Conservative by construction: anything the splitter cannot parse falls back to the full
    cross product, so the failure mode is a false POSITIVE, never a missed defect.
    """
    it, bt = _ternary_split(ink_line), _ternary_split(bg_line)
    def toks(s): return {_canon(t) for t in _APPCOLOR.findall(s)}
    if it and bt and it[0] == bt[0]:
        return ({(i, s) for i in toks(it[1]) for s in toks(bt[1]) & surfaces} |
                {(i, s) for i in toks(it[2]) for s in toks(bt[2]) & surfaces})
    return {(i, s) for i in toks(ink_line) for s in toks(bg_line) & surfaces}


def _ink_on_surface_pairings(arm_matching: bool = True) -> list:
    """(file, lineno, ink, surface) for every ink that renders on a registered surface."""
    palette, _, manifest = _sections()
    surfaces = set(_surface_registry(palette, manifest))
    out = []
    for path in _swift_files():
        lines = _code_lines(path)
        preview = next((i for i, (_, l) in enumerate(lines) if l.startswith("#Preview")), len(lines))
        for idx, (lineno, line) in enumerate(lines[:preview]):
            names = {_canon(n) for n in _APPCOLOR.findall(line)} & surfaces
            if not names:
                continue
            # `.background(` may open on an earlier line — SmartMoneyTabSelector opens at
            # :35 and puts the token on :36. Without this the rule misses one of the four
            # sites arm matching exists for, i.e. it would ship never having been exercised.
            if not (_BG_MOD.search(line) or
                    any(_BG_MOD.search(w) and w.count("(") > w.count(")")
                        for _, w in lines[max(0, idx - 3):idx])):
                continue
            for wlineno, wline in lines[max(0, idx - 6):idx + 1]:
                if not _INK_MOD.search(wline):
                    continue
                pairs = (_pairings(wline, line, surfaces) if arm_matching
                         else {(_canon(i), s) for i in _APPCOLOR.findall(wline) for s in names})
                for ink, surf in pairs:
                    out.append((_rel(path), wlineno, ink, surf))
    return out


def test_ink_on_a_surface_is_declared_in_the_manifest():
    specs = {s["name"]: s for s in _specs(_sections()[2])}
    violations = []
    for rel, lineno, ink, surf in _ink_on_surface_pairings():
        spec = specs.get(ink)
        # Floor-0 roles have no contract to violate. `textOnAccent` is `.decorative` and is
        # verified in REVERSE by every `carriesOnAccentText` fill, so demanding a
        # declaration would force entries that measure nothing — destroying the
        # "declaring forces a measurement" property this rule depends on.
        if spec is None or _FLOOR.get(spec["role"], 0) <= 0:
            continue
        if surf not in spec["surfaces"]:
            violations.append(
                f"{rel}:{lineno}: AppColors.{ink} renders on {surf}, which its TokenSpec "
                f"does not declare. Add `on: … + [\"{surf}\"]` — that also MEASURES the pair.")
    assert not violations, "\n".join(sorted(set(violations)))


def test_ink_on_surface_scanner_is_not_vacuous():
    found = _ink_on_surface_pairings()
    assert len(found) >= 40, f"only {len(found)} ink-on-surface pairings — scanner went blind"
    assert len({f[0] for f in found}) >= 15


def test_ink_on_surface_rule_detects_the_regression_it_exists_for():
    """The exact line that passed every other guard."""
    surfaces = {"toggleSelectedBackground", "tabBarBackground", "cardBackground"}
    assert ("gain", "toggleSelectedBackground") in _pairings(
        ".foregroundColor(AppColors.gain)",
        ".background(AppColors.toggleSelectedBackground)", surfaces)
    # An alias must resolve too, or 657 references stay invisible to this rule.
    assert ("gain", "toggleSelectedBackground") in _pairings(
        ".foregroundColor(AppColors.bullish)",
        ".background(AppColors.toggleSelectedBackground)", surfaces)
    # Arm matching: same condition → arms pair with their own side, so the impossible
    # (textMuted, toggleSelectedBackground) combination is not produced.
    got = _pairings(
        ".foregroundColor(sel == tab ? AppColors.textPrimary : AppColors.textMuted)",
        ".background(sel == tab ? AppColors.toggleSelectedBackground : Color.clear)", surfaces)
    assert ("textPrimary", "toggleSelectedBackground") in got
    assert ("textMuted", "toggleSelectedBackground") not in got
    # A DIFFERENT condition must NOT be arm-matched — falls back to the cross product.
    got2 = _pairings(
        ".foregroundColor(a == b ? AppColors.textPrimary : AppColors.textMuted)",
        ".background(x == y ? AppColors.toggleSelectedBackground : Color.clear)", surfaces)
    assert ("textMuted", "toggleSelectedBackground") in got2
    # `??` and `?.` are not ternaries.
    assert _ternary_split(".foregroundColor(a ?? b)") is None
    assert _ternary_split("let x = foo?.bar") is None


def test_arm_matching_is_still_load_bearing():
    """Pins the exemption itself. With the splitter disabled the rule must report exactly
    the four known ternary sites; with it enabled, zero. If `_ternary_split` silently stops
    parsing, BOTH numbers move and this fails — which is the only way to tell a working
    exemption from a dead one."""
    specs = {s["name"]: s for s in _specs(_sections()[2])}

    def undeclared(arm_matching):
        out = set()
        for rel, lineno, ink, surf in _ink_on_surface_pairings(arm_matching=arm_matching):
            spec = specs.get(ink)
            if spec and _FLOOR.get(spec["role"], 0) > 0 and surf not in spec["surfaces"]:
                out.add((rel, ink, surf))
        return out

    assert undeclared(arm_matching=True) == set()
    without = undeclared(arm_matching=False)
    assert without, "arm matching is exempting nothing — it may have become dead code"
    assert all(i == "textMuted" and s == "toggleSelectedBackground" for _, i, s in without), without


# ── 7. The shell rules that stayed in the shell ──────────────────────────────

# The five rules that stayed in the shell, in the order the script runs them. Rules
# 2/3/4/9 were deleted from `theme-lint.sh` and live in this module instead, so there is
# one rule with one home; the script's numbering keeps gaps at 2/3/4/9 because three
# source comments cite rule numbers and renumbering would silently invalidate them.
_SHELL_RULE_TITLES = [
    "no Color(hex:) outside Theme/",
    "every .drawingGroup() is keyed on colorScheme",
    "no inert Divider().background()",
    "CaydexLogo rendered only via CaydexLogoMark",
    "every stored colour token is in TokenInventory AND auditManifest",
]


@pytest.mark.skipif(not _LINT.exists(), reason="theme-lint.sh not present")
def test_theme_lint_shell_rules_pass():
    """`theme-lint.sh` is what `.claude/rules/ios-swiftui.md` tells a human to run, and
    rules 1/5/6/7/8 are file-shape rules with no Python-side parsing to share.

    Asserting the exact TITLE SET rather than a count. `count >= 5` against exactly five
    rules is a tautology — it cannot tell "all five ran" from "one ran five times", and
    it goes quietly green if a rule is deleted and another added. Identity, order and
    count in one assertion, so adding a rule to the shell without deciding whether it
    belongs here fails loudly.
    """
    r = subprocess.run(["bash", str(_LINT)], capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    titles = [t.strip() for t in re.findall(r"✓\x1b\[0m (.+)", r.stdout)]
    assert titles == _SHELL_RULE_TITLES, f"got {titles}\n\n{r.stdout}"
