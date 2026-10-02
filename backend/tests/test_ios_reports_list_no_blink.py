"""The Reports list keeps every card's identity across the 5 s poll, and pull-to-refresh no
longer dims the whole Research tab.

TestFlight 1.0 (9), iPhone 16 Pro on mobile data: "When a report is running, the whole screen
here is blink sometime". Two causes, both fixed:

* `AnalysisReport` declared `let id = UUID()` with an id-only `==` / `hash`. `loadReports()`
  rebuilds every row (`AnalysisReport.from`) on the 5 s poll, on `.started`, on every 25 %
  bucket and on completion, and `ReportsListSection` keys `ForEach(group.reports)` on that id,
  so every load removed and re-inserted every card. The only stateful leaf in a card is
  `CompanyLogoView`'s image, which restarted on its initials tile — in dark mode every logo
  flipped from a white chip to a dark letter tile and back, all at once, every ~5 s.
  Fix: `var id: String { backendId ?? "mock:\\(ticker):\\(persona.key)" }` (the server row id;
  previews fall back to their unique (ticker, persona) pair) AND synthesized memberwise
  `Equatable` / `Hashable`. The two ship together: with a stable id, an id-only `==` would let
  SwiftUI call a card "unchanged" when only its progress, step text or status moved, and freeze
  it — so no row view in the chain may short-circuit on `==` (`.equatable()`, `EquatableView`,
  an `Equatable` conformance) either.
* `if viewModel.isLoading { LoadingOverlay() }` put a full-screen dark scrim plus a second
  spinner over the tab on every pull-to-refresh. Removed with the flag; the system refresh
  control is the indicator.
* Contributing: list answers were applied in ARRIVAL order. A poll GET sent before a report
  completed but answered after the completion reload flipped the finished card READY → dimmed
  PROCESSING → READY. `loadReports()` now numbers each request (`reportsRequestSeq`) and applies
  an outcome only when it is newer than the last one applied (`reportsAppliedSeq`), checked
  BEFORE the drain, the slot release and the assignment; `handleIdentityChange` marks every
  in-flight answer superseded so the previous account's late answer never lands.

Hardened after an adversarial review, which brought the blink back through source mutations
that every earlier guard let through:
* identity ONE LEVEL UP: the outer `ForEach(sections)` keys on `ReportSectionGroup.id`, and
  `groupedReports` rebuilds every group on every render, so that id is pinned too; no `.id(_:)`
  anywhere from the Research tab mount down to the card; the card draws its logo through
  `CompanyLogoView` (the stateful leaf — its cache is owned by `test_ios_company_logo_cache.py`).
* the scrim is pinned by SHAPE, not by the names `isLoading` / `LoadingOverlay`: the screen's
  ZStack holds two layers, one overlay (the selection bar), and both `.refreshable` closures and
  `refresh()` are exact.
* `loadReports()` does nothing before its request, a superseded outcome only logs and returns,
  the catch does nothing before its guards, `reportsAppliedSeq = seq` directly follows the guard;
  the two seq counters are written only in their known roles, the identity mark runs
  unconditionally, and the list is emptied only by an identity change or a refusal.
* round 2: that refusal clear is bounded to the typed-refusal ARM (not the whole catch tail);
  nothing `await`s between the do-block's stale guard and the assignment, nor before the
  identity mark; no type in the row-chain files is `Equatable` under any name; and the screen
  owns its ViewModel as a `@StateObject`.

Not here (owned by `test_ios_reports_gate_from_refusal.py`): the typed-refusal arm in the
catch, its `reportsAppliedSeq` advance, the in-flight wait / poll re-arm / epoch in
`handleIdentityChange`. Not here either: the pinned drain/assign tokens of
`test_research_list_timeout_contract.py`, `test_ios_generate_button_cap_state.py` and
`test_ios_sticky_list_preferences.py` — this file pins only their ORDER relative to the guard.

There is no XCTest target (testing.md §3), so this pins the Swift source: comments are stripped
before every assertion (the fix's own comments name `UUID`, `==`, `LoadingOverlay` and the seq
counters), every check is brace-bounded to the declaration it means, and each test asserts it is
reading a real, non-trivial declaration (anti-vacuity).

Mutation-tested IN MEMORY (``pathlib.Path.read_text`` monkeypatched for the one target file —
the real Swift files are never touched, other sessions read them concurrently). The table runs
on every pass as ``test_each_mutation_is_killed``; each mutation must fail with the assertion
message that names it (``pytest.raises(match=…)``), so it cannot "pass" by tripping an unrelated
earlier check, and every anchor must occur exactly once so it cannot hit the wrong occurrence.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_MODELS = _IOS / "Models" / "ResearchModels.swift"
_VM = _IOS / "ViewModels" / "ResearchViewModel.swift"
_SECTION = _IOS / "Views" / "Organisms" / "ReportsListSection.swift"
_ROW = _IOS / "Views" / "Molecules" / "SelectableReportRow.swift"
_CARD = _IOS / "Views" / "Molecules" / "ReportCard.swift"
_CONTENT = _IOS / "ContentView.swift"

_STRUCT = "struct AnalysisReport: Identifiable, Hashable"


def _strip_swift_comments(src: str) -> str:
    """Drop block comments, whole-line `//` comments and trailing `//` tails.

    Load-bearing: the fix's own comments name `UUID`, `==`, `LoadingOverlay` and the seq guard
    while explaining them, so an un-stripped scan for their ABSENCE fails on prose and a scan
    for their PRESENCE passes on a revert whose comment survived. A tail needs leading
    whitespace, so a `https://` inside a string literal is not cut.
    """
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


def _balanced(src: str, start: int, open_: str = "{", close: str = "}") -> str:
    """The balanced `open_`…`close` span whose opener is the first `open_` at/after `start`."""
    begin = src.index(open_, start)
    depth = 0
    for i in range(begin, len(src)):
        if src[i] == open_:
            depth += 1
        elif src[i] == close:
            depth -= 1
            if depth == 0:
                return src[begin: i + 1]
    raise AssertionError(f"unbalanced `{open_}{close}` at offset {start}")


def _block(src: str, header: str, open_: str = "{", close: str = "}") -> str:
    """The balanced `open_`…`close` body that follows the ONLY `header` (a literal prefix).

    `[`/`]` bound an array literal (the mock reports), `{`/`}` a declaration.
    """
    assert src.count(header) == 1, f"expected exactly one `{header}`, found {src.count(header)}"
    return _balanced(src, src.index(header) + len(header), open_, close)


def _swift_tree() -> list[pathlib.Path]:
    root = _IOS.parent  # frontend/ios: ios/, Shared/, CaydexWidgets/
    files = sorted(root.rglob("*.swift"))
    assert len(files) > 500 and _MODELS in files and _CARD in files, (
        f"scanned {len(files)} Swift files under {root} — the tree moved; this guard is vacuous")
    return files


def _analysis_report_body() -> str:
    models = _code(_MODELS)
    assert _STRUCT in models, f"`{_STRUCT}` is gone — the identity guard cannot find the model"
    body = _block(models, _STRUCT)
    # Anti-vacuity: the real list model, not a stub that happens to share the name.
    assert ("let backendId: String?" in body
            and "func withClientTimeout() -> AnalysisReport" in body
            and "static let mockReports" in body), body[:300]
    return body


# ── 1. Stable identity ────────────────────────────────────────────────────────


def test_analysis_report_id_is_the_backend_id():
    """A per-instance `UUID()` re-mints the id on every `loadReports()` — the root cause."""
    body = _analysis_report_body()
    assert "UUID" not in body, (
        "AnalysisReport mints a UUID again: every 5 s poll re-inserts every Reports card and "
        "every company logo flashes back to its initials tile")
    assert not re.search(r"\blet\s+id\b", body), (
        "a stored `let id` is back on AnalysisReport — derive it from backendId (a stored id is "
        "re-minted by every construction in from() / withClientTimeout())")
    m = re.search(r'\bvar\s+id\s*:\s*String\s*\{\s*backendId\s*\?\?\s*"([^"\n]*)"\s*\}', body)
    assert m, (
        'AnalysisReport.id must be the computed `var id: String { backendId ?? "…" }` — the '
        "server row id; never the ticker or a ticker+persona pair (one screen can list two AVGO rows)")
    fallback = m.group(1)
    assert r"\(ticker)" in fallback and r"\(persona.key)" in fallback, (
        f'the mock fallback id "{fallback}" must interpolate BOTH \\(ticker) and \\(persona.key) — '
        "otherwise preview rows collide in one ForEach")
    assert len(re.findall(r"\b(?:var|let)\s+id\b", body)) == 1, "AnalysisReport declares `id` twice"


def test_from_threads_the_server_id_and_the_timeout_flip_keeps_it():
    """The id is only stable because every construction carries the server row id."""
    models = _code(_MODELS)
    header = "static func from(_ item: BackendReportListItem) -> AnalysisReport"
    assert header in models, "AnalysisReport.from(_:) is gone"
    built = _block(models, header)
    assert "parseISO(item.processingStartedAt)" in built, "not the list-row mapper"
    assert built.count("AnalysisReport(") == 1, "from(_:) must build exactly one row"
    assert re.search(r"\bbackendId:\s*item\.id\s*,", built), (
        "AnalysisReport.from(_:) must thread the server row id (`backendId: item.id,`) — without "
        "it every live row falls back to the mock id and same-ticker/persona rows collide")

    flip = _block(_analysis_report_body(), "func withClientTimeout() -> AnalysisReport")
    assert "status: .failed" in flip, "not the client-timeout copy"
    assert re.search(r"\bbackendId:\s*backendId\s*,", flip), (
        "withClientTimeout() must pass backendId through — the local timeout flip must keep the "
        "card's identity, or the card is torn down and re-inserted as it turns FAILED")


def test_mock_reports_have_unique_fallback_ids():
    """Previews have no backendId, so their id is `mock:<ticker>:<persona>` — the pairs must be
    unique or two preview rows share one ForEach id."""
    body = _analysis_report_body()
    header = "static let mockReports: [AnalysisReport] ="
    assert header in body, "AnalysisReport.mockReports is gone"
    mocks = _block(body, header, "[", "]")
    entries = mocks.split("AnalysisReport(")[1:]
    assert len(entries) >= 2, f"mockReports holds {len(entries)} rows — the uniqueness check is vacuous"
    pairs = []
    for entry in entries:
        ticker = re.search(r'\bticker:\s*"([^"]+)"', entry)
        persona = re.search(r"\bpersona:\s*\.(\w+)", entry)
        assert ticker and persona, f"cannot read a mock row's ticker/persona: {entry[:120]}"
        pairs.append((ticker.group(1), persona.group(1)))
    assert len(set(pairs)) == len(pairs), (
        f"mock reports repeat a (ticker, persona) pair: {pairs} — their fallback ids collide")
    assert len(re.findall(r"\bbackendId:\s*nil\s*,", mocks)) == len(entries), (
        "every mock report must have `backendId: nil` — the (ticker, persona) uniqueness above is "
        "what keeps the fallback ids apart, and it says nothing about a mock carrying a server id")


# ── 2. Equality is memberwise, and nothing in the row chain short-circuits on it ──


def test_analysis_report_equality_is_synthesized():
    body = _analysis_report_body()
    assert not re.search(r"\bstatic\s+func\s*==", body), (
        "a hand-written `==` is back on AnalysisReport — with a stable id, an id-only `==` makes "
        "SwiftUI skip a card whose progress, step text or status changed")
    assert not re.search(r"\bfunc\s+hash\s*\(\s*into\b", body), (
        "a hand-written hash(into:) is back on AnalysisReport — keep Hashable synthesized "
        "(memberwise), consistent with the synthesized `==`")


def test_no_hand_written_equality_for_analysis_report_anywhere():
    """The struct-body check cannot see an `extension AnalysisReport` (or a file-scope operator)
    in another file that re-adds the id-only `==`."""
    ext_eq: dict[str, int] = {}
    ext_hash: dict[str, int] = {}
    operator: dict[str, int] = {}
    for f in _swift_tree():
        src = _strip_swift_comments(f.read_text(encoding="utf-8"))
        if "AnalysisReport" not in src:
            continue
        rel = str(f.relative_to(_IOS.parent))
        for m in re.finditer(r"\bextension\s+AnalysisReport\b", src):
            ext = _balanced(src, m.end())
            if re.search(r"\bstatic\s+func\s*==", ext):
                ext_eq[rel] = ext_eq.get(rel, 0) + 1
            if re.search(r"\bfunc\s+hash\s*\(\s*into\b", ext):
                ext_hash[rel] = ext_hash.get(rel, 0) + 1
        n = len(re.findall(r"\bfunc\s*==\s*\(\s*\w+(?:\s+\w+)?\s*:\s*AnalysisReport\b", src))
        if n:
            operator[rel] = n
    assert not ext_eq, (
        f"an `extension AnalysisReport` declares `==` in {ext_eq} — equality must stay synthesized")
    assert not ext_hash, (
        f"an `extension AnalysisReport` declares hash(into:) in {ext_hash} — hashing must stay synthesized")
    assert not operator, (
        f"a hand-written `==` taking AnalysisReport exists in {operator} — equality must stay synthesized")


def test_report_rows_are_not_equatable_short_circuited():
    """A view that SwiftUI compares with a custom `==` (`.equatable()`, `EquatableView`, an
    `Equatable` conformance) skips the redraw the memberwise `==` exists to trigger."""
    row = _code(_ROW)
    assert "ReportCard(" in row and "struct SelectableReportRow: View" in row, "not the report row"
    section, card = _code(_SECTION), _code(_CARD)
    assert "struct ReportCard: View" in card, "not the report card"
    files = {_SECTION.name: section, _ROW.name: row, _CARD.name: card}

    hits = [name for name, src in files.items() if ".equatable()" in src]
    assert not hits, (
        f"`.equatable()` in the report row chain ({hits}) — rows would compare with a custom `==` "
        "and freeze progress/status updates")
    hits = [name for name, src in files.items() if "EquatableView" in src]
    assert not hits, f"`EquatableView` in the report row chain ({hits}) — rows would freeze"

    for name, src in files.items():
        for m in re.finditer(r"\bstruct\s+(ReportsListSection|SelectableReportRow|ReportCard)\s*:([^{]*)\{", src):
            assert not re.search(r"\bEquatable\b", m.group(2)), (
                f"{m.group(1)} conforms to Equatable ({name}) — SwiftUI would diff it with its `==` "
                "instead of the report's memberwise one")

    ext_hits = {}
    for f in _swift_tree():
        src = _strip_swift_comments(f.read_text(encoding="utf-8"))
        n = len(re.findall(r"\bextension\s+(?:ReportsListSection|SelectableReportRow|ReportCard)\b[^{]*\bEquatable\b", src))
        if n:
            ext_hits[str(f.relative_to(_IOS.parent))] = n
    assert not ext_hits, (
        f"an extension makes a report row view Equatable in {ext_hits} — it would freeze rows")

    # The named scan above misses a WRAPPER: `ReportCardCell: View, Equatable` with an id-only
    # `==`, inserted between the row and the card, is the same freeze under a new name.
    any_name = []
    for name, src in files.items():
        for m in re.finditer(r"\b(?:struct|extension)\s+(\w+)\s*:([^{]*)\{", src):
            if re.search(r"\bEquatable\b", m.group(2)):
                any_name.append((name, m.group(1)))
        if re.search(r"\bstatic\s+func\s*==", src):
            any_name.append((name, "static func =="))
    assert not any_name, (
        f"a type in the report row chain files conforms to Equatable or declares `==` ({any_name}) — "
        "under any name, a view diffed with a custom `==` freezes progress/status updates")


def test_the_list_keys_rows_on_the_model_id_and_stays_lazy():
    section = _code(_SECTION)
    assert "private var list: some View" in section, "ReportsListSection.list is gone"
    body = _block(section, "private var list: some View")
    assert "SelectableReportRow(" in body, "not the Reports list"
    assert "LazyVStack(" in body, (
        "the Reports list must stay lazy (LazyVStack) — up to 50 rows with logos, and "
        "test_ios_fixed_section_layout_guards requires data lists to stay lazy")
    lazy = _block(body, "LazyVStack(")
    assert "ForEach(group.reports) { report in" in lazy, (
        "rows must key on AnalysisReport.id: `ForEach(group.reports) { report in` inside the "
        "LazyVStack — a ticker/other key collides (two AVGO rows) or rebuilds rows")
    assert "UUID" not in body, "the Reports list mints its own ids — every poll would rebuild every card"
    assert ".id(" not in body, (
        "a `.id(...)` modifier in the Reports list overrides the model id — rows must keep "
        "AnalysisReport.id as their only identity")


def test_report_sections_keep_a_stable_identity():
    """One level above the rows: the outer `ForEach(sections)` keys each time band on
    `ReportSectionGroup.id`, and `ResearchViewModel.groupedReports` builds new groups on every
    body evaluation. A minted (`UUID()`) or row-derived section id therefore gives every band a
    new identity on each render and tears down every card and logo in it — the same blink."""
    models = _code(_MODELS)
    assert "enum ReportTimeSection: String, CaseIterable" in models, "ReportTimeSection is gone"
    grp = _block(models, "struct ReportSectionGroup: Identifiable")
    assert "let section: ReportTimeSection" in grp and "let reports: [AnalysisReport]" in grp, (
        f"not the Reports section group: {grp[:200]}")
    assert "UUID" not in grp, (
        "ReportSectionGroup mints a UUID — groupedReports rebuilds every group on every render, so "
        "every band (and every card and logo in it) is torn down and re-inserted on each 5 s poll")
    assert (re.search(r"\bvar\s+id\s*:\s*ReportTimeSection\s*\{\s*section\s*\}", grp)
            and len(re.findall(r"\b(?:var|let)\s+id\b", grp)) == 1), (
        "ReportSectionGroup.id must be exactly `var id: ReportTimeSection { section }` — the time "
        "band, never a value derived from its rows (their count and content change on every poll)")

    lazy = _block(_block(_code(_SECTION), "private var list: some View"), "LazyVStack(")
    assert lazy.count("ForEach(") == 2 and "ForEach(sections) { group in" in lazy, (
        "the outer ForEach must key sections on ReportSectionGroup.id: exactly "
        "`ForEach(sections) { group in` (no `id:` override, no enumerated/offset key)")


_ID_MODIFIER = re.compile(r"\.id\s*\(")


def test_nothing_resets_the_rows_identity_from_above():
    """`.id(_:)` REPLACES a view's identity. On any view from the Research tab's mount down to the
    card, a value that changes (a UUID, a count, a status) tears down every card below it and
    restarts every logo on its initials tile — the blink, re-introduced by an ancestor. Today
    there is none anywhere on that path."""
    chain = {p.name: _code(p) for p in (_SECTION, _ROW, _CARD)}
    hits = [name for name, src in chain.items() if _ID_MODIFIER.search(src)]
    assert not hits, (
        f"an `.id(...)` modifier in the report view chain ({hits}) — it overrides the identity of "
        "the list, a row, a card or its logo and resets them whenever its value changes")

    content = _code(_CONTENT)
    screen = _block(content, "struct ResearchViewWithBinding: View")
    assert "ReportsListSection(" in screen, "not the Research screen"
    assert not _ID_MODIFIER.search(screen), (
        "ResearchViewWithBinding applies an `.id(...)` — on the Reports list (or any ancestor of it) "
        "that tears down every card whenever its value changes")

    mounts = list(re.finditer(r"\bResearchViewWithBinding\(\s*selectedTab:", content))
    assert len(mounts) == 1 and "TrackingViewWithBinding(" in content[mounts[0].end():], (
        "cannot find the single Research tab mount in ContentView's tab ZStack")
    start = mounts[0].start()
    mount = content[start: content.index("TrackingViewWithBinding(", start)]
    assert ".opacity(selectedTab == .research ? 1 : 0)" in mount, f"not the Research tab mount: {mount[:200]}"
    assert not _ID_MODIFIER.search(mount), (
        "the Research tab mount carries an `.id(...)` — it re-creates the whole screen (ViewModel "
        "included) whenever that value changes, wiping and re-inserting every card")


def test_the_research_screen_owns_its_view_model():
    """Re-creating the ViewModel needs no `.id(_:)`: an `@ObservedObject` initialised in `init`
    is rebuilt on every ContentView body evaluation, so the list empties and refills."""
    screen = _block(_code(_CONTENT), "struct ResearchViewWithBinding: View")
    assert (screen.count("@StateObject private var viewModel: ResearchViewModel") == 1
            and "StateObject(wrappedValue: ResearchViewModel(" in screen
            and "@ObservedObject" not in screen), (
        "ResearchViewWithBinding must OWN its ViewModel as `@StateObject private var viewModel: "
        "ResearchViewModel` — an observed one is re-created with its parent, and the list empties "
        "and refills")


def test_report_card_draws_its_logo_through_company_logo_view():
    """The blink lived in this leaf: the logo is a card's only stateful view. `CompanyLogoView`
    reads the process-wide logo cache, so even a card that IS re-inserted paints its logo at
    once; an `AsyncImage` restarts on its placeholder every time its card is rebuilt."""
    card = _block(_code(_CARD), "struct ReportCard: View")
    assert "Text(report.companyName)" in card and "ReportStatusBadge(" in card, "not the report card"
    assert (card.count("CompanyLogoView(") == 1
            and re.search(r"\bCompanyLogoView\(\s*ticker:\s*report\.ticker\s*,", card)), (
        "ReportCard must draw its logo through exactly one `CompanyLogoView(ticker: report.ticker, …)` "
        "— the cached logo view, keyed by the ticker")
    hits = [p.name for p in (_SECTION, _ROW, _CARD) if re.search(r"\bAsyncImage\b", _code(p))]
    assert not hits, (
        f"AsyncImage in the report view chain ({hits}) — it re-fetches and shows its placeholder "
        "whenever a card is rebuilt; draw logos through CompanyLogoView")


# ── 3. No full-screen dim on pull-to-refresh ─────────────────────────────────


def test_research_screen_has_no_loading_overlay():
    content = _code(_CONTENT)
    assert "struct ResearchViewWithBinding: View" in content, "ResearchViewWithBinding is gone"
    body = _block(content, "struct ResearchViewWithBinding: View")
    assert "ReportsListSection(" in body and body.count(".refreshable") == 2, (
        "not the Research screen (expected the Reports list and both pull-to-refresh modifiers)")
    assert "LoadingOverlay" not in body, (
        "Research shows a full-screen LoadingOverlay again — a dark scrim plus a second spinner "
        "over the tab on every pull-to-refresh (the TestFlight 'whole screen blinks')")
    assert not re.search(r"\bviewModel\.isLoading\b", body), (
        "ResearchViewWithBinding reads viewModel.isLoading again — the system refresh control "
        "is the only refresh indicator")


def test_refresh_raises_no_loading_flag():
    vm = _code(_VM)
    assert "func refresh() async" in vm, "ResearchViewModel.refresh() is gone"
    body = _block(vm, "func refresh() async")
    assert "await loadBackendData()" in body, "not the pull-to-refresh entry point"
    assert not re.search(r"\bisLoading\b", body), (
        "refresh() raises a loading flag again — that flag drove the full-screen scrim")
    guard = re.search(r"guard\s+!isDeletingReports\s+else\s*\{\s*return\s*\}", body)
    assert guard and guard.start() < body.index("await loadBackendData()"), (
        "refresh() lost its isDeletingReports guard (before the load) — a refresh would race "
        "the delete fan-out and resurrect rows being deleted")
    assert not re.search(r"\bvar\s+isLoading\b", vm), (
        "ResearchViewModel declares isLoading again — its only reader was the full-screen "
        "overlay that was removed")
    # By SHAPE, not by name: a flag called anything else (`isRefreshing`) drives a scrim as well.
    assert re.fullmatch(
        r"\{\s*guard\s+!isDeletingReports\s+else\s*\{\s*return\s*\}\s*await\s+loadBackendData\(\)\s*\}",
        body), (
        "refresh() must be exactly the delete guard plus `await loadBackendData()` — a flag it raises "
        "under any name can drive a full-screen scrim again")


def test_research_screen_has_no_full_screen_layer_under_any_name():
    """The scrim came back in review under a new name (`isRefreshing` + an `.overlay` dimming the
    tab) with every name-based check above green. So the screen is pinned by its SHAPE: the body's
    ZStack holds only the background and the content VStack, the only overlay is the floating
    selection bar, and both pull-to-refresh closures do nothing but call `refresh()`."""
    screen = _block(_code(_CONTENT), "struct ResearchViewWithBinding: View")
    zstack = _block(_block(screen, "var body: some View"), "ZStack(alignment: .bottom)")
    head = re.match(r"\{\s*AppColors\.background\s*\.ignoresSafeArea\(\)\s*VStack\(spacing:\s*0\)\s*\{", zstack)
    assert head, (
        "ResearchViewWithBinding's ZStack must open with `AppColors.background.ignoresSafeArea()` and "
        "then the content `VStack(spacing: 0)` — nothing layered in between")
    content_stack = _balanced(zstack, head.end() - 1)
    assert re.fullmatch(r"\s*\}", zstack[head.end() - 1 + len(content_stack):]), (
        "ResearchViewWithBinding's ZStack must hold ONLY the background and the content VStack, with "
        "no modifier on that VStack — a third layer (a scrim, a spinner) covers the whole tab")

    overlays = [m.start() for m in re.finditer(r"\.overlay\b", screen)]
    assert len(overlays) == 1 and re.match(
        r"\.overlay\(alignment:\s*\.bottom\)\s*\{\s*if\s+viewModel\.isSelectingReports\s*\{\s*ReportsSelectionBar\(",
        screen[overlays[0]:]), (
        f"ResearchViewWithBinding has an `.overlay` other than the floating selection bar "
        f"({len(overlays)} found) — a full-screen dim would sit there under any flag name")

    closures = [_balanced(screen, m.end()) for m in re.finditer(r"\.refreshable\b", screen)]
    assert len(closures) == 2 and all(
        re.fullmatch(r"\{\s*await\s+viewModel\.refresh\(\)\s*\}", c) for c in closures), (
        "both `.refreshable` closures must be exactly `{ await viewModel.refresh() }` — the system "
        f"refresh control is the only indicator, so they raise no flag of their own: {closures}")


# ── 4. Stale list answers are dropped before they touch anything ─────────────

_SEQ_GUARD = re.compile(r"guard\s+seq\s*>\s*reportsAppliedSeq\s+else\s*\{([^{}]*)\}")
_REQUEST_THEN_GUARD = re.compile(
    r"try\s+await\s+apiClient\.request\(\s*endpoint:\s*\.getMyReports\(limit:\s*\d+\)\s*,\s*"
    r"responseType:\s*\[BackendReportListItem\]\.self\s*\)\s*guard\s+seq\s*>\s*reportsAppliedSeq\s+else\s*\{")
_CANCEL_THEN_GUARD = re.compile(
    r"guard\s+!appError\.isCancellation\s+else\s*\{\s*return\s*\}\s*"
    r"guard\s+seq\s*>\s*reportsAppliedSeq\s+else\s*\{")


def _load_reports_parts() -> tuple[str, str, str]:
    """(prefix before `do`, the do-block, the catch-block) of `loadReports()`."""
    vm = _code(_VM)
    assert "func loadReports() async" in vm, "ResearchViewModel.loadReports() is gone"
    body = _block(vm, "func loadReports() async")
    assert ".getMyReports(limit:" in body and "AnalysisReport.from(" in body, "not the list load"
    dos = list(re.finditer(r"\bdo\s*\{", body))
    catches = list(re.finditer(r"\}\s*catch\s*\{", body))
    assert len(dos) == 1 and len(catches) == 1, (
        f"loadReports() must have exactly one do/catch, found {len(dos)}/{len(catches)}")
    return body[:dos[0].start()], _balanced(body, dos[0].start()), _balanced(body, catches[0].start() + 1)


def _assert_seq_guard_logs_and_returns(block: str, where: str) -> None:
    guards = _SEQ_GUARD.findall(block)
    assert len(guards) == 1, (
        f"{where}: a superseded list outcome must be dropped by exactly one "
        f"`guard seq > reportsAppliedSeq else {{ … }}` (strict >), found {len(guards)}")
    guard_body = guards[0].strip()
    assert re.search(r'\bprint\("[^"\n]*superseded', guard_body), (
        f"{where}: the superseded outcome must be logged in the guard (never dropped silently)")
    assert re.search(r"\breturn\s*$", guard_body), (
        f"{where}: the superseded-outcome guard must end in `return`")


def test_a_stale_list_response_is_dropped_before_it_drains():
    prefix, do, catch = _load_reports_parts()

    # Every request gets its own number BEFORE it is sent.
    bump = re.search(r"\breportsRequestSeq\s*&?\+=\s*1\b", prefix)
    assert bump, "loadReports() must bump reportsRequestSeq before the request (outside the do)"
    capture = re.search(r"\blet\s+seq\s*=\s*reportsRequestSeq\b", prefix)
    assert capture and bump.start() < capture.start(), (
        "loadReports() must capture `let seq = reportsRequestSeq` AFTER the bump and BEFORE the request")

    # do: the guard is the first thing after the answer arrives, then the ordered writes.
    _assert_seq_guard_logs_and_returns(do, "loadReports' do-block")
    assert _REQUEST_THEN_GUARD.search(do), (
        "the stale-answer guard must be the FIRST statement after the list request — anything "
        "before it runs for a superseded answer too")
    applied = list(re.finditer(r"\breportsAppliedSeq\s*=(?!=)\s*seq\b", do))
    assert len(applied) == 1, (
        f"the do-block must record the applied seq exactly once (`reportsAppliedSeq = seq`), "
        f"found {len(applied)} — without it no stale answer is ever dropped")
    order = [
        ("the stale-answer guard", _SEQ_GUARD.search(do).start()),
        ("`reportsAppliedSeq = seq`", applied[0].start()),
        ("the formIntersection drain", do.find("locallyTimedOutReportIds.formIntersection(")),
        ("releaseFinishedSlots(against: backendReports)", do.find("releaseFinishedSlots(against: backendReports)")),
        ("`self.reports = backendReports`", do.find("self.reports = backendReports")),
    ]
    for name, pos in order:
        assert pos >= 0, f"loadReports' do-block lost {name}"
    for (a, pa), (b, pb) in zip(order, order[1:]):
        assert pa < pb, f"in loadReports' do-block, {a} must come before {b}"

    # catch: the guard directly follows the cancellation guard, before analytics and the alert.
    _assert_seq_guard_logs_and_returns(catch, "loadReports' catch")
    assert _CANCEL_THEN_GUARD.search(catch), (
        "the catch's stale-outcome guard must DIRECTLY follow the cancellation guard — nothing "
        "may clear, gate or alert for a superseded failure")
    guard_at = _SEQ_GUARD.search(catch).start()
    analytics = catch.find("Analytics.shared.track(.backgroundSyncFailed")
    alert = catch.find("self.error = appError.message")
    assert 0 <= analytics and 0 <= alert, "the catch lost its sync-failed analytics or its alert"
    assert guard_at < analytics < alert, (
        "in loadReports' catch, the stale-outcome guard must come before the sync-failed "
        "analytics, and the analytics before the alert")


def test_an_identity_change_invalidates_in_flight_list_responses():
    vm = _code(_VM)
    header = "func handleIdentityChange(isActiveTab: Bool) async"
    assert header in vm, "ResearchViewModel.handleIdentityChange is gone"
    body = _block(vm, header)
    gate = re.search(r"guard\s+isActiveTab\s+else\s*\{\s*return\s*\}", body)
    assert gate, "handleIdentityChange lost its `guard isActiveTab else { return }`"
    prefix = body[:gate.start()]
    assert "reports = []" in prefix and "stopReportsPolling()" in prefix, (
        "not the identity-change clear (expected `reports = []` and `stopReportsPolling()` before the gate)")
    marks = list(re.finditer(r"\breportsAppliedSeq\s*=(?!=)\s*reportsRequestSeq\b", body))
    assert marks, (
        "handleIdentityChange must mark every in-flight list answer superseded "
        "(`reportsAppliedSeq = reportsRequestSeq`) — else the previous account's late answer "
        "repopulates the list (auth.md §7)")
    assert marks[0].start() < gate.start(), (
        "`reportsAppliedSeq = reportsRequestSeq` must run BEFORE `guard isActiveTab` — a hidden "
        "tab's ViewModel must drop the previous account's late answers too")
    before = body[1:marks[0].start()]
    assert (before.count("{") == before.count("}")
            and not re.search(r"\b(?:return|throw|guard|defer)\b", before)), (
        "`reportsAppliedSeq = reportsRequestSeq` must run UNCONDITIONALLY — at the top level of "
        "handleIdentityChange with no branch, closure or early exit before it; a conditional mark "
        "lets the previous account's late answers land")
    # Balanced braces admit `if … { await … }`: during that suspension the previous account's
    # in-flight answer still outranks reportsAppliedSeq and lands in the list just cleared.
    assert not re.search(r"\bawait\b", before), (
        "handleIdentityChange suspends before `reportsAppliedSeq = reportsRequestSeq` — during that "
        "await the previous account's in-flight list answer lands in the cleared list (auth.md §7)")


_DO_HEAD = re.compile(
    r"\{\s*let\s+backendReports\s*:\s*\[BackendReportListItem\]\s*=\s*try\s+await\s+apiClient\.request\(\s*"
    r"endpoint:\s*\.getMyReports\(limit:\s*\d+\)\s*,\s*responseType:\s*\[BackendReportListItem\]\.self\s*\)\s*"
    r"guard\s+seq\s*>\s*reportsAppliedSeq\s+else\s*\{")
_CATCH_HEAD = re.compile(
    r"\{\s*let\s+appError\s*=\s*AppError\.from\(error\)\s*"
    r"guard\s+!appError\.isCancellation\s+else\s*\{\s*return\s*\}\s*"
    r"guard\s+seq\s*>\s*reportsAppliedSeq\s+else\s*\{")
_LOG_AND_RETURN = re.compile(r'\s*print\("[^"\n]*"\)\s*return\s*')


def test_nothing_touches_the_list_before_the_outcome_is_known_to_be_newest():
    """The guards above say WHAT exists and in which ORDER; this pins that nothing else slips in
    around them. A clear or a flag before the request runs for the whole round trip of every
    poll; a statement in a guard's else, or ahead of the catch's guards, runs for every stale or
    cancelled outcome — each one empties or dims the list and refills it: the blink."""
    prefix, do, catch = _load_reports_parts()
    assert re.fullmatch(
        r'\{\s*reportsRequestSeq\s*&\+=\s*1\s*let\s+seq\s*=\s*reportsRequestSeq\s*print\("[^"\n]*"\)\s*',
        prefix), (
        "loadReports() must do nothing before its request but number it and log — a clear or a "
        "loading flag there empties or dims the list for the whole round trip of every 5 s poll")
    assert _DO_HEAD.match(do), (
        "the list request must be the FIRST statement of loadReports' do-block, directly followed by "
        "the stale-answer guard — anything before it runs while the request is in flight")
    assert re.search(
        r"guard\s+seq\s*>\s*reportsAppliedSeq\s+else\s*\{[^{}]*\}\s*reportsAppliedSeq\s*=(?!=)\s*seq\b", do), (
        "`reportsAppliedSeq = seq` must be the statement DIRECTLY after the do-block's stale-answer "
        "guard — inside the guard's else (or behind a branch) a success never advances the applied seq")
    do_guard = _SEQ_GUARD.search(do)
    assert do_guard and _LOG_AND_RETURN.fullmatch(do_guard.group(1)), (
        "loadReports' do-block: the superseded-answer guard may only log and return — any other "
        "statement in it runs for every stale answer")

    assert _CATCH_HEAD.match(catch), (
        "loadReports' catch must open with `let appError = AppError.from(error)`, the cancellation "
        "guard, then the stale-outcome guard — a statement before them runs for every cancelled "
        "tick and every superseded failure")
    catch_guard = _SEQ_GUARD.search(catch)
    assert catch_guard and _LOG_AND_RETURN.fullmatch(catch_guard.group(1)), (
        "loadReports' catch: the superseded-outcome guard may only log and return — any other "
        "statement in it runs for every superseded failure")


def test_nothing_suspends_between_the_stale_guard_and_the_assignment():
    """The guard says "newest so far" only at the instant it runs. An `await` between it and
    `self.reports = backendReports` (an async releaseFinishedSlots, an awaited loadCredits) lets
    answer N+1 pass its own guard and land; N then resumes and overwrites it — READY flips back
    to PROCESSING, the arrival-order flip the seq exists to stop — and an identity change landing
    in that await lets the previous account's list in (auth.md §7)."""
    _, do, _ = _load_reports_parts()
    guard = _SEQ_GUARD.search(do)
    assert guard and do.count("self.reports = backendReports") == 1, (
        "loadReports' do-block must assign the list exactly once (`self.reports = backendReports`) "
        "after its stale-answer guard — a second assignment escapes the no-suspension window below")
    window = do[guard.end(): do.index("self.reports = backendReports")]
    assert not re.search(r"\bawait\b", window), (
        "loadReports' do-block suspends between the stale-answer guard and `self.reports = "
        "backendReports` — a newer list answer (or an identity change) lands during that await, "
        "and this older list then overwrites it")


