"""The Research tab keeps its Reports segment across a cover, and the "AI Deep Research"
handoff always lands on the Research segment with the requested ticker.

TestFlight 1.0 (9) follow-up (Fix D of the Reports-blink plan). `ResearchViewWithBinding` is
mounted ONCE in ContentView's tab ZStack and never re-created, so its lifecycle hooks re-run
whenever SwiftUI re-appears it after a full-screen cover (report reader, Profile, Trending, the
shell's chat cover or push/widget destination):

* `.onAppear { viewModel.selectedTab = initialSubTab }` (always `.research`) snapped a Reports
  segment back to Research every time a report was closed.
* `.onDisappear` stopped the 5 s Reports poll and nothing re-armed it, so a processing card
  froze after the cover closed.
* `.task(id: isActiveTab)` re-runs on a re-APPEARANCE, not only on an id change, so
  `researchTabDidActivate()` wiped the analyst the user had just picked.
* The handoff observed `prefilledTicker` alone: a repeat of the ticker the tab already held was
  no change and did nothing, and no handoff ever switched the segment, so arriving from Reports
  hid the ticker the user asked for.

Fix (ContentView.swift only): no appear-time segment seed; the shell bumps
`researchHandoffSeq &+= 1` on every handoff (never reset); the tab observes
`ResearchHandoff(ticker:seq:)` WITHOUT `initial: true`, reads the ticker from the NEW value, sets
the Research segment and THEN calls `applyPrefilledTicker(ticker)`; `.onAppear` re-arms the poll
(gated on `isActiveTab`) before the `.onDisappear` stop; the activation re-seed is latched per
visit by `@State hasActivatedThisVisit` (cleared only when the tab goes inactive). A ViewModel
method a lifecycle hook calls must not write the segment either, so the ViewModel's only segment
writer stays `generateAnalysis()` (a user tap).

Pinned beyond the fix's own lines, because each was a proven way back to the bug with the first
version of this guard still green: the observer sits on `body`'s root chain (inside a segment
subview it is unmounted on Reports); `prefilledTicker` / `handoffSeq` stay plain `let`s (as
`@State` they are seeded once and freeze); nothing else observes, reads or applies the ticker;
`ResearchHandoff`'s equality stays the synthesized one; the ViewModel's segment-write scan sees
`self?.` and `_selectedTab`; `researchTabDidActivate` has no caller anywhere in the tree but
the latched task; both handoff handlers are pinned statement for statement (an early return or an
`if` that skips a repeat of the held ticker is the same bug); and `ResearchHeader`, mounted inside
this screen and holding the segment's only binding, never writes it (its own hooks re-run after a
cover too).

There is no XCTest target (testing.md §3), so this pins the Swift source: comments are stripped
before every assertion (the fix's own comments name `onAppear`, `researchTabDidActivate()` and
`viewModel.searchText =` while explaining why), every check is brace-bounded to the declaration it
means, and each test asserts it is reading the real, non-trivial declaration (anti-vacuity).

Mutation-tested IN MEMORY (``pathlib.Path.read_text`` monkeypatched for the one target file —
the real Swift files are never touched, other sessions read them concurrently). The table runs on
every pass as ``test_each_mutation_is_killed``; every anchor must occur exactly once, and each
mutation must fail with the assertion message that names it (``pytest.raises(match=…)``), so a
mutation cannot "pass" by tripping an unrelated earlier check.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_CONTENT = _IOS / "ContentView.swift"
_VM = _IOS / "ViewModels" / "ResearchViewModel.swift"
_HEADER_VIEW = _IOS / "Views" / "Organisms" / "ResearchHeader.swift"

# A write to the Research/Reports segment, including one through the binding's projection.
_SEGMENT_WRITE = re.compile(r"\$?viewModel\.selectedTab(?:\.wrappedValue)?\s*=(?!=)")
# The same write inside the ViewModel, through ANY receiver: bare, `self.`, the ViewModel's own
# `[weak self]` idiom `self?.`, or the wrapper's storage `_selectedTab`. Not `$selectedTab` (the
# publisher) and not the `selectedTab: ResearchTab = .research` declaration.
_VM_SEGMENT_WRITE = re.compile(r"(?<![\w$])_?selectedTab\s*=(?!=)")
# An observer keyed on the ticker ALONE (any spacing, `self.`, extra arguments such as
# `initial: true`): a repeat of the same ticker is no change to it.
_TICKER_ONLY_OBSERVER = re.compile(r"\.(?:onChange\(of:|task\(id:)\s*(?:self\.)?prefilledTicker\b")
# The Trending detail's own "research this ticker" action: the one other sanctioned applier.
_TRENDING_APPLY = "TrendingAnalysisDetailView(analysis: analysis)"
_INIT = "init(selectedTab:"
# The handoff observer: ticker AND token, in one value, with no `initial: true`.
_HANDOFF_KEY = re.compile(
    r"\.onChange\(of:\s*ResearchHandoff\(ticker:\s*prefilledTicker,\s*seq:\s*handoffSeq\)\)\s*\{")
_HANDOFF_HEADER = ".onChange(of: ResearchHandoff("
_SHELL_HANDOFF = ".onChange(of: appState.pendingResearchTicker, initial: true)"
_ACTIVE_TASK = ".task(id: isActiveTab)"
# Every write to the shell's token, whatever the operator; group 1 is the operator.
_SEQ_WRITE = re.compile(r"(?<!var )\bresearchHandoffSeq\s*((?:&?[-+*]|[/%&|^]|<<|>>)?=)(?!=)")
_EMPTY_TICKER_GUARD = re.compile(
    r"guard\s+let\s+ticker\s*,\s*!ticker\.isEmpty\s+else\s*\{\s*return\s*\}")
_GATED_REARM = re.compile(r"if\s+isActiveTab\s*\{\s*viewModel\.startReportsPolling\(\)\s*\}")
_LATCH_RESET = re.compile(r"(?<!var )\bhasActivatedThisVisit\s*=\s*false\b")
_LATCH_WRITE = re.compile(
    r"(?<!var )\bhasActivatedThisVisit\s*=(?!=)|\$hasActivatedThisVisit\b|\bhasActivatedThisVisit\.toggle\(")
# Inside ResearchHeader: a write to its `selectedTab` binding through any spelling — bare,
# `self.`, the projection `$selectedTab.wrappedValue` or the storage `_selectedTab.wrappedValue`.
_HEADER_TAB_WRITE = re.compile(r"(?<!\w)[$_]?selectedTab(?:\.wrappedValue)?\s*=(?!=)")
# The shell's pendingResearchTicker handler after its parameter and empty-ticker guard: exactly
# these four statements, in any order, each on its own line (or `;`-separated), none conditional.
_SHELL_HANDOFF_STATEMENTS = (
    "researchTickerSymbol = ticker",
    "researchHandoffSeq &+= 1",
    "selectedTab = .research",
    "appState.pendingResearchTicker = nil",
)
# Statement separator inside a closure: a newline or a `;`.
_SEP = r"(?:\s*;\s*|\s+)"


def _strip_swift_comments(src: str) -> str:
    """Drop block comments, whole-line `//` comments and trailing `//` tails.

    Load-bearing: the fix's own comments name `researchTabDidActivate()` and
    `viewModel.searchText = ticker` while explaining the rules, so an un-stripped scan for
    their ABSENCE (or an exact count) fails on prose, and a scan for their PRESENCE passes on a
    revert whose comment survived. A tail needs leading whitespace, so a `https://` inside a
    string literal is not cut.
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


