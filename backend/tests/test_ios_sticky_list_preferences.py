"""List preferences are set once and stay set — and a saved filter never hides the list.

TestFlight 1.0 (9): "I did change Extended hours to off but it turns on again. For everything
in here, it should ... set up once and permanently keep them." The chart sheet was fixed on its
own (`test_ios_chart_settings_persistence.py`); "check the rest too" found these list controls
falling back to their defaults on every launch (or every visit), and the user chose to keep them
on THIS device (2026-10-01):

* Research → Reports: the sort (`caydex_reports_sort`) and the persona filter tags
  (`caydex_reports_persona_filter`, a sorted `[String]` of persona keys).
* Updates: the News filter sheet (`caydex_updates_news_filter`, sources + sentiments) and the
  news-tone window (`caydex_updates_trend_window`; nothing saved = the adaptive auto mode).
* Tracking → All Whales: the sort (`caydex_all_whales_sort`). The category chip stays
  session-local on purpose.

The rules every one of them follows, pinned below:
  1. Store a STABLE id (`storageID`), never `rawValue` — each of these enums' raw values is the
     on-screen label ("Newest First", "Positive", "7D", "A–Z").
  2. Restore without writing: a garbage or retired id reads as the default and is NOT written
     back; only the user's own pick writes.
  3. A saved filter never hides the list. A saved publisher the loaded feed does not carry, or
     a saved analyst the backend no longer serves, has no row/chip to switch it off — so the
     list applies the intersection, and the saved value is never narrowed in the store. The
     sheet compares publishers case-insensitively, like the filter itself, so a saved
     "reuters" that is filtering "Reuters" rows shows its checkmark.
  4. Session end (owner decision 2026-10-01, auth.md §7): the two FILTERS are removed by
     `AppState.discardDataForEndedSession()` — like the Activity chip — and each ViewModel
     RE-READS its store in `handleIdentityChange` (both live for the whole app run). A re-read,
     never a reset: a transient-restore heal of the SAME account also fires that handler. The
     two SORTS, the news-tone window and the All Whales sort are device display preferences
     and are never cleared.

iOS half only (there is no XCTest target — testing.md §3). Every scan is comment-stripped and
brace-bounded. `test_the_scans_reject_the_reviewed_regressions` re-applies the 2026-10-01
review's regressions to the stripped source in memory and requires each checker to fail with
its own message. Mutation-tested IN MEMORY as well (a monkeypatched `Path.read_text` served the
mutated source; nothing on disk was touched) — each of these turned the named test red:
  * `reportSortOption.storageID` → `.rawValue` in the didSet          → test_reports_sort_is_saved…
  * restore via `reportSortOption = savedSort` instead of the wrapper → test_reports_sort_is_saved…
  * drop `sortReports()` after `self.reports = backendReports`        → test_reports_sort_is_saved…
  * `storageID` "date_newest" → "Newest First"                        → test_every_storage_id_is_stable…
  * drop the `.intersection(known)` on read                           → test_reports_persona_filter…
  * `selectedPersonaKeys` back to a plain `savedPersonaKeys`          → test_reports_persona_filter…
  * `savedPersonaKeys = []` (a reset, not a re-read) on identity      → test_reports_persona_filter…
  * drop the save from `togglePersonaTag`                             → test_reports_persona_filter…
  * drop the identity re-read of the persona filter                   → test_reports_persona_filter…
  * drop `filterOptions.save()` from the didSet                       → test_news_filter_is_saved…
  * `filterOptions = .default` (a reset) on identity                  → test_news_filter_is_saved…
  * the didSet saves during the re-read (flag check dropped)          → test_news_filter_is_saved…
  * `loadSaved` reads `stored["source"]`                              → test_news_filter_is_saved…
  * `loadSaved` returns `sentiments: []`                              → test_news_filter_is_saved…
  * filter with `filterOptions.matches` in applyFiltersAndGroup       → test_the_timeline_applies…
  * compute `availableSources` AFTER filtering                        → test_the_timeline_applies…
  * the chip reads `viewModel.filterOptions.chipLabel`                → test_the_timeline_applies…
  * `restrictingSources` without `.lowercased()`                      → test_the_timeline_applies…
  * `restrictingSources` returns `self`, not the narrowed copy        → test_the_timeline_applies…
  * the sheet's Apply cuts saved sources to this feed's               → test_the_filter_sheet…
  * the sheet's checkmark back to exact-case `contains(source)`       → test_the_filter_sheet…
  * the sheet's untick removes only the tapped spelling               → test_the_filter_sheet…
  * `window.saveAsPick()` also in `present(_:)`                       → test_trend_window_pick…
  * `userPickedWindow = false` back in `handleIdentityChange`         → test_trend_window_pick…
  * restore helper writes `trendWindow.saveAsPick()`                  → test_trend_window_pick…
  * the tap guard back to `guard window != trendWindow` (auto mode)   → test_trend_window_pick…
  * All Whales sort back to `@State private var sortOption`           → test_all_whales_sort…
  * `@AppStorage("caydex_all_whales_sort")` → `"all_whales_sort"`     → test_all_whales_sort…
  * the discard drops either filter removal                           → test_session_end_clears…
  * `removeObject(forKey: "caydex_reports_sort")` added to the discard → test_session_end_clears…
"""
from __future__ import annotations

