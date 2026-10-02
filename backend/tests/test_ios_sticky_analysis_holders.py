"""Source-scan guards: the Analysis-tab timeframes, the Technical Meter and the Holders
pills are "set once and keep" device preferences.

TestFlight 1.0 (9), about the Chart Settings sheet: "I changed Extended hours to off but it
turns on again. For everything in here, it should ... set up once and permanently keep
them." The owner then asked for the rest of the detail screens to behave the same. What
reset, and why:

- Analyst Momentum 6M/1Y, Sentiment 24H/7D and crypto Fear & Greed 1D/7D/30D were
  `@Published` copies on each detail ViewModel with hard defaults, so every pushed detail
  screen and every relaunch started over. They are now ONE `@AppStorage` per choice at the
  screen level (`TickerDetailView`, `CryptoDetailView`), bound down; the ViewModel copies
  are gone and must not come back (nor be seeded from UserDefaults at init, which recreates
  the stale per-screen copy).
- The Technical Meter's Daily/Weekly was a private `@State` over a private duplicate of
  `TechnicalTimeframe`, and `TechnicalAnalysisDetailView` started its own `@State .daily` —
  so the meter could read Weekly while Details opened on Daily. Both now read the one key.
- Holders -> Recent Activities: the Institutions sort, the Insiders filter and the Congress
  sort were `@State` defaults. They are `@AppStorage` now; the sub-tab stays `@State`
  because a notification deep link seeds it.

Every stored value is an id (`storageID`), never the rawValue — those are the pill labels
("By Value ($)", "Last 24H", "6M"), and storing a label means a relabel silently resets
everyone. An unknown id resolves to the default for DISPLAY only and is never written back.
They are device display preferences, deliberately NOT cleared on session end.

Comments are stripped and every scan is brace-bounded to the declaration it checks
(`.claude/rules/testing.md` §3): the explanatory comments beside each fix name the very
tokens asserted here.

Mutation-tested in memory (each mutated source served through a monkeypatched
`Path.read_text`, never written to disk), every one killed:
  1. TickerDetailView: binding back to `$viewModel.selectedMomentumPeriod`.
  2. CryptoDetailView: the Fear & Greed `@AppStorage` line replaced by `@State`.
  3. TickerDetailViewModel: `@Published var selectedSentimentTimeframe` re-added.
  4. CryptoDetailViewModel: seeding from `UserDefaults ... FearGreedTimeframe.storageKey`.
  5. AnalystMomentumPeriod.binding writing `$0.rawValue` instead of `$0.storageID`.
  6. SentimentTimeframe.storageID returning the label (`"Last 24H"`).
  7. FearGreedTimeframe.stored falling back to `.thirtyDay`.
  8. TechnicalMeter: `@State private var selectedPeriod: TechnicalTimeframe = .daily` back.
  9. TechnicalAnalysisDetailView: the picker back on `$selectedTimeframe`.
 10. TechnicalTimeframe.storageKey renamed (meter and detail would still share it, but the
     pinned key — and every stored choice — would be lost).
 11. RecentActivitiesSection: congress selector bound to `$institutionsSortID`.
 12. RecentActivitiesSection: `selectedFilter` back to `@State ... = .all`.
 13. RecentActivitiesSortOption.storageID returning `"By Value ($)"`.
 14. AppState.discardDataForEndedSession removing `caydex_holders_sort`.
 15. TickerDetailView's momentum `@AppStorage` line commented out: the stripped scan misses it.
 16. TechnicalAnalysisDetailView: `@State private var selectedTimeframe ... = .daily` re-added.
 17-18. The holders bindings' getter made constant (`get: { .byValue }` / `get: { .all }`):
     the pill highlight would stick on the default.
 19-30. Host write-backs, each caught by the file-wide host scan: `.onAppear { timeframeID =
     "daily" }` (Details), `.onAppear { insiderFilterID = "all" }` (Recent), an extra default
     write in TechnicalMeter, a reset in a same-file `extension TickerDetailView`,
     `$fearGreedTimeframeID.wrappedValue = …`, the meter's Daily write moved from its tap to
     `.onAppear`, a raw `$timeframeID` handed to a child, `UserDefaults…removeObject(forKey:
     TechnicalTimeframe.storageKey)`, a `UserDefaults…set` on the literal holders key, a second
     `@AppStorage` alias on the sentiment key, `timeframeID += ""`, and
     `removeObject(forKey: Self.insiderFilterKey)`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"
_STOCK_VIEW = _IOS / "Views/Screens/TickerDetailView.swift"
_CRYPTO_VIEW = _IOS / "Views/Screens/CryptoDetailView.swift"
_STOCK_VM = _IOS / "ViewModels/TickerDetailViewModel.swift"
_CRYPTO_VM = _IOS / "ViewModels/CryptoDetailViewModel.swift"
_CONTENT = _IOS / "Views/Organisms/TickerAnalysisContent.swift"
_MOMENTUM = _IOS / "Views/Molecules/AnalysisMomentumSection.swift"
_SENTIMENT = _IOS / "Views/Organisms/SentimentAnalysisSection.swift"
_FEAR_GREED = _IOS / "Views/Organisms/CryptoFearGreedSection.swift"
_METER = _IOS / "Views/Molecules/TechnicalMeter.swift"
_TECH_DETAIL = _IOS / "Views/Screens/TechnicalAnalysisDetailView.swift"
_RECENT = _IOS / "Views/Organisms/RecentActivitiesSection.swift"
_APP_STATE = _IOS / "Core/State/AppState.swift"
_SYNC = _IOS / "Core/Services/SettingsSyncManager.swift"


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """Block, full-line AND trailing `//` comments. The trailing form needs whitespace (or
    line start) before `//`, so a URL's `https://` in a string literal survives."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"(^|\s)//.*$", "", line) for line in src.splitlines())