def _block(src: str, header: str, open_: str = "{", close: str = "}") -> str:
    """The balanced `open_`…`close` body that follows the ONLY `header` (a literal prefix).

    `(`/`)` bound a call's argument list, `{`/`}` a declaration or closure.
    """
    assert src.count(header) == 1, f"expected exactly one `{header}`, found {src.count(header)}"
    start = src.index(open_, src.index(header) + len(header))
    depth = 0
    for i in range(start, len(src)):
        if src[i] == open_:
            depth += 1
        elif src[i] == close:
            depth -= 1
            if depth == 0:
                return src[start: i + 1]
    raise AssertionError(f"unbalanced `{open_}{close}` after `{header}`")


def _blocks(src: str, header: str) -> list[tuple[int, str]]:
    """`(offset, closure)` for EVERY occurrence of `header` (a modifier such as `.onAppear`).

    The closure must follow the header directly — at most one parenthesised argument list in
    between (`.task(id: isActiveTab) {`). Without that check a future `.onAppear(perform: f)`
    would silently bind to whatever unrelated block comes next in the file.
    """
    out: list[tuple[int, str]] = []
    at = src.find(header)
    while at != -1:
        after = at + len(header)
        start = src.find("{", after)
        assert start != -1, f"`{header}` at offset {at} has no closure after it"
        between = src[after:start]
        assert re.fullmatch(r"\s*(\([^{}]*\))?\s*", between), (
            f"`{header}` at offset {at} is not followed directly by its own closure (found "
            f"`{between[:60]}` first) — re-derive this guard rather than read an unrelated block")
        depth = 0
        for i in range(start, len(src)):
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    out.append((at, src[start: i + 1]))
                    break
        else:
            raise AssertionError(f"unbalanced braces after `{header}` at offset {at}")
        at = src.find(header, after)
    return out


def _live() -> str:
    body = _block(_code(_CONTENT), "struct ResearchViewWithBinding: View")
    # Anti-vacuity: the real Research screen, not a stub or a preview copy sharing the name.
    assert "reportsTabContent" in body and "ResearchHeader(" in body and len(body) > 3000, (
        "struct ResearchViewWithBinding is not the live Research screen — this guard is stale")
    return body


def _shell() -> str:
    body = _block(_code(_CONTENT), "struct ContentView: View")
    assert "HomeDashboardView(" in body and "pendingResearchTicker" in body and len(body) > 3000, (
        "struct ContentView is not the tab shell — this guard is stale")
    return body


def _span(src: str, header: str) -> tuple[int, int]:
    """Where `_block(src, header)` sits inside `src` (call only after asserting the header)."""
    body = _block(src, header)
    start = src.index(body, src.index(header))
    return start, start + len(body)


# ── 1. Nothing re-seeds the segment when the view re-appears ────────────────


def test_no_lifecycle_hook_reseeds_the_segment():
    live = _live()
    for hook in (".onAppear", ".onDisappear", ".task"):
        for at, body in _blocks(live, hook):
            assert not _SEGMENT_WRITE.search(body), (
                f"a lifecycle hook writes viewModel.selectedTab (`{hook}` at offset {at}): the view "
                "is mounted once, so an appear/disappear/task hook re-runs after every cover and "
                "snaps the Reports segment back")

    code = _code(_CONTENT)
    for old in ("initialSubTab", "researchSubTab"):
        assert not re.search(rf"\b{old}\b", code), (
            f"`{old}` is back in ContentView — the appear-time segment seed it fed is the "
            "snap-back bug; the segment starts at the ViewModel default and only the handoff sets it")

    writers = [m.start() for m in _SEGMENT_WRITE.finditer(live)]
    assert len(writers) == 3, (
        f"viewModel.selectedTab has {len(writers)} writers in the live screen, expected exactly 3 "
        "(View Progress, the empty-state CTA, the handoff) — a new writer is a new snap-back path")

    sanctioned = ("onViewProgress:", "onGenerateFirst:", _HANDOFF_HEADER)
    for header in sanctioned:
        assert live.count(header) == 1, (
            f"the sanctioned segment writer `{header}` is gone or duplicated in the live screen")
    spans = [_span(live, h) for h in sanctioned]
    stray = [w for w in writers if not any(s <= w < e for s, e in spans)]
    assert not stray, (
        "a viewModel.selectedTab write sits outside the three sanctioned closures (View Progress, "
        f"the empty-state CTA, the handoff handler) at offsets {stray}")

    assert live.count("$viewModel.selectedTab") == 1, (
        "a second binding to the segment: only ResearchHeader's picker may hold "
        "`$viewModel.selectedTab` — another holder is a writer the scans above cannot see")

    # That one holder is mounted INSIDE this screen, so its own `.onAppear` / `.task` re-run
    # after every cover exactly like the screen's. It hands the binding to the segmented
    # control (whose tap is the user's write) and never writes it itself.
    hdr = _block(_code(_HEADER_VIEW), "struct ResearchHeader: View")
    assert ("@Binding var selectedTab: ResearchTab" in hdr and hdr.count("SegmentedTabControl") == 1
            and "selectedTab: $selectedTab" in _block(hdr, "SegmentedTabControl", "(", ")")), (
        "ResearchHeader no longer takes the segment as `@Binding var selectedTab: ResearchTab` and "
        "hands it to SegmentedTabControl (`selectedTab: $selectedTab`) — this guard is stale")
    assert not _HEADER_TAB_WRITE.search(hdr), (
        "ResearchHeader writes its selectedTab binding — it is mounted inside this screen, so an "
        "`.onAppear` / `.task` there re-runs after every cover and snaps Reports back")


def test_the_old_segment_seed_is_gone_from_the_whole_tree():
    root = _IOS.parent  # frontend/ios: ios/, Shared/, CaydexWidgets/
    files = sorted(root.rglob("*.swift"))
    assert len(files) > 500 and _CONTENT in files, (
        f"scanned {len(files)} Swift files under {root} — the tree moved; this guard is vacuous")
    hits = {}
    for f in files:
        n = len(re.findall(r"\b(?:initialSubTab|researchSubTab)\b",
                           _strip_swift_comments(f.read_text(encoding="utf-8"))))
        if n:
            hits[str(f.relative_to(root))] = n
    assert not hits, (
        f"initialSubTab/researchSubTab survives in {hits} — the segment seed is gone; do not "
        "reintroduce a parameter that seeds the Research segment")


