"""Account's credit renewal line and the Max "Plans" row (owner, 2026-10-13).

1. "Renews on <date>", not "Resets on": since migration 196 a Pro/Max subscriber's allowance
   renews with Apple's billing period, so `resets_at` IS their renewal day. The owner asked for
   "Renews on Nov 25 or something". Free and complimentary accounts keep the ET 1st.
2. The date is rendered in the right zone for its kind (`Core/Utilities/CreditRenewalDate.swift`):
   the calendar boundary (00:00 ET on the 1st) in ET, a subscriber's Apple renewal instant in the
   device's zone — how Apple's own subscription screen states it. Pinned to ET it read one day
   early for anyone east of New York whose renewal falls in the evening ET.
3. Max is the top plan: its "Plans" row says only "You're on Max" — no "compare or change
   plans" (owner: "they are on the highest level. so, we don't want them to move down"). The row
   stays: it is how App Review finds the subscriptions.

Source scans with comments stripped and declarations brace-bound; each guard was broken by hand
once and seen to fail (see the mutation table at the bottom).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_HELPER = _IOS / "Core" / "Utilities" / "CreditRenewalDate.swift"
_PROFILE_VM = _IOS / "ViewModels" / "ProfileViewModel.swift"
_PROFILE_VIEW = _IOS / "Views" / "Screens" / "ProfileView.swift"
_MODELS = _IOS / "Models" / "ResearchModels.swift"


def _code(src: str) -> str:
    """Strip // comments (whole-line and trailing) without touching string literals' contents."""
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            out.append("")
            continue
        in_str = False
        cut = len(line)
        i = 0
        while i < len(line) - 1:
            ch = line[i]
            if ch == '"' and (i == 0 or line[i - 1] != "\\"):
                in_str = not in_str
            elif not in_str and line[i:i + 2] == "//":
                cut = i
                break
            i += 1
        out.append(line[:cut])
    return "\n".join(out)


def _block(src: str, header: str) -> str:
    start = src.find(header)
    assert start >= 0, f"`{header}` not found — re-derive this scan"
    brace = src.find("{", start)
    depth = 0
    for i in range(brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"`{header}` has no closing brace")


def _check_label(vm: str) -> None:
    label = _block(vm, "var creditResetLabel: String")
    assert '"Renews on \\(CreditRenewalDate.short(date))"' in label, (
        "Account's renewal line is no longer \"Renews on <date>\" through CreditRenewalDate")
    assert '"Resets on' not in label, "Account says \"Resets on\" again"


def _check_zones(helper: str) -> None:
    boundary = _block(helper, "static func isCalendarBoundary(_ date: Date) -> Bool")
    assert re.search(r"c\.day\s*==\s*1\s*&&\s*c\.hour\s*==\s*0\s*&&\s*c\.minute\s*==\s*0\s*&&\s*c\.second\s*==\s*0",
                     boundary), "the calendar boundary is no longer exactly 00:00:00 on the 1st"
    assert "calendar.timeZone = eastern" in boundary, "the boundary is no longer read in ET"
    short = _block(helper, "static func short(_ date: Date) -> String")
    long = _block(helper, "static func long(_ date: Date) -> String")
    assert "isCalendarBoundary(date) ? shortEastern : shortLocal" in short, (
        "short() no longer picks ET for the calendar 1st and the device zone for a renewal")
    assert "isCalendarBoundary(date) ? longEastern : longLocal" in long, (
        "long() no longer picks ET for the calendar 1st and the device zone for a renewal")
    for name, zone in (("shortEastern", "eastern"), ("longEastern", "eastern"),
                       ("shortLocal", ".autoupdatingCurrent"), ("longLocal", ".autoupdatingCurrent")):
        assert re.search(rf"static let {name} = formatter\(\"[^\"]+\", timeZone: {re.escape(zone)}\)", helper), (
            f"{name} is no longer built for {zone}")
    assert 'private static let eastern = TimeZone(identifier: "America/New_York")' in helper, (
        "the ET zone is gone")
    fmt = _block(helper, "private static func formatter(")
    assert 'f.locale = Locale(identifier: "en_US_POSIX")' in fmt and "Calendar(identifier: .gregorian)" in fmt, (
        "the renewal-date formatter no longer pins en_US_POSIX + Gregorian")