def _code(path: Path) -> str:
    return _strip_comments(_read(path))


def _block(src: str, header: str, open_ch: str = "{", close_ch: str = "}") -> str:
    """The balanced `{…}` (or `(…)`) that follows `header` in already-stripped `src`."""
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    first = src.index(open_ch, start)
    depth = 0
    for i in range(first, len(src)):
        if src[i] == open_ch:
            depth += 1
        elif src[i] == close_ch:
            depth -= 1
            if depth == 0:
                return src[first:i + 1]
    pytest.fail(f"unbalanced {open_ch}{close_ch} after {header!r}")


def _call(src: str, header: str) -> str:
    return _block(src, header, "(", ")")


# ── each remembered choice: key, ids (not labels), display-only fallback ──────

# (file, type, key, default case, {case: rawValue label})
_CHOICES = [
    (_MOMENTUM, "AnalystMomentumPeriod", "caydex_analyst_momentum_period", "sixMonths",
     {"sixMonths": "6M", "oneYear": "1Y"}),
    (_SENTIMENT, "SentimentTimeframe", "caydex_sentiment_timeframe", "last24h",
     {"last24h": "Last 24H", "last7d": "Last 7D"}),
    (_FEAR_GREED, "FearGreedTimeframe", "caydex_fear_greed_timeframe", "today",
     {"today": "1D", "sevenDay": "7D", "thirtyDay": "30D"}),
    (_METER, "TechnicalTimeframe", "caydex_technical_timeframe", "daily",
     {"daily": "Daily", "weekly": "Weekly"}),
]


def _storage_ids(ext: str) -> dict[str, str]:
    switch = _block(ext, "var storageID: String")
    return dict(re.findall(r'case \.(\w+):\s*return "([^"]+)"', switch))


@pytest.mark.parametrize("path,type_name,key,default,labels", _CHOICES,
                         ids=[c[1] for c in _CHOICES])