# Every role each counter may play; any other mention (a reset, an inout, a tuple write) fails.
_END = r"(?=[ \t]*(?:\n|;|\}|$))"
_COUNTER_ROLES = {
    "reportsRequestSeq": (
        r"\bprivate\s+var\s+reportsRequestSeq\s*=\s*0" + _END,
        r"\breportsRequestSeq\s*&\+=\s*1" + _END,
        r"\blet\s+seq\s*=\s*reportsRequestSeq" + _END,
        r"\breportsAppliedSeq\s*=\s*reportsRequestSeq" + _END,
    ),
    "reportsAppliedSeq": (
        r"\bprivate\s+var\s+reportsAppliedSeq\s*=\s*0" + _END,
        r"\bguard\s+seq\s*>\s*reportsAppliedSeq\s+else\b",
        r"\\\(reportsAppliedSeq\)",
        r"\breportsAppliedSeq\s*=\s*(?:seq|reportsRequestSeq)" + _END,
    ),
}
_BUMP = re.compile(r"\breportsRequestSeq\s*&\+=\s*1" + _END)
_IDENTITY_MARK_RE = re.compile(r"\breportsAppliedSeq\s*=\s*reportsRequestSeq" + _END)
_APPLIED_MARK = re.compile(r"\breportsAppliedSeq\s*=\s*seq" + _END)