def _check_research(models: str) -> None:
    text = _block(models, "private var renewalDateText: String")
    assert "CreditRenewalDate.long(renewalDate)" in text, (
        "the Research credits card no longer uses the shared CreditRenewalDate")
    assert "creditResetFormatter" not in models, "a second, ET-only reset formatter came back"


def _check_plans(view: str) -> None:
    m = re.search(r'title:\s*"Plans",\s*subtitle:(.*?)\n\s*\)', view, re.S)
    assert m, "the Plans row's subtitle is gone — re-derive this scan"
    subtitle = m.group(1)
    max_arm = re.search(r'viewModel\.userTier\s*==\s*\.premium\s*\?\s*"([^"]*)"\s*:\s*"([^"]*)"', subtitle)
    assert max_arm, "the Plans subtitle no longer branches on Max (.premium)"
    max_text, other_text = max_arm.group(1), max_arm.group(2)
    assert max_text == "You're on Max", f"Max's Plans subtitle is {max_text!r}, not \"You're on Max\""
    assert "change" not in max_text.lower() and "compare" not in max_text.lower()
    assert other_text.startswith("You're on Pro"), f"the non-Max arm reads {other_text!r}"


def test_account_says_renews_on():
    _check_label(_code(_PROFILE_VM.read_text(encoding="utf-8")))


def test_the_renewal_day_is_rendered_in_the_right_zone_for_its_kind():
    _check_zones(_code(_HELPER.read_text(encoding="utf-8")))


def test_the_research_card_states_the_same_day_as_account():
    _check_research(_code(_MODELS.read_text(encoding="utf-8")))


def test_max_is_not_invited_to_change_plans():
    _check_plans(_code(_PROFILE_VIEW.read_text(encoding="utf-8")))


def test_the_row_still_opens_the_plans():
    """App Review (demo account on Max) once could not find the subscriptions: the row stays."""
    view = _code(_PROFILE_VIEW.read_text(encoding="utf-8"))
    m = re.search(r'Button\(action:\s*\{\s*showPaywall\s*=\s*true\s*\}\)\s*\{\s*ProfileSettingsRowContent\('
                  r'\s*icon:\s*"crown\.fill",.*?title:\s*"Plans"', view, re.S)
    assert m, "the Plans row no longer opens the paywall for paid accounts"


def test_comments_do_not_satisfy_the_scans():
    """Anti-vacuity: the explanatory comments carry the same tokens; stripped, the code must
    still hold them, and the stripper must really strip."""
    assert _code('let a = 1 // "Renews on"\n// isCalendarBoundary(date) ? shortEastern') == "let a = 1 \n"


# ── mutation table: each entry must make its guard fail ──────────────────────────────────