def test_each_choice_stores_an_id_under_its_pinned_key(path, type_name, key, default, labels):
    ext = _block(_code(path), f"extension {type_name} {{")
    assert f'static let storageKey = "{key}"' in ext, f"{type_name}: the stored key moved"
    assert f"static let defaultChoice: {type_name} = .{default}" in ext, type_name

    ids = _storage_ids(ext)
    assert set(ids) == set(labels), f"{type_name}: every case needs its own id, got {ids}"
    assert len(set(ids.values())) == len(ids), f"{type_name}: two cases share an id"
    for case, stored_id in ids.items():
        assert stored_id != labels[case], (
            f"{type_name}.{case} stores its LABEL {stored_id!r} — a relabel would reset it")

    stored = _block(ext, f"static func stored(_ id: String) -> {type_name}")
    assert "allCases.first { $0.storageID == id } ?? defaultChoice" in stored, (
        f"{type_name}: an unknown id must read as the default")
    binding = _block(ext, f"static func binding(_ id: Binding<String>) -> Binding<{type_name}>")
    assert "get: { Self.stored(id.wrappedValue) }" in binding
    assert "set: { id.wrappedValue = $0.storageID }" in binding, (
        f"{type_name}: the setter must store the id, never the rawValue label")
    # Display-only fallback: nothing in the mapping writes to the store by itself.
    assert "UserDefaults" not in ext and "rawValue" not in ext, type_name


def test_the_stored_keys_are_distinct_and_namespaced():
    keys = [c[2] for c in _CHOICES] + [
        "caydex_holders_sort", "caydex_holders_insider_filter", "caydex_holders_congress_sort"]
    assert len(set(keys)) == len(keys)
    assert all(k.startswith("caydex_") for k in keys)


# ── the screens own the Analysis choices; the ViewModels do not ──────────────

_SCREEN_BINDINGS = {
    _STOCK_VIEW: [
        ("AnalystMomentumPeriod", "momentumPeriodID", "selectedMomentumPeriod"),
        ("SentimentTimeframe", "sentimentTimeframeID", "selectedSentimentTimeframe"),
    ],
    _CRYPTO_VIEW: [
        ("AnalystMomentumPeriod", "momentumPeriodID", "selectedMomentumPeriod"),
        ("SentimentTimeframe", "sentimentTimeframeID", "selectedSentimentTimeframe"),
        ("FearGreedTimeframe", "fearGreedTimeframeID", "selectedFearGreedTimeframe"),
    ],
}
_SCREEN_STRUCT = {_STOCK_VIEW: "struct TickerDetailView: View", _CRYPTO_VIEW: "struct CryptoDetailView: View"}


@pytest.mark.parametrize("view", list(_SCREEN_BINDINGS), ids=lambda p: p.stem)
def test_the_screen_binds_its_app_storage_into_the_analysis_tab(view):
    src = _code(view)
    struct = _block(src, _SCREEN_STRUCT[view])
    call = _call(struct, "TickerAnalysisContent(")
    for type_name, var, param in _SCREEN_BINDINGS[view]:
        decl = (f"@AppStorage({type_name}.storageKey) private var {var}: String = "
                f"{type_name}.defaultChoice.storageID")
        assert decl in struct, f"{view.name}: {var} is not the stored preference"
        assert f"{param}: {type_name}.binding(${var})" in call, (
            f"{view.name}: {param} is not bound to the stored choice")
        assert f"$viewModel.{param}" not in call, (
            f"{view.name}: {param} is back on a per-screen ViewModel copy")
        assert f"@State private var {var}" not in struct


@pytest.mark.parametrize("vm", [_STOCK_VM, _CRYPTO_VM], ids=lambda p: p.stem)
def test_the_view_models_hold_no_copy_and_seed_none(vm):
    src = _code(vm)
    for name in ("selectedMomentumPeriod", "selectedSentimentTimeframe",
                 "selectedFearGreedTimeframe"):
        assert not re.search(rf"\bvar {name}\b", src), (
            f"{vm.name}: `{name}` is back — a per-ViewModel copy resets on every push")
    for type_name in ("AnalystMomentumPeriod", "SentimentTimeframe", "FearGreedTimeframe"):
        assert f"{type_name}.storageKey" not in src, (
            f"{vm.name}: seeding from UserDefaults at init recreates the stale copy")
    for key in ("caydex_analyst_momentum_period", "caydex_sentiment_timeframe",
                "caydex_fear_greed_timeframe"):
        assert key not in src, vm.name