# ── 2. The shell's handoff token ─────────────────────────────────────────────


def test_the_shell_bumps_a_handoff_token_on_every_handoff():
    code = _code(_CONTENT)
    shell = _shell()
    assert "@State private var researchHandoffSeq = 0" in shell, (
        "the handoff token `@State private var researchHandoffSeq = 0` is gone from ContentView")

    assert shell.count(_SHELL_HANDOFF) == 1, (
        f"the shell's handoff handler `{_SHELL_HANDOFF}` is gone or duplicated")
    handler = _block(shell, _SHELL_HANDOFF)
    assert "researchTickerSymbol = ticker" in handler, (
        "the pendingResearchTicker handler no longer seeds researchTickerSymbol")
    bump = re.search(r"\bresearchHandoffSeq\s*&\+=\s*1\b", handler)
    assert bump, (
        "the pendingResearchTicker handler no longer bumps researchHandoffSeq with `&+= 1` — "
        "without it a repeat of the ticker the Research tab already holds changes nothing")
    guard = _EMPTY_TICKER_GUARD.search(handler)
    assert guard and guard.end() <= bump.start(), (
        "researchHandoffSeq is bumped before the empty-ticker guard: clearing the parked ticker "
        "(the handler's own nil write, or session end) would re-fire the tab's handoff")
    # Positional checks let a condition through: `guard ticker != researchTickerSymbol else
    # { return }`, or `if ticker != researchTickerSymbol { researchHandoffSeq &+= 1 }`, keeps the
    # bump after the guard and brings the bug back. So the whole handler is pinned: its
    # parameter, the empty-ticker guard, then exactly the four statements (order free).
    stmts = [re.sub(r"\s+", " ", s).strip() for s in re.split(r"[;\n]", handler[1:-1])]
    stmts = [s for s in stmts if s]
    assert (stmts[:2] == ["_, ticker in", "guard let ticker, !ticker.isEmpty else { return }"]
            and sorted(stmts[2:]) == sorted(_SHELL_HANDOFF_STATEMENTS)), (
        "the pendingResearchTicker handler must be EXACTLY its empty-ticker guard followed by "
        f"{list(_SHELL_HANDOFF_STATEMENTS)}, found {stmts} — no other statement and no condition: "
        "an early return or an `if` around the bump makes a repeat of the ticker the Research tab "
        "already holds do nothing again")

    writes = list(_SEQ_WRITE.finditer(code))
    bad = [m.group(0) for m in writes if m.group(1) != "&+="]
    assert not bad, (
        f"researchHandoffSeq must only ever increase (`&+= 1`), found {bad} — a reset or "
        "decrease is itself a change the Research tab reacts to")
    assert len(writes) == 1, (
        f"researchHandoffSeq is written {len(writes)} times — it is written outside the "
        "pendingResearchTicker handler; a stray bump re-applies an old handoff")

    assert shell.count("ResearchViewWithBinding") == 1, (
        "ContentView no longer mounts ResearchViewWithBinding exactly once")
    call = _block(shell, "ResearchViewWithBinding", "(", ")")
    assert "prefilledTicker: researchTickerSymbol" in call, (
        "ResearchViewWithBinding is not handed researchTickerSymbol as prefilledTicker")
    assert re.search(r"\bhandoffSeq:\s*researchHandoffSeq\b", call), (
        "ResearchViewWithBinding is not handed the handoff token (`handoffSeq: researchHandoffSeq`)")


# ── 3. The Research tab applies every handoff, from the new value ────────────