_MUTATIONS = [
    ("label-resets", _PROFILE_VM, _check_label,
     '"Renews on \\(CreditRenewalDate.short(date))"', '"Resets on \\(CreditRenewalDate.short(date))"'),
    ("label-raw-et-formatter", _PROFILE_VM, _check_label,
     "CreditRenewalDate.short(date)", "date.formatted()"),
    ("zones-always-eastern", _HELPER, _check_zones,
     "isCalendarBoundary(date) ? shortEastern : shortLocal", "shortEastern"),
    ("zones-local-is-eastern", _HELPER, _check_zones,
     'static let longLocal = formatter("MMM d, yyyy", timeZone: .autoupdatingCurrent)',
     'static let longLocal = formatter("MMM d, yyyy", timeZone: eastern)'),
    ("zones-boundary-any-day", _HELPER, _check_zones,
     "c.day == 1 && c.hour == 0", "c.hour == 0"),
    ("zones-no-locale", _HELPER, _check_zones,
     'f.locale = Locale(identifier: "en_US_POSIX")', ""),
    ("research-own-formatter", _MODELS, _check_research,
     "CreditRenewalDate.long(renewalDate)", "ResearchFormatters.mediumDateFormatter.string(from: renewalDate)"),
    ("plans-max-invited", _PROFILE_VIEW, _check_plans,
     '? "You\'re on Max"', '? "You\'re on Max · compare or change plans"'),
    ("plans-no-branch", _PROFILE_VIEW, _check_plans,
     'subtitle: viewModel.userTier == .premium\n                                ? "You\'re on Max"\n'
     '                                : "You\'re on Pro · compare or change plans"',
     'subtitle: "You\'re on \\(viewModel.userTier == .premium ? "Max" : "Pro") · compare or change plans"'),
]


@pytest.mark.parametrize("name,path,check,old,new", _MUTATIONS, ids=[m[0] for m in _MUTATIONS])
def test_each_mutation_is_killed(name, path, check, old, new):
    src = path.read_text(encoding="utf-8")
    assert src.count(old) == 1, f"{name}: anchor occurs {src.count(old)}x — re-derive"
    check(_code(src))   # the real source passes
    with pytest.raises(AssertionError):
        check(_code(src.replace(old, new, 1)))


# ── 4. the plan screen opens on the right plan (owner, 2026-10-13) ───────────────────────
#
# "when i'm at Max … it points to Pro. i don't want that. point to Max only. but if they are
# free, then point to Pro. if they are Pro, then point to Max." Free → Pro and Pro → Max come
# from the "cheapest plan ABOVE the current tier" rule; the top plan has nothing above, and the
# old fallback (`paid.first`) opened a Max account on Pro.

_PAYWALL = _IOS / "Views" / "Screens" / "PaywallView.swift"


def _check_default_selection(view: str) -> None:
    body = _block(view, "private func defaultSelection(_ catalog: PlanCatalog) -> String?")
    ret = re.search(r"return\s*\((.*?)\)\?\.tier", body, re.S)
    assert ret, "defaultSelection no longer returns one chained choice — re-derive this scan"
    chain = [part.strip() for part in ret.group(1).split("??")]
    assert chain[:2] == ["unlocks", "above.first"], (
        f"the upgrade path changed: {chain} — Free must open on Pro and Pro on Max")
    assert "paid.first" in chain, "the no-tier fallback is gone"
    top = next((i for i, p in enumerate(chain) if "onTopPlan" in p and "current" in p), None)
    assert top is not None and top < chain.index("paid.first"), (
        "a Max account opens on a cheaper plan again (the top-plan arm must come before paid.first)")
    assert re.search(r"let onTopPlan = above\.isEmpty && \(current\?\.priceCents \?\? 0\) > 0", body), (
        "the top-plan test no longer means 'nothing above AND currently on a paid plan'")


def test_the_plan_screen_opens_on_max_for_a_max_account():
    _check_default_selection(_code(_PAYWALL.read_text(encoding="utf-8")))


_MUTATIONS_4 = [
    ("default-max-opens-on-pro", "return (unlocks ?? above.first ?? (onTopPlan ? current : nil) ?? paid.first)?.tier",
     "return (unlocks ?? above.first ?? paid.first)?.tier"),
    ("default-top-after-paid", "return (unlocks ?? above.first ?? (onTopPlan ? current : nil) ?? paid.first)?.tier",
     "return (unlocks ?? above.first ?? paid.first ?? (onTopPlan ? current : nil))?.tier"),
    ("default-free-counts-as-top", "let onTopPlan = above.isEmpty && (current?.priceCents ?? 0) > 0",
     "let onTopPlan = above.isEmpty"),
]