def test_the_shared_content_still_takes_bindings():
    """The organism stays storage-agnostic: its host decides where the value lives."""
    struct = _block(_code(_CONTENT), "struct TickerAnalysisContent: View")
    assert "@Binding var selectedMomentumPeriod: AnalystMomentumPeriod" in struct
    assert "@Binding var selectedSentimentTimeframe: SentimentTimeframe" in struct
    assert "var selectedFearGreedTimeframe: Binding<FearGreedTimeframe>? = nil" in struct
    assert "@AppStorage" not in struct


# ── the Technical Meter and its Details screen read ONE key ──────────────────


def test_the_meter_reads_the_shared_key_and_a_tap_writes_the_id():
    struct = _block(_code(_METER), "struct TechnicalMeter: View")
    assert ("@AppStorage(TechnicalTimeframe.storageKey) private var timeframeID: String = "
            "TechnicalTimeframe.defaultChoice.storageID") in struct
    assert "enum TechnicalPeriod" not in struct, "the private duplicate of TechnicalTimeframe is back"
    assert not re.search(r"@State\s+private\s+var\s+selectedPeriod", struct), (
        "a private @State period ignores the Details screen and resets on every screen")
    getter = _block(struct, "private var selectedPeriod: TechnicalTimeframe")
    assert "TechnicalTimeframe.stored(timeframeID)" in getter
    assert struct.count("timeframeID = TechnicalTimeframe.daily.storageID") == 1
    assert struct.count("timeframeID = TechnicalTimeframe.weekly.storageID") == 1
    assert "selectedPeriod = ." not in struct, "a tap must write the stored id"


def test_the_details_screen_reads_the_same_key():
    struct = _block(_code(_TECH_DETAIL), "struct TechnicalAnalysisDetailView: View")
    assert ("@AppStorage(TechnicalTimeframe.storageKey) private var timeframeID: String = "
            "TechnicalTimeframe.defaultChoice.storageID") in struct
    assert not re.search(r"@State\s+private\s+var\s+selectedTimeframe", struct), (
        "Details is back on its own @State .daily — it will disagree with the meter")
    assert 'Picker("Timeframe", selection: TechnicalTimeframe.binding($timeframeID))' in struct
    getter = _block(struct, "private var selectedTimeframe: TechnicalTimeframe")
    assert "TechnicalTimeframe.stored(timeframeID)" in getter
    # The four readers follow the stored choice.
    assert struct.count("(for: selectedTimeframe)") == 4


# ── Holders → Recent Activities ──────────────────────────────────────────────

_HOLDER_PREFS = [
    ("institutionsSortKey", "caydex_holders_sort", "institutionsSortID",
     "RecentActivitiesSortOption", "byValue"),
    ("insiderFilterKey", "caydex_holders_insider_filter", "insiderFilterID",
     "InsiderActivityFilterOption", "all"),
    ("congressSortKey", "caydex_holders_congress_sort", "congressSortID",
     "RecentActivitiesSortOption", "byValue"),
]


def _recent_struct() -> str:
    return _block(_code(_RECENT), "struct RecentActivitiesSection: View")


def test_recent_activities_stores_each_pill_under_its_own_key():
    struct = _recent_struct()
    for key_name, key, var, type_name, default in _HOLDER_PREFS:
        assert f'private static let {key_name} = "{key}"' in struct, key
        assert (f"@AppStorage(RecentActivitiesSection.{key_name}) private var {var}: String = "
                f"{type_name}.{default}.storageID") in struct, var
    for gone in ("selectedSort", "selectedFilter", "congressSort"):
        assert not re.search(rf"@State\s+private\s+var\s+{gone}\b", struct), (
            f"`{gone}` is back on @State — it resets on every ticker")
    # Display values resolve from the store; nothing assigns them.
    assert "private var selectedSort: RecentActivitiesSortOption { .stored(institutionsSortID) }" in struct
    assert "private var selectedFilter: InsiderActivityFilterOption { .stored(insiderFilterID) }" in struct
    assert "private var congressSort: RecentActivitiesSortOption { .stored(congressSortID) }" in struct