def test_the_research_tab_applies_every_handoff_from_the_new_value():
    live = _live()
    assert len(_HANDOFF_KEY.findall(live)) == 1, (
        "the Research tab no longer observes ResearchHandoff(ticker: prefilledTicker, seq: "
        "handoffSeq) exactly once (ticker-only, or with `initial: true` that re-applies an old "
        "handoff on a re-appear)")

    # WHERE the observer sits. `researchTabContent` / `reportsTabContent` are built only while
    # their segment shows, so an observer inside one is unmounted on the other segment and a
    # handoff that arrives there is simply lost — the "arriving from Reports" bug, back.
    assert live.count("var body: some View") == 1, (
        "expected exactly one `var body: some View` in the live screen — this guard is stale")
    root = _block(live, "var body: some View")
    assert _HANDOFF_HEADER in root, (
        "the handoff observer must sit on `body` itself — inside researchTabContent (or any "
        "segment subview) it is unmounted on the Reports segment and a handoff from Reports is lost")
    before = root[: root.index(_HANDOFF_HEADER)]
    assert before.count("{") - before.count("}") == 1 and before.count("(") == before.count(")"), (
        "the handoff observer must be a modifier on body's ROOT chain (brace depth 1, outside any "
        "argument list) — nested in the ZStack content, a segment branch or a conditional "
        "overlay, it is not mounted while the Reports segment shows")

    # HOW the view holds the two inputs the key is built from. The view is mounted once, so a
    # `@State` copy is seeded once and frozen: every later handoff would be no change again.
    assert live.count(_INIT) == 1, f"expected exactly one `{_INIT}` in the live screen"
    init_body = _block(live, _INIT)
    for prop, typ in (("handoffSeq", "Int"), ("prefilledTicker", "String?")):
        decls = [ln.strip() for ln in live.splitlines()
                 if re.search(rf"\b(?:let|var)\s+{prop}\b", ln)]
        assert (decls == [f"let {prop}: {typ}"] and f"self.{prop} = {prop}" in init_body
                and not re.search(rf"\b_{prop}\b", live)), (
            f"`{prop}` must stay a plain `let {prop}: {typ}` assigned in init "
            f"(`self.{prop} = {prop}`), found {decls} — as `@State` it is seeded once (this view "
            "is mounted once) and freezes, so every later handoff is no change")

    handler = _block(live, _HANDOFF_HEADER)
    param = re.match(r"\{\s*_\s*,\s*(\w+)\s+in\b", handler)
    assert param, (
        "the handoff handler must bind the NEW value as its second parameter (`{ _, handoff in`) — "
        "the old value's ticker is nil on a first handoff")
    name = param.group(1)
    assert re.search(
        rf"guard\s+let\s+ticker\s*=\s*{name}\.ticker\s*,\s*!ticker\.isEmpty\s+else\s*\{{\s*return\s*\}}",
        handler), (
        f"the handoff handler must read the ticker from the NEW value (`guard let ticker = "
        f"{name}.ticker, !ticker.isEmpty else {{ return }}`)")
    assert not re.search(r"\bprefilledTicker\b", handler), (
        "the handoff handler reads the `prefilledTicker` view property instead of the new value")

    seg = handler.find("viewModel.selectedTab = .research")
    assert seg != -1, (
        "the handoff handler no longer lands on the Research segment — arriving from Reports hides "
        "the ticker the user asked for")
    apply_ = handler.find("viewModel.applyPrefilledTicker(ticker)")
    assert apply_ != -1, "the handoff handler no longer goes through viewModel.applyPrefilledTicker(ticker)"
    assert seg < apply_ and len(_SEGMENT_WRITE.findall(handler)) == 1, (
        "the handoff handler must switch to the Research segment exactly once, BEFORE "
        "applyPrefilledTicker(ticker)")
    assert "viewModel.searchText =" not in handler, (
        "the handoff writes viewModel.searchText directly — the chip and Generate would disagree")
    # The checks above are positional, so a condition slips through them: `guard ticker !=
    # viewModel.searchText else { return }`, or the segment write wrapped in an `if`. Pin the
    # whole handler, statement for statement, in this order.
    assert re.fullmatch(
        rf"\{{\s*_\s*,\s*{name}\s+in\s+"
        rf"guard\s+let\s+ticker\s*=\s*{name}\.ticker\s*,\s*!ticker\.isEmpty\s+else\s*\{{\s*return\s*\}}"
        rf"{_SEP}viewModel\.selectedTab\s*=\s*\.research"
        rf"{_SEP}viewModel\.applyPrefilledTicker\(ticker\)\s*;?\s*\}}",
        handler), (
        "the handoff handler must be EXACTLY `guard let ticker = <new value>.ticker, "
        "!ticker.isEmpty else { return }`, `viewModel.selectedTab = .research`, "
        "`viewModel.applyPrefilledTicker(ticker)` — no other statement and no condition: an early "
        "return or an `if` (e.g. `guard ticker != viewModel.searchText`) leaves a handoff of the "
        f"ticker the tab already holds on Reports; found `{handler}`")

    # No second way in for the ticker. Each check below is narrower than the next, so a
    # regression fails on the message that names it.
    assert not _TICKER_ONLY_OBSERVER.search(live), (
        "a ticker-only observer (`.onChange(of: prefilledTicker…)` / `.task(id: prefilledTicker)`) "
        "is back — it misses a repeat of the same ticker, and with `initial: true` re-applies an "
        "old handoff on every re-appear")
    init_start = live.index(_INIT)
    init_end = live.index(init_body, init_start) + len(init_body)
    key = _HANDOFF_KEY.search(live)
    decl = re.search(r"^[ \t]*let prefilledTicker: String\?[ \t]*$", live, re.M)
    reads = [m.start() for m in re.finditer(r"\bprefilledTicker\b", live)
             if not (init_start <= m.start() < init_end or key.start() <= m.start() < key.end()
                     or decl.start() <= m.start() < decl.end())]
    assert not reads, (
        f"the Research tab reads `prefilledTicker` outside init and the handoff key (offsets "
        f"{reads}) — an appear/task re-apply of the ticker overwrites the user's later pick after "
        "every cover; only the ResearchHandoff observer may react to it")
    spans = [_span(live, h) for h in (_HANDOFF_HEADER, _TRENDING_APPLY)
             if live.count(h) == 1]
    appliers = [m.start() for m in re.finditer(r"\bapplyPrefilledTicker\b", live)]
    per_span = [sum(s <= a < e for a in appliers) for s, e in spans]
    assert len(spans) == 2 and per_span == [1, 1] and len(appliers) == 2, (
        f"viewModel.applyPrefilledTicker must be called exactly twice — once in the handoff handler "
        f"and once in the Trending detail's action — found {len(appliers)} (per sanctioned "
        f"closure: {per_span}); a third applier (an appear/task re-apply) overwrites the user's "
        "later pick after every cover")

    code = _code(_CONTENT)
    assert code.count("private struct ResearchHandoff: Equatable") == 1, (
        "`private struct ResearchHandoff: Equatable` is gone — the observer needs ticker AND token "
        "in one Equatable value")
    body = _block(code, "private struct ResearchHandoff: Equatable")
    fields = [line.strip() for line in body[1:-1].splitlines() if line.strip()]
    assert fields == ["let ticker: String?", "let seq: Int"], (
        f"ResearchHandoff must hold exactly `let ticker: String?` and `let seq: Int`, got {fields} — "
        "any other field (e.g. a timestamp) changes the value on every render and re-fires the handoff")
    # Equality must stay the SYNTHESIZED one over both fields. A hand-written `==` (in an
    # extension, at file scope, or inherited through another conformance) that ignores `seq`
    # makes a repeat of the same ticker no change again. The struct is `private`, so any such
    # `==` must live in this file and must name the type.
    assert re.search(r"private struct ResearchHandoff: Equatable\s*\{", code), (
        "ResearchHandoff must conform to Equatable ALONE — another conformance can carry a "
        "default `==` that ignores `seq`")
    names = len(re.findall(r"\bResearchHandoff\b", code))
    assert names == 2, (
        f"ResearchHandoff is named {names} times in ContentView, expected 2 (its declaration and "
        "the observer key) — an `extension ResearchHandoff` or a file-scope `==` can replace the "
        "synthesized equality and drop `seq`")


# ── 4. The Reports poll is re-armed when the screen re-appears ───────────────


def test_the_reports_poll_is_rearmed_when_the_screen_reappears():
    live = _live()
    stops = [at for at, b in _blocks(live, ".onDisappear") if "viewModel.stopReportsPolling()" in b]
    assert stops, "the `.onDisappear { viewModel.stopReportsPolling() }` stop is gone (control)"

    rearms = [(at, b) for at, b in _blocks(live, ".onAppear") if "startReportsPolling()" in b]
    assert rearms, (
        "the Reports poll is never re-armed on appear: a cover's disappear stops it and a "
        "processing card freezes after the cover closes")
    for at, body in rearms:
        assert _GATED_REARM.search(body) and "startReportsPolling" not in _GATED_REARM.sub("", body), (
            f"the appear-time re-arm at offset {at} must be gated on isActiveTab "
            "(`if isActiveTab { viewModel.startReportsPolling() }`) — a hidden tab re-arms through "
            "its activation task")
    assert max(at for at, _ in rearms) < min(stops), (
        "the appear-time re-arm must precede the `.onDisappear` stop")

    polls = [b for _, b in _blocks(live, _ACTIVE_TASK)
             if re.search(r"guard\s+isActiveTab\s+else", b) and "viewModel.startReportsPolling()" in b]
    assert polls, (
        "the activation poll task (`.task(id: isActiveTab)` with `guard isActiveTab else` and "
        "startReportsPolling()) is gone")


# ── 5. A cover cycle cannot wipe a manual analyst pick ───────────────────────


