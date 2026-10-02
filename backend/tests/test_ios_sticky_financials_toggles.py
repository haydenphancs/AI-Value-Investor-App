"""The Financials tab's card controls are set once and kept — on this device.

TestFlight 1.0 (9), on the Chart Settings sheet: "For everything in here, it should ... set
up once and permanently keep them." The owner then asked for the rest of the app to be
checked. The four Financials cards held their controls in `@State`: Growth's metric chip and
Annual/Quarterly, Profit Power's Annual/Quarterly, Earnings' EPS/Revenue, 1Y/3Y and Price
line, and Signal of Confidence's Yield %/Capital $. `TickerDetailView`'s tab switch tears the
Financials tab down, so every tab switch, every new ticker and every relaunch reset them.

Pinned here, iOS half (there is no XCTest target — testing.md §3). Comments are stripped
before every assertion and every check is brace-bound to ONE declaration:

1. Each control is `@AppStorage("caydex_…")`: a String TOKEN for an enum choice, a Bool for
   the Price line (default OFF), and no `@State` holds it any more.
2. A token is the case name, never `rawValue` (the toggle wording). The mapping covers every
   case and no two cases share a token.
3. The store is written in ONE place, the `selected…` setter, and that setter runs only from a
   tap. The fallbacks only read: the getter's `??`, Growth's `displayed…` pair and Profit
   Power's new `displayedPeriod`. A ticker that lacks the saved choice cannot overwrite it.
   The Price line has no setter: only its toggle's `$showPriceLine` binding writes it. No
   card has an `extension` in its file, so the brace-bound struct body is ALL the code that
   can reach its `private` storage.
4. Growth keeps its data-derived fallback (`initialSelection()` while nothing usable is
   saved). With no period saved, the period follows the metric ON SCREEN (its first period,
   Annual first), not `initialSelection()`'s metric, and nothing on the metric side reads a
   period (no cycle). Profit Power gains a fallback. A saved period with no company margins
   shows the period that has them, until the user taps, because "Margin data isn't
   available for this company." would be false. It uses the same company-margin test as
   `ProfitPowerChartView`.
5. Each key is distinct (Growth's period ≠ Profit Power's) and is named ONCE in ONE Swift
   file, its card, with no `UserDefaults` there and no computed `@AppStorage` key. So nothing
   writes it around the setter, nothing syncs it to the account (SettingsSyncManager) and
   nothing clears it at sign-out (AppState.discardDataForEndedSession): it is a display
   preference of this phone.

Mutation-tested in memory: `pathlib.Path.read_text` was patched to return the mutated source,
so the Swift files on disk were never touched. Each mutation turned the named test red:
  * Growth `selectedMetric` back to `@State private var selectedMetric: GrowthMetricType`
                                                         → test_each_choice_is_saved…[growth-metric]
  * Growth getter `?? .eps` instead of `?? fallbackSelection.metric`
                                                         → test_each_choice_is_saved…[growth-metric]
  * Growth `displayedPeriod` writes back `storedPeriodToken = …` → test_each_choice_is_saved…[growth-period]
  * `.onAppear { selectedPeriod = displayedPeriod }` on the Growth card
                                                         → test_each_choice_is_saved…[growth-period]
  * Growth init without `initialSelection()`             → test_growth_keeps_its_data_derived_fallback…
  * Growth toggle binding reads `selectedPeriod`         → test_growth_keeps_its_data_derived_fallback…
  * Profit Power `@AppStorage("caydex_growth_period")`   → test_each_choice_is_saved…[profit-power-period]
                                                           and test_the_keys_are_distinct…
  * Profit Power `currentDataPoints` reads `selectedPeriod` → test_profit_power_falls_back…
  * drop `periodWasTapped ||` from `displayedPeriod`     → test_profit_power_falls_back…
  * drop `periodWasTapped = true` from the toggle binding → test_profit_power_falls_back…
  * add `p.sectorAverageNetMargin` to `hasCompanyMargins` → test_profit_power_falls_back…
  * delete the `.onChange(of: displayedPeriod)` reset    → test_profit_power_falls_back…
  * Earnings series token `.revenue` → `"Revenue"` (its rawValue) → test_tokens_are_stable_case_names…[earnings-series]
  * Earnings time-range mapping loses `.threeYears`      → test_tokens_are_stable_case_names…[earnings-range]
  * Signal token `.capital` → `"yield"` (a duplicate)    → test_tokens_are_stable_case_names…[capital-return-view]
  * Price line default `= true`                          → test_the_price_line_is_saved…
  * Price line back to `@State private var showPriceLine` → test_the_price_line_is_saved…
  * `caydex_capital_return_view` also written by SettingsSyncManager.swift → test_the_keys_are_distinct…
  * comment out `case .revenue: return "revenue"` (the token survives only in a comment)
                                                         → test_tokens_are_stable_case_names…[earnings-series]
Added after review (the first two passed every test before):
  * `Color.clear.onAppear { showPriceLine = false }` in the Earnings body
                                                         → test_the_price_line_is_saved…
  * `.onAppear { showPriceLine.toggle() }` / a second `$showPriceLine` binding
                                                         → test_the_price_line_is_saved…
  * `let _ = UserDefaults.standard.set("annual", forKey: "caydex_growth_period")` in the
    Growth body                                          → test_the_keys_are_distinct…
  * `@AppStorage("caydex_" + "growth_period") private var p` (a computed key)
                                                         → test_the_keys_are_distinct…
  * `extension GrowthSectionCard { func reset() { storedPeriodToken = "" } }`
                                                         → test_each_choice_is_saved…[growth-*]
  * Growth period getter back to `?? fallbackSelection.period`
                                                         → test_each_choice_is_saved…[growth-period]
  * `displayedMetric` consulting `availablePeriods` (an infinite getter cycle)
                                                         → test_growth_keeps_its_data_derived_fallback…
"""
from __future__ import annotations