def test_each_selector_writes_its_own_stored_id():
    struct = _recent_struct()
    institutions = _block(struct, "private var institutionsContent: some View")
    insiders = _block(struct, "private var insidersContent: some View")
    congress = _block(struct, "private var congressContent: some View")
    assert ("RecentActivitiesSortSelector(selectedSort: "
            "RecentActivitiesSortOption.binding($institutionsSortID))") in institutions
    assert ("InsiderFilterSelector(selectedFilter: "
            "InsiderActivityFilterOption.binding($insiderFilterID))") in insiders
    assert ("RecentActivitiesSortSelector(selectedSort: "
            "RecentActivitiesSortOption.binding($congressSortID))") in congress, (
        "the Congress sort must not share the Institutions choice")


def test_the_deep_link_seeded_sub_tab_is_left_alone():
    struct = _recent_struct()
    assert "@State private var selectedTab: RecentActivitiesTab" in struct
    init = _block(struct, "init(data: RecentActivitiesData")
    assert "self._selectedTab = State(initialValue: initialTab ?? .insiders)" in init


@pytest.mark.parametrize("type_name,default,labels", [
    ("RecentActivitiesSortOption", "byValue", {"byValue": "By Value ($)", "byDate": "By Date"}),
    ("InsiderActivityFilterOption", "all", {"all": "All", "informative": "Informative"}),
])
def test_holder_pills_store_ids_not_labels(type_name, default, labels):
    ext = _block(_code(_RECENT), f"private extension {type_name} {{")
    ids = _storage_ids(ext)
    assert set(ids) == set(labels), ids
    assert len(set(ids.values())) == len(ids)
    for case, stored_id in ids.items():
        assert stored_id != labels[case], f"{type_name}.{case} stores its label"
    stored = _block(ext, f"static func stored(_ id: String) -> {type_name}")
    assert f"allCases.first {{ $0.storageID == id }} ?? .{default}" in stored
    binding = _block(ext, f"static func binding(_ id: Binding<String>) -> Binding<{type_name}>")
    # The getter feeds the pill highlight: a constant (`get: { .byValue }`) would leave the
    # highlight stuck on the default while the list sorts/filters by the stored choice.
    assert "get: { Self.stored(id.wrappedValue) }" in binding, (
        f"{type_name}: the selector must read the stored choice")
    assert "set: { id.wrappedValue = $0.storageID }" in binding
    assert "UserDefaults" not in ext and "rawValue" not in ext


# ── the hosts never reset or write back a stored choice ──────────────────────
#
# The extensions above prove the mapping never writes; these prove the HOSTS do not either.
# Scanned file-wide (stripped), so a same-file `extension TickerDetailView { … }` cannot hide
# a reset. Only this group's names are matched, so another group's `@AppStorage` in the same
# screen is left alone.

_STORED_IDS = ("momentumPeriodID", "sentimentTimeframeID", "fearGreedTimeframeID",
               "institutionsSortID", "insiderFilterID", "congressSortID", "timeframeID")
_ID_ALT = "|".join(_STORED_IDS)
# An assignment in any spelling — `x = …`, `self.x = …`, `$x.wrappedValue = …`, `x += …`.
# Not `==` / `!=`, and not the declaration: its `: String` comes before the `=`.
_WRITE = re.compile(rf"\b(?:{_ID_ALT})\b(?:\.wrappedValue)?\s*\+?=(?!=)")
# A projected binding handed out raw lets a child write any string (a label included); the
# only sanctioned hand-out is `<Type>.binding($id)`, whose setter stores the id.
_RAW_BINDING = re.compile(rf"(?<!\.binding\()\$(?:{_ID_ALT})\b")
_KEY_EXPR = (r"(?:(?:AnalystMomentumPeriod|SentimentTimeframe|FearGreedTimeframe|TechnicalTimeframe)"
             r"\.storageKey|(?:RecentActivitiesSection\.)?(?:institutionsSortKey|insiderFilterKey"
             r"|congressSortKey))")