def test_a_cover_cycle_cannot_reset_a_manual_analyst_pick():
    live = _live()
    code = _code(_CONTENT)
    assert "@State private var hasActivatedThisVisit = false" in live, (
        "the per-visit latch `@State private var hasActivatedThisVisit = false` must be declared")

    tasks = [b for _, b in _blocks(live, _ACTIVE_TASK) if "researchTabDidActivate()" in b]
    assert len(tasks) == 1, (
        f"exactly one `.task(id: isActiveTab)` block must call researchTabDidActivate(), found "
        f"{len(tasks)}")
    task = tasks[0]
    assert task.count("guard isActiveTab else") == 1, (
        "the activation task lost its `guard isActiveTab else` branch")
    inactive = _block(task, "guard isActiveTab else")
    assert _LATCH_RESET.search(inactive), (
        "hasActivatedThisVisit is not cleared when the tab goes inactive — the next visit would "
        "never re-seed the default analyst")

    tail = task[task.index("{", task.index("guard isActiveTab else")) + len(inactive):]
    a = (m.start() if (m := re.search(r"guard\s+!hasActivatedThisVisit\s+else\s*\{\s*return\s*\}", tail)) else -1)
    b = (m.start() if (m := re.search(r"\bhasActivatedThisVisit\s*=\s*true\b", tail)) else -1)
    c = tail.find("viewModel.researchTabDidActivate()")
    assert 0 <= a < b < c, (
        "researchTabDidActivate() is not latched per visit: the active path must check "
        "`guard !hasActivatedThisVisit else { return }`, then set it true, then call — a re-appear "
        "re-runs `.task` and would wipe the analyst just picked")

    assert len(re.findall(r"\bresearchTabDidActivate\(\)", code)) == 1, (
        "researchTabDidActivate() is called from more than one place in ContentView — only the "
        "latched activation task may call it")
    assert len(_LATCH_RESET.findall(live)) == 1, (
        "hasActivatedThisVisit is reset outside the not-active branch — a cover's "
        "disappear/appear would re-open the latch")
    assert len(_LATCH_WRITE.findall(live)) == 2, (
        "hasActivatedThisVisit is written outside the activation task (expected exactly the "
        "inactive reset and the active set)")


def test_nothing_outside_the_latched_task_activates_the_research_tab():
    """The latch above guards ContentView's ONE call. A second caller anywhere else bypasses
    it: a ViewModel method that a lifecycle hook runs (`loadIfStale`, `handleIdentityChange`,
    the poll) re-runs after every cover, and so would any other view's hook. Every mention
    counts, call or method reference."""
    vm = _code(_VM)
    assert vm.count("func researchTabDidActivate()") == 1, (
        "ResearchViewModel.researchTabDidActivate() is gone — this guard is stale")
    in_vm = len(re.findall(r"\bresearchTabDidActivate\b", vm))
    assert in_vm == 1, (
        f"ResearchViewModel names researchTabDidActivate {in_vm} times, expected only its "
        "declaration — the ViewModel calls it itself, and a lifecycle-called method that does so "
        "bypasses the per-visit latch and wipes the analyst after every cover")

    root = _IOS.parent  # frontend/ios: ios/, Shared/, CaydexWidgets/
    files = sorted(root.rglob("*.swift"))
    assert len(files) > 500 and _CONTENT in files and _VM in files, (
        f"scanned {len(files)} Swift files under {root} — the tree moved; this guard is vacuous")
    hits = {}
    for f in files:
        n = len(re.findall(r"\bresearchTabDidActivate\b",
                           _strip_swift_comments(f.read_text(encoding="utf-8"))))
        if n:
            hits[str(f.relative_to(root))] = n
    expected = {str(_CONTENT.relative_to(root)): 1, str(_VM.relative_to(root)): 1}
    assert hits == expected, (
        f"researchTabDidActivate is named in {hits}, expected {expected} (ContentView's latched "
        "`.task(id: isActiveTab)` call and the ViewModel's declaration) — any other caller or "
        "method reference re-seeds the analyst outside the per-visit latch")


# ── 6. Lifecycle-called ViewModel code does not write the segment either ─────


def test_the_view_model_writes_the_segment_only_from_generate():
    """A hook that calls a ViewModel method (`researchTabDidActivate`, `loadIfStale`,
    `handleIdentityChange`, the poll) re-runs after every cover too. The ViewModel's only
    segment writer is `generateAnalysis()` — a user tap that moves to Reports."""
    vm = _code(_VM)
    assert re.search(r"@Published\s+var\s+selectedTab\s*:\s*ResearchTab\s*=\s*\.research\b", vm), (
        "ResearchViewModel.selectedTab no longer defaults to .research — the screen relies on that "
        "default now that the appear-time seed is gone")
    assert vm.count("func generateAnalysis()") == 1, "ResearchViewModel.generateAnalysis() is gone"
    start, end = _span(vm, "func generateAnalysis()")
    writes = [m.start() for m in _VM_SEGMENT_WRITE.finditer(vm)]
    assert any(start <= w < end for w in writes), (
        "generateAnalysis() no longer switches to Reports — this guard reads the wrong file")
    stray = [w for w in writes if not start <= w < end]
    assert not stray, (
        f"ResearchViewModel writes selectedTab outside generateAnalysis() (offsets {stray}) — a "
        "lifecycle-called method that writes the segment snaps Reports back after a cover")


# ── 7. The stripping the scans depend on ─────────────────────────────────────


def test_comment_stripping_is_load_bearing():
    out = _strip_swift_comments("// .onAppear { viewModel.selectedTab = initialSubTab }\nlet x = 1")
    assert "selectedTab" not in out and "let x = 1" in out, out

    # On the real source the comments DO carry the tokens: an un-stripped count/absence check
    # would fail on prose (or pass on a revert whose comment survived).
    raw = _CONTENT.read_text(encoding="utf-8")
    code = _code(_CONTENT)
    assert raw.count("researchTabDidActivate()") > code.count("researchTabDidActivate()") == 1, (
        "expected the latch comment to name researchTabDidActivate() — stripping no longer matters "
        "for the single-caller check")
    assert "viewModel.searchText =" in _block(raw, _HANDOFF_HEADER), (
        "expected the handoff handler's comment to name `viewModel.searchText =`")
    assert "viewModel.searchText =" not in _block(code, _HANDOFF_HEADER)


# ── 8. The mutations above, re-run in memory on every pass ──────────────────

_STOP = ".onDisappear { viewModel.stopReportsPolling() }"
_REARM = ".onAppear { if isActiveTab { viewModel.startReportsPolling() } }"
_KEY = ".onChange(of: ResearchHandoff(ticker: prefilledTicker, seq: handoffSeq))"
_SEG_LINE = "            viewModel.selectedTab = .research\n"
_APPLY_END = "            viewModel.applyPrefilledTicker(ticker)\n        }\n"
_BUMP = "            researchHandoffSeq &+= 1\n"
_SHELL_BODY = (
    "            guard let ticker, !ticker.isEmpty else { return }\n"
    "            researchTickerSymbol = ticker\n" + _BUMP
    + "            selectedTab = .research\n"
    "            appState.pendingResearchTicker = nil\n")