import pathlib
import re

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_RESEARCH_VM = _IOS / "ViewModels" / "ResearchViewModel.swift"
_RESEARCH_MODELS = _IOS / "Models" / "ResearchModels.swift"
_UPDATES_VM = _IOS / "ViewModels" / "UpdatesViewModel.swift"
_UPDATES_MODELS = _IOS / "Models" / "UpdatesModels.swift"
_UPDATES_VIEW = _IOS / "Views" / "Screens" / "UpdatesView.swift"
_ALL_WHALES = _IOS / "Views" / "Screens" / "AllWhalesView.swift"
_APP_STATE = _IOS / "Core" / "State" / "AppState.swift"

# key literal → the one file allowed to name it.
_KEYS = {
    "caydex_reports_sort": _RESEARCH_VM,
    "caydex_reports_persona_filter": _RESEARCH_VM,
    "caydex_updates_news_filter": _UPDATES_MODELS,
    "caydex_updates_trend_window": _UPDATES_MODELS,
    "caydex_all_whales_sort": _ALL_WHALES,
}


def _strip_swift_comments(src: str) -> str:
    """Block comments, whole-line `//` comments and trailing ` //` comments. The trailing form
    needs whitespace before `//`, so a URL inside a string literal survives."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for raw in src.splitlines():
        if raw.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", raw))
    return "\n".join(out)


def _code(path: pathlib.Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return _strip_swift_comments(path.read_text(encoding="utf-8"))


def _block(src: str, header: str) -> str:
    """The brace-balanced block opened by the first `{` at or after `header`, which must occur
    exactly once — a duplicate would make the scan read whichever came first."""
    assert src.count(header) == 1, f"{header!r} found {src.count(header)}× — this scan has drifted"
    start = src.index("{", src.index(header))
    depth = 0
    for j in range(start, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start: j + 1]
    raise AssertionError(f"unbalanced braces after {header!r}")


def _blocks_containing(src: str, header: str, needle: str) -> list[str]:
    """Every block opened by `header` (which may repeat — e.g. several `extension X {`) that
    contains `needle`."""
    found = []
    for m in re.finditer(re.escape(header), src):
        start = src.index("{", m.start())
        depth = 0
        for j in range(start, len(src)):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    if needle in src[start: j + 1]:
                        found.append(src[start: j + 1])
                    break
    return found


def _span(src: str, header: str) -> tuple[int, int]:
    block = _block(src, header)
    start = src.index("{", src.index(header))
    return start, start + len(block)


def _code_call(src: str, opener: str) -> str:
    """The paren-balanced argument list of the single call `opener` (ending in `(`)."""
    assert src.count(opener) == 1, f"{opener!r} found {src.count(opener)}×"
    start = src.index(opener) + len(opener) - 1
    depth = 0
    for j in range(start, len(src)):
        if src[j] == "(":
            depth += 1
        elif src[j] == ")":
            depth -= 1
            if depth == 0:
                return src[start: j + 1]
    raise AssertionError(f"unbalanced parens after {opener!r}")


def _line_at(src: str, pos: int) -> str:
    end = src.find("\n", pos)
    return src[pos: end if end != -1 else len(src)].strip()


def _enum_labels(enum_block: str) -> dict[str, str]:
    """`case name = "Label"` → {name: label}."""
    labels = dict(re.findall(r'case (\w+) = "([^"]*)"', enum_block))
    assert len(labels) >= 2, "no labelled cases parsed — this scan would pass vacuously"
    return labels


def _storage_ids(storage_block: str) -> dict[str, str]:
    """`case .name: return "id"` → {name: id}."""
    ids = dict(re.findall(r'case \.(\w+):\s*return "([^"]*)"', storage_block))
    assert len(ids) >= 2, "no storage ids parsed — this scan would pass vacuously"
    return ids


# ── 1. stable ids, never the label ───────────────────────────────────────────


def _storage_enums() -> list[tuple[str, str, str]]:
    """(name, enum block, storageID block) for every enum this change persists."""
    research = _code(_RESEARCH_MODELS)
    updates = _code(_UPDATES_MODELS)
    whales = _code(_ALL_WHALES)
    whale_enum = _block(whales, "private enum WhaleSortOption: String, CaseIterable {")
    # NewsSentiment has more than one extension; exactly one may define the storage id.
    sentiment_ext = _blocks_containing(updates, "extension NewsSentiment {", "var storageID: String {")
    assert len(sentiment_ext) == 1, f"NewsSentiment.storageID defined {len(sentiment_ext)}×"
    return [
        ("ReportSortOption",
         _block(research, "enum ReportSortOption: String, CaseIterable {"),
         _block(_block(research, "extension ReportSortOption {"), "var storageID: String {")),
        ("NewsSentiment",
         _block(updates, "enum NewsSentiment: String, CaseIterable {"),
         _block(sentiment_ext[0], "var storageID: String {")),
        ("SentimentTrendWindow",
         _block(updates, "enum SentimentTrendWindow: String, CaseIterable, Equatable {"),
         _block(_block(updates, "extension SentimentTrendWindow {"), "var storageID: String {")),
        ("WhaleSortOption", whale_enum, _block(whale_enum, "var storageID: String {")),
    ]


def test_every_storage_id_is_stable_and_never_the_label():
    seen = 0
    for name, enum_block, storage_block in _storage_enums():
        labels = _enum_labels(enum_block)
        ids = _storage_ids(storage_block)
        assert set(ids) == set(labels), f"{name}: storageID must cover every case exactly"
        assert len(set(ids.values())) == len(ids), f"{name}: two cases share a storage id"
        for case, sid in ids.items():
            assert re.fullmatch(r"[a-z][a-z0-9_]*", sid), f"{name}.{case}: {sid!r} is not an id"
            assert sid not in labels.values(), f"{name}.{case} stores its on-screen label"
        seen += 1
    assert seen == 4


def test_unknown_ids_read_as_nothing_never_a_guess():
    research = _code(_RESEARCH_MODELS)
    init = _block(_block(research, "extension ReportSortOption {"), "init?(storageID: String) {")
    assert "Self.allCases.first(where: { $0.storageID == storageID })" in init
    assert "return nil" in init
    updates = _code(_UPDATES_MODELS)
    window = _block(_block(updates, "extension SentimentTrendWindow {"), "init?(storageID: String) {")
    assert "return nil" in window
    whale_enum = _block(_code(_ALL_WHALES), "private enum WhaleSortOption: String, CaseIterable {")
    whale_init = _block(whale_enum, "init(storageID: String) {")
    assert "?? .followers" in whale_init, "a garbage id must read as the default, Followers"


# ── 2. Research → Reports: sort ──────────────────────────────────────────────


def test_reports_sort_is_saved_as_an_id_and_restored_without_a_write_back():
    vm = _code(_RESEARCH_VM)
    assert 'private static let reportSortKey = "caydex_reports_sort"' in vm
    decl = _block(vm, "@Published var reportSortOption: ReportSortOption = .dateNewest {")
    assert "sortReports()" in decl
    assert "UserDefaults.standard.set(reportSortOption.storageID, forKey: Self.reportSortKey)" in decl
    assert ".rawValue" not in decl, "the menu label must never be what is stored"

    reader = _block(vm, "private static func storedReportSort() -> ReportSortOption? {")
    assert "UserDefaults.standard.string(forKey: reportSortKey)" in reader
    assert "ReportSortOption.init(storageID:)" in reader

    init = _block(vm, "init(prefilledTicker: String? = nil, apiClient: APIClient = .shared) {")
    assert "if let savedSort = Self.storedReportSort() {" in init
    assert "_reportSortOption = Published(initialValue: savedSort)" in init
    # Through the property it would fire didSet and write a value back on every launch.
    assert not re.search(r"(?<![_\w])reportSortOption\s*=(?!=)", init), (
        "init must restore through `_reportSortOption`, never the observed property"
    )

    # The restore does not fire didSet, so the rows are sorted where they are built.
    load = _block(vm, "func loadReports() async {")
    built = load.index("self.reports = backendReports")
    assert "sortReports()" in load[built:load.index("} catch {", built)], (
        "the restored sort must reach the rows after every list rebuild"
    )


# ── 3. Research → Reports: persona filter ────────────────────────────────────

_PERSONA_SAVE = "UserDefaults.standard.set(savedPersonaKeys.sorted(), forKey: Self.personaFilterKey)"
_PERSONA_REREAD = "savedPersonaKeys = Self.storedPersonaFilter()"


def _check_persona_filter(vm: str) -> None:
    """`vm`: the comment-stripped ResearchViewModel.swift."""
    # Not private: AppState's session-end funnel removes it by this name.
    assert re.search(r'^\s*static let personaFilterKey = "caydex_reports_persona_filter"$', vm, re.M), (
        "personaFilterKey must be a non-private constant — the session-end funnel removes it"
    )
    # No observer: a didSet save would make the identity handler's re-read write the store.
    assert re.search(r"@Published private var savedPersonaKeys: Set<String> = \[\]\n", vm), (
        "savedPersonaKeys must have no didSet — its re-read would write the store back"
    )

    reader = _block(vm, "private static func storedPersonaFilter() -> Set<String> {")
    assert "Set(AnalysisPersona.allCases.map(\\.key))" in reader
    assert "UserDefaults.standard.stringArray(forKey: personaFilterKey)" in reader
    assert ".intersection(known)" in reader, "unknown persona keys must be dropped on read"

    init = _block(vm, "init(prefilledTicker: String? = nil, apiClient: APIClient = .shared) {")
    assert "_savedPersonaKeys = Published(initialValue: Self.storedPersonaFilter())" in init

    # What the list and the chips use is DERIVED: saved ∩ what has a chip.
    assert "@Published var selectedPersonaKeys" not in vm
    derived = _block(vm, "var selectedPersonaKeys: Set<String> {")
    assert "savedPersonaKeys.intersection(personas.map(\\.key))" in derived, (
        "the list must apply saved ∩ served analysts, or a hidden tag filters invisibly"
    )
    filtered = _block(vm, "var filteredReports: [AnalysisReport] {")
    assert "let personaKeys = selectedPersonaKeys" in filtered
    assert "personaKeys.contains($0.persona.key)" in filtered
    assert "savedPersonaKeys" not in filtered, "the list must apply the derived set"

    cls = _block(vm, "class ResearchViewModel: ObservableObject {")
    # Saved exactly once, by the tap, AFTER the change.
    saves = re.findall(r"UserDefaults\.standard\.set\(([^\n]*?), forKey: Self\.personaFilterKey\)", cls)
    assert saves == ["savedPersonaKeys.sorted()"], f"the persona filter is saved {len(saves)}×, not once by the tap"
    toggle = _block(cls, "func togglePersonaTag(_ persona: AnalysisPersona) {")
    save_at = toggle.find(_PERSONA_SAVE)
    assert save_at > toggle.index("savedPersonaKeys.insert(persona.key)") and \
        save_at > toggle.index("savedPersonaKeys.remove(persona.key)"), (
            "togglePersonaTag must save the tags after changing them"
        )
    assert "removeObject(forKey: Self.personaFilterKey)" not in cls, "only the session-end funnel clears it"

    # Session end: the funnel removed the stored tags; this long-lived VM must drop its copy by
    # RE-READING (a reset would also wipe the same user's tags on a transient-restore heal),
    # ahead of the tab gate. The sort is a device preference and stays.
    identity = _block(cls, "func handleIdentityChange(isActiveTab: Bool) async {")
    assert _PERSONA_REREAD in identity, "handleIdentityChange must re-read the saved persona filter"
    assert identity.index(_PERSONA_REREAD) < identity.index("guard isActiveTab"), (
        "the persona filter re-read must run before the isActiveTab gate"
    )
    for name in ("selectedPersonaKeys", "reportSortOption"):
        assert name not in identity, f"handleIdentityChange must not reset {name}"

    # Writers: the tap's insert + remove, and the identity handler's re-read — nothing else.
    lo, hi = _span(cls, "func togglePersonaTag(_ persona: AnalysisPersona) {")
    writes = list(re.finditer(
        r"\bsavedPersonaKeys\s*(?:=(?!=)|\.(?:insert|remove|removeAll|formUnion|"
        r"formIntersection|subtract|formSymmetricDifference)\b)", cls))
    assert len([m for m in writes if lo <= m.start() < hi]) == 2, "expected the toggle's insert + remove"
    stray = [_line_at(cls, m.start()) for m in writes if not lo <= m.start() < hi]
    assert stray == [_PERSONA_REREAD], f"savedPersonaKeys is written outside the tap and the re-read: {stray}"


def test_reports_persona_filter_is_saved_restored_and_never_an_invisible_filter():
    _check_persona_filter(_code(_RESEARCH_VM))


# ── 4. Updates → News filter ─────────────────────────────────────────────────

_NEWS_REREAD = ("isRereadingSavedFilter = true\n"
                "        filterOptions = NewsFilterOptions.loadSaved()\n"
                "        isRereadingSavedFilter = false")


def _check_news_filter_store(models: str) -> None:
    """`models`: the comment-stripped UpdatesModels.swift."""
    ext = _block(models, "extension NewsFilterOptions {")
    assert 'static let storageKey = "caydex_updates_news_filter"' in ext
    save = _block(ext, "func save(to defaults: UserDefaults = .standard) {")
    assert "sentiments.map(\\.storageID)" in save and "rawValue" not in save
    assert "forKey: Self.storageKey" in save
    load = _block(ext, "static func loadSaved(from defaults: UserDefaults = .standard) -> NewsFilterOptions {")
    assert "defaults.dictionary(forKey: storageKey)" in load
    assert "defaults.set" not in load, "reading must never repair the store"

    # The field names must agree, or the filter saves and never restores.
    written = set(re.findall(r'"(\w+)":', save))
    read = set(re.findall(r'stored\["(\w+)"\]', load))
    assert written == {"sources", "sentiments"}, f"save writes {sorted(written)}"
    assert read == written, f"loadSaved reads {sorted(read)} but save writes {sorted(written)}"

    # What was parsed reaches the result.
    assert re.search(r'let sentiments = \(stored\["sentiments"\] as\? \[String\] \?\? \[\]\)\s*'
                     r"\.compactMap\(NewsSentiment\.init\(storageID:\)\)", load), "unknown ids are dropped"
    result = _code_call(load, "return NewsFilterOptions(")
    assert "sources: Array(Set(sources)).sorted()" in result, "the parsed sources must reach the result"
    assert "sentiments: NewsSentiment.allCases.filter { sentiments.contains($0) }" in result, (
        "the parsed sentiments must reach the result"
    )


def _check_news_filter_vm(vm: str) -> None:
    """`vm`: the comment-stripped UpdatesViewModel.swift."""
    decl = _block(vm, "@Published var filterOptions: NewsFilterOptions = .default {")
    assert decl.count("filterOptions.save()") == 1 and "applyFiltersAndGroup()" in decl
    assert "if !isRereadingSavedFilter { filterOptions.save() }" in decl, (
        "the didSet must not save while the identity handler re-reads the store"
    )
    init = _block(vm, "init(apiClient: APIClient = .shared) {")
    assert "_filterOptions = Published(initialValue: NewsFilterOptions.loadSaved())" in init
    assert not re.search(r"(?<![_\w])filterOptions\s*=(?!=)", init)

    cls = _block(vm, "final class UpdatesViewModel: ObservableObject {")
    assert "private var isRereadingSavedFilter = false" in cls
    assert len(re.findall(r"\bisRereadingSavedFilter = true\b", cls)) == 1
    # The VM never writes the saved filter itself (a narrowed copy would delete a publisher);
    # the one assignment is the identity handler's re-read of the store.
    writes = [_line_at(cls, m.start())
              for m in re.finditer(r"(?<![_\w.])filterOptions(?:\.\w+)?\s*=(?!=)", cls)]
    assert writes == ["filterOptions = NewsFilterOptions.loadSaved()"], (
        f"the VM assigns its saved filter other than by the identity re-read: {writes}"
    )
    identity = _block(cls, "func handleIdentityChange(isActiveTab: Bool) async {")
    assert _NEWS_REREAD in identity, "handleIdentityChange must re-read the saved News filter, unsaved"
    assert identity.index(_NEWS_REREAD) < identity.index("guard isActiveTab"), (
        "the News filter re-read must run before the isActiveTab gate"
    )


def test_news_filter_is_saved_as_ids_and_restored_without_a_write_back():
    _check_news_filter_store(_code(_UPDATES_MODELS))
    _check_news_filter_vm(_code(_UPDATES_VM))


def _check_effective_filter(models: str, vm: str, view: str) -> None:
    narrow = _block(_block(models, "extension NewsFilterOptions {"),
                    "func restrictingSources(to available: [String]) -> NewsFilterOptions {")
    assert "guard !sources.isEmpty else { return self }" in narrow
    assert "Set(available.map { $0.lowercased() })" in narrow, "case-insensitive, like matches(_:)"
    assert "narrowed.sources = sources.filter { present.contains($0.lowercased()) }" in narrow
    assert narrow.count("return self") == 1 and re.search(r"return narrowed\s*\}\Z", narrow), (
        "restrictingSources must return the NARROWED copy"
    )

    effective = _block(vm, "var effectiveFilterOptions: NewsFilterOptions {")
    assert "filterOptions.restrictingSources(to: availableSources)" in effective

    apply = _block(vm, "private func applyFiltersAndGroup() {")
    assert "filterOptions.matches" not in apply, "the timeline must apply the EFFECTIVE filter"
    sources_at = apply.index("availableSources = ")
    effective_at = apply.index("let effective = effectiveFilterOptions")
    assert sources_at < effective_at, "availableSources must be this feed's before intersecting"
    assert "newsArticles = allNewsArticles.filter { effective.matches($0) }" in apply[effective_at:]
    assert "effectiveFilterOptions.hasActiveFilters" in _block(vm, "var hasActiveFeedFilter: Bool {")

    header = _code_call(view, "LiveNewsHeader(")
    assert "filterLabel: viewModel.effectiveFilterOptions.chipLabel" in header
    assert "hasActiveFilters: viewModel.effectiveFilterOptions.hasActiveFilters" in header
    assert "viewModel.filterOptions.chipLabel" not in view
    assert "viewModel.filterOptions.hasActiveFilters" not in view


def test_the_timeline_applies_the_saved_filter_cut_to_this_feeds_publishers():
    _check_effective_filter(_code(_UPDATES_MODELS), _code(_UPDATES_VM), _code(_UPDATES_VIEW))


def _check_filter_sheet(view: str) -> None:
    """`view`: the comment-stripped UpdatesView.swift. The sheet is the real WRITER of the
    saved filter, through the binding."""
    assert "filterOptions: $viewModel.filterOptions" in _code_call(view, "NewsFilterSheet(")
    sheet = _block(view, "struct NewsFilterSheet: View {")

    apply = _block(sheet, 'Button("Apply") {')
    assert "availableSources" not in apply, (
        "Apply must keep saved publishers this feed does not list — only Reset drops them"
    )
    assert "filterOptions.sources = Array(selectedSources)" in apply, "Apply must save every ticked source"
    assert "filterOptions.sentiments = Array(selectedSentiments)" in apply, "Apply must save the sentiments"
    writes = [_line_at(sheet, m.start())
              for m in re.finditer(r"(?<![_\w.])filterOptions(?:\.\w+)?\s*=(?!=)", sheet)]
    assert writes == ["filterOptions.sources = Array(selectedSources)",
                      "filterOptions.sentiments = Array(selectedSentiments)"], f"sheet writes: {writes}"

    # Publishers compare case-insensitively here exactly as in the filter: a saved "reuters"
    # is narrowing the "Reuters" rows, so it must show their checkmark, and unticking must
    # drop every spelling.
    selected = _block(sheet, "private func isSourceSelected(_ source: String) -> Bool {")
    assert "let key = source.lowercased()" in selected
    assert "selectedSources.contains { $0.lowercased() == key }" in selected, (
        "the sheet's checkmark must match publishers case-insensitively"
    )
    toggle = _block(sheet, "private func toggleSource(_ source: String) {")
    assert "if isSourceSelected(source) {" in toggle and "selectedSources.insert(source)" in toggle
    assert "selectedSources = selectedSources.filter { $0.lowercased() != key }" in toggle, (
        "unticking must drop every spelling of the publisher"
    )
    rows = _block(sheet, "ForEach(availableSources, id: \\.self) { source in")
    assert "if isSourceSelected(source) {" in rows and "toggleSource(source)" in rows
    assert not re.search(r"selectedSources\.(?:contains|remove)\(source\)", sheet), (
        "the sheet's checkmark must match publishers case-insensitively"
    )
    seed = _block(sheet, ".onAppear {")
    assert "availableSources.first(where: { $0.lowercased() == key }) ?? saved" in seed, (
        "seed each saved source in this feed's spelling, keeping one it does not list"
    )
    assert "selectedSentiments = Set(filterOptions.sentiments)" in seed


def test_the_filter_sheet_writes_every_saved_source_and_matches_case_insensitively():
    _check_filter_sheet(_code(_UPDATES_VIEW))


# ── 5. Updates → news-tone window ────────────────────────────────────────────

_TAP_GUARD = "guard window != trendWindow || !userPickedWindow else { return }"


def _check_trend_window(models: str, vm: str) -> None:
    ext = _block(models, "extension SentimentTrendWindow {")
    assert 'static let savedPickKey = "caydex_updates_trend_window"' in ext
    pick = _block(ext, "static func savedPick(from defaults: UserDefaults = .standard) -> SentimentTrendWindow? {")
    assert "SentimentTrendWindow.init(storageID:)" in pick and "defaults.set" not in pick
    save = _block(ext, "func saveAsPick(to defaults: UserDefaults = .standard) {")
    assert "defaults.set(storageID, forKey: Self.savedPickKey)" in save

    cls = _block(vm, "final class UpdatesViewModel: ObservableObject {")
    # Saved from the user's tap ONLY: the auto pick (`present`) and the failure snap-back
    # (`loadTrend`) assign `trendWindow` too, and storing either overwrites the real choice.
    assert cls.count(".saveAsPick(") == 1, "the window is saved somewhere other than the tap"
    tap = _block(cls, "func setTrendWindow(_ window: SentimentTrendWindow) {")
    # In auto mode a tap on the window already shown is still the user's pick: unrecorded,
    # the next scope or a relaunch auto-picks another window.
    assert _TAP_GUARD in tap, "a tap on the auto-chosen window must still be recorded and saved"
    assert "guard window != trendWindow else { return }" not in tap
    assert tap.index(_TAP_GUARD) < tap.index("userPickedWindow = true") < tap.index("window.saveAsPick()")
    assert tap.index("window.saveAsPick()") < tap.index("guard let scope = selectedTab?.scope else { return }"), (
        "the pick must be saved even when no tab is selected yet"
    )
    assert "startTrendLoad(scope: scope, force: false)" in tap
    for name in ("private func present(", "private func loadTrend(", "private func startTrendLoad("):
        assert "saveAsPick" not in _block(cls, name), f"{name} must not save the window"

    restore = _block(cls, "private func restoreTrendWindowPreference() {")
    assert "let saved = SentimentTrendWindow.savedPick()" in restore
    assert "userPickedWindow = saved != nil" in restore
    assert "trendWindow = saved ?? .month" in restore
    assert "saveAsPick" not in restore and "UserDefaults" not in restore, "restoring never writes"

    assert "restoreTrendWindowPreference()" in _block(cls, "init(apiClient: APIClient = .shared) {")
    identity = _block(cls, "func handleIdentityChange(isActiveTab: Bool) async {")
    gate = identity.index("guard isActiveTab")
    assert identity.index("restoreTrendWindowPreference()") < gate
    for stale in ("userPickedWindow = false", "trendWindow = .month"):
        assert stale not in identity, f"handleIdentityChange forces auto mode again ({stale})"


def test_trend_window_pick_is_saved_on_a_tap_only_and_restored_as_a_pick():
    _check_trend_window(_code(_UPDATES_MODELS), _code(_UPDATES_VM))


# ── 6. Tracking → All Whales sort ────────────────────────────────────────────


def test_all_whales_sort_is_saved_and_the_category_chip_is_not():
    view = _code(_ALL_WHALES)
    body = _block(view, "struct AllWhalesView: View {")
    assert ('@AppStorage("caydex_all_whales_sort") private var storedSortID: String = '
            "WhaleSortOption.followers.storageID") in body
    assert "@State private var sortOption" not in body
    derived = _block(body, "private var sortOption: WhaleSortOption {")
    assert "WhaleSortOption(storageID: storedSortID)" in derived
    writes = re.findall(r"\bstoredSortID\s*=(?!=)\s*([^\n]+)", body)
    assert writes == ["next.storageID"], f"only the Sort tap may write the saved sort: {writes}"
    assert "rawValue" not in "".join(writes)
    # The category chip is navigation within the screen, not a setting.
    assert "@State private var selectedFilter: WhaleCategoryFilter = .all" in body


# ── 7. session end: the two FILTERS go, the display preferences stay ─────────

_FILTER_REMOVALS = (
    "UserDefaults.standard.removeObject(forKey: ResearchViewModel.personaFilterKey)",
    "UserDefaults.standard.removeObject(forKey: NewsFilterOptions.storageKey)",
)
# The sorts, the news-tone window and the All Whales sort, by every name they go by.
_KEPT = ("caydex_reports_sort", "caydex_updates_trend_window", "caydex_all_whales_sort",
         "reportSortKey", "savedPickKey", "storedSortID", "ReportSortOption",
         "SentimentTrendWindow", "WhaleSortOption")


def _check_discard(discard: str) -> None:
    assert "LearnIdentityEpoch.bump()" in discard, "anti-vacuity: this is the real funnel"
    assert "ActivityFilter.storageKey" in discard, "anti-vacuity: the Activity chip precedent"
    for line in _FILTER_REMOVALS:
        assert discard.count(line) == 1, f"discardDataForEndedSession must remove the filter once: {line}"
    for name in _KEPT:
        assert name not in discard, f"discardDataForEndedSession clears the device preference {name}"


def test_session_end_clears_the_two_filters_and_keeps_the_display_preferences():
    _check_discard(_block(_code(_APP_STATE), "private func discardDataForEndedSession() {"))

    # Each key literal is named in exactly one file (its owner), so nothing else can reuse it;
    # the funnel reaches the two filter keys only through their owners' constants.
    counts = {key: {} for key in _KEYS}
    refs = {"ResearchViewModel.personaFilterKey": {}, "NewsFilterOptions.storageKey": {}}
    for path in _IOS.rglob("*.swift"):
        src = _code(path)
        for key in _KEYS:
            n = len(re.findall(rf'"{re.escape(key)}"', src))
            if n:
                counts[key][path] = n
        for ref in refs:
            n = len(re.findall(rf"(?<![\w.]){re.escape(ref)}\b", src))
            if n:
                refs[ref][path] = n
    for key, owner in _KEYS.items():
        assert counts[key] == {owner: 1}, (
            f"{key} must be declared once, in {owner.name}: "
            f"{ {p.name: n for p, n in counts[key].items()} }"
        )
    for ref, where in refs.items():
        assert where == {_APP_STATE: 1}, (
            f"{ref} may be named only by the session-end funnel: "
            f"{ {p.name: n for p, n in where.items()} }"
        )


# ── 8. the checkers reject the reviewed regressions (anti-vacuity) ───────────


def _swap(src: str, old: str, new: str) -> str:
    assert src.count(old) == 1, f"self-test anchor drifted: {old[:70]!r}"
    return src.replace(old, new)


def test_the_scans_reject_the_reviewed_regressions():
    """Each 2026-10-01 review finding, re-applied to the stripped source IN MEMORY, must fail its
    checker with that checker's own message — a guard that stays green here proves nothing."""
    rvm = _code(_RESEARCH_VM)
    models = _code(_UPDATES_MODELS)
    uvm = _code(_UPDATES_VM)
    view = _code(_UPDATES_VIEW)
    discard = _block(_code(_APP_STATE), "private func discardDataForEndedSession() {")

    # The un-mutated sources pass, so every failure below is the mutation's.
    _check_persona_filter(rvm)
    _check_news_filter_store(models)
    _check_news_filter_vm(uvm)
    _check_effective_filter(models, uvm, view)
    _check_filter_sheet(view)
    _check_trend_window(models, uvm)
    _check_discard(discard)

    # The review's literal suggestion, a reset: it would also wipe the same user's tags on a
    # transient-restore heal, which fires the same handler.
    with pytest.raises(AssertionError, match="must re-read the saved persona filter"):
        _check_persona_filter(_swap(rvm, _PERSONA_REREAD, "savedPersonaKeys = []"))
    with pytest.raises(AssertionError, match="must re-read the saved persona filter"):
        _check_persona_filter(_swap(rvm, "        " + _PERSONA_REREAD + "\n", ""))
    with pytest.raises(AssertionError, match="written outside the tap and the re-read"):
        _check_persona_filter(_swap(rvm, "func loadReports() async {",
                                    "func loadReports() async {\n        savedPersonaKeys = []"))
    with pytest.raises(AssertionError, match="saved 0×, not once by the tap"):
        _check_persona_filter(_swap(rvm, "        " + _PERSONA_SAVE + "\n", ""))

    with pytest.raises(AssertionError, match="loadSaved reads"):
        _check_news_filter_store(_swap(models, 'stored["sources"]', 'stored["source"]'))
    with pytest.raises(AssertionError, match="parsed sentiments must reach the result"):
        _check_news_filter_store(_swap(
            models, "sentiments: NewsSentiment.allCases.filter { sentiments.contains($0) }", "sentiments: []"))
    with pytest.raises(AssertionError, match="other than by the identity re-read"):
        _check_news_filter_vm(_swap(
            uvm, "filterOptions = NewsFilterOptions.loadSaved()", "filterOptions = .default"))
    with pytest.raises(AssertionError, match="must not save while the identity handler re-reads"):
        _check_news_filter_vm(_swap(
            uvm, "if !isRereadingSavedFilter { filterOptions.save() }", "filterOptions.save()"))

    with pytest.raises(AssertionError, match="must return the NARROWED copy"):
        _check_effective_filter(_swap(models, "return narrowed", "return self"), uvm, view)

    with pytest.raises(AssertionError, match="Apply must keep saved publishers"):
        _check_filter_sheet(_swap(view, "filterOptions.sources = Array(selectedSources)",
                                  "filterOptions.sources = Array(selectedSources.filter { availableSources.contains($0) })"))
    with pytest.raises(AssertionError, match="checkmark must match publishers case-insensitively"):
        _check_filter_sheet(_swap(view, "selectedSources.contains { $0.lowercased() == key }",
                                  "selectedSources.contains(source)"))
    with pytest.raises(AssertionError, match="must drop every spelling"):
        _check_filter_sheet(_swap(view, "selectedSources = selectedSources.filter { $0.lowercased() != key }",
                                  "selectedSources.remove(source)"))

    with pytest.raises(AssertionError, match="tap on the auto-chosen window must still be recorded"):
        _check_trend_window(models, _swap(uvm, _TAP_GUARD, "guard window != trendWindow else { return }"))

    with pytest.raises(AssertionError, match="must remove the filter once"):
        _check_discard(_swap(discard, _FILTER_REMOVALS[1], ""))
    with pytest.raises(AssertionError, match="clears the device preference caydex_reports_sort"):
        _check_discard(discard + '\nUserDefaults.standard.removeObject(forKey: "caydex_reports_sort")')