_KEY_REF = re.compile(rf"(?<!\w){_KEY_EXPR}\b")
_KEY_DEF = re.compile(r"\bstatic let (?:institutionsSortKey|insiderFilterKey|congressSortKey) = ")
_STORE_DECL = re.compile(rf"@AppStorage\(({_KEY_EXPR})\) private var (\w+): String = ")
_ALL_KEYS = [c[2] for c in _CHOICES] + [p[1] for p in _HOLDER_PREFS]


def _assert_no_write_back(src: str, where: str, writes_allowed: int = 0) -> None:
    """`src` is a whole stripped host file."""
    writes = _WRITE.findall(src)
    assert len(writes) == writes_allowed, (
        f"{where}: writes the stored choice {len(writes)}x (allowed {writes_allowed}): {writes} — "
        "a reset or write-back overrides what the user picked")
    raw = _RAW_BINDING.findall(src)
    assert not raw, f"{where}: hands out a raw stored-id binding {raw} — pass <Type>.binding($id)"
    decls = _STORE_DECL.findall(src)
    names = [name for _, name in decls]
    assert set(names) <= set(_STORED_IDS), (
        f"{where}: a second @AppStorage alias on a stored key: {names}")
    assert len({key for key, _ in decls}) == len(decls), f"{where}: one key stored twice: {decls}"
    assert len(_KEY_REF.findall(src)) == len(decls) + len(_KEY_DEF.findall(src)), (
        f"{where}: a stored key is used outside its @AppStorage declaration "
        "(a UserDefaults set/remove resets the choice behind the view's back)")
    for key in _ALL_KEYS:
        assert src.count(f'"{key}"') == len(re.findall(rf'\bstatic let \w+ = "{key}"', src)), (
            f"{where}: the literal key {key!r} is used outside its one definition")


# (file, writes the file may hold) — the meter's two are its badge taps, pinned below.
_HOSTS = [(_STOCK_VIEW, 0), (_CRYPTO_VIEW, 0), (_RECENT, 0), (_TECH_DETAIL, 0), (_METER, 2)]


@pytest.mark.parametrize("path,writes_allowed", _HOSTS, ids=[h[0].stem for h in _HOSTS])
def test_no_host_resets_or_writes_back_a_stored_choice(path, writes_allowed):
    _assert_no_write_back(_code(path), path.name, writes_allowed)


def test_the_meters_only_writes_are_its_two_badge_taps():
    src = _code(_METER)
    struct = _block(src, "struct TechnicalMeter: View")
    taps = [_block(struct[m.start():], ".onTapGesture")
            for m in re.finditer(r"\.onTapGesture\b", struct)]
    assert len(taps) == 2, f"expected the Daily and Weekly badge taps, found {len(taps)}"
    assert [len(_WRITE.findall(t)) for t in taps] == [1, 1], (
        "each badge tap writes the stored id exactly once")
    assert len(_WRITE.findall(src)) == 2, "the meter writes the stored id outside a tap"


_WRITE_BACK_MUTANTS = [
    (_TECH_DETAIL, '.onAppear { timeframeID = "daily" }', "writes the stored choice 1x"),
    (_TECH_DETAIL, "func r() { timeframeID += \"\" }", "writes the stored choice 1x"),
    (_RECENT, '.onAppear { insiderFilterID = "all" }', "writes the stored choice 1x"),
    (_RECENT, 'func r() { self.congressSortID = "by_value" }', "writes the stored choice 1x"),
    (_STOCK_VIEW, "extension TickerDetailView { func r() { momentumPeriodID = "
                  "AnalystMomentumPeriod.defaultChoice.storageID } }", "writes the stored choice 1x"),
    (_CRYPTO_VIEW, 'func r() { $fearGreedTimeframeID.wrappedValue = "today" }',
     "writes the stored choice 1x"),
    (_METER, ".onAppear { timeframeID = TechnicalTimeframe.defaultChoice.storageID }",
     r"writes the stored choice 3x \(allowed 2\)"),
    (_TECH_DETAIL, ".background(TimeframeEcho(id: $timeframeID))", "raw stored-id binding"),
    (_CRYPTO_VIEW, '@AppStorage(SentimentTimeframe.storageKey) private var otherID: String = "x"',
     "second @AppStorage alias"),
    (_TECH_DETAIL, ".onDisappear { UserDefaults.standard.removeObject(forKey: "
                   "TechnicalTimeframe.storageKey) }", "used outside its @AppStorage declaration"),
    (_RECENT, 'UserDefaults.standard.set("by_value", forKey: "caydex_holders_sort")',
     "literal key 'caydex_holders_sort'"),
]