import pathlib
import re

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_GROWTH = _IOS / "Views" / "Organisms" / "GrowthSectionCard.swift"
_PP = _IOS / "Views" / "Organisms" / "ProfitPowerSectionCard.swift"
_EARN = _IOS / "Views" / "Organisms" / "EarningsSectionCard.swift"
_SOC = _IOS / "Views" / "Organisms" / "SignalOfConfidenceSectionCard.swift"
_PP_CHART = _IOS / "Views" / "Molecules" / "ProfitPowerChartView.swift"
_SYNC = _IOS / "Core" / "Services" / "SettingsSyncManager.swift"
_APP_STATE = _IOS / "Core" / "State" / "AppState.swift"

_GROWTH_MODELS = _IOS / "Models" / "GrowthModels.swift"
_PP_MODELS = _IOS / "Models" / "ProfitPowerModels.swift"
_SOC_MODELS = _IOS / "Models" / "SignalOfConfidenceModels.swift"
_EARN_MODELS = _IOS / "Models" / "TickerDetailModels.swift"

# (id, card file, card struct, computed choice, enum, enum's model file, storage var, key,
#  getter fallback, how many TAP sites assign the choice)
_CHOICES = [
    ("growth-metric", _GROWTH, "GrowthSectionCard", "selectedMetric", "GrowthMetricType",
     _GROWTH_MODELS, "storedMetricToken", "caydex_growth_metric", "fallbackSelection.metric", 1),
    ("growth-period", _GROWTH, "GrowthSectionCard", "selectedPeriod", "GrowthPeriodType",
     _GROWTH_MODELS, "storedPeriodToken", "caydex_growth_period",
     "(availablePeriods.first ?? fallbackSelection.period)", 1),
    ("profit-power-period", _PP, "ProfitPowerSectionCard", "selectedPeriod", "ProfitPowerPeriodType",
     _PP_MODELS, "storedPeriodToken", "caydex_profit_power_period", ".annual", 1),
    ("earnings-series", _EARN, "EarningsSectionCard", "selectedDataType", "EarningsDataType",
     _EARN_MODELS, "storedSeriesToken", "caydex_earnings_series", ".eps", 1),
    ("earnings-range", _EARN, "EarningsSectionCard", "selectedTimeRange", "EarningsTimeRange",
     _EARN_MODELS, "storedRangeToken", "caydex_earnings_range", ".oneYear", 1),
    ("capital-return-view", _SOC, "SignalOfConfidenceSectionCard", "selectedView",
     "SignalOfConfidenceViewType", _SOC_MODELS, "storedViewToken", "caydex_capital_return_view",
     ".yield", 1),
]
_PRICE_KEY = "caydex_earnings_show_price"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _strip_comments(src: str) -> str:
    """Drop `//` and (nested) `/* */` comments, keeping string literals intact — a `"//"`
    inside a string is not a comment, and a comment quoting code cannot satisfy a guard."""
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        if src.startswith('"""', i):
            end = src.find('"""', i + 3)
            end = n if end < 0 else end + 3
            out.append(src[i:end])
            i = end
        elif src[i] == '"':
            j = i + 1
            while j < n and src[j] != '"' and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1])
            i = j + 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if src.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif src.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            i = j
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


