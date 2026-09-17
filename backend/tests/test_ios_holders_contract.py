"""Holders-tab contract guards for the 2026-09-16 TestFlight fixes (E3 + E5).

E3 — the Congress segment is Pro/Max. The server redacts it for Free and raises
`congress_locked`; iOS must (a) decode the pair as OPTIONAL (a backend predating it still
decodes), (b) keep `congressData` non-Optional (the wire is empty-but-well-formed, never
null), (c) show the locked stub BEFORE the empty state (a redacted series would otherwise
print "No congress activity data available"), (d) keep Cay AI from narrating a zeroed
Congress line, and (e) refetch on `entitlementGeneration` because the DTO is cached 24h.

E5 — `institutions_unknown` (an implausible 13F aggregate is UNKNOWN, not 100%). The wire
floats stay non-Optional (shipped builds decode a plain Double); the companion flag is
Optional on iOS and drives the "—" formatters and the muted remainder bar.

Comment-stripped, brace-bounded, mutation-tested by hand (`.claude/rules/testing.md` §3).
"""
from __future__ import annotations

import pathlib
import re

_REPO = pathlib.Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios" / "ios"
_REPO_SWIFT = _IOS / "Core" / "Repositories" / "StockRepository.swift"
_MODELS = _IOS / "Models" / "HoldersModels.swift"
_SELECTOR = _IOS / "Views" / "Molecules" / "SmartMoneyTabSelector.swift"
_SMART = _IOS / "Views" / "Organisms" / "SmartMoneySection.swift"
_RECENT = _IOS / "Views" / "Organisms" / "RecentActivitiesSection.swift"
_HOLDERS_CONTENT = _IOS / "Views" / "Organisms" / "TickerHoldersContent.swift"
_VM = _IOS / "ViewModels" / "TickerDetailViewModel.swift"
_VIEW = _IOS / "Views" / "Screens" / "TickerDetailView.swift"
_LOCKED_CARD = _IOS / "Views" / "Molecules" / "LockedSectionCard.swift"
_SUBS = _IOS / "Models" / "SubscriptionModels.swift"