_TAB_GUARD = "            guard let ticker = handoff.ticker, !ticker.isEmpty else { return }\n"
_HEADER_PAD = "        .padding(.bottom, AppSpacing.sm)\n"
_LEAVE = "                researchTickerSymbol = nil\n"
_LATCH_CHECK = "guard !hasActivatedThisVisit else { return }"
_LATCH_SET = "            hasActivatedThisVisit = true\n            viewModel.researchTabDidActivate()\n"
_POLL_TASK = "            guard isActiveTab else { return }\n            viewModel.startReportsPolling()\n"
_RESEARCH_CONTENT_END = (
    "            await viewModel.refresh()\n        }\n    }\n\n    // MARK: - Reports Tab Content")
_ACTIVATE_IN_VM = "    func researchTabDidActivate() {\n"
_INIT_DECL = (
    "    init(selectedTab: Binding<HomeTab>, prefilledTicker: String? = nil, handoffSeq: Int = 0) {\n")


def _raw_slice(path: pathlib.Path, start: str, end: str) -> str:
    """The RAW source from the only `start` through the first `end` after it — a mutation
    anchor that moves a whole closure, comment lines included, derived from the file so a
    comment edit does not orphan it. A missing marker yields a sentinel that the harness's
    exactly-once anchor check then reports by name."""
    src = path.read_text(encoding="utf-8") if path.exists() else ""
    if src.count(start) != 1 or end not in src[src.find(start):]:
        return f"<marker `{start.strip()}` … `{end.strip()}` not found once in {path.name}>"
    at = src.index(start)
    return src[at: src.index(end, at) + len(end)]


# The whole handoff closure as written (raw, comments and all), for the mutations that MOVE it.
_HANDLER_RAW = _raw_slice(_CONTENT, "        " + _HANDOFF_HEADER, _APPLY_END)

_T1 = test_no_lifecycle_hook_reseeds_the_segment
_T1W = test_the_old_segment_seed_is_gone_from_the_whole_tree
_T2 = test_the_shell_bumps_a_handoff_token_on_every_handoff
_T3 = test_the_research_tab_applies_every_handoff_from_the_new_value
_T4 = test_the_reports_poll_is_rearmed_when_the_screen_reappears
_T5 = test_a_cover_cycle_cannot_reset_a_manual_analyst_pick
_T5W = test_nothing_outside_the_latched_task_activates_the_research_tab
_T6 = test_the_view_model_writes_the_segment_only_from_generate