def test_the_seq_counters_are_written_only_in_their_known_roles():
    """The ordering above holds only if nothing else moves the counters. Resetting them (e.g.
    `= 0` after the identity gate) re-admits the previous account's in-flight answers, whose
    numbers are now 'newer' — auth.md §7 — and lands stale lists in arrival order again."""
    vm = _code(_VM)
    for name, roles in _COUNTER_ROLES.items():
        spans = [m.span() for pattern in roles for m in re.finditer(pattern, vm)]
        unknown = [vm[max(0, m.start() - 40): m.end() + 30].strip()
                   for m in re.finditer(rf"\b{name}\b", vm)
                   if not any(a <= m.start() and m.end() <= b for a, b in spans)]
        assert not unknown, (
            f"`{name}` is used outside its known roles (declare / bump / capture / guard / log / "
            f"applied mark / identity mark): {unknown}")

    load = _block(vm, "func loadReports() async")
    identity = _block(vm, "func handleIdentityChange(isActiveTab: Bool) async")
    assert len(_BUMP.findall(vm)) == 1 and len(_BUMP.findall(load)) == 1, (
        "reportsRequestSeq must be bumped exactly once, in loadReports() — a bump anywhere else "
        "numbers no request and skews which answer counts as newest")
    assert len(_IDENTITY_MARK_RE.findall(vm)) == 1 and len(_IDENTITY_MARK_RE.findall(identity)) == 1, (
        "`reportsAppliedSeq = reportsRequestSeq` (drop every in-flight answer) must appear exactly "
        "once, in handleIdentityChange")
    assert len(_APPLIED_MARK.findall(vm)) == len(_APPLIED_MARK.findall(load)) >= 1, (
        "`reportsAppliedSeq = seq` must only appear inside loadReports() — `seq` is that request's "
        "own number; anywhere else it marks an unrelated value as applied")