@pytest.mark.parametrize("path,injected,message", _WRITE_BACK_MUTANTS,
                         ids=[f"{m[0].stem}-{i}" for i, m in enumerate(_WRITE_BACK_MUTANTS)])
def test_the_write_back_guard_catches_each_spelling(path, injected, message):
    allowed = dict(_HOSTS)[path]
    with pytest.raises(AssertionError, match=message):
        _assert_no_write_back(_code(path) + "\n" + injected + "\n", path.name, allowed)


def test_the_write_back_guard_ignores_reads_and_declarations():
    # Comparisons and the existing declarations / bindings are not writes.
    reads = 'if timeframeID == "daily" {} ; let a = insiderFilterID != "all"'
    _assert_no_write_back(_code(_TECH_DETAIL) + "\n" + reads + "\n", "reads", 0)
    assert _STORE_DECL.findall(_code(_RECENT)) == [
        ("RecentActivitiesSection.institutionsSortKey", "institutionsSortID"),
        ("RecentActivitiesSection.insiderFilterKey", "insiderFilterID"),
        ("RecentActivitiesSection.congressSortKey", "congressSortID"),
    ], "the declaration pattern drifted — the alias and key-use checks would go vacuous"
    assert [n for _, n in _STORE_DECL.findall(_code(_CRYPTO_VIEW))] == [
        "fearGreedTimeframeID", "momentumPeriodID", "sentimentTimeframeID"]


# ── device preferences: kept across sign-out, never synced ───────────────────


def test_session_end_does_not_clear_these_device_preferences():
    keys = [c[2] for c in _CHOICES] + [p[1] for p in _HOLDER_PREFS]
    types = [c[1] for c in _CHOICES]
    app_state = _code(_APP_STATE)
    discard = _block(app_state, "private func discardDataForEndedSession()")
    for key in keys:
        assert key not in app_state, f"{key} is a display preference of this phone, not account data"
    for type_name in types:
        assert f"{type_name}.storageKey" not in discard
    sync = _code(_SYNC)
    for key in keys:
        assert key not in sync, f"{key} is device-only by product decision"


# ── anti-vacuity ─────────────────────────────────────────────────────────────


def test_the_scans_are_not_vacuous():
    # The explanatory comments name the very tokens the stripped scans must NOT find.
    stock_vm_raw = _read(_STOCK_VM)
    assert "do not re-add one" in stock_vm_raw
    assert "do not re-add one" not in _strip_comments(stock_vm_raw)
    meter_raw = _read(_METER)
    assert "private `@State`" in meter_raw and "private `@State`" not in _strip_comments(meter_raw)
    # The bounded blocks are the real declarations, not a stray mention.
    struct = _block(_code(_STOCK_VIEW), "struct TickerDetailView: View")
    call = _call(struct, "TickerAnalysisContent(")
    assert "isAnalystLoaded: viewModel.isAnalystLoaded" in call
    assert ".onAppear" not in call, "the call block must end at its closing paren"
    recent = _recent_struct()
    assert "var body: some View" in recent and "private extension" not in recent, (
        "the struct block must end before the file-private storage extensions")
    # URL-safe stripping: a `https://` literal survives, a trailing comment does not.
    assert _strip_comments('let u = "https://a.b" // note') == 'let u = "https://a.b"'