def _strip_comments(src: str) -> str:
    """Block comments, full-line `//` comments AND trailing `//` comments — a trailing
    comment on a code line (`foo() // fetchHolders(...)`) must not satisfy an `in`."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())


def _decl_body(src: str, prefix: str) -> str:
    at = src.index(prefix)
    start = src.index("{", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start: i + 1]
    raise AssertionError(f"unbalanced braces after {prefix!r}")


def _code(path: pathlib.Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return _strip_comments(path.read_text(encoding="utf-8"))


# ── E3: decode ──────────────────────────────────────────────────────────────

def test_holders_dto_decodes_the_lock_pair_as_optional():
    body = _decl_body(_code(_REPO_SWIFT), "struct HoldersResponseDTO")
    assert re.search(r"let congressLocked:\s*Bool\?", body), body
    assert re.search(r"let congressTierRequired:\s*String\?", body), body
    assert 'case congressLocked = "congress_locked"' in body
    assert 'case congressTierRequired = "congress_tier_required"' in body
    assert "isCongressLocked: congressLocked ?? false" in body
    # anti-vacuity + the wire contract: the segment itself stays REQUIRED (never null)
    assert re.search(r"let congressData:\s*SmartMoneyDataDTO\b(?!\?)", body), body


def test_holders_model_defaults_the_lock_to_false():
    body = _decl_body(_code(_MODELS), "struct HoldersData")
    assert re.search(r"var isCongressLocked:\s*Bool\s*=\s*false", body), body


# ── E3: rendering ───────────────────────────────────────────────────────────

def test_tab_selector_marks_locked_tabs():
    body = _decl_body(_code(_SELECTOR), "struct SmartMoneyTabSelector")
    assert "var lockedTabs: Set<SmartMoneyTab>" in body
    assert '"lock.fill"' in body
    assert ", locked" in body
    assert "SmartMoneyTab.allCases" in body  # anti-vacuity


def test_smart_money_section_gates_congress_before_the_empty_state():
    src = _code(_SMART)
    body = _decl_body(src, "var body: some View")
    assert "lockedTabs: holdersData.isCongressLocked ? [.congress] : []" in body
    gate = body.index("selectedTab == .congress && holdersData.isCongressLocked")
    empty = body.index("No \\(selectedTab.rawValue.lowercased()) activity data available")
    assert gate < empty, "the locked stub must be reachable before the empty-state branch"
    assert "LockedSectionCard(" in body
    assert "PaywallView(context: .congressHolders)" in body
    assert re.search(r"PaywallView\(context: \.congressHolders\)\s*\.environment\(\\\.appState, appState\)", body), body


def test_recent_activities_congress_rows_are_gated():
    src = _code(_RECENT)
    body = _decl_body(src, "var body: some View")
    arm = body[body.index("case .congress:"):]
    assert arm.index("isCongressLocked") < arm.index("congressContent")
    assert "LockedSectionCard(" in arm
    assert "PaywallView(context: .congressHolders)" in body
    host = _code(_HOLDERS_CONTENT)
    assert "isCongressLocked: holdersData.isCongressLocked" in host


def test_locked_section_card_is_the_shared_molecule():
    body = _decl_body(_code(_LOCKED_CARD), "struct LockedSectionCard")
    assert '"lock.fill"' in body and "AppColors.primaryBlue" in body
    assert '"Upgrade to unlock"' in body
    assert ".buttonStyle(.plain)" in body
    assert '.accessibilityLabel("\\(title), locked")' in body
    # the whale profile delegates to it rather than keeping a private copy
    whale = _code(_IOS / "Views" / "Screens" / "WhaleProfileView.swift")
    assert "LockedSectionCard(title: title, message: message)" in whale


def test_locked_card_nested_in_a_card_takes_the_nested_fill():
    """ios-swiftui.md: a card inside a card MUST take `cardBackgroundNested` or it measures
    1.00:1 against its parent in dark and vanishes. The Holders tab hosts the stub inside
    the Smart Money and Recent Activities cards; the whale profile hosts it standalone."""
    body = _decl_body(_code(_LOCKED_CARD), "struct LockedSectionCard")
    assert re.search(r"var nested:\s*Bool\s*=\s*false", body), body
    assert re.search(
        r"\.cardSurface\(nested \? AppColors\.cardBackgroundNested : AppColors\.cardBackground", body
    ), body
    smart = _decl_body(_code(_SMART), "var body: some View")
    assert re.search(r"LockedSectionCard\([^)]*nested: true\)", smart), smart
    recent = _decl_body(_code(_RECENT), "var body: some View")
    assert re.search(r"LockedSectionCard\([^)]*nested: true\)", recent), recent
    whale = _code(_IOS / "Views" / "Screens" / "WhaleProfileView.swift")
    assert "nested: true" not in whale, "the whale profile's stub is a top-level card"


def test_recent_activities_selector_marks_the_locked_tab_like_smart_money():
    sel = _decl_body(
        _code(_IOS / "Views" / "Molecules" / "RecentActivitiesTabSelector.swift"),
        "struct RecentActivitiesTabSelector",
    )
    assert "let lockedTabs: Set<RecentActivitiesTab>" in sel
    assert "lockedTabs.contains(tab)" in sel and '"lock.fill"' in sel and ", locked" in sel
    body = _decl_body(_code(_RECENT), "var body: some View")
    assert "lockedTabs: isCongressLocked ? [.congress] : []" in body, body


def test_the_holders_cache_dies_with_the_session():
    """auth.md §7: a store keyed without a user id is reset when a session ends. The holders
    payload is TIER-SHAPED (`congress_locked`), so a Pro session's 24 h entry must not be
    served to the Free account that signs in next on this phone."""
    repo = _code(_REPO_SWIFT)
    clear = _decl_body(repo, "func clearForEndedSession()")
    assert "clearCache()" in clear, clear
    app = _code(_IOS / "Core" / "State" / "AppState.swift")
    funnel = _decl_body(app, "private func discardDataForEndedSession()")
    assert "StockRepository.shared.clearForEndedSession()" in funnel, "not in the session-end funnel"


def test_paywall_context_congress_holders_names_a_served_feature():
    src = _code(_SUBS)
    body = _decl_body(src, "enum PaywallContext")
    assert 'case congressHolders = "congress_holders"' in body
    key = _decl_body(body, "var featureKey: String")
    assert re.search(r"case \.congressHolders:\s*return \"signals\"", key), key


# ── E3: Cay AI context + refetch ────────────────────────────────────────────

def test_holders_context_skips_congress_when_locked():
    body = _decl_body(_code(_VM), "var holdersContext: String?")
    gate = _decl_body(body, "if !data.isCongressLocked")
    assert 'flowLine("Congress"' in gate, "the Congress line must sit INSIDE the not-locked gate"
    assert body.count('flowLine("Congress"') == 1, "a second, ungated Congress line exists"
    assert 'flowLine("Insider"' in body and 'flowLine("Insider"' not in gate  # anti-vacuity


def test_ticker_detail_refreshes_holders_on_entitlement_change():
    view = _code(_VIEW)
    assert ".onChange(of: appState.entitlementGeneration)" in view
    assert "refreshHoldersAfterEntitlementChange" in view
    assert ".onChange(of: appState.user.tier" not in view
    vm = _code(_VM)
    body = _decl_body(vm, "func refreshHoldersAfterEntitlementChange()")
    assert "fetchHolders(tickerSymbol, forceRefresh: true)" in body
    assert "isHoldersLoaded = false" not in body, "must not flash the placeholder mid-purchase"


# ── E5: an implausible institutional aggregate is UNKNOWN, not 100% ─────────

def test_breakdown_dto_decodes_the_unknown_flag_as_optional_and_keeps_the_floats_required():
    body = _decl_body(_code(_REPO_SWIFT), "struct ShareholderBreakdownDTO")
    assert re.search(r"let institutionsUnknown:\s*Bool\?", body), body
    assert 'case institutionsUnknown = "institutions_unknown"' in body
    assert "institutionsUnknown: institutionsUnknown ?? false" in body
    # the wire floats must stay non-Optional: shipped builds decode a plain Double
    for name in ("insidersPercent", "institutionsPercent", "publicOtherPercent"):
        assert re.search(rf"let {name}:\s*Double\b(?!\?)", body), name


def test_breakdown_formatters_render_a_dash_when_unknown():
    body = _decl_body(_code(_MODELS), "struct ShareholderBreakdown: Identifiable")
    for formatter in ("var formattedInstitutions: String", "var formattedPublicOther: String"):
        fb = _decl_body(body, formatter)
        assert "institutionsUnknown" in fb and '"—"' in fb, fb
    insiders = _decl_body(body, "var formattedInsiders: String")
    assert "institutionsUnknown" not in insiders, "the insider figure is real and stays"


def test_breakdown_bar_normalises_by_100_and_draws_a_muted_remainder_when_unknown():
    src = _code(_IOS / "Views" / "Molecules" / "ShareholderBreakdownBar.swift")
    body = _decl_body(src, "struct ShareholderBreakdownBar")
    width = _decl_body(body, "private func segmentWidth(")
    assert "institutionsUnknown" in width and "100.0" in width, width
    assert "HoldersColors.publicOther.opacity(" in body
    section = _code(_IOS / "Views" / "Organisms" / "ShareholderBreakdownSection.swift")
    assert "institutionsUnknown: breakdownData.institutionsUnknown" in section