_LIST_CLEAR = re.compile(
    r"\breports\s*(?:=\s*\[\s*\]|=\s*\[\s*AnalysisReport\s*\]\(\s*\)|=\s*\.init\(\s*\)"
    r"|\.removeAll\(\s*(?:keepingCapacity\s*:\s*\w+\s*)?\))")


def test_the_list_is_emptied_only_on_an_identity_change_or_a_refusal():
    """An empty `reports` renders the empty state; a clear followed by a refill is a blink. The
    list may be emptied only by `handleIdentityChange` (the previous account's rows must go) and,
    after the stale-outcome guard, by loadReports' catch (the refusal arm owned by
    `test_ios_reports_gate_from_refusal.py`) — never by a load, a poll tick or a refresh."""
    vm = _code(_VM)
    identity = _block(vm, "func handleIdentityChange(isActiveTab: Bool) async")
    _, _, catch = _load_reports_parts()
    guard = _SEQ_GUARD.search(catch)
    assert guard, "loadReports' catch lost its stale-outcome guard"
    in_identity = len(_LIST_CLEAR.findall(identity))
    in_refusal = len(_LIST_CLEAR.findall(catch[guard.end():]))
    total = len(_LIST_CLEAR.findall(vm))
    assert in_identity == 1 and total == in_identity + in_refusal, (
        f"the Reports list is emptied outside handleIdentityChange and the catch's refusal arm "
        f"({total} clears: {in_identity} on identity change, {in_refusal} after the catch's stale "
        "guard) — a clear on a load, poll tick or refresh shows the empty state and refills it")
    # "After the stale guard" is wider than the refusal arm: it also holds the real-failure path
    # (analytics, the alert, the network-blip `else`). A clear there empties the list on every
    # failed poll tick on mobile data and the next tick refills it — the blink. Bound the count
    # to the typed-refusal arm itself.
    arm = _block(catch, "if case .signInRequired = appError")
    in_arm = len(_LIST_CLEAR.findall(arm))
    assert in_arm <= 1 and total == in_identity + in_arm, (
        f"loadReports' catch empties the list outside its typed-refusal arm ({total} clears: "
        f"{in_identity} on identity change, {in_arm} inside `if case .signInRequired = appError`) — "
        "a real failure (a mobile-data blip on a poll tick) must keep the rows on screen")


