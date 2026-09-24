"""Home › "Trillion-Dollar Club Bets" — the iOS invariants that must not regress.

There is no XCTest target, so these read the Swift source (comments stripped and string
literals lifted out by the same scanner `test_trillion_club_schema_parity.py` uses, every
check brace-bounded to the declaration it is about). Each guard is a function returning its
violations, and each has an in-memory MUTATION test beside it: the defect is re-introduced into
the real source text, and the guard must fire. A guard that cannot fail proves nothing
(`project_source_scan_guard_vacuity`).

What is pinned, and why:
  1. The section is a PLAIN horizontal ScrollView + HStack: no `Lazy*` (a lazy container of
     self-sizing cards hung Home at 100% CPU), no `.scrollPosition` (writes its binding during
     layout — banned app-wide), no `GeometryReader`, and no endless loop.
  2. Chips are LABELS, never Buttons — a chip that looks tappable and ignores taps is a dead
     control — and each carries a full-sentence VoiceOver label.
  3. No gain/loss colour anywhere in the card, row, chip, section or detail: none of these
     facts is good or bad news, and a green "Increased" reads as a buy signal.
  4. The detail reloads on `appState.entitlementGeneration` (a purchase unlocks in place), and
     every cover it and Home present injects BOTH `AppState` spellings and is cleared by
     Home's `.onPresentationReset`.
  5. Copy: the plan's exact strings exist, and no user-facing literal uses a banned word
     (picks, conviction, bullish, worth, bought, "-backed", follow/copy/mirror…), matched on
     word boundaries so "Alphabet", "Holdings" and "Photonics" stay legal.
  6. The endpoint, its auth policy, the error mapping and the paywall context are wired.
  7. The 2026-09-24 hardening fixes, each pinned where it was made: the info sheet's member
     list carries its own meaning, the section presents nothing itself, the whole card is the
     tap target, card text wraps, the Club member chip sits under the name, sentences stay out
     of `FlowLayout`, the segment chips clear 4.5:1 composed, the sheet's titles are headings,
     the 13F facts, the source-neutral / profile-honest explainers, the Commitment sentence,
     and the detail's no-filing / gap / history-lock rules.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Iterable, List, Tuple

import pytest

from test_trillion_club_schema_parity import match_brace, scan_swift, type_body

REPO = Path(__file__).resolve().parents[2]
IOS = REPO / "frontend" / "ios" / "ios"
BACKEND = REPO / "backend"

MODELS = IOS / "Models/TrillionClubModels.swift"
SECTION = IOS / "Views/Organisms/TrillionClubSection.swift"
CARD = IOS / "Views/Molecules/TrillionClubCard.swift"
CHIP = IOS / "Views/Molecules/ClubStakeChip.swift"
ROW = IOS / "Views/Molecules/ClubHoldingRow.swift"
INFO = IOS / "Views/Molecules/TrillionClubInfoSheet.swift"
DETAIL = IOS / "Views/Screens/TrillionClubDetailView.swift"
DETAIL_VM = IOS / "ViewModels/TrillionClubDetailViewModel.swift"
HOME = IOS / "Views/Screens/HomeDashboardView.swift"
WHALE = IOS / "Views/Screens/WhaleProfileView.swift"
ENDPOINT = IOS / "Core/Services/APIEndpoint.swift"
APP_ERROR = IOS / "Core/Utilities/AppError.swift"
SUBSCRIPTION = IOS / "Models/SubscriptionModels.swift"

NEW_FILES = [MODELS, SECTION, CARD, CHIP, ROW, INFO, DETAIL, DETAIL_VM]
VIEW_FILES = [SECTION, CARD, CHIP, ROW, INFO, DETAIL]


def _src(path: Path) -> str:
    if not path.is_file():
        pytest.skip(f"{path} not present")
    return path.read_text(encoding="utf-8")


def _code(src: str) -> str:
    """Comments stripped, string literals replaced by placeholders."""
    return scan_swift(src)[0]


def _strings(src: str) -> List[str]:
    return scan_swift(src)[1]


def _closure_after(code: str, anchor: str) -> str:
    """The `{…}` block that opens at or after `anchor` (brace-bounded)."""
    at = code.find(anchor)
    assert at != -1, f"anchor {anchor!r} not found — the scan drifted"
    brace = code.index("{", at + len(anchor) - 1)
    return code[brace:match_brace(code, brace + 1) + 1]


# ══════════════════════════════════════════════════════════════════════════════════════
# 1. The section: plain, non-looping, no layout traps
# ══════════════════════════════════════════════════════════════════════════════════════

_LAYOUT_TRAPS = [
    (re.compile(r"\bLazy[HV](?:Stack|Grid)\b"), "a Lazy* container"),
    (re.compile(r"\.scrollPosition\s*\("), ".scrollPosition"),
    (re.compile(r"\bGeometryReader\b"), "GeometryReader"),
    (re.compile(r"\bScrollViewReader\b|\bscrollTo\s*\(|\bThemeCarouselLoop\b"), "an endless-loop mechanism"),
]


def layout_violations(src: str) -> List[str]:
    code = _code(src)
    return [label for rx, label in _LAYOUT_TRAPS if rx.search(code)]


def section_shape_violations(src: str) -> List[str]:
    body = type_body(_code(src), "TrillionClubSection")
    out = layout_violations(src)
    if "ScrollView(.horizontal" not in body:
        out.append("not a horizontal ScrollView")
    if not re.search(r"\bHStack\s*\(", body):
        out.append("no plain HStack")
    if ".scrollTargetBehavior(.viewAligned)" not in body:
        out.append("no view-aligned paging")
    if not re.search(r"if\s+!group\.isEmpty\s*\{", body):
        out.append("not hidden when empty")
    return out


def test_section_is_a_plain_non_looping_row():
    src = _src(SECTION)
    body = type_body(_code(src), "TrillionClubSection")
    assert "TrillionClubCard(" in body, "anti-vacuity: the section no longer builds cards"
    assert section_shape_violations(src) == []


@pytest.mark.parametrize("path", VIEW_FILES, ids=lambda p: p.name)
def test_no_layout_trap_in_any_club_view(path):
    assert layout_violations(_src(path)) == [], path.name


@pytest.mark.parametrize("mutation", [
    ("HStack(alignment: .top, spacing: AppSpacing.md) {", "LazyHStack(alignment: .top, spacing: AppSpacing.md) {"),
    (".scrollTargetBehavior(.viewAligned)", ".scrollTargetBehavior(.viewAligned).scrollPosition(id: $x)"),
    ("ScrollView(.horizontal, showsIndicators: false) {", "GeometryReader { _ in }\nScrollView(.horizontal, showsIndicators: false) {"),
    ("ScrollView(.horizontal, showsIndicators: false) {", "ScrollViewReader { proxy in }\nScrollView(.horizontal, showsIndicators: false) {"),
    (".scrollTargetBehavior(.viewAligned)", ".scrollTargetBehavior(.paging)"),
    ("if !group.isEmpty {", "if true {"),
])
def test_section_guard_fires_on_each_trap(mutation):
    src = _src(SECTION)
    old, new = mutation
    assert old in src
    assert section_shape_violations(src.replace(old, new, 1)) != []


def test_a_trap_mentioned_only_in_a_comment_does_not_fire():
    """The file header NAMES every trap it avoids — stripping comments is load-bearing."""
    src = _src(SECTION)
    assert "LazyVStack" in src or "Lazy*" in src, "anti-vacuity: the header no longer names the trap"
    assert ".scrollPosition(id:)" in src
    assert layout_violations(src) == []


# ══════════════════════════════════════════════════════════════════════════════════════
# 2. Chips are labels, not Buttons, and speak a full sentence
# ══════════════════════════════════════════════════════════════════════════════════════


def chip_violations(src: str) -> List[str]:
    code = _code(src)
    body = type_body(code, "ClubStakeChip")
    out = []
    for name, block in (("chip", body), ("chip group", type_body(code, "ClubChipGroup"))):
        if re.search(r"\bButton\b|\.onTapGesture\b|\bNavigationLink\b|\bLink\s*\(", block):
            out.append(f"the {name} is tappable")
    if ".accessibilityLabel(chip.accessibilityText(source: source))" not in body:
        out.append("no full-sentence VoiceOver label (naming the stake's source)")
    if ".accessibilityElement(children: .ignore)" not in body:
        out.append("VoiceOver reads the fragments instead of the sentence")
    return out


def test_chip_is_a_plain_label_with_a_sentence():
    src = _src(CHIP)
    assert "TintedTagBadge(" in type_body(_code(src), "ClubStakeChip"), "anti-vacuity"
    assert chip_violations(src) == []


@pytest.mark.parametrize("mutation", [
    ("        TintedTagBadge(\n", "        Button(action: {}) { EmptyView() }\n        TintedTagBadge(\n"),
    (".accessibilityLabel(chip.accessibilityText(source: source))", ".accessibilityLabel(chip.label)"),
    (".accessibilityLabel(chip.accessibilityText(source: source))", ".accessibilityLabel(chip.accessibilityText)"),
    ("            ForEach(sentences) { chip in\n", "            Button(\"x\") {}\n            ForEach(sentences) { chip in\n"),
    (".accessibilityElement(children: .ignore)", ".accessibilityElement(children: .contain)"),
])
def test_chip_guard_fires(mutation):
    src = _src(CHIP)
    old, new = mutation
    assert old in src
    assert chip_violations(src.replace(old, new, 1)) != []


def test_chip_sentences_are_full_sentences():
    strings = _strings(_src(MODELS))
    body = type_body(_code(_src(MODELS)), "ClubChip", kind="enum")
    assert "accessibilityText" in body
    for label in ("Private: not publicly traded, so there's no market price.",
                  "Non-U.S. listed: listed outside the U.S., so it never appears on a 13F.",
                  "Tied to a deal: this stake came with a business agreement between the two companies."):
        assert label in strings


# ══════════════════════════════════════════════════════════════════════════════════════
# 3. No gain/loss colour anywhere in the section
# ══════════════════════════════════════════════════════════════════════════════════════

_DIRECTIONAL = re.compile(
    r"AppColors\.(?:gain|loss|bullish|bearish)\w*|\bColor\.(?:green|red)\b|\(\s*\.(?:green|red)\s*\)")


def colour_violations(src: str) -> List[str]:
    return _DIRECTIONAL.findall(_code(src))


@pytest.mark.parametrize("path", VIEW_FILES, ids=lambda p: p.name)
def test_no_directional_colour(path):
    src = _src(path)
    assert "AppColors." in _code(src), f"anti-vacuity: {path.name} stripped to nothing"
    assert colour_violations(src) == [], f"{path.name} inks with a gain/loss colour"


@pytest.mark.parametrize("token", ["AppColors.gain", "AppColors.lossGraphic", "AppColors.bullish",
                                   "AppColors.bearish", "Color.green", "AppColors.gainFill"])
def test_colour_guard_fires(token):
    src = _src(ROW)
    anchor = "let ink = change == .newlyReported ? AppColors.primaryBlue : AppColors.textSecondary"
    assert anchor in src
    mutated = src.replace(anchor, f"let ink = change == .newlyReported ? {token} : AppColors.textSecondary")
    assert colour_violations(mutated) != []


def test_a_colour_named_in_a_comment_does_not_fire():
    assert colour_violations("// never AppColors.gain here\nlet x = AppColors.textSecondary\n") == []


# ══════════════════════════════════════════════════════════════════════════════════════
# 4. Entitlement reload, covers, and the presentation reset
# ══════════════════════════════════════════════════════════════════════════════════════


def detail_reload_violations(src: str) -> List[str]:
    code = _code(src)
    body = type_body(code, "TrillionClubDetailView")
    out = []
    if not re.search(r"@Environment\(AppState\.self\)\s+private\s+var\s+appState", body):
        out.append("does not read AppState")
    anchor = ".onChange(of: appState.entitlementGeneration)"
    if anchor not in body:
        return out + ["does not observe entitlementGeneration"]
    closure = _closure_after(body, anchor)
    if "viewModel.load()" not in closure:
        out.append("observes entitlementGeneration but does not reload")
    if re.search(r"\.onChange\(of:\s*appState\.user\.tier", body):
        out.append("observes user.tier (fires on every cold launch of a paid account)")
    return out


def test_detail_reloads_when_a_purchase_lands():
    assert detail_reload_violations(_src(DETAIL)) == []


@pytest.mark.parametrize("mutation", [
    (".onChange(of: appState.entitlementGeneration)", ".onChange(of: appState.presentationResetToken)"),
    ("                Task { await viewModel.load() }\n            }\n            .inAppBrowser",
     "                Task { }\n            }\n            .inAppBrowser"),
    ("@Environment(AppState.self) private var appState", "@Environment(\\.appState) private var appState"),
])
def test_detail_reload_guard_fires(mutation):
    src = _src(DETAIL)
    old, new = mutation
    assert old in src
    assert detail_reload_violations(src.replace(old, new, 1)) != []


_BOTH_ENVS = (".environment(appState)", ".environment(\\.appState, appState)")


def cover_violations(src: str, covers: Iterable[Tuple[str, str]]) -> List[str]:
    """Each `(binding, presented view)` cover must present that view and inject BOTH envs."""
    code = _code(src)
    out = []
    for binding, view in covers:
        anchor = f".fullScreenCover(item: ${binding})"
        if anchor not in code:
            out.append(f"no cover for {binding}")
            continue
        closure = _closure_after(code, anchor)
        if view not in closure:
            out.append(f"{binding} does not present {view}")
        for env in _BOTH_ENVS:
            if env not in closure:
                out.append(f"{binding} cover lacks {env}")
    return out


_HOME_COVERS = [("trillionClubTarget", "TrillionClubDetailView(slug:"),
                ("trillionClubProfileTarget", "WhaleProfileView(whaleId:")]
_DETAIL_COVERS = [("selectedTicker", "TickerDetailView(tickerSymbol:"),
                  ("profileTarget", "WhaleProfileView(whaleId:")]


def test_home_covers_inject_both_app_states():
    assert cover_violations(_src(HOME), _HOME_COVERS) == []


def test_detail_covers_inject_both_app_states():
    assert cover_violations(_src(DETAIL), _DETAIL_COVERS) == []


def test_cover_guard_fires_when_an_injection_is_dropped():
    src = _src(HOME)
    code_anchor = "TrillionClubDetailView(slug: target.slug)\n            }\n            .environment(appState)\n            .environment(\\.appState, appState)"
    assert code_anchor in src
    mutated = src.replace(code_anchor, "TrillionClubDetailView(slug: target.slug)\n            }\n            .environment(appState)")
    assert cover_violations(mutated, _HOME_COVERS) != []
    swapped = src.replace("TrillionClubDetailView(slug: target.slug)", "ThemeDetailView(slug: target.slug)", 1)
    assert cover_violations(swapped, _HOME_COVERS) != []


def reset_violations(src: str) -> List[str]:
    code = _code(src)
    body = type_body(code, "HomeDashboardView")
    reset = _closure_after(body, ".onPresentationReset")
    return [f"{name} is not cleared" for name, _ in _HOME_COVERS
            if not re.search(rf"\b{name}\s*=\s*nil\b", reset)]


def test_home_reset_clears_both_club_presentations():
    assert "themeDetailTarget = nil" in _closure_after(type_body(_code(_src(HOME)), "HomeDashboardView"),
                                                       ".onPresentationReset"), "anti-vacuity"
    assert reset_violations(_src(HOME)) == []


def test_reset_guard_fires():
    src = _src(HOME)
    assert "            trillionClubTarget = nil\n" in src
    assert reset_violations(src.replace("            trillionClubTarget = nil\n", "", 1)) != []


def test_paywall_sheet_names_the_context_and_injects_app_state():
    code = _code(_src(DETAIL))
    closure = _closure_after(code, ".sheet(isPresented: $showPaywall)")
    assert "PaywallView(context: .trillionClub)" in closure
    assert ".environment(\\.appState, appState)" in closure


def test_section_sits_between_frontiers_and_the_disclaimer():
    body = type_body(_code(_src(HOME)), "HomeDashboardView")
    content = _closure_after(body, "private var content: some View")
    themes = content.find("TrendingThemesSection(")
    club = content.find("TrillionClubSection(")
    disclaimer = content.find("InlineDisclaimerNotice()")
    assert -1 not in (themes, club, disclaimer), "a section moved out of Home's content"
    assert themes < club < disclaimer
    assert re.search(r"if\s+!data\.trillionClub\.isEmpty\s*\{\s*TrillionClubSection\(", content)


# ══════════════════════════════════════════════════════════════════════════════════════
# 5. Copy
# ══════════════════════════════════════════════════════════════════════════════════════

TITLE = "Trillion-Dollar Club Bets"

BANNED: List[Tuple[str, re.Pattern]] = [
    ("picks", re.compile(r"\bpicks?\b", re.I)),
    ("smart money", re.compile(r"\bsmart\s+money\b", re.I)),
    ("conviction", re.compile(r"\bconviction\b", re.I)),
    ("bullish", re.compile(r"\bbullish\b", re.I)),
    ("bearish", re.compile(r"\bbearish\b", re.I)),
    ("hot", re.compile(r"\bhot\b", re.I)),
    ("loaded up", re.compile(r"\bloaded\s+up\b", re.I)),
    ("vote of confidence", re.compile(r"\bvote\s+of\s+confidence\b", re.I)),
    ("endorse", re.compile(r"\bendors(?:e|es|ed|ement)\b", re.I)),
    ("secret", re.compile(r"\bsecret\b", re.I)),
    ("hidden", re.compile(r"\bhidden\b", re.I)),
    ("-backed", re.compile(r"\w-backed\b", re.I)),
    ("worth", re.compile(r"\bworth\b", re.I)),
    ("bought / buys / buying", re.compile(r"\b(?:bought|buys|buying)\b", re.I)),
    ("follow / copy / mirror", re.compile(r"\b(?:follow|copy|mirror)(?:s|ing|ed)?\b", re.I)),
    ("bet(s) outside the title", re.compile(r"\bbets?\b", re.I)),
]


def banned_hits(text: str) -> List[str]:
    """Banned words in ONE user-facing string. The section title is the only place "Bets"
    may appear, so it is removed before the scan, never whitelisted as a word."""
    text = text.replace(TITLE, "")
    return [name for name, rx in BANNED if rx.search(text)]


@pytest.mark.parametrize("text", ["Alphabet", "Holdings", "Photonics", "Coherent", "Hotel Shilla",
                                  "Mitsubishi", TITLE, "What the $1 trillion companies own in other companies",
                                  "not a recommendation to buy, sell or hold any security",
                                  "committed up to $10B", "not necessarily a new purchase"])
def test_banned_words_do_not_fire_on_legal_text(text):
    assert banned_hits(text) == [], text


@pytest.mark.parametrize("text", ["bullish", "They loaded up on it", "Their top picks", "NVIDIA-backed",
                                  "worth $5B", "Nvidia bought Intel", "Follow their strategy",
                                  "copy this", "a hot stock", "the secret portfolio", "hidden gems",
                                  "high conviction", "Smart Money", "your bets", "Bet on it",
                                  "a vote of confidence", "an endorsement"])
def test_banned_words_fire_on_advice_text(text):
    assert banned_hits(text) != [], text


def _user_literals(path: Path) -> List[str]:
    """Every string literal in the file, minus the logger messages (developer-facing, never
    shown) and the sample-data URLs (addresses, not copy)."""
    src = _src(path)
    code, strings = scan_swift(src)
    logged = set()
    for m in re.finditer(r"\.(?:debug|info|notice|warning|error|fault)\(\s*\"__S(\d+)__\"", code):
        logged.add(int(m.group(1)))
    out = []
    for i, s in enumerate(strings):
        if i in logged:
            continue
        s = re.sub(r"https://\S+", "", s)
        out.append(s)
    return out


@pytest.mark.parametrize("path", NEW_FILES, ids=lambda p: p.name)
def test_no_banned_word_in_any_club_string(path):
    literals = _user_literals(path)
    assert literals, f"anti-vacuity: no literals parsed from {path.name}"
    hits = [(s[:90], banned_hits(s)) for s in literals if banned_hits(s)]
    assert not hits, f"{path.name}: {hits}"


def test_the_literal_scan_would_catch_a_planted_word():
    src = _src(MODELS)
    anchor = '"+\\(TrillionClubFormat.grouped(more)) more in the details"'
    assert anchor in src
    mutated = src.replace(anchor, '"+\\(TrillionClubFormat.grouped(more)) more bullish picks"')
    _, strings = scan_swift(mutated)
    assert any(banned_hits(s) for s in strings)


def _paywall_arm(case: str, prop: str) -> str:
    code, strings = scan_swift(_src(SUBSCRIPTION))
    enum_body = type_body(code, "PaywallContext", kind="enum")
    m = re.search(rf"var {prop}: String \{{", enum_body)
    assert m, f"PaywallContext.{prop} not found"
    block = enum_body[m.end():match_brace(enum_body, m.end())]
    arm = re.search(rf"case \.{case}:\s*return \"__S(\d+)__\"", block)
    assert arm, f"PaywallContext.{prop} has no .{case} arm"
    return strings[int(arm.group(1))]


def test_paywall_context_has_clean_copy():
    headline = _paywall_arm("trillionClub", "headline")
    sub = _paywall_arm("trillionClub", "subheadline")
    assert headline and sub
    assert banned_hits(headline) == [] and banned_hits(sub) == []
    assert "priorit" not in (headline + sub).lower()   # the paywall-copy rule
    assert _paywall_arm("trillionClub", "featureKey") == "whale_detail"


def test_the_plans_exact_copy_is_present():
    models = _strings(_src(MODELS))
    required = [
        TITLE,
        "What the $1 trillion companies own in other companies",
        "From SEC filings and company reports · Not a recommendation",
        "Informational only — not a recommendation to buy, sell or hold any security.",
        "13F filer",
        "Private", "Non-U.S. listed", "Tied to a deal", "Commitment", "Club member",
        "Newly reported", "No longer reported",
        "First time on a 13F — not necessarily a new purchase",
        "Left the filing — sold, merged, too small to report, or kept confidential",
        "Open profile",
    ]
    for text in required:
        assert text in models, f"missing copy: {text!r}"
    rule = next(s for s in models if "straight trading days" in s)
    assert "joins after 10 straight trading days closing at $1 trillion or more" in rule
    assert "leaves after 20 straight days below" in rule
    assert any("Not a recommendation" in s for s in _strings(_src(INFO))) or \
        "TrillionClubCopy.detailFooter" in _code(_src(INFO))


def test_whale_help_copy_no_longer_reads_as_advice():
    """The Berkshire card links to the whale profile, so its help sheets must not say
    "follow their strategy", "bullish" or "conviction" (plan M5)."""
    code, strings = scan_swift(_src(WHALE))
    for sheet in ("SectorExposureInfoSheet", "RecentTradesInfoSheet"):
        body = type_body(code, sheet)
        literals = [strings[int(i)] for i in re.findall(r'"__S(\d+)__"', body)]
        assert len(literals) > 5, f"anti-vacuity: {sheet} parsed no copy"
        hits = [(s, banned_hits(s)) for s in literals if banned_hits(s) or "opportunit" in s.lower()]
        assert not hits, f"{sheet}: {hits}"


# ══════════════════════════════════════════════════════════════════════════════════════
# 6. Endpoint, auth policy, error mapping
# ══════════════════════════════════════════════════════════════════════════════════════


def test_endpoint_path_matches_the_backend_route():
    code, strings = scan_swift(_src(ENDPOINT))
    assert re.search(r"^\s*case getTrillionClubDetail\(slug: String\)", code, flags=re.MULTILINE)
    arm = re.search(r"case \.getTrillionClubDetail\(let slug\):\s*return \"__S(\d+)__\"", code)
    assert arm, "no path arm"
    assert strings[int(arm.group(1))] == "/api/v1/home/trillion-club/\\(slug)"
    home = (BACKEND / "app/api/v1/endpoints/home.py").read_text(encoding="utf-8")
    api = (BACKEND / "app/api/v1/api.py").read_text(encoding="utf-8")
    assert '@router.get("/trillion-club/{slug}"' in home
    assert re.search(r'include_router\(home\.router,\s*prefix="/home"', api)


def test_endpoint_is_sign_in_required():
    from test_ios_auth_policy_parity import _policy_by_case
    policies = _policy_by_case(_src(ENDPOINT))
    assert len(policies) >= 140, "anti-vacuity: the policy parser drifted"
    assert policies.get("getTrillionClubDetail") == "signInRequired"


def test_not_found_code_is_mapped_to_a_typed_error():
    from app.api.error_response import ErrorCode
    assert ErrorCode.TRILLION_CLUB_COMPANY_NOT_FOUND.value == "TRILLION_CLUB_COMPANY_NOT_FOUND"
    code, strings = scan_swift(_src(APP_ERROR))
    idx = strings.index("TRILLION_CLUB_COMPANY_NOT_FOUND")
    m = re.search(rf'if code == "__S{idx}__" \{{\s*return \.notFound\(resource: "__S(\d+)__"\)', code)
    assert m, "TRILLION_CLUB_COMPANY_NOT_FOUND falls through to a generic error"
    assert strings[int(m.group(1))] == "company"


def test_view_model_routes_errors_through_app_error():
    code = _code(_src(DETAIL_VM))
    body = type_body(code, "TrillionClubDetailViewModel", kind="class")
    assert "AppError.from(error)" in body
    assert ".getTrillionClubDetail(slug: slug)" in body
    assert "generation == loadGeneration" in body, "a stale reload could overwrite a fresh one"
    assert re.search(r"final class TrillionClubDetailViewModel:\s*ObservableObject", code)


# ══════════════════════════════════════════════════════════════════════════════════════
# 7. Hardening fixes (2026-09-24) — one guard per fix, each mutation-tested below it
# ══════════════════════════════════════════════════════════════════════════════════════

THEME = IOS / "Theme/AppTheme.swift"
FILTER_CHIP = IOS / "Views/Atoms/AccentFilterChip.swift"


def _chain_after(block: str, anchor: str) -> str:
    """The modifier lines (`.x(...)`) that directly follow the line holding `anchor`."""
    at = block.find(anchor)
    assert at != -1, f"anchor {anchor!r} not found — the scan drifted"
    lines = block[at:].split("\n")
    out = [lines[0]]
    for line in lines[1:]:
        if not line.strip().startswith("."):
            break
        out.append(line)
    return "\n".join(out)


def _replace_once(src: str, old: str, new: str) -> str:
    assert src.count(old) == 1, f"mutation anchor {old[:60]!r} is not unique — update the mutation"
    return src.replace(old, new)


# ── 7.1 The info sheet's member list carries its own meaning ─────────────────────────
# The detail used to pass `otherMembers` (EVERY other member) to the sheet's only list, captioned
# "Members with no disclosed stake large enough for a card." — and named Microsoft, Alphabet and
# nine more carded companies as having no disclosed stake.

_NO_CARD = "no disclosed stake"


def members_violations(info_src: str, detail_src: str, home_src: str) -> List[str]:
    out = []
    code, strings = scan_swift(info_src)
    sheet = type_body(code, "TrillionClubInfoSheet")
    members = type_body(sheet, "Members", kind="enum")
    caption = _closure_after(members, "var caption: String?")
    arm = re.search(r'case \.withoutCard:\s*return "__S(\d+)__"', caption)
    if not arm or _NO_CARD not in strings[int(arm.group(1))].lower():
        out.append("the no-card caption is not the .withoutCard arm")
    if not re.search(r"case \.others:\s*return nil", caption):
        out.append(".others carries a caption")
    if sum(_NO_CARD in s.lower() for s in strings) != 1:
        out.append("the no-card sentence is written somewhere other than the .withoutCard arm")
    view = sheet.replace(members, "")
    if "members.heading" not in view or "members.caption" not in view:
        out.append("the sheet does not render the list's own heading and caption")
    detail = type_body(_code(detail_src), "TrillionClubDetailView")
    if "TrillionClubInfoSheet(members: .others(viewModel.detail?.otherMembers" not in detail:
        out.append("the detail's sheet does not pass .others(otherMembers)")
    if ".withoutCard(" in detail:
        out.append("the detail puts a list under the no-card caption")
    home = type_body(_code(home_src), "HomeDashboardView")
    if not re.search(r"TrillionClubInfoSheet\(members:\s*\.withoutCard\([^)]*trillionClub\.alsoInClub", home):
        out.append("Home's sheet does not pass .withoutCard(trillionClub.alsoInClub)")
    return out


def test_the_member_list_is_never_mislabelled():
    assert members_violations(_src(INFO), _src(DETAIL), _src(HOME)) == []


def test_member_guard_fires():
    info, detail, home = _src(INFO), _src(DETAIL), _src(HOME)
    as_no_card = _replace_once(detail, "TrillionClubInfoSheet(members: .others(", "TrillionClubInfoSheet(members: .withoutCard(")
    assert members_violations(info, as_no_card, home)
    swapped = _replace_once(
        info,
        'case .withoutCard: return "Members with no disclosed stake large enough for a card."\n'
        "            case .others: return nil",
        "case .withoutCard: return nil\n"
        '            case .others: return "Members with no disclosed stake large enough for a card."')
    assert members_violations(swapped, detail, home)
    home_others = _replace_once(home, "TrillionClubInfoSheet(members: .withoutCard(", "TrillionClubInfoSheet(members: .others(")
    assert members_violations(info, detail, home_others)


# ── 7.2 The section presents nothing; Home owns (and resets) the info sheet ───────────


def section_presentation_violations(section_src: str, home_src: str) -> List[str]:
    out = []
    body = type_body(_code(section_src), "TrillionClubSection")
    if re.search(r"\.sheet\s*\(|\.fullScreenCover\s*\(|\.popover\s*\(|@State\b", body):
        out.append("the section owns a presentation Home's reset cannot reach")
    if not re.search(r"Button\s*\{\s*onInfoTap\(\)\s*\}", body):
        out.append("the info button does not call onInfoTap")
    home = type_body(_code(home_src), "HomeDashboardView")
    if not re.search(r"\bshowTrillionClubInfo\s*=\s*false\b", _closure_after(home, ".onPresentationReset")):
        out.append("Home's presentation reset does not clear the club info sheet")
    anchor = ".sheet(isPresented: $showTrillionClubInfo)"
    if anchor not in home or "TrillionClubInfoSheet(" not in _closure_after(home, anchor):
        out.append("Home does not present the club info sheet")
    if not re.search(r"onInfoTap:\s*\{\s*showTrillionClubInfo\s*=\s*true\s*\}", home):
        out.append("Home's section call does not open the sheet")
    return out


def test_the_info_sheet_is_cleared_by_home_reset():
    assert section_presentation_violations(_src(SECTION), _src(HOME)) == []


def test_section_presentation_guard_fires():
    section, home = _src(SECTION), _src(HOME)
    owned = _replace_once(section, "    let onInfoTap: () -> Void\n",
                          "    let onInfoTap: () -> Void\n    @State private var showInfo = false\n")
    assert section_presentation_violations(owned, home)
    sheet = _replace_once(section, "                .scrollTargetBehavior(.viewAligned)\n",
                          "                .scrollTargetBehavior(.viewAligned)\n                .sheet(isPresented: .constant(false)) { EmptyView() }\n")
    assert section_presentation_violations(sheet, home)
    assert section_presentation_violations(section, _replace_once(home, "            showTrillionClubInfo = false\n", ""))


# ── 7.3 The whole card is the tap target ───────────────────────────────────────────


def card_tap_violations(card_src: str) -> List[str]:
    card = type_body(_code(card_src), "TrillionClubCard")
    body = _closure_after(card, "var body: some View")
    m = re.search(r"Button\(action:\s*onTap\)\s*\{", body)
    if not m:
        return ["no main Button"]
    label = body[m.end() - 1:match_brace(body, m.end()) + 1]
    out = []
    if not re.search(r"\.frame\(maxWidth:\s*\.infinity,\s*maxHeight:\s*\.infinity", label):
        out.append("the Button's label does not take the card's full height")
    if ".contentShape(Rectangle())" not in label:
        out.append("the Button's empty area is not hit-testable")
    if re.search(r"\bSpacer\s*\(", body):
        out.append("a Spacer in the card body is card surface outside the Button")
    return out


def test_the_whole_card_is_the_tap_target():
    assert card_tap_violations(_src(CARD)) == []


def test_card_tap_guard_fires():
    src = _src(CARD)
    assert card_tap_violations(_replace_once(src, ".frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)",
                                             ".frame(maxWidth: .infinity, alignment: .leading)"))
    assert card_tap_violations(_replace_once(src, "\n            if company.kind == .whaleLink, company.whaleId != nil {",
                                             "\n            Spacer(minLength: 0)\n            if company.kind == .whaleLink, company.whaleId != nil {"))


# ── 7.4 Card text wraps; the stake's source keeps its date ─────────────────────────


def card_wrap_violations(card_src: str) -> List[str]:
    card = type_body(_code(card_src), "TrillionClubCard")
    out = []
    for decl in ("private func stakeLine(", "private var header: some View"):
        if ".lineLimit(1)" in _closure_after(card, decl):
            out.append(f"{decl}: a one-line cap truncates")
    source = _chain_after(_closure_after(card, "private func stakeLine("), "Text(stake.sourceText)")
    if ".fixedSize(horizontal: false, vertical: true)" not in source or ".lineLimit(" in source:
        out.append("the source line does not wrap")
    return out


def test_card_text_wraps():
    assert card_wrap_violations(_src(CARD)) == []


def test_card_wrap_guard_fires():
    src = _src(CARD)
    name = ("                    .font(AppTypography.headingSmall)\n"
            "                    .foregroundColor(AppColors.textPrimary)\n"
            "                    .lineLimit(2)\n")
    assert card_wrap_violations(_replace_once(src, name, name.replace("lineLimit(2)", "lineLimit(1)")))
    source = ("                .foregroundColor(AppColors.textMuted)\n"
              "                .fixedSize(horizontal: false, vertical: true)\n"
              "            let chips")
    assert card_wrap_violations(_replace_once(src, source, source.replace(
        ".fixedSize(horizontal: false, vertical: true)", ".lineLimit(1)")))


# ── 7.5 The compact row gives the NAME the room ────────────────────────────────────


def compact_row_violations(row_src: str) -> List[str]:
    body = _closure_after(_code(row_src), "private var compactRow: some View")
    column = re.search(r"VStack\([^)]*\)\s*\{", body)
    if not column:
        return ["the name has no column of its own"]
    col = body[column.end() - 1:match_brace(body, column.end()) + 1]
    out = []
    if "Text(position.name)" not in col or "ClubStakeChip(chip: .clubMember)" not in col:
        out.append("the Club member chip is not on its own line under the name")
    if ".lineLimit(1)" in body:
        out.append("the name is capped at one line")
    if re.search(r"ClubStakeChip\(chip: \.clubMember\)\s*\.fixedSize\(\)", body):
        out.append("the chip is pinned to its full width beside the name")
    return out


def test_compact_row_gives_the_name_room():
    assert compact_row_violations(_src(ROW)) == []


_OLD_COMPACT = '''        HStack(spacing: AppSpacing.sm) {
            Text(position.name)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textPrimary)
                .lineLimit(1)
            if position.clubMemberSlug != nil {
                ClubStakeChip(chip: .clubMember)
                    .fixedSize()
                    .layoutPriority(1)
            }
            Spacer(minLength: AppSpacing.xs)'''


def test_compact_row_guard_fires():
    src = _src(ROW)
    start = src.index("        HStack(alignment: .firstTextBaseline, spacing: AppSpacing.sm) {")
    end = src.index("            Spacer(minLength: AppSpacing.xs)", start) + len("            Spacer(minLength: AppSpacing.xs)")
    reverted = src[:start] + _OLD_COMPACT + src[end:]
    assert compact_row_violations(reverted)


# ── 7.6 Sentences stay out of FlowLayout ───────────────────────────────────────────
# FlowLayout measured and placed each child at its ONE-LINE width until 2026-09-24, so a sentence
# inside it never wrapped: "Small position · under 1% …" ran into the weight column, and "Listed
# since … — not on a 13F yet" (279pt) ran past a 260pt card. The atom now caps a child at the row
# width (pinned by test_ios_flow_layout_guards.py); sentences still get a line of their own.


def flow_violations(src: str) -> List[str]:
    code = _code(src)
    out = []
    for m in re.finditer(r"FlowLayout\([^)]*\)\s*\{", code):
        block = code[m.end() - 1:match_brace(code, m.end()) + 1]
        if re.search(r"\bText\(|smallText|\.listedSince|ForEach\(chips\)|ForEach\(sentences\)", block):
            out.append(f"a sentence can reach FlowLayout: {block[:80]!r}")
    return out


def chip_group_violations(chip_src: str, card_src: str, detail_src: str) -> List[str]:
    out = []
    group = type_body(_code(chip_src), "ClubChipGroup")
    if "filter(\\.isSentence)" not in group or "ForEach(words)" not in group or "ForEach(sentences)" not in group:
        out.append("ClubChipGroup no longer splits word chips from sentence chips")
    for name, src in (("card", card_src), ("detail", detail_src)):
        if "ClubChipGroup(chips: chips, source: stake.sourceTitle)" not in _code(src):
            out.append(f"the {name} lays stake chips out without ClubChipGroup")
    return out


@pytest.mark.parametrize("path,has_flow", [(ROW, True), (DETAIL, True), (CHIP, True), (INFO, True),
                                           (CARD, False)], ids=lambda p: getattr(p, "name", str(p)))
def test_no_sentence_inside_a_flow_layout(path, has_flow):
    """The card lays its chips out only through `ClubChipGroup` (no FlowLayout of its own)."""
    code = _code(_src(path))
    assert ("FlowLayout(" in code) is has_flow, f"{path.name}: FlowLayout presence changed — re-derive"
    assert flow_violations(_src(path)) == []


def test_stake_chips_go_through_the_chip_group():
    assert chip_group_violations(_src(CHIP), _src(CARD), _src(DETAIL)) == []


def test_flow_guards_fire():
    row = _src(ROW)
    moved = _replace_once(row, "                        if position.clubMemberSlug != nil {\n"
                               "                            ClubStakeChip(chip: .clubMember)\n"
                               "                        }\n",
                          "                        if position.clubMemberSlug != nil {\n"
                          "                            ClubStakeChip(chip: .clubMember)\n"
                          "                        }\n"
                          "                        if let small = position.smallText { Text(small) }\n")
    assert flow_violations(moved)
    card = _replace_once(_src(CARD), "ClubChipGroup(chips: chips, source: stake.sourceTitle)",
                         "FlowLayout(spacing: AppSpacing.xs) { ForEach(chips) { ClubStakeChip(chip: $0) } }")
    assert flow_violations(card) and chip_group_violations(_src(CHIP), card, _src(DETAIL))
    chip = _replace_once(_src(CHIP), "ForEach(words) {", "ForEach(chips) {")
    assert flow_violations(chip) and chip_group_violations(chip, _src(CARD), _src(DETAIL))


# ── 7.7 Segment chips clear 4.5:1, COMPOSED (ink on a tint of itself over the page) ───


def _token_hex(theme_src: str, name: str) -> Tuple[str, str]:
    m = re.search(rf'static let {name} = Color\(lightHex: "([0-9A-Fa-f]{{6}})", darkHex: "([0-9A-Fa-f]{{6}})"', theme_src)
    assert m, f"AppColors.{name} not found as a lightHex/darkHex pair"
    return m.group(1), m.group(2)


def _rgb(hexstr: str) -> Tuple[float, float, float]:
    return tuple(int(hexstr[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _luminance(rgb) -> float:
    def lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def segment_contrast(detail_src: str, theme_src: str, chip_src: str) -> List[Tuple[str, float]]:
    """(appearance, ratio) of the RESTING segment label: `accent` ink on `accent @ opacity`
    blended over the page background — what the eye actually sees."""
    picker = _closure_after(type_body(_code(detail_src), "TrillionClubDetailView"), "private var segmentPicker: some View")
    token = re.search(r"accent:\s*AppColors\.(\w+)", picker)
    assert token, "segmentPicker passes no accent token"
    opacity = float(re.search(r"accent\.opacity\(([\d.]+)\)", _code(chip_src)).group(1))
    ink, page = _token_hex(theme_src, token.group(1)), _token_hex(theme_src, "background")
    out = []
    for i, mode in enumerate(("light", "dark")):
        fg, bg = _rgb(ink[i]), _rgb(page[i])
        tint = tuple(opacity * f + (1 - opacity) * b for f, b in zip(fg, bg))
        out.append((mode, round(_contrast(fg, tint), 2)))
    return out


def test_segment_chips_clear_text_contrast_in_both_appearances():
    ratios = segment_contrast(_src(DETAIL), _src(THEME), _src(FILTER_CHIP))
    assert all(r >= 4.5 for _, r in ratios), ratios


def test_segment_contrast_guard_fires():
    """primaryBlue on its own 15% tint is 3.87:1 on the light page — the shipped defect."""
    mutated = _replace_once(_src(DETAIL), "accent: AppColors.textSecondary,", "accent: AppColors.primaryBlue,")
    ratios = dict(segment_contrast(mutated, _src(THEME), _src(FILTER_CHIP)))
    assert ratios["light"] < 4.5, ratios


# ── 7.8 Section titles are VoiceOver headings ─────────────────────────────────────


def heading_violations(info_src: str, detail_src: str) -> List[str]:
    out = []
    sheet = type_body(_code(info_src), "TrillionClubInfoSheet")
    section = _closure_after(sheet, "private func section(title: String, body: String) -> some View")
    if ".accessibilityAddTraits(.isHeader)" not in _chain_after(section, "Text(title)"):
        out.append("an info-sheet section title is not a heading")
    if ".accessibilityElement(children: .combine)" in section:
        out.append("an info-sheet section is combined into one element (its title stops being a heading)")
    if ".accessibilityAddTraits(.isHeader)" not in _chain_after(sheet, "Text(members.heading)"):
        out.append("the member list's title is not a heading")
    stakes = _closure_after(type_body(_code(detail_src), "TrillionClubDetailView"), "private func stakesSection(")
    if ".accessibilityAddTraits(.isHeader)" not in _chain_after(stakes, "Text(title)"):
        out.append("the detail's stakes-section title is not a heading")
    return out


def test_section_titles_are_headings():
    assert heading_violations(_src(INFO), _src(DETAIL)) == []


def test_heading_guard_fires():
    info, detail = _src(INFO), _src(DETAIL)
    title = ("                .fixedSize(horizontal: false, vertical: true)\n"
             "                .accessibilityAddTraits(.isHeader)\n"
             "            Text(body)")
    assert heading_violations(_replace_once(info, title, title.replace("                .accessibilityAddTraits(.isHeader)\n", "")), detail)
    combined = _replace_once(info, "                .fixedSize(horizontal: false, vertical: true)\n        }\n    }\n}",
                             "                .fixedSize(horizontal: false, vertical: true)\n        }\n"
                             "        .accessibilityElement(children: .combine)\n    }\n}")
    assert heading_violations(combined, detail)


# ── 7.9 The info sheet's 13F facts ────────────────────────────────────────────────
# A 13F is due WITHIN 45 days (Amazon filed Q1 2026 after 35), and it does list some
# non-stock securities (convertible notes, options) — the builder drops those rows.


def info_fact_violations(info_src: str) -> List[str]:
    text = "".join(_strings(info_src))
    out = []
    if re.search(r"at least six weeks|\bdue 45 days\b|only covers U\.S\.-listed stocks", text):
        out.append("a false 13F timing / scope claim")
    if not re.search(r"\b(?:up to|within) 45 days\b", text):
        out.append("the 45 days is not stated as a deadline")
    if "convertible notes" not in text or "options" not in text:
        out.append("the sheet no longer says a 13F lists some securities besides stocks")
    return out


def test_info_sheet_states_the_13f_facts_correctly():
    assert info_fact_violations(_src(INFO)) == []


def test_info_fact_guard_fires():
    src = _src(INFO)
    old = '"A 13F is filed up to 45 days after the quarter ends, so its "'
    assert info_fact_violations(_replace_once(src, old, '"13F filings are due 45 days after the quarter ends, so the most "'))
    assert info_fact_violations(_replace_once(src, '"as convertible notes and options — we leave those out. Private "',
                                              '"as warrants — we leave those out. Private "'))


# ── 7.10 Model copy: explainers and the Commitment sentence ────────────────────────


def model_copy_violations(models_src: str) -> List[str]:
    code, strings = scan_swift(models_src)
    out = []
    if any("annual report" in s.lower() for s in strings):
        out.append("a card names 'annual report' (Samsung's stakes are from interim statements)")
    if "Each stake names its source and date." not in strings:
        out.append("the non-U.S. explainer changed")
    explainer = _closure_after(code, "var explainer: String?")
    if not re.search(r"case \.whaleLink:\s*return\s+whaleId\s*!=\s*nil\s*\?", explainer):
        out.append("the profile sentence is not conditioned on a whale id")
    chip = type_body(code, "ClubChip", kind="enum")
    chip_lits = [strings[int(i)] for i in re.findall(r'"__S(\d+)__"', chip)]
    for banned in ("agreed to invest", "holds today", "not shares"):
        if any(banned in s for s in chip_lits):
            out.append(f"a chip sentence claims {banned!r}")
    if not any("not a reported holding" in s for s in chip_lits):
        out.append("the Commitment sentence changed")
    return out


def test_model_copy_is_source_neutral_and_profile_honest():
    assert model_copy_violations(_src(MODELS)) == []


def test_model_copy_guard_fires():
    src = _src(MODELS)
    assert model_copy_violations(_replace_once(src, '"Each stake names its source and date."',
                                               '"Stakes are from its own annual report."'))
    assert model_copy_violations(_replace_once(src, "            return whaleId != nil\n                ? ",
                                               "            return true\n                ? "))
    assert model_copy_violations(_replace_once(
        src, 'return "Commitment: a commitment or right disclosed \\(from), not a reported holding."',
        'return "Commitment: an amount the company has agreed to invest, not shares it holds today."'))


# ── 7.11 The detail and the card render through the model's rules ────────────────


def rendering_rule_violations(detail_src: str, card_src: str, models_src: str) -> List[str]:
    out = []
    code, strings = scan_swift(detail_src)
    detail = type_body(code, "TrillionClubDetailView")
    content = _closure_after(detail, "private var content: some View")
    if "if detail.showsThirteenFSegments {" not in content:
        out.append("the 13F segments are not gated on a filing being on file")
    history = _closure_after(detail, "private func historySection(")
    if "if detail.showsHistoryLock {" not in history or re.search(r"if\s+detail\.isLocked\b", history):
        out.append("the History lock shows with nothing behind it")
    if "detail.changesEmptyText" not in _closure_after(detail, "private func changesSection("):
        out.append("the Changes empty text ignores the comparison")
    if any("No share-count changes" in s for s in strings):
        out.append("the detail hard-codes a 'no changes' claim")
    card, card_strings = scan_swift(card_src)
    # The original defect lived INSIDE an interpolation ("+\(company.stakes.count - …) more"),
    # which the scanner lifts out of `code` — so the lifted strings are searched too.
    block = _closure_after(type_body(card, "TrillionClubCard"), "private var stakesBlock: some View")
    if (re.search(r"stakes\.count\s*-", card) or any(re.search(r"stakes\.count\s*-", t) for t in card_strings)
            or "company.moreStakesText(shown:" not in block):
        out.append("the card counts '+N more' from its material stakes")
    for name, src in (("card", card), ("detail", code)):
        if "Text(company.monogram)" not in src or "name.prefix(" in src:
            out.append(f"the {name}'s letter tile is not the name's first letter")
    if "logoSymbol: ClubSanitize.usTicker(dto.logoSymbol)" not in _code(models_src):
        out.append("a non-U.S. logo symbol reaches the logo CDN")
    return out


def test_views_render_through_the_models_rules():
    assert rendering_rule_violations(_src(DETAIL), _src(CARD), _src(MODELS)) == []


def test_rendering_rule_guard_fires():
    detail, card, models = _src(DETAIL), _src(CARD), _src(MODELS)
    for old, new in [("if detail.showsThirteenFSegments {", "if detail.company.kind == .thirteenF {"),
                     ("if detail.showsHistoryLock {", "if detail.isLocked {"),
                     ("if let text = detail.changesEmptyText {", "if let text = Optional(\"No share-count changes vs the quarter before.\") {"),
                     ("            Text(company.monogram)\n                .font(AppTypography.heading)",
                      "            Text(String(company.name.prefix(1)))\n                .font(AppTypography.heading)")]:
        assert rendering_rule_violations(_replace_once(detail, old, new), card, models), old
    assert rendering_rule_violations(detail, _replace_once(card, "if let more = company.moreStakesText(shown: shownStakes) {\n",
                                                           "if let more = Optional(\"+\\(company.stakes.count - 2) more\") {\n"), models)
    assert rendering_rule_violations(detail, card, _replace_once(models, "logoSymbol: ClubSanitize.usTicker(dto.logoSymbol)",
                                                                 "logoSymbol: ClubSanitize.symbol(dto.logoSymbol)"))