def _code(path: pathlib.Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _decl_body(src: str, pattern: str) -> str:
    """Brace-balanced body of the ONE declaration matching regex `pattern` (braces inside
    string literals are skipped)."""
    matches = list(re.finditer(pattern, src))
    assert len(matches) == 1, f"{pattern!r} matched {len(matches)} times"
    start = src.index("{", matches[0].end())
    depth, i, n = 0, start, len(src)
    while i < n:
        ch = src[i]
        if ch == '"':
            j = i + 1
            while j < n and src[j] != '"':
                j += 2 if src[j] == "\\" else 1
            i = j + 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces after {pattern!r}")


def _card(path: pathlib.Path, struct: str) -> str:
    return _decl_body(_code(path), rf"\bstruct\s+{struct}\s*:\s*View\b")


def _assignments(src: str, name: str) -> list[int]:
    """Offsets of `name = …` / `self.name = …` / `name += …` (an assignment, never `==`).

    `self.` and compound forms are included on purpose: a `.onAppear { self.showPriceLine
    = false }` is the TestFlight reset-on-open bug spelled differently, and it got past the
    first version of this helper."""
    pattern = rf"(?:(?<![\w.$])|(?<=\bself\.)){name}\s*[-+*/%]?=(?!=)"
    return [m.start() for m in re.finditer(pattern, src)]


def _enum_cases(models: pathlib.Path, enum: str) -> dict[str, str]:
    """`case name = "raw"` declarations of `enum` → {case name: rawValue}."""
    body = _decl_body(_code(models), rf"\benum\s+{enum}\s*:")
    return dict(re.findall(r'^\s*case\s+(\w+)\s*=\s*"([^"]*)"', body, flags=re.M))


def _token_map(card_file: pathlib.Path, enum: str) -> dict[str, str]:
    """{case name: token} from the card's `private extension <enum>`'s `preferenceToken`."""
    ext = _decl_body(_code(card_file), rf"\bprivate\s+extension\s+{enum}\b")
    getter = _decl_body(ext, r"\bvar\s+preferenceToken\s*:\s*String\b")
    pairs = re.findall(r'case\s+\.(\w+)\s*:\s*return\s+"([^"]*)"', getter)
    assert len(pairs) == len({c for c, _ in pairs}), f"{enum}: a case is mapped twice"
    return dict(pairs)


def test_helpers_are_not_vacuous():
    """The scans are `substring in body`; prove the helpers really strip and bound."""
    stripped = _strip_comments('let a = "x // y" // storedPeriodToken = 1\n/* a /* b */ c */let b = 1')
    assert '"x // y"' in stripped and "storedPeriodToken" not in stripped and "let b = 1" in stripped
    body = _decl_body('struct A: View { var x: Int { 1 } } struct B { let y = 2 }', r"\bstruct\s+A\s*:")
    assert "y = 2" not in body and "var x" in body
    assert _decl_body('func f() { let s = "}"; return }', r"\bfunc\s+f\b") == '{ let s = "}"; return }'
    assert _assignments("a = 1; a == 2; b.a = 3; $a = 4; aa = 5", "a") == [0]
    assert _assignments("self.a = 6", "a") == [5]
    assert _assignments("a += 7", "a") == [0]
    # The tables above really reach the source: every card and every enum is found.
    for _, card_file, struct, *_rest in _CHOICES:
        assert "var body: some View" in _card(card_file, struct)
    for _, _, _, _, enum, models, *_rest in _CHOICES:
        assert len(_enum_cases(models, enum)) >= 2, enum


# ── 1–3. Every choice: saved as a token, written only from a tap ─────────────


@pytest.mark.parametrize(
    "card_file, struct, prop, enum, storage, key, fallback, tap_sites",
    [c[1:3] + c[3:5] + c[6:] for c in _CHOICES],
    ids=[c[0] for c in _CHOICES],
)
def test_each_choice_is_saved_as_a_token_and_written_only_from_a_tap(
    card_file, struct, prop, enum, storage, key, fallback, tap_sites,
):
    card = _card(card_file, struct)

    # Backed by the device store, as a String token, with "" meaning "nothing saved".
    assert re.search(
        rf'@AppStorage\("{re.escape(key)}"\)\s*private\s+var\s+{storage}\s*:\s*String\s*=\s*""', card
    ), f"{struct}.{prop} is not saved under {key}"
    # …and no longer held in view state, which the tab switch destroys.
    assert not re.search(rf"@State\b[^\n]*\bvar\s+{prop}\b", card), f"{prop} is @State again"

    # The choice decodes the token (unknown → the fallback) and a set encodes it.
    decl = _decl_body(card, rf"\bprivate\s+var\s+{prop}\s*:\s*{enum}\s*")
    assert re.search(
        rf"\bget\s*\{{\s*{enum}\(preferenceToken:\s*{storage}\)\s*\?\?\s*{re.escape(fallback)}\s*\}}", decl
    ), f"{prop}'s getter must decode the token, falling back to {fallback}"
    assert re.search(
        rf"\bnonmutating\s+set\s*\{{\s*{storage}\s*=\s*newValue\.preferenceToken\s*\}}", decl
    ), f"{prop}'s setter must save the token"

    # ONE write to the store (that setter) — a fallback or an onAppear writing back would
    # replace the user's choice with whatever this ticker happens to have.
    assert len(_assignments(card, storage)) == 1, f"{storage} is written outside {prop}'s setter"
    assert f"${storage}" not in card, f"a binding to {storage} bypasses the setter"
    assert f"_{storage}" not in card, f"the {storage} wrapper is reached around the setter"
    # Everything that can reach the `private` storage is the struct body scanned here: Swift
    # opens it to an extension of the card in the same file, and there must be none.
    assert not re.search(rf"\bextension\s+{struct}\b", _code(card_file)), \
        f"an extension of {struct} can write {storage} outside the scanned body"

    # The choice itself is assigned only from a tap: a toggle binding's `set:` or a chip's
    # `action:` — never from `.onAppear`, `.onChange` or `.task`.
    sites = _assignments(card, prop)
    assert len(sites) == tap_sites, f"{prop} is assigned at {len(sites)} sites, expected {tap_sites}"
    for at in sites:
        before = card[max(0, at - 200):at]
        assert re.search(r"\b(set|action)\s*:", before), f"{prop} assigned outside a tap: {card[at - 80:at + 40]!r}"
        for hook in (".onAppear", ".onChange", ".task"):
            last = before.rfind(hook)
            assert last < 0 or before.rfind("set:") > last or before.rfind("action:") > last, \
                f"{prop} assigned from {hook}"


@pytest.mark.parametrize(
    "card_file, enum, models", [(c[1], c[4], c[5]) for c in _CHOICES], ids=[c[0] for c in _CHOICES],
)
def test_tokens_are_stable_case_names_never_the_toggle_wording(card_file, enum, models):
    cases = _enum_cases(models, enum)
    tokens = _token_map(card_file, enum)
    assert set(tokens) == set(cases), f"{enum}: unmapped or unknown cases {set(cases) ^ set(tokens)}"
    assert len(set(tokens.values())) == len(tokens), f"{enum}: two cases share a token"
    labels = set(cases.values())
    for case, token in tokens.items():
        assert token not in labels, f"{enum}.{case} is stored as its label {token!r}"
        assert token == case, f"{enum}.{case} stored as {token!r}; the token is the case name"
    ext = _decl_body(_code(card_file), rf"\bprivate\s+extension\s+{enum}\b")
    assert "rawValue" not in ext, f"{enum}'s token must not be derived from the label"
    init = _decl_body(ext, r"\binit\?\(preferenceToken:\s*String\)")
    assert re.search(r"Self\.allCases\.first\(where:\s*\{\s*\$0\.preferenceToken\s*==\s*preferenceToken\s*\}\)", init)
    assert re.search(r"else\s*\{\s*return\s+nil\s*\}", init), "an unknown token must decode to nil"


def test_the_price_line_is_saved_and_stays_off_by_default():
    card = _card(_EARN, "EarningsSectionCard")
    assert re.search(
        rf'@AppStorage\("{_PRICE_KEY}"\)\s*private\s+var\s+showPriceLine\s*:\s*Bool\s*=\s*false\b', card
    ), "the Price line must be saved, and OFF until the user turns it on"
    assert not re.search(r"@State\b[^\n]*\bvar\s+showPriceLine\b", card)
    controls = _decl_body(card, r"\bprivate\s+var\s+controlsRow\s*:\s*some\s+View\s*")
    assert "EarningsPriceToggle(isEnabled: $showPriceLine)" in controls
    assert "EarningsDataTypeToggle(" in controls and "EarningsTimeRangeToggle(" in controls
    body = _decl_body(card, r"\bvar\s+body\s*:\s*some\s+View\s*")
    assert "showPriceLine: showPriceLine" in body

    # Written ONLY by the toggle, through that one binding. An `.onAppear { showPriceLine =
    # false }` is exactly the reset-on-open bug from the TestFlight report, and it passed
    # every check above.
    assert _assignments(card, "showPriceLine") == [], "the Price line is written outside its toggle"
    assert not re.search(r"\bshowPriceLine\s*\.\s*toggle\s*\(", card), "the Price line is toggled outside its toggle"
    assert card.count("$showPriceLine") == 1, "a second binding can write the Price line"
    assert "_showPriceLine" not in card, "the Price line's wrapper is reached around the toggle"
    assert not re.search(r"\bextension\s+EarningsSectionCard\b", _code(_EARN)), \
        "an extension of the card can write the Price line outside the scanned body"


# ── 4. The fallbacks: derived, never written ─────────────────────────────────


def test_growth_keeps_its_data_derived_fallback_and_never_writes_it():
    card = _card(_GROWTH, "GrowthSectionCard")
    init = _decl_body(card, r"\binit\(growthData:\s*GrowthSectionData,\s*onDetailTapped:")
    assert re.search(r"self\.fallbackSelection\s*=\s*growthData\.initialSelection\(\)", init), \
        "with nothing saved the card must still open on a metric that has data"
    assert re.search(
        r"private\s+let\s+fallbackSelection\s*:\s*\(metric:\s*GrowthMetricType,\s*period:\s*GrowthPeriodType\)", card
    )

    metric = _decl_body(card, r"\bprivate\s+var\s+displayedMetric\s*:\s*GrowthMetricType\s*")
    assert "availableMetrics.contains(selectedMetric) ? selectedMetric : (availableMetrics.first ?? selectedMetric)" in metric
    period = _decl_body(card, r"\bprivate\s+var\s+displayedPeriod\s*:\s*GrowthPeriodType\s*")
    assert "availablePeriods.contains(selectedPeriod) ? selectedPeriod : (availablePeriods.first ?? selectedPeriod)" in period
    for derived in (metric, period):
        assert not re.search(r"(?<![=!<>])=(?!=)", derived), "a displayed fallback must only read"

    # With no period saved, the period follows the metric ON SCREEN (the getter's
    # `availablePeriods.first`, pinned in the per-choice test above), not the metric
    # `initialSelection()` chose: with Revenue saved and a quarterly-only EPS, its period
    # opened Revenue on Quarterly although it has Annual.
    periods = _decl_body(card, r"\bprivate\s+var\s+availablePeriods\s*:\s*\[GrowthPeriodType\]\s*")
    assert "growthData.periodsWithData(for: displayedMetric)" in periods
    # selectedPeriod → availablePeriods → displayedMetric → selectedMetric/availableMetrics.
    # Nothing on the metric side may read a period back, or the getters recurse forever.
    period_names = r"\b(selectedPeriod|displayedPeriod|availablePeriods|periodSelection|storedPeriodToken)\b"
    for name, decl in (
        ("availablePeriods", periods),
        ("displayedMetric", metric),
        ("selectedMetric", _decl_body(card, r"\bprivate\s+var\s+selectedMetric\s*:\s*GrowthMetricType\s*")),
        ("availableMetrics", _decl_body(card, r"\bprivate\s+var\s+availableMetrics\s*:\s*\[GrowthMetricType\]\s*")),
    ):
        assert not re.search(period_names, decl), f"{name} reads a period: the period getter would recurse"

    # The toggle shows the period on screen; a tap saves it. The chip highlight follows the
    # metric on screen; a chip tap saves it.
    binding = _decl_body(card, r"\bprivate\s+var\s+periodSelection\s*:\s*Binding<GrowthPeriodType>\s*")
    assert re.search(r"get:\s*\{\s*displayedPeriod\s*\}\s*,\s*set:\s*\{\s*selectedPeriod\s*=\s*\$0\s*\}", binding)
    assert "GrowthPeriodToggle(selectedPeriod: periodSelection)" in _decl_body(
        card, r"\bprivate\s+var\s+card\s*:\s*some\s+View\s*"
    )
    chips = _decl_body(card, r"\bprivate\s+var\s+metricChips\s*:\s*some\s+View\s*")
    assert "isSelected: displayedMetric == metric" in chips and "selectedMetric = metric" in chips


def test_profit_power_falls_back_to_the_period_with_margins_until_a_tap():
    card = _card(_PP, "ProfitPowerSectionCard")

    shown = _decl_body(card, r"\bprivate\s+var\s+displayedPeriod\s*:\s*ProfitPowerPeriodType\s*")
    assert re.search(
        r"if\s+periodWasTapped\s*\|\|\s*hasCompanyMargins\(selectedPeriod\)\s*\{\s*return\s+selectedPeriod\s*\}", shown
    ), "a tap is shown as is; a SAVED period only where it has margins"
    assert re.search(
        r"return\s+ProfitPowerPeriodType\.allCases\.first\(where:\s*\{\s*hasCompanyMargins\(\$0\)\s*\}\)\s*\?\?\s*selectedPeriod",
        shown,
    ), "fall back to the period that has company margins"
    assert not re.search(r"(?<![=!<>|&])=(?!=)", shown), "the fallback must only read"

    # The same company-margin test the chart's empty state uses — a peer line alone is not
    # data, or a ticker with only a benchmark would "fall back" onto an empty chart.
    margins = _decl_body(card, r"\bprivate\s+func\s+hasCompanyMargins\(_\s+period:\s*ProfitPowerPeriodType\)")
    assert "profitPowerData.dataPoints(for: period)" in margins and "isFinite == true" in margins
    chart_has_data = _decl_body(_code(_PP_CHART), r"\bprivate\s+var\s+hasData\s*:\s*Bool\s*")
    fields = lambda s: set(re.findall(r"\bp\.(\w+Margin)\b", s))  # noqa: E731
    assert fields(margins) == fields(chart_has_data) == {"grossMargin", "operatingMargin", "fcfMargin", "netMargin"}

    # The chart, legend and tooltip all read the period ON SCREEN.
    current = _decl_body(card, r"\bprivate\s+var\s+currentDataPoints\s*:\s*\[ProfitPowerDataPoint\]\s*")
    assert "profitPowerData.dataPoints(for: displayedPeriod)" in current and "selectedPeriod" not in current

    # A tap marks itself and saves; it is the only write to the flag.
    binding = _decl_body(card, r"\bprivate\s+var\s+periodSelection\s*:\s*Binding<ProfitPowerPeriodType>\s*")
    assert re.search(r"get:\s*\{\s*displayedPeriod\s*\}", binding)
    assert re.search(r"set:\s*\{\s*period\s+in\s*periodWasTapped\s*=\s*true\s*selectedPeriod\s*=\s*period\s*\}", binding)
    assert re.search(r"@State\s+private\s+var\s+periodWasTapped\s*:\s*Bool\s*=\s*false", card)
    assert len(_assignments(card, "periodWasTapped")) == 1

    body = _decl_body(card, r"\bvar\s+body\s*:\s*some\s+View\s*")
    assert "ProfitPowerPeriodToggle(selectedPeriod: periodSelection)" in body
    # The tooltip clears when the CHOICE changes (pinned by test_profit_power_deepcheck_ios)
    # and when the period on screen does — a reload can swap it with no new choice.
    assert re.search(r"\.onChange\(of: selectedPeriod\)\s*\{\s*selectedDataPoint = nil\s*\}", body)
    assert re.search(r"\.onChange\(of: displayedPeriod\)\s*\{\s*selectedDataPoint = nil\s*\}", body)


# ── 5. Keys: distinct, device-only, kept at sign-out ─────────────────────────


def test_the_keys_are_distinct_device_only_and_kept_at_sign_out():
    keys = [c[7] for c in _CHOICES] + [_PRICE_KEY]
    assert len(set(keys)) == len(keys), "two controls share a key (e.g. Growth flipping Profit Power)"
    assert all(k.startswith("caydex_") for k in keys)

    # Each key is named in exactly ONE Swift file, its card — so no settings sync uploads it
    # and no sign-out funnel clears it (a display preference of this phone, not account data).
    # The whole iOS tree, widget extension and Shared/ included.
    owner = {c[7]: c[1] for c in _CHOICES} | {_PRICE_KEY: _EARN}
    found: dict[str, list[pathlib.Path]] = {k: [] for k in keys}
    swift_files = sorted(_IOS.parent.rglob("*.swift"))
    assert _GROWTH in swift_files and len(swift_files) > 100, "the tree scan found nothing"
    for path in swift_files:
        raw = path.read_text(encoding="utf-8")
        if not any(k in raw for k in keys):
            continue  # stripping comments char by char is the slow part; most files skip it
        code = _strip_comments(raw)
        for k in keys:
            if f'"{k}"' in code:
                found[k].append(path)
    for k in keys:
        assert found[k] == [owner[k]], f"{k} is named in {[p.name for p in found[k]]}"

    # …and ONCE there: its `@AppStorage` declaration. The per-choice test counts writes to
    # the storage VAR; a direct `UserDefaults.standard.set(…, forKey: key)` in the same card,
    # a second `@AppStorage` on the key, or one whose key is computed (so this scan cannot
    # see it) would write around the setter with every other check green.
    for card_file in sorted(set(owner.values())):
        code = _code(card_file)
        for k in (k for k in keys if owner[k] == card_file):
            named = code.count(f'"{k}"')
            assert named == 1, f"{k} is named {named}× in {card_file.name}"
            assert re.search(rf'@AppStorage\("{re.escape(k)}"\)', code), f"{k} is not its @AppStorage key"
        assert "UserDefaults" not in code, f"{card_file.name} reaches UserDefaults around its @AppStorage"
        for m in re.finditer(r"@AppStorage\b", code):
            assert re.match(r'@AppStorage\("[^"\\]*"\)', code[m.start():]), \
                f"{card_file.name}: an @AppStorage key is not one string literal: {code[m.start():m.start() + 60]!r}"

    # Belt and braces for the two places that would change the contract.
    for path in (_SYNC, _APP_STATE):
        code = _code(path)
        assert not any(k in code for k in keys), f"{path.name} names a Financials card key"