# ── 5. The mutations above, re-run in memory on every pass ──────────────────

_ID = 'var id: String { backendId ?? "mock:\\(ticker):\\(persona.key)" }'
_SYNTH = "    // Equatable / Hashable are SYNTHESIZED"
_ID_EQ = "static func == (lhs: AnalysisReport, rhs: AnalysisReport) -> Bool { lhs.id == rhs.id }"
_DO_GUARD = (
    "            guard seq > reportsAppliedSeq else {\n"
    '                print("ℹ️ ResearchVM: superseded list answer dropped (seq \\(seq) ≤ \\(reportsAppliedSeq))")\n'
    "                return\n"
    "            }\n"
)
_CATCH_PRINT = (
    '                print("ℹ️ ResearchVM: superseded list outcome dropped (seq \\(seq) ≤ '
    '\\(reportsAppliedSeq), \\(appError.analyticsCode))")\n'
)
_CATCH_GUARD = "            guard seq > reportsAppliedSeq else {\n" + _CATCH_PRINT + "                return\n            }\n"
_CANCEL = "            guard !appError.isCancellation else { return }\n"
_REQUEST_END = "                responseType: [BackendReportListItem].self\n            )\n"
_DRAIN = "            locallyTimedOutReportIds.formIntersection(Set(backendReports.map(\\.id)))\n"
_LIVE = "            applyLiveProgress()   // keep the in-flight row at the live stream %\n"
_REFRESH = "        guard !isDeletingReports else { return }   // don't race the delete fan-out\n        await loadBackendData()\n"
_IDENTITY_MARK = "        reportsAppliedSeq = reportsRequestSeq\n"
_ACTIVE_GATE = "        guard isActiveTab else { return }\n"
_GROUP_ID = "    var id: ReportTimeSection { section }\n"
_LOGO = "CompanyLogoView(ticker: report.ticker, size: 36)"
_BUMP_LINE = "        reportsRequestSeq &+= 1\n"
_REQUEST_START = "            let backendReports: [BackendReportListItem] = try await apiClient.request(\n"
_APP_ERROR = "            let appError = AppError.from(error)\n            // A cancelled tick"
_CATCH_TOP = "            // NEVER fabricate. This used to fall back"
_REFRESH_GUARD = "        guard !isDeletingReports else { return }   // don't race the delete fan-out\n"
_FIRST_REFRESHABLE = (
    "        .refreshable {\n            await viewModel.refresh()\n        }\n    }\n\n"
    "    // MARK: - Reports Tab Content")
_REAL_FAILURE_ALERT = (
    "            if reports.isEmpty {\n                self.error = appError.message\n            } else {\n")
_BLIP_ELSE = "            } else {\n                // Network blip with rows already on screen"
_RELEASE_THEN_ASSIGN = "            releaseFinishedSlots(against: backendReports)\n            self.reports"
_RELEASE_DECL = "    private func releaseFinishedSlots(against backendReports: [BackendReportListItem]) {"