# (id, file, ((anchor, replacement), ...), guard, the assertion message the guard must fail WITH).
# Edits apply in order; every anchor occurs exactly once. The message is matched so a mutation
# cannot pass by tripping an unrelated, earlier assertion.
_MUTATIONS = [
    # 1. Appear-time segment writes.
    ("M1-seed-restored", _CONTENT,
     ((_STOP, ".onAppear {\n            viewModel.selectedTab = initialSubTab\n        }\n        " + _STOP),),
     _T1, "a lifecycle hook writes viewModel.selectedTab"),
    ("M2-task-seed", _CONTENT,
     ((_STOP, ".task { viewModel.selectedTab = .research }\n        " + _STOP),),
     _T1, "a lifecycle hook writes viewModel.selectedTab"),
    ("M2b-binding-seed", _CONTENT,
     ((_STOP, ".onAppear { $viewModel.selectedTab.wrappedValue = .research }\n        " + _STOP),),
     _T1, "a lifecycle hook writes viewModel.selectedTab"),
    ("M3-onchange-active-seed", _CONTENT,
     ((_STOP, ".onChange(of: isActiveTab) { _, a in if a { viewModel.selectedTab = .research } }\n        "
       + _STOP),),
     _T1, "writers in the live screen"),
    ("M3b-writer-swapped-for-a-hook", _CONTENT,
     (("onGenerateFirst: { viewModel.selectedTab = .research }", "onGenerateFirst: { }"),
      (_STOP, ".onChange(of: isActiveTab) { _, a in if a { viewModel.selectedTab = .research } }\n        "
       + _STOP)),
     _T1, "sits outside the three sanctioned closures"),
    ("M3c-second-binding", _CONTENT,
     ((_STOP, ".background(SegmentResetter(tab: $viewModel.selectedTab))\n        " + _STOP),),
     _T1, "a second binding to the segment"),
    ("M3d-old-state-back", _CONTENT,
     (("    @State private var researchHandoffSeq = 0\n",
       "    @State private var researchHandoffSeq = 0\n    @State private var researchSubTab: ResearchTab = .research\n"),),
     _T1, "`researchSubTab` is back in ContentView"),
    # 1b. The header, mounted inside the screen, holds the segment's only binding.
    ("M2c-header-appear-seed", _HEADER_VIEW,
     ((_HEADER_PAD, _HEADER_PAD + "        .onAppear { selectedTab = .research }\n"),),
     _T1, "ResearchHeader writes its selectedTab binding"),
    ("M2c2-header-task-storage-seed", _HEADER_VIEW,
     ((_HEADER_PAD, _HEADER_PAD + "        .task { _selectedTab.wrappedValue = .research }\n"),),
     _T1, "ResearchHeader writes its selectedTab binding"),
    ("M2c3-header-picker-unbound", _HEADER_VIEW,
     (("                selectedTab: $selectedTab\n", "                selectedTab: .constant(.research)\n"),),
     _T1, "ResearchHeader no longer takes the segment as"),
    ("M3e-old-param-in-vm", _VM,
     (("    @Published var selectedTab: ResearchTab = .research\n",
       "    @Published var selectedTab: ResearchTab = .research\n    var initialSubTab: ResearchTab = .research\n"),),
     _T1W, "initialSubTab/researchSubTab survives in"),
    # 2. The shell's token.
    ("M4-no-bump", _CONTENT, ((_BUMP, ""),),
     _T2, "no longer bumps researchHandoffSeq"),
    ("M4b-trapping-bump", _CONTENT, ((_BUMP, "            researchHandoffSeq += 1\n"),),
     _T2, "no longer bumps researchHandoffSeq"),
    ("M4c-bump-before-guard", _CONTENT,
     (("            guard let ticker, !ticker.isEmpty else { return }\n"
       "            researchTickerSymbol = ticker\n" + _BUMP,
       _BUMP + "            guard let ticker, !ticker.isEmpty else { return }\n"
       "            researchTickerSymbol = ticker\n"),),
     _T2, "bumped before the empty-ticker guard"),
    ("M4d-shell-dedupe-early-return", _CONTENT,
     ((_SHELL_BODY,
       "            guard let ticker, !ticker.isEmpty else { return }\n"
       "            selectedTab = .research\n"
       "            appState.pendingResearchTicker = nil\n"
       "            guard ticker != researchTickerSymbol else { return }\n"
       "            researchTickerSymbol = ticker\n" + _BUMP),),
     _T2, "the pendingResearchTicker handler must be EXACTLY"),
    ("M4e-shell-bump-only-on-new-ticker", _CONTENT,
     (("            researchTickerSymbol = ticker\n" + _BUMP,
       "            if ticker != researchTickerSymbol { researchHandoffSeq &+= 1 }\n"
       "            researchTickerSymbol = ticker\n"),),
     _T2, "the pendingResearchTicker handler must be EXACTLY"),
    ("M4f-shell-no-tab-switch", _CONTENT,
     ((_BUMP + "            selectedTab = .research\n", _BUMP),),
     _T2, "the pendingResearchTicker handler must be EXACTLY"),
    ("M5-reset-on-leave", _CONTENT, ((_LEAVE, _LEAVE + "                researchHandoffSeq = 0\n"),),
     _T2, "must only ever increase"),
    ("M5b-stray-bump", _CONTENT, ((_LEAVE, _LEAVE + "                researchHandoffSeq &+= 1\n"),),
     _T2, "written outside the pendingResearchTicker handler"),
    ("M6-constant-token", _CONTENT, (("handoffSeq: researchHandoffSeq", "handoffSeq: 0"),),
     _T2, "is not handed the handoff token"),
    ("M6b-token-dropped", _CONTENT,
     ((",\n                handoffSeq: researchHandoffSeq\n", "\n"),),
     _T2, "is not handed the handoff token"),
    # 3. The tab's observer.
    ("M7-ticker-only", _CONTENT, ((_KEY, ".onChange(of: prefilledTicker)"),),
     _T3, "no longer observes ResearchHandoff"),
    ("M8-initial-true", _CONTENT, ((_KEY, _KEY[:-1] + ", initial: true)"),),
     _T3, "no longer observes ResearchHandoff"),
    ("M9-reads-view-property", _CONTENT,
     (("guard let ticker = handoff.ticker", "guard let ticker = prefilledTicker"),),
     _T3, "must read the ticker from the NEW value"),
    ("M9b-binds-old-value", _CONTENT, ((_KEY + " { _, handoff in", _KEY + " { handoff, _ in"),),
     _T3, "must bind the NEW value"),
    ("M10-no-segment-switch", _CONTENT, ((_SEG_LINE, ""),),
     _T3, "no longer lands on the Research segment"),
    ("M10b-switch-after-apply", _CONTENT,
     ((_SEG_LINE, ""),
      (_APPLY_END, "            viewModel.applyPrefilledTicker(ticker)\n" + _SEG_LINE + "        }\n")),
     _T3, "BEFORE applyPrefilledTicker(ticker)"),
    ("M10c-direct-search-text", _CONTENT,
     ((_APPLY_END, "            viewModel.searchText = ticker\n        }\n"),),
     _T3, "no longer goes through viewModel.applyPrefilledTicker(ticker)"),
    ("M10d-per-render-field", _CONTENT,
     (("    let seq: Int\n}", "    let seq: Int\n    let at = Date()\n}"),),
     _T3, "ResearchHandoff must hold exactly"),
    ("M10g-tab-dedupe-early-return", _CONTENT,
     ((_TAB_GUARD, _TAB_GUARD + "            guard ticker != viewModel.searchText else { return }\n"),),
     _T3, "the handoff handler must be EXACTLY"),
    ("M10h-tab-conditional-segment-switch", _CONTENT,
     ((_SEG_LINE, "            if viewModel.searchText != ticker { viewModel.selectedTab = .research }\n"),),
     _T3, "the handoff handler must be EXACTLY"),
    # 3b. Where the observer sits, and how the view holds its inputs.
    ("M7a-stale-second-body", _CONTENT,
     ((_INIT_DECL, "    private struct Inner: View { var body: some View { EmptyView() } }\n\n" + _INIT_DECL),),
     _T3, "expected exactly one `var body: some View` in the live screen"),
    ("M6b2-stale-init-renamed", _CONTENT,
     ((_INIT_DECL, _INIT_DECL.replace("init(selectedTab:", "init(tab:")),),
     _T3, "expected exactly one `init(selectedTab:` in the live screen"),
    ("M7b-observer-in-segment-subview", _CONTENT,
     ((_HANDLER_RAW, ""),
      (_RESEARCH_CONTENT_END, "            await viewModel.refresh()\n        }\n" + _HANDLER_RAW
       + "    }\n\n    // MARK: - Reports Tab Content")),
     _T3, "must sit on `body` itself"),
    ("M7c-observer-in-research-branch", _CONTENT,
     ((_HANDLER_RAW, ""),
      ("                    researchTabContent\n", "                    researchTabContent\n" + _HANDLER_RAW)),
     _T3, "must be a modifier on body's ROOT chain"),
    ("M7d-observer-in-conditional-overlay", _CONTENT,
     ((_HANDLER_RAW, ""),
      (_STOP, ".overlay(viewModel.selectedTab == .research ? AnyView(Color.clear\n" + _HANDLER_RAW
       + "        ) : AnyView(EmptyView()))\n        " + _STOP)),
     _T3, "must be a modifier on body's ROOT chain"),
    ("M6c-token-frozen-in-state", _CONTENT,
     (("    let handoffSeq: Int\n", "    @State private var handoffSeq: Int\n"),
      ("        self.handoffSeq = handoffSeq\n", "        self._handoffSeq = State(initialValue: handoffSeq)\n")),
     _T3, "`handoffSeq` must stay a plain `let handoffSeq: Int`"),
    ("M6d-ticker-frozen-in-state", _CONTENT,
     (("    let prefilledTicker: String?\n", "    @State private var prefilledTicker: String?\n"),
      ("        self.prefilledTicker = prefilledTicker\n",
       "        self._prefilledTicker = State(initialValue: prefilledTicker)\n")),
     _T3, "`prefilledTicker` must stay a plain `let prefilledTicker: String?`"),
    # 3c. No second way in for the ticker.
    ("M7e-ticker-only-initial-true", _CONTENT,
     ((_STOP, ".onChange(of: prefilledTicker, initial: true) { _, t in "
              "if let t { viewModel.applyPrefilledTicker(t) } }\n        " + _STOP),),
     _T3, "a ticker-only observer"),
    ("M7f-task-keyed-on-ticker", _CONTENT,
     ((_STOP, ".task(id: prefilledTicker) { if let t = prefilledTicker { "
              "viewModel.applyPrefilledTicker(t) } }\n        " + _STOP),),
     _T3, "a ticker-only observer"),
    ("M7g-appear-reapplies-the-property", _CONTENT,
     ((_STOP, ".onAppear { if let t = prefilledTicker { viewModel.applyPrefilledTicker(t) } }\n        "
       + _STOP),),
     _T3, "reads `prefilledTicker` outside init and the handoff key"),
    ("M7h-third-applier", _CONTENT,
     ((_STOP, ".onAppear { if let t = appState.pendingResearchTicker { "
              "viewModel.applyPrefilledTicker(t) } }\n        " + _STOP),),
     _T3, "must be called exactly twice"),
    # 3d. Equality stays synthesized over both fields.
    ("M10e-extension-eq-drops-seq", _CONTENT,
     (("    let seq: Int\n}\n",
       "    let seq: Int\n}\n\nextension ResearchHandoff {\n    static func == (l: ResearchHandoff, "
       "r: ResearchHandoff) -> Bool { l.ticker == r.ticker }\n}\n"),),
     _T3, "ResearchHandoff is named"),
    ("M10e2-file-scope-eq-drops-seq", _CONTENT,
     (("    let seq: Int\n}\n",
       "    let seq: Int\n}\n\nprivate func == (l: ResearchHandoff, r: ResearchHandoff) -> Bool "
       "{ l.ticker == r.ticker }\n"),),
     _T3, "ResearchHandoff is named"),
    ("M10f-extra-conformance", _CONTENT,
     (("private struct ResearchHandoff: Equatable {\n",
       "private struct ResearchHandoff: Equatable, TickerKeyed {\n"),),
     _T3, "must conform to Equatable ALONE"),
    # 4. The poll re-arm.
    ("M11-no-rearm", _CONTENT, ((_REARM, ""),),
     _T4, "never re-armed on appear"),
    ("M12-ungated-rearm", _CONTENT,
     (("if isActiveTab { viewModel.startReportsPolling() }", "viewModel.startReportsPolling()"),),
     _T4, "must be gated on isActiveTab"),
    ("M12b-rearm-after-stop", _CONTENT,
     ((_REARM + "\n        " + _STOP, _STOP + "\n        " + _REARM),),
     _T4, "must precede the `.onDisappear` stop"),
    ("M12c-activation-poll-dropped", _CONTENT,
     ((_POLL_TASK, "            guard isActiveTab else { return }\n"),),
     _T4, "the activation poll task"),
    ("M12d-stop-dropped", _CONTENT, ((_STOP, ""),),
     _T4, "stop is gone (control)"),
    # 5. The per-visit latch.
    ("M13-latch-unchecked", _CONTENT, ((_LATCH_CHECK, ""),),
     _T5, "is not latched per visit"),
    ("M13b-latch-set-after-call", _CONTENT,
     ((_LATCH_SET, "            viewModel.researchTabDidActivate()\n            hasActivatedThisVisit = true\n"),),
     _T5, "is not latched per visit"),
    ("M13c-latch-starts-closed", _CONTENT,
     (("@State private var hasActivatedThisVisit = false", "@State private var hasActivatedThisVisit = true"),),
     _T5, "the per-visit latch"),
    ("M14-no-inactive-reset", _CONTENT, (("                hasActivatedThisVisit = false\n", ""),),
     _T5, "is not cleared when the tab goes inactive"),
    ("M15-reset-on-disappear", _CONTENT,
     ((_STOP, ".onDisappear {\n            viewModel.stopReportsPolling()\n"
              "            hasActivatedThisVisit = false\n        }"),),
     _T5, "reset outside the not-active branch"),
    ("M15b-set-on-disappear", _CONTENT,
     ((_STOP, ".onDisappear {\n            viewModel.stopReportsPolling()\n"
              "            hasActivatedThisVisit = true\n        }"),),
     _T5, "written outside the activation task"),
    ("M16-activate-on-appear", _CONTENT,
     ((_REARM, ".onAppear { if isActiveTab { viewModel.startReportsPolling(); "
               "viewModel.researchTabDidActivate() } }"),),
     _T5, "called from more than one place"),
    ("M16b-activate-in-second-task", _CONTENT,
     (("            await viewModel.loadIfStale()\n",
       "            await viewModel.loadIfStale()\n            viewModel.researchTabDidActivate()\n"),),
     _T5, "exactly one `.task(id: isActiveTab)` block must call researchTabDidActivate()"),
    ("M16b2-stale-vm-activation-renamed", _VM,
     ((_ACTIVATE_IN_VM, "    func researchTabDidAppear() {\n"),),
     _T5W, "ResearchViewModel.researchTabDidActivate() is gone"),
    ("M16c-vm-activates-from-loadIfStale", _VM,
     (("        if let last = lastLoadedAt, Date().timeIntervalSince(last) < maxAge { return }\n"
       "        await loadBackendData()\n",
       "        if let last = lastLoadedAt, Date().timeIntervalSince(last) < maxAge { return }\n"
       "        researchTabDidActivate()\n        await loadBackendData()\n"),),
     _T5W, "ResearchViewModel names researchTabDidActivate 2 times"),
    ("M16d-vm-weak-self-activation", _VM,
     (("        applyDefaultPersona(force: true)\n",
       "        applyDefaultPersona(force: true)\n"
       "        Task { @MainActor [weak self] in self?.researchTabDidActivate() }\n"),),
     _T5W, "ResearchViewModel names researchTabDidActivate 2 times"),
    ("M16e-method-reference-in-content", _CONTENT,
     ((_REARM, _REARM + "\n        .onAppear(perform: viewModel.researchTabDidActivate)"),),
     _T5W, "researchTabDidActivate is named in"),
    ("M16f-another-view-activates", _HEADER_VIEW,
     (("        .fullScreenCover(isPresented: $showSloganSheet) {\n",
       "        .onAppear { researchViewModel?.researchTabDidActivate() }\n"
       "        .fullScreenCover(isPresented: $showSloganSheet) {\n"),),
     _T5W, "researchTabDidActivate is named in"),
    # 6. The ViewModel.
    ("M17-vm-activation-writes-segment", _VM,
     (("    func researchTabDidActivate() {\n",
       "    func researchTabDidActivate() {\n        selectedTab = .research\n"),),
     _T6, "writes selectedTab outside generateAnalysis()"),
    ("M17c-vm-weak-self-segment-write", _VM,
     ((_ACTIVATE_IN_VM,
       _ACTIVATE_IN_VM + "        Task { @MainActor [weak self] in self?.selectedTab = .research }\n"),),
     _T6, "writes selectedTab outside generateAnalysis()"),
    ("M17d-vm-storage-segment-write", _VM,
     ((_ACTIVATE_IN_VM, _ACTIVATE_IN_VM + "        _selectedTab = Published(initialValue: .research)\n"),),
     _T6, "writes selectedTab outside generateAnalysis()"),
    ("M17b-vm-default-changed", _VM,
     (("@Published var selectedTab: ResearchTab = .research", "@Published var selectedTab: ResearchTab = .reports"),),
     _T6, "no longer defaults to .research"),
]


@pytest.mark.parametrize(
    "path,edits,test,message",
    [m[1:] for m in _MUTATIONS],
    ids=[m[0] for m in _MUTATIONS],
)
def test_each_mutation_is_killed(monkeypatch, path, edits, test, message):
    """Each guard above must go red on the regression it names, WITH the message that names
    it. Patched in memory only — other sessions' tests read these Swift files concurrently,
    so they are never rewritten."""
    real_read_text = pathlib.Path.read_text
    original = real_read_text(path, encoding="utf-8")
    mutated = original
    for old, new in edits:
        assert original.count(old) == 1, (
            f"mutation anchor `{old[:60]}` occurs {original.count(old)} times in {path.name}, "
            "expected exactly once — re-derive this mutation against the new source rather than "
            "deleting it")
        assert mutated.count(old) == 1, f"an earlier edit disturbed anchor `{old[:60]}`"
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