@pytest.mark.parametrize("name,old,new", _MUTATIONS_4, ids=[m[0] for m in _MUTATIONS_4])
def test_each_default_selection_mutation_is_killed(name, old, new):
    src = _PAYWALL.read_text(encoding="utf-8")
    assert src.count(old) == 1, f"{name}: anchor occurs {src.count(old)}x — re-derive"
    _check_default_selection(_code(src))
    with pytest.raises(AssertionError):
        _check_default_selection(_code(src.replace(old, new, 1)))


# ── 5. a Max subscriber is never offered a lower plan (owner, 2026-10-13) ───────────────
#
# "hide the lower plans for them". Only for a STORE-BACKED Max: App Review's hand-set Max demo
# account must still be able to buy a plan, so it keeps every row.

_PAYWALL_VM = _IOS / "ViewModels" / "PaywallViewModel.swift"


def _check_hides_lower_plans(vm: str, view: str) -> None:
    rule = _block(vm, "nonisolated static func hidesLowerPlans(")
    assert re.search(r"currentTier\s*==\s*\.premium\s*&&\s*currentTierIsStoreBacked", rule), (
        "hidesLowerPlans is no longer 'a store-backed Max' — a hand-set demo Max would lose its "
        "buy buttons, or a real Max subscriber would be offered Pro again")
    visible = _block(view, "private func visiblePlans(_ catalog: PlanCatalog) -> [PlanDTO]")
    assert "PaywallViewModel.hidesLowerPlans(" in visible and \
        "currentTierIsStoreBacked: viewModel.currentTierIsStoreBacked" in visible, (
        "the plan list no longer asks hidesLowerPlans with the live store-backed flag")
    assert "catalog.plans[currentIndex...]" in visible, (
        "a Max subscriber's list is no longer 'current plan and above'")
    selector = _block(view, "private func planSelector(_ catalog: PlanCatalog) -> some View")
    assert "ForEach(visiblePlans(catalog))" in selector, "the plan rows are drawn from the full catalog again"
    selected = _block(view, "private var selectedPlan: PlanDTO?")
    assert "visiblePlans(catalog)" in selected and "catalog.plans.first" not in selected, (
        "the selected plan can point at a hidden lower plan")


def test_a_max_subscriber_sees_no_lower_plan():
    _check_hides_lower_plans(_code(_PAYWALL_VM.read_text(encoding="utf-8")),
                             _code(_PAYWALL.read_text(encoding="utf-8")))


_MUTATIONS_5 = [
    ("hide-even-demo", _PAYWALL_VM, "currentTier == .premium && currentTierIsStoreBacked", "currentTier == .premium"),
    ("hide-for-pro-too", _PAYWALL_VM, "currentTier == .premium && currentTierIsStoreBacked",
     "currentTier != .free && currentTierIsStoreBacked"),
    ("rows-from-full-catalog", _PAYWALL, "ForEach(visiblePlans(catalog))", "ForEach(catalog.plans)"),
    ("selection-from-full-catalog", _PAYWALL, "return plans.first { $0.tier == tier } ?? plans.last",
     "return catalog.plans.first { $0.tier == tier } ?? catalog.plans.last"),
]


@pytest.mark.parametrize("name,path,old,new", _MUTATIONS_5, ids=[m[0] for m in _MUTATIONS_5])
def test_each_lower_plan_mutation_is_killed(name, path, old, new):
    vm = _PAYWALL_VM.read_text(encoding="utf-8")
    view = _PAYWALL.read_text(encoding="utf-8")
    src = vm if path == _PAYWALL_VM else view
    assert src.count(old) == 1, f"{name}: anchor occurs {src.count(old)}x — re-derive"
    _check_hides_lower_plans(_code(vm), _code(view))
    mutated = src.replace(old, new, 1)
    with pytest.raises(AssertionError):
        if path == _PAYWALL_VM:
            _check_hides_lower_plans(_code(mutated), _code(view))
        else:
            _check_hides_lower_plans(_code(vm), _code(mutated))