def _in_refresh(statements: str):
    """Insert `statements` into refresh(), between its delete guard and its load."""
    return (_VM, ((_REFRESH, _REFRESH_GUARD + statements + "        await loadBackendData()\n"),))


def _m(path, old, new, test, message):
    return (path, ((old, new),), test, message)


# (file, ((anchor, replacement), …), guard, the assertion message the guard must fail WITH).
# The message is matched so a mutation cannot pass by tripping an unrelated, earlier assertion.
# A tuple of several edits expresses a MOVE (remove here, insert there).
_MUTATIONS = [
    # ── identity
    _m(_MODELS, _ID, "let id = UUID()",
       test_analysis_report_id_is_the_backend_id, "AnalysisReport mints a UUID again"),
    _m(_MODELS, _ID, "var id: UUID { UUID() }",
       test_analysis_report_id_is_the_backend_id, "AnalysisReport mints a UUID again"),
    _m(_MODELS, _ID, "let id: String",
       test_analysis_report_id_is_the_backend_id, "a stored `let id` is back on AnalysisReport"),
    _m(_MODELS, _ID, "var id: String { ticker }",
       test_analysis_report_id_is_the_backend_id, "AnalysisReport.id must be the computed"),
    _m(_MODELS, _ID, 'var id: String { "\\(ticker):\\(persona.key)" }',
       test_analysis_report_id_is_the_backend_id, "AnalysisReport.id must be the computed"),
    _m(_MODELS, _ID, 'var id: String { backendId ?? "mock" }',
       test_analysis_report_id_is_the_backend_id, "must interpolate BOTH"),
    _m(_MODELS, _ID, 'var id: String { backendId ?? "mock:\\(ticker)" }',
       test_analysis_report_id_is_the_backend_id, "must interpolate BOTH"),
    _m(_MODELS, "backendId: item.id,", "backendId: nil,",
       test_from_threads_the_server_id_and_the_timeout_flip_keeps_it,
       "AnalysisReport.from(_:) must thread the server row id"),
    _m(_MODELS, "backendId: backendId,", "backendId: nil,",
       test_from_threads_the_server_id_and_the_timeout_flip_keeps_it,
       "withClientTimeout() must pass backendId through"),
    _m(_MODELS, 'companyName: "Apple Inc.",\n            ticker: "AAPL",',
       'companyName: "Apple Inc.",\n            ticker: "ORCL",',
       test_mock_reports_have_unique_fallback_ids, "mock reports repeat a (ticker, persona) pair"),
    _m(_MODELS, '            backendId: nil,\n            companyName: "Tesla Inc.",',
       '            backendId: "mock-tsla",\n            companyName: "Tesla Inc.",',
       test_mock_reports_have_unique_fallback_ids, "every mock report must have `backendId: nil`"),
    # ── equality
    _m(_MODELS, _SYNTH, "    " + _ID_EQ + "\n" + _SYNTH,
       test_analysis_report_equality_is_synthesized, "a hand-written `==` is back on AnalysisReport"),
    _m(_MODELS, _SYNTH, "    func hash(into hasher: inout Hasher) { hasher.combine(id) }\n" + _SYNTH,
       test_analysis_report_equality_is_synthesized, "a hand-written hash(into:) is back on AnalysisReport"),
    _m(_MODELS, "extension AnalysisReport {\n",
       "extension AnalysisReport {\n    " + _ID_EQ + "\n}\n\nextension AnalysisReport {\n",
       test_no_hand_written_equality_for_analysis_report_anywhere,
       "an `extension AnalysisReport` declares `==`"),
    _m(_CARD, "struct ReportCard: View {",
       "extension AnalysisReport: Equatable {\n    static func == (a: Self, b: Self) -> Bool { a.id == b.id }\n}\n\n"
       "struct ReportCard: View {",
       test_no_hand_written_equality_for_analysis_report_anywhere,
       "an `extension AnalysisReport` declares `==`"),
    _m(_CARD, "struct ReportCard: View {",
       "extension AnalysisReport {\n    func hash(into hasher: inout Hasher) { hasher.combine(id) }\n}\n\n"
       "struct ReportCard: View {",
       test_no_hand_written_equality_for_analysis_report_anywhere,
       "an `extension AnalysisReport` declares hash(into:)"),
    _m(_VM, "@MainActor\nclass ResearchViewModel: ObservableObject {",
       "func == (lhs: AnalysisReport, rhs: AnalysisReport) -> Bool { lhs.id == rhs.id }\n\n"
       "@MainActor\nclass ResearchViewModel: ObservableObject {",
       test_no_hand_written_equality_for_analysis_report_anywhere,
       "a hand-written `==` taking AnalysisReport exists"),
    # ── row chain
    _m(_ROW, "                onRetry: isSelecting ? nil : onRetry\n            )\n",
       "                onRetry: isSelecting ? nil : onRetry\n            )\n            .equatable()\n",
       test_report_rows_are_not_equatable_short_circuited, "`.equatable()` in the report row chain"),
    _m(_SECTION, "onToggleSelect: { onToggleSelect?(report) }\n                    )\n",
       "onToggleSelect: { onToggleSelect?(report) }\n                    )\n                    .equatable()\n",
       test_report_rows_are_not_equatable_short_circuited, "`.equatable()` in the report row chain"),
    _m(_SECTION, "SelectableReportRow(", "EquatableView(content: SelectableReportRow(",
       test_report_rows_are_not_equatable_short_circuited, "`EquatableView` in the report row chain"),
    _m(_CARD, "struct ReportCard: View {", "struct ReportCard: View, Equatable {",
       test_report_rows_are_not_equatable_short_circuited, "ReportCard conforms to Equatable"),
    _m(_ROW, "struct SelectableReportRow: View {", "struct SelectableReportRow: View, Equatable {",
       test_report_rows_are_not_equatable_short_circuited, "SelectableReportRow conforms to Equatable"),
    _m(_CONTENT, "struct ResearchViewWithBinding: View {",
       "extension ReportCard: Equatable {\n    static func == (a: Self, b: Self) -> Bool { a.report.id == b.report.id }\n}\n\n"
       "struct ResearchViewWithBinding: View {",
       test_report_rows_are_not_equatable_short_circuited, "an extension makes a report row view Equatable"),
    # ── list identity / laziness
    _m(_SECTION, "ForEach(group.reports) { report in", "ForEach(group.reports, id: \\.ticker) { report in",
       test_the_list_keys_rows_on_the_model_id_and_stays_lazy, "rows must key on AnalysisReport.id"),
    _m(_SECTION, "LazyVStack(alignment: .leading, spacing: AppSpacing.md) {",
       "VStack(alignment: .leading, spacing: AppSpacing.md) {",
       test_the_list_keys_rows_on_the_model_id_and_stays_lazy, "the Reports list must stay lazy"),
    _m(_SECTION, "onToggleSelect: { onToggleSelect?(report) }\n                    )\n",
       "onToggleSelect: { onToggleSelect?(report) }\n                    )\n                    .id(UUID())\n",
       test_the_list_keys_rows_on_the_model_id_and_stays_lazy, "the Reports list mints its own ids"),
    _m(_SECTION, "onToggleSelect: { onToggleSelect?(report) }\n                    )\n",
       "onToggleSelect: { onToggleSelect?(report) }\n                    )\n                    .id(report.ticker)\n",
       test_the_list_keys_rows_on_the_model_id_and_stays_lazy, "a `.id(...)` modifier in the Reports list"),
    # ── no scrim
    _m(_CONTENT, "            // No full-screen LoadingOverlay here.",
       "            if viewModel.isLoading {\n                LoadingOverlay()\n            }\n"
       "            // No full-screen LoadingOverlay here.",
       test_research_screen_has_no_loading_overlay, "Research shows a full-screen LoadingOverlay again"),
    _m(_CONTENT, ".onDisappear { viewModel.stopReportsPolling() }",
       ".overlay { if viewModel.isLoading { ProgressView() } }\n        .onDisappear { viewModel.stopReportsPolling() }",
       test_research_screen_has_no_loading_overlay, "ResearchViewWithBinding reads viewModel.isLoading again"),
    _m(_VM, _REFRESH,
       "        guard !isDeletingReports else { return }   // don't race the delete fan-out\n"
       "        isLoading = true\n        await loadBackendData()\n        isLoading = false\n",
       test_refresh_raises_no_loading_flag, "refresh() raises a loading flag again"),
    _m(_VM, _REFRESH, "        await loadBackendData()\n",
       test_refresh_raises_no_loading_flag, "refresh() lost its isDeletingReports guard"),
    _m(_VM, "    @Published var analysisCost: AnalysisCost = .standard\n",
       "    @Published var analysisCost: AnalysisCost = .standard\n    @Published var isLoading: Bool = false\n",
       test_refresh_raises_no_loading_flag, "ResearchViewModel declares isLoading again"),
    # ── seq ordering: the request number
    _m(_VM, "        reportsRequestSeq &+= 1\n", "",
       test_a_stale_list_response_is_dropped_before_it_drains,
       "loadReports() must bump reportsRequestSeq before the request"),
    (_VM, (("        let seq = reportsRequestSeq\n", ""),
           (_REQUEST_END, _REQUEST_END + "            let seq = reportsRequestSeq\n")),
     test_a_stale_list_response_is_dropped_before_it_drains,
     "loadReports() must capture `let seq = reportsRequestSeq` AFTER the bump"),
    # ── seq ordering: the do-block
    _m(_VM, _DO_GUARD, "",
       test_a_stale_list_response_is_dropped_before_it_drains,
       "loadReports' do-block: a superseded list outcome must be dropped by exactly one"),
    _m(_VM, _DO_GUARD, _DO_GUARD.replace("seq > reportsAppliedSeq", "seq >= reportsAppliedSeq"),
       test_a_stale_list_response_is_dropped_before_it_drains,
       "loadReports' do-block: a superseded list outcome must be dropped by exactly one"),
    _m(_VM, _DO_GUARD, _DO_GUARD.replace("superseded list answer dropped", "dropping"),
       test_a_stale_list_response_is_dropped_before_it_drains,
       "loadReports' do-block: the superseded outcome must be logged"),
    _m(_VM, _REQUEST_END, _REQUEST_END + "            releaseFinishedSlots(against: backendReports)\n",
       test_a_stale_list_response_is_dropped_before_it_drains,
       "the stale-answer guard must be the FIRST statement after the list request"),
    _m(_VM, "            reportsAppliedSeq = seq\n            requiresSignInForReports = false\n",
       "            requiresSignInForReports = false\n",
       test_a_stale_list_response_is_dropped_before_it_drains,
       "the do-block must record the applied seq exactly once"),
    (_VM, ((_DRAIN, ""), (_LIVE, _LIVE + _DRAIN)),
     test_a_stale_list_response_is_dropped_before_it_drains,
     "the formIntersection drain must come before releaseFinishedSlots(against: backendReports)"),
    (_VM, (("            releaseFinishedSlots(against: backendReports)\n", ""),
           (_LIVE, _LIVE + "            releaseFinishedSlots(against: backendReports)\n")),
     test_a_stale_list_response_is_dropped_before_it_drains,
     "releaseFinishedSlots(against: backendReports) must come before `self.reports = backendReports`"),
    # ── seq ordering: the catch
    _m(_VM, _CATCH_GUARD, "",
       test_a_stale_list_response_is_dropped_before_it_drains,
       "loadReports' catch: a superseded list outcome must be dropped by exactly one"),
    _m(_VM, _CATCH_PRINT, "",
       test_a_stale_list_response_is_dropped_before_it_drains,
       "loadReports' catch: the superseded outcome must be logged"),
    _m(_VM, _CANCEL, _CANCEL + "            requiresSignInForReports = false\n",
       test_a_stale_list_response_is_dropped_before_it_drains,
       "the catch's stale-outcome guard must DIRECTLY follow the cancellation guard"),
    # ── identity change
    _m(_VM, _IDENTITY_MARK, "",
       test_an_identity_change_invalidates_in_flight_list_responses,
       "handleIdentityChange must mark every in-flight list answer superseded"),
    _m(_VM, _IDENTITY_MARK, "        reportsRequestSeq = reportsAppliedSeq\n",
       test_an_identity_change_invalidates_in_flight_list_responses,
       "handleIdentityChange must mark every in-flight list answer superseded"),
    (_VM, ((_IDENTITY_MARK, ""), (_ACTIVE_GATE, _ACTIVE_GATE + _IDENTITY_MARK)),
     test_an_identity_change_invalidates_in_flight_list_responses,
     "must run BEFORE `guard isActiveTab`"),

    # ══ Gaps an adversarial review proved (each survived every guard above) ══
    # ── A: section identity, one level above the rows
    _m(_MODELS, _GROUP_ID, "    let id = UUID()\n",
       test_report_sections_keep_a_stable_identity, "ReportSectionGroup mints a UUID"),
    _m(_MODELS, _GROUP_ID, '    var id: String { "\\(section.rawValue):\\(reports.count)" }\n',
       test_report_sections_keep_a_stable_identity, "ReportSectionGroup.id must be exactly"),
    _m(_SECTION, "ForEach(sections) { group in", "ForEach(sections, id: \\.reports) { group in",
       test_report_sections_keep_a_stable_identity, "the outer ForEach must key sections"),
    # ── D: an `.id(...)` on an ancestor (or inside the chain) resets every card
    _m(_SECTION, "            } else {\n                list\n            }\n",
       "            } else {\n                list.id(UUID())\n            }\n",
       test_nothing_resets_the_rows_identity_from_above, "an `.id(...)` modifier in the report view chain"),
    _m(_ROW, "                onRetry: isSelecting ? nil : onRetry\n            )\n",
       "                onRetry: isSelecting ? nil : onRetry\n            )\n            .id(report.status)\n",
       test_nothing_resets_the_rows_identity_from_above, "an `.id(...)` modifier in the report view chain"),
    _m(_CARD, _LOGO, _LOGO + "\n                            .id(report.progress)",
       test_nothing_resets_the_rows_identity_from_above, "an `.id(...)` modifier in the report view chain"),
    _m(_CONTENT, "                .padding(.top, AppSpacing.sm)\n",
       "                .id(viewModel.reports.count)\n                .padding(.top, AppSpacing.sm)\n",
       test_nothing_resets_the_rows_identity_from_above, "ResearchViewWithBinding applies an `.id(...)`"),
    _m(_CONTENT, "            .opacity(selectedTab == .research ? 1 : 0)\n",
       "            .id(researchHandoffSeq)\n            .opacity(selectedTab == .research ? 1 : 0)\n",
       test_nothing_resets_the_rows_identity_from_above, "the Research tab mount carries an `.id(...)`"),
    # ── the stateful leaf: the card's logo
    _m(_CARD, _LOGO,
       'AsyncImage(url: URL(string: "https://financialmodelingprep.com/image-stock/\\(report.ticker).png"))',
       test_report_card_draws_its_logo_through_company_logo_view, "ReportCard must draw its logo through"),
    _m(_CARD, _LOGO, "CompanyLogoView(ticker: report.companyName, size: 36)",
       test_report_card_draws_its_logo_through_company_logo_view, "ReportCard must draw its logo through"),
    _m(_CARD, _LOGO, _LOGO + "\n                        AsyncImage(url: nil)",
       test_report_card_draws_its_logo_through_company_logo_view, "AsyncImage in the report view chain"),
    # ── G: the scrim under another name
    (_VM, (("    @Published var analysisCost: AnalysisCost = .standard\n",
            "    @Published var analysisCost: AnalysisCost = .standard\n    @Published var isRefreshing = false\n"),
           (_REFRESH, _REFRESH_GUARD + "        isRefreshing = true\n        await loadBackendData()\n"
                      "        isRefreshing = false\n")),
     test_refresh_raises_no_loading_flag, "refresh() must be exactly the delete guard"),
    _m(_CONTENT, ".onDisappear { viewModel.stopReportsPolling() }",
       ".overlay { if viewModel.isRefreshing { Color.black.opacity(0.4).ignoresSafeArea() } }\n"
       "        .onDisappear { viewModel.stopReportsPolling() }",
       test_research_screen_has_no_full_screen_layer_under_any_name,
       "has an `.overlay` other than the floating selection bar"),
    _m(_CONTENT, "            // No full-screen LoadingOverlay here.",
       "            if viewModel.isRefreshing {\n                Color.black.opacity(0.4).ignoresSafeArea()\n"
       "            }\n            // No full-screen LoadingOverlay here.",
       test_research_screen_has_no_full_screen_layer_under_any_name,
       "ZStack must hold ONLY the background and the content VStack"),
    _m(_CONTENT, "            VStack(spacing: 0) {\n                // Header (pinned outside scroll)\n",
       "            if viewModel.isRefreshing { ProgressView() }\n"
       "            VStack(spacing: 0) {\n                // Header (pinned outside scroll)\n",
       test_research_screen_has_no_full_screen_layer_under_any_name, "ZStack must open with"),
    _m(_CONTENT, _FIRST_REFRESHABLE,
       _FIRST_REFRESHABLE.replace("            await viewModel.refresh()\n",
                                  "            isRefreshing = true\n            await viewModel.refresh()\n"
                                  "            isRefreshing = false\n"),
       test_research_screen_has_no_full_screen_layer_under_any_name,
       "both `.refreshable` closures must be exactly"),
    # ── B / E and the heads: nothing slips in around the stale-outcome guards
    _m(_VM, _BUMP_LINE, "        isLoadingReports = true\n" + _BUMP_LINE,
       test_nothing_touches_the_list_before_the_outcome_is_known_to_be_newest,
       "loadReports() must do nothing before its request but number it and log"),
    _m(_VM, _REQUEST_START, "            reports = []\n" + _REQUEST_START,
       test_nothing_touches_the_list_before_the_outcome_is_known_to_be_newest,
       "the list request must be the FIRST statement of loadReports' do-block"),
    _m(_VM, _DO_GUARD + "            reportsAppliedSeq = seq\n",
       _DO_GUARD.replace("                return\n", "                reportsAppliedSeq = seq\n                return\n"),
       test_nothing_touches_the_list_before_the_outcome_is_known_to_be_newest,
       "`reportsAppliedSeq = seq` must be the statement DIRECTLY after"),
    _m(_VM, _DO_GUARD, _DO_GUARD.replace("else {\n", "else {\n                reports = []\n", 1),
       test_nothing_touches_the_list_before_the_outcome_is_known_to_be_newest,
       "loadReports' do-block: the superseded-answer guard may only log and return"),
    _m(_VM, _APP_ERROR, _APP_ERROR.replace("\n", "\n            reports = []\n", 1),
       test_nothing_touches_the_list_before_the_outcome_is_known_to_be_newest,
       "loadReports' catch must open with"),
    _m(_VM, _CATCH_TOP, "            reports = []\n" + _CATCH_TOP,
       test_nothing_touches_the_list_before_the_outcome_is_known_to_be_newest,
       "loadReports' catch must open with"),
    _m(_VM, _CATCH_GUARD, _CATCH_GUARD.replace("else {\n", "else {\n                reports = []\n", 1),
       test_nothing_touches_the_list_before_the_outcome_is_known_to_be_newest,
       "loadReports' catch: the superseded-outcome guard may only log and return"),
    # ── C: the counters have no other writer
    _m(_VM, _ACTIVE_GATE, _ACTIVE_GATE + "        reportsRequestSeq = 0\n        reportsAppliedSeq = 0\n",
       test_the_seq_counters_are_written_only_in_their_known_roles,
       "`reportsRequestSeq` is used outside its known roles"),
    _m(_VM, _ACTIVE_GATE, _ACTIVE_GATE + "        swap(&reportsAppliedSeq, &reportsRequestSeq)\n",
       test_the_seq_counters_are_written_only_in_their_known_roles,
       "`reportsRequestSeq` is used outside its known roles"),
    _m(_VM, _ACTIVE_GATE, _ACTIVE_GATE + "        reportsAppliedSeq = 0\n",
       test_the_seq_counters_are_written_only_in_their_known_roles,
       "`reportsAppliedSeq` is used outside its known roles"),
    _m(_VM, _ACTIVE_GATE, _ACTIVE_GATE + "        (reportsAppliedSeq, error) = (0, nil)\n",
       test_the_seq_counters_are_written_only_in_their_known_roles,
       "`reportsAppliedSeq` is used outside its known roles"),
    (*_in_refresh("        reportsRequestSeq &+= 1\n"),
     test_the_seq_counters_are_written_only_in_their_known_roles,
     "reportsRequestSeq must be bumped exactly once, in loadReports()"),
    (*_in_refresh("        reportsAppliedSeq = reportsRequestSeq\n"),
     test_the_seq_counters_are_written_only_in_their_known_roles,
     "`reportsAppliedSeq = reportsRequestSeq` (drop every in-flight answer) must appear exactly once"),
    (*_in_refresh("        let seq = reportsRequestSeq\n        reportsAppliedSeq = seq\n"),
     test_the_seq_counters_are_written_only_in_their_known_roles,
     "`reportsAppliedSeq = seq` must only appear inside loadReports()"),
    # ── F: the identity mark cannot be made conditional
    _m(_VM, _IDENTITY_MARK, "        if isActiveTab { reportsAppliedSeq = reportsRequestSeq }\n",
       test_an_identity_change_invalidates_in_flight_list_responses, "must run UNCONDITIONALLY"),
    _m(_VM, "        identityEpoch &+= 1\n",
       "        identityEpoch &+= 1\n        guard identityEpoch > 1 else { return }\n",
       test_an_identity_change_invalidates_in_flight_list_responses, "must run UNCONDITIONALLY"),
    # ── the list is emptied only where it must be
    (*_in_refresh("        reports = []\n"),
     test_the_list_is_emptied_only_on_an_identity_change_or_a_refusal,
     "the Reports list is emptied outside handleIdentityChange"),
    (*_in_refresh("        reports.removeAll()\n"),
     test_the_list_is_emptied_only_on_an_identity_change_or_a_refusal,
     "the Reports list is emptied outside handleIdentityChange"),
    (*_in_refresh("        self.reports = .init()\n"),
     test_the_list_is_emptied_only_on_an_identity_change_or_a_refusal,
     "the Reports list is emptied outside handleIdentityChange"),
    _m(_VM, _APP_ERROR, _APP_ERROR.replace("\n", "\n            self.reports = []\n", 1),
       test_the_list_is_emptied_only_on_an_identity_change_or_a_refusal,
       "the Reports list is emptied outside handleIdentityChange"),

    # ══ Round 2 of the adversarial review (each survived every guard above) ══
    # ── a real failure (not the refusal arm) empties the list after the stale guard
    _m(_VM, _REAL_FAILURE_ALERT, "            reports = []\n" + _REAL_FAILURE_ALERT,
       test_the_list_is_emptied_only_on_an_identity_change_or_a_refusal,
       "loadReports' catch empties the list outside its typed-refusal arm"),
    _m(_VM, _BLIP_ELSE, _BLIP_ELSE.replace("else {\n", "else {\n                reports.removeAll()\n", 1),
       test_the_list_is_emptied_only_on_an_identity_change_or_a_refusal,
       "loadReports' catch empties the list outside its typed-refusal arm"),
    # ── a suspension between the do-block's stale guard and the assignment
    (_VM, ((_RELEASE_THEN_ASSIGN, "            await " + _RELEASE_THEN_ASSIGN.lstrip(" ")),
           (_RELEASE_DECL, _RELEASE_DECL.replace(") {", ") async {"))),
     test_nothing_suspends_between_the_stale_guard_and_the_assignment,
     "loadReports' do-block suspends between the stale-answer guard"),
    _m(_VM, _RELEASE_THEN_ASSIGN,
       _RELEASE_THEN_ASSIGN.replace(
           "            self.reports",
           "            if backendReports.contains(where: { $0.isRefunded == true }) { await loadCredits() }\n"
           "            self.reports"),
       test_nothing_suspends_between_the_stale_guard_and_the_assignment,
       "loadReports' do-block suspends between the stale-answer guard"),
    _m(_VM, _LIVE, _LIVE + "            await loadCredits()\n"
                           "            self.reports = backendReports.map { AnalysisReport.from($0) }\n",
       test_nothing_suspends_between_the_stale_guard_and_the_assignment,
       "loadReports' do-block must assign the list exactly once"),
    # ── the identity change's in-flight wait-out moved above the mark
    _m(_VM, "        identityEpoch &+= 1\n",
       "        identityEpoch &+= 1\n        if let running = loadTask, !running.isCancelled { await running.value }\n",
       test_an_identity_change_invalidates_in_flight_list_responses,
       "handleIdentityChange suspends before `reportsAppliedSeq = reportsRequestSeq`"),
    # ── an Equatable wrapper (any name) between the row and the card
    (_ROW, (("            ReportCard(\n                report: report,",
             "            ReportCardCell(\n                report: report,"),
            ("#Preview {",
             "struct ReportCardCell: View, Equatable {\n    let report: AnalysisReport\n"
             "    var onTap: (() -> Void)?\n    var onRetry: (() -> Void)?\n"
             "    static func == (lhs: Self, rhs: Self) -> Bool { lhs.report.id == rhs.report.id }\n"
             "    var body: some View { ReportCard(report: report, onTap: onTap, onRetry: onRetry) }\n}\n\n"
             "#Preview {")),
     test_report_rows_are_not_equatable_short_circuited,
     "a type in the report row chain files conforms to Equatable or declares `==`"),
    # ── the screen observes (re-creates) its ViewModel instead of owning it
    (_CONTENT, (("    @StateObject private var viewModel: ResearchViewModel\n",
                 "    @ObservedObject private var viewModel: ResearchViewModel\n"),
                ("        self._viewModel = StateObject(wrappedValue: ResearchViewModel(prefilledTicker: prefilledTicker))\n",
                 "        self.viewModel = ResearchViewModel(prefilledTicker: prefilledTicker)\n")),
     test_the_research_screen_owns_its_view_model,
     "ResearchViewWithBinding must OWN its ViewModel"),
]


@pytest.mark.parametrize(
    "path,edits,test,message",
    _MUTATIONS,
    ids=[f"{p.name}:{i}" for i, (p, *_rest) in enumerate(_MUTATIONS)],
)
def test_each_mutation_is_killed(monkeypatch, path, edits, test, message):
    """Each guard above must go red on the regression it names, WITH the message that names
    it. Patched in memory only — other sessions' tests read these Swift files concurrently,
    so they are never rewritten."""
    real_read_text = pathlib.Path.read_text
    original = real_read_text(path, encoding="utf-8")
    mutated = original
    for old, new in edits:
        assert original.count(old) == 1 and mutated.count(old) == 1, (
            f"mutation anchor `{old[:60]}` occurs {original.count(old)}× in {path.name} (need "
            "exactly one) — re-derive this mutation against the new source rather than deleting it")
        mutated = mutated.replace(old, new, 1)
    assert mutated != original

    def fake_read_text(self, *args, **kwargs):
        if pathlib.Path(self) == path:
            return mutated
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", fake_read_text)
    # The unmutated source passes (the plain tests above prove it); mutated, it must fail.
    with pytest.raises(AssertionError, match=re.escape(message)):
        test()
