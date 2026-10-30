""""Run Again" in a report's overflow menu starts a NEW run of that analysis.

The report screen's ••• menu (`ReportHeaderBar`) offers "Run Again": the Research tab's Generate
Analysis for the report's ticker and analyst, after which the report closes onto Research → Reports,
where the new run's card shows its progress. The tap SPENDS 20 credits with no dialog after it, so
what is pinned here is the money path, not the layout:

1. The menu item sits below View Detailed Analysis, outside the PDF gate, and its second line
   states the cost (and the analyst) — the disclosure `GenerateAnalysisButton` makes.
2. The report screen PARKS the request on `AppState.pendingResearchRerun` and then asks every tab
   root to take its presentations down, with no `dismiss()` of its own — the "AI Deep Research"
   shape (`test_ios_deep_research_route.py`). The screen is presented from the Reports list AND
   from notification covers on other tabs, and only the Research tab's ViewModel can start and
   monitor a generation.
3. The analyst is the report ON SCREEN's (`report.agent`), never the route's, and the account that
   tapped is stamped on the request.
4. The request is cleared at session end (auth.md §7) — one left behind would charge the next account.
5. `ResearchViewWithBinding` is its only consumer and clearer, from body's root chain; it brings
   the Research tab forward at once and runs only once the presentation stack has cleared, so a
   refused run's alert is neither wiped by the reset nor lost under a closing cover.
6. `ResearchViewModel.rerunAnalysis` drops a request whose account no longer holds the session,
   then goes through `applyPrefilledTicker`, `selectPersona` and `generateAnalysis()` — every guard
   a Generate tap runs, and the segment's only ViewModel-side writer
   (`test_ios_research_segment_survives_covers.py`) — synchronously, so nothing runs between the
   owner check and the charge.

There is no XCTest target, so this pins the Swift source: comments are stripped before every
assertion (the fix's own comments name the tokens asserted on), every check is brace-bounded to
its declaration, and each guard is mutation-tested IN MEMORY (``pathlib.Path.read_text``
monkeypatched for the one target file — the Swift files are never rewritten) by
``test_each_mutation_is_killed``, which must fail with the assertion message that names it.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_HEADER = _IOS / "Views" / "Molecules" / "ReportHeaderBar.swift"
_REPORT = _IOS / "Views" / "Screens" / "TickerReportView.swift"
_APP_STATE = _IOS / "Core" / "State" / "AppState.swift"
_MODELS = _IOS / "Models" / "ResearchModels.swift"
_VM = _IOS / "ViewModels" / "ResearchViewModel.swift"
_CONTENT = _IOS / "ContentView.swift"

_TAP = "private func handleRunAgain()"
_DETAIL = "private func runAgainDetail(_ report: TickerReportData) -> String"
_RERUN = "func rerunAnalysis(_ request: ResearchRerunRequest)"
_OBSERVER = ".onChange(of: appState.pendingResearchRerun, initial: true)"
_LIVE = "struct ResearchViewWithBinding: View"
_ENDED = "private func discardDataForEndedSession()"


def _strip_swift_comments(src: str) -> str:
    """Drop block comments, whole-line `//` comments and trailing `//` tails (a tail needs
    leading whitespace, so a `https://` inside a string literal is not cut)."""
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
    """The `open_`…`close` block whose opening delimiter is at `start`."""
    assert src[start] == open_, f"expected `{open_}` at offset {start}"
    depth = 0
    for i in range(start, len(src)):
        if src[i] == open_:
            depth += 1
        elif src[i] == close:
            depth -= 1
            if depth == 0:
                return src[start: i + 1]
    raise AssertionError(f"unbalanced `{open_}{close}` from offset {start}")


def _block(src: str, header: str, open_: str = "{", close: str = "}") -> str:
    """The balanced block opened by the first `open_` at or after the ONLY `header`."""
    assert src.count(header) == 1, f"expected exactly one `{header}`, found {src.count(header)}"
    return _balanced(src, src.index(open_, src.index(header)), open_, close)


def _menu() -> str:
    menu = _block(_code(_HEADER), "Menu {")
    assert "View Detailed Analysis" in menu and "role: .destructive" in menu, (
        "ReportHeaderBar's Menu is not the report's overflow menu — this guard is stale")
    return menu


def _live() -> str:
    body = _block(_code(_CONTENT), _LIVE)
    assert "reportsTabContent" in body and "ResearchHeader(" in body and len(body) > 3000, (
        "struct ResearchViewWithBinding is not the live Research screen — this guard is stale")
    return body


# ── 1. The menu offers it, below View Detailed Analysis, with the cost on it ──


def test_the_menu_offers_run_again_below_view_detailed_analysis():
    menu = _menu()
    assert menu.count('Label("Run Again"') == 1, "the report menu no longer offers Run Again"
    run = menu.index('Label("Run Again"')
    assert menu.index("View Detailed Analysis") < run < menu.index("role: .destructive"), (
        "Run Again must sit below View Detailed Analysis and above Delete")
    assert "Run Again" not in _block(menu, "if canExportPDF"), (
        "Run Again is inside the PDF gate — a report with no saved row could not be re-run")
    button = _block(menu, "Button(action: onRunAgain)")
    assert re.search(r"\bText\(runAgainDetail\)", button), (
        "Run Again lost its second line — the tap spends 20 credits at once, so the item itself "
        "must state the cost")


def test_the_line_under_it_names_the_cost_and_the_reports_own_analyst():
    report = _code(_REPORT)
    detail = _block(report, _DETAIL)
    assert "AnalysisCost.standard.credits" in detail, "the Run Again line no longer states the cost"
    assert "report.agent.personaKey" in detail, (
        "the Run Again line no longer names the analyst of the report on screen")
    call = _block(report, "ReportHeaderBar(", "(", ")")
    assert (re.search(r"\bonRunAgain:\s*handleRunAgain\b", call)
            and re.search(r"\brunAgainDetail:\s*runAgainDetail\(report\)", call)), (
        "the report screen no longer wires Run Again (its handler and its cost line) into the header")


# ── 2 + 3. The tap parks the request, then takes the stack down ──────────────


def test_the_tap_parks_the_request_then_takes_the_stack_down():
    body = _block(_code(_REPORT), _TAP)
    park = body.find("appState.pendingResearchRerun = ResearchRerunRequest(")
    teardown = body.find("appState.dismissAllPresentations()")
    assert park != -1, "Run Again no longer parks the request for the Research tab"
    assert teardown != -1, (
        "Run Again parks the request but never asks the presentation stack to come down — the "
        "report stays on screen over the tab that is running it")
    assert park < teardown, (
        "the request is parked AFTER the teardown — park first, so the tab has switched before "
        "the covers animate away")
    assert "dismiss()" not in body, (
        "Run Again dismisses itself too — the root teardown already closes the report, and two "
        "dismissals in one runloop wedge UIKit")
    request = _block(body, "ResearchRerunRequest(", "(", ")")
    assert re.search(r"\bticker:\s*report\.symbol\b", request), (
        "the request no longer re-runs the ticker of the report on screen")
    assert re.search(r"\bpersonaKey:\s*report\.agent\.personaKey\b", request), (
        "the re-run's analyst must be the report ON SCREEN's (`report.agent`) — the route's "
        "persona defaults to warren_buffett on a notification with none")
    assert re.search(r"\bownerId:\s*AppActions\.shared\.currentAccountId\b", request), (
        "the request no longer carries the account that tapped")


# ── 4. Declared once, and cleared at session end ─────────────────────────────


def test_app_state_declares_the_request_and_clears_it_at_session_end():
    code = _code(_APP_STATE)
    assert "var pendingResearchRerun: ResearchRerunRequest?" in code, (
        "AppState no longer declares `var pendingResearchRerun: ResearchRerunRequest?`")
    assert re.search(r"\bpendingResearchRerun\s*=\s*nil\b", _block(code, _ENDED)), (
        "the parked re-run is not cleared at session end — one left behind would run, and charge, "
        "for the next account")
    model = _block(_code(_MODELS), "struct ResearchRerunRequest: Equatable")
    for field in ("let ticker: String", "let personaKey: String", "let ownerId: String?"):
        assert field in model, f"ResearchRerunRequest lost `{field}`"


def test_nothing_else_reads_or_writes_the_request():
    """ONE OWNER PER PARKED INTENT: a second reader races the consumer's clear, and a second
    writer is a second door into a 20-credit charge."""
    root = _IOS.parent  # frontend/ios: ios/, Shared/, CaydexWidgets/
    files = sorted(root.rglob("*.swift"))
    assert len(files) > 500 and _CONTENT in files and _REPORT in files, (
        f"scanned {len(files)} Swift files under {root} — the tree moved; this guard is vacuous")
    hits = {}
    for f in files:
        n = len(re.findall(r"\bpendingResearchRerun\b",
                           _strip_swift_comments(f.read_text(encoding="utf-8"))))
        if n:
            hits[str(f.relative_to(root))] = n
    expected = {
        "ios/Core/State/AppState.swift": 2,            # the declaration + the session-end clear
        "ios/Views/Screens/TickerReportView.swift": 1,  # the tap that parks it
        "ios/ContentView.swift": 2,                    # the Research tab's observer + its clear
    }
    assert hits == expected, (
        f"pendingResearchRerun is named in {hits}, expected {expected} — only the report's tap "
        "parks it and only the Research tab consumes it")


# ── 5. The Research tab consumes it once and runs once the stack is clear ────


def test_the_research_tab_consumes_it_once_and_runs_after_the_stack_clears():
    live = _live()
    assert live.count(".onChange(of: appState.pendingResearchRerun") == 1 and _OBSERVER in live, (
        f"the Research tab no longer observes the request exactly once (`{_OBSERVER}`)")
    root = _block(live, "var body: some View")
    assert _OBSERVER in root, "the Run Again observer must sit on `body` itself"
    before = root[: root.index(_OBSERVER)]
    assert before.count("{") - before.count("}") == 1 and before.count("(") == before.count(")"), (
        "the Run Again observer must be a modifier on body's ROOT chain — inside a segment subview "
        "or a conditional it is unmounted part of the time, and a request is lost")

    handler = _block(live, _OBSERVER)
    clear = handler.find("appState.pendingResearchRerun = nil")
    tab = handler.find("selectedTab = .research")
    wait = handler.find("await ModalPresentationProbe.waitUntilNothingPresented()")
    run = handler.find("viewModel.rerunAnalysis(request)")
    assert clear != -1, (
        "the observer does not clear the request it read — consume-and-clear is what makes one "
        "tap run once")
    assert tab != -1, "the Research tab is not brought forward for the run"
    assert run != -1, "the observer no longer hands the request to viewModel.rerunAnalysis"
    assert -1 < wait < run and clear < run and tab < wait, (
        "the run must wait for the presentation stack to clear — the reset that closed the report "
        "nils this view's `error`, and an alert cannot present over a closing cover")
    assert "viewModel.selectedTab" not in handler, (
        "the observer writes the Research segment itself — `generateAnalysis()` moves it to "
        "Reports when the run actually starts")
    assert "applyPrefilledTicker" not in handler and "generateAnalysis" not in handler, (
        "the observer drives the ViewModel past `rerunAnalysis`, skipping its account check")


# ── 6. The ViewModel runs it through Generate, for the account that tapped ───


def test_the_view_model_runs_it_through_generate_for_the_account_that_tapped():
    body = _block(_code(_VM), _RERUN)
    owner = re.search(
        r"guard\s+request\.ownerId\s*==\s*AppActions\.shared\.currentAccountId\s+else\s*\{", body)
    charge = body.find("generateAnalysis()")
    assert owner and charge != -1 and owner.start() < charge, (
        "rerunAnalysis reaches generateAnalysis() without checking the request's account — a "
        "request that crossed an account change would charge whoever holds the session now")
    drop = _balanced(body, owner.end() - 1)
    assert re.search(r"\breturn\b", drop) and not re.search(r"\berror\s*=|AppActions\.", drop), (
        "a request from an ended account must be dropped quietly — no alert for the next one")

    ticker = body.find("applyPrefilledTicker(request.ticker)")
    persona = body.find("selectPersona(persona)")
    assert -1 < ticker < charge and -1 < persona < charge, (
        "rerunAnalysis must set the target and the analyst through their single writers BEFORE "
        "generateAnalysis(), which reads them")
    for direct in (r"\b_?selectedTab\s*=(?!=)", r"\bsearchText\s*=(?!=)",
                   r"\bselectedTarget\s*=(?!=)", r"\bselectedPersona\s*=(?!=)"):
        assert not re.search(direct, body), (
            f"rerunAnalysis writes `{direct}` directly — the chip, the analyst and the segment each "
            "have one writer")
    assert not re.search(r"\bawait\b|\bTask\s*\{", body), (
        "rerunAnalysis suspends — a window between the owner check and the charge in which the "
        "account can change")


# ── The guards above, mutated in memory on every pass ────────────────────────

_T_MENU = test_the_menu_offers_run_again_below_view_detailed_analysis
_T_DETAIL = test_the_line_under_it_names_the_cost_and_the_reports_own_analyst
_T_TAP = test_the_tap_parks_the_request_then_takes_the_stack_down
_T_STATE = test_app_state_declares_the_request_and_clears_it_at_session_end
_T_OWNERS = test_nothing_else_reads_or_writes_the_request
_T_TAB = test_the_research_tab_consumes_it_once_and_runs_after_the_stack_clears
_T_VM = test_the_view_model_runs_it_through_generate_for_the_account_that_tapped

_TEARDOWN = "        appState.dismissAllPresentations()\n    }\n"
_OWNER_GUARD = (
    "        guard request.ownerId == AppActions.shared.currentAccountId else {\n"
    "            print(\"🛑 ResearchVM: Run Again for \\(request.ticker) dropped — the account that "
    "tapped is no longer signed in\")\n"
    "            return\n"
    "        }\n")

# (id, file, ((anchor, replacement), ...), guard, the assertion message the guard must fail WITH).
_MUTATIONS = [
    ("label-renamed", _HEADER,
     (('Label("Run Again", systemImage: "arrow.clockwise")',
       'Label("Regenerate", systemImage: "arrow.clockwise")'),),
     _T_MENU, "the report menu no longer offers Run Again"),
    ("cost-line-dropped", _HEADER,
     (("                    Text(runAgainDetail)\n", ""),),
     _T_MENU, "Run Again lost its second line"),
    ("cost-dropped-from-detail", _REPORT,
     (('return "\\(analyst) · \\(AnalysisCost.standard.credits) credits"', 'return analyst'),),
     _T_DETAIL, "the Run Again line no longer states the cost"),
    ("handler-unwired", _REPORT,
     (("onRunAgain: handleRunAgain,", "onRunAgain: {},"),),
     _T_DETAIL, "the report screen no longer wires Run Again"),
    ("no-teardown", _REPORT,
     ((_TEARDOWN, "    }\n"),),
     _T_TAP, "never asks the presentation stack to come down"),
    ("teardown-first", _REPORT,
     (("    private func handleRunAgain() {\n"
       "        guard let report = viewModel.reportData, !report.symbol.isEmpty else { return }\n",
       "    private func handleRunAgain() {\n"
       "        appState.dismissAllPresentations()\n"
       "        guard let report = viewModel.reportData, !report.symbol.isEmpty else { return }\n"),
      (_TEARDOWN, "    }\n")),
     _T_TAP, "the request is parked AFTER the teardown"),
    ("self-dismiss", _REPORT,
     ((_TEARDOWN, "        appState.dismissAllPresentations()\n        dismiss()\n    }\n"),),
     _T_TAP, "Run Again dismisses itself too"),
    ("route-persona", _REPORT,
     (("personaKey: report.agent.personaKey,", 'personaKey: "warren_buffett",'),),
     _T_TAP, "the re-run's analyst must be the report ON SCREEN's"),
    ("no-owner-stamp", _REPORT,
     (("ownerId: AppActions.shared.currentAccountId", "ownerId: nil"),),
     _T_TAP, "the request no longer carries the account that tapped"),
    ("not-cleared-at-session-end", _APP_STATE,
     (("        pendingResearchRerun = nil\n", ""),),
     _T_STATE, "the parked re-run is not cleared at session end"),
    ("second-reader", _CONTENT,
     (("            appState.pendingResearchRerun = nil\n",
       "            appState.pendingResearchRerun = nil\n"
       "            _ = appState.pendingResearchRerun\n"),),
     _T_OWNERS, "pendingResearchRerun is named in"),
    ("consumer-does-not-clear", _CONTENT,
     (("            appState.pendingResearchRerun = nil\n", ""),),
     _T_TAB, "the observer does not clear the request it read"),
    ("no-wait", _CONTENT,
     (("                _ = await ModalPresentationProbe.waitUntilNothingPresented()\n", ""),),
     _T_TAB, "the run must wait for the presentation stack to clear"),
    ("observer-writes-segment", _CONTENT,
     (("            selectedTab = .research\n            Task { @MainActor in\n",
       "            selectedTab = .research\n            viewModel.selectedTab = .reports\n"
       "            Task { @MainActor in\n"),),
     _T_TAB, "the observer writes the Research segment itself"),
    ("no-owner-check", _VM,
     ((_OWNER_GUARD, ""),),
     _T_VM, "rerunAnalysis reaches generateAnalysis() without checking the request's account"),
    ("owner-drop-alerts", _VM,
     ((_OWNER_GUARD,
       _OWNER_GUARD.replace("            return\n", "            error = \"Signed out.\"\n            return\n")),),
     _T_VM, "a request from an ended account must be dropped quietly"),
    ("direct-search-text", _VM,
     (("        applyPrefilledTicker(request.ticker)\n", "        searchText = request.ticker\n"),),
     _T_VM, "through their single writers BEFORE"),
    ("suspends-before-charge", _VM,
     (("        selectPersona(persona)\n        generateAnalysis()\n",
       "        selectPersona(persona)\n"
       "        Task { await self.loadCredits(); self.generateAnalysis() }\n"),),
     _T_VM, "rerunAnalysis suspends"),
]


@pytest.mark.parametrize(
    "path,edits,test,message",
    [m[1:] for m in _MUTATIONS],
    ids=[m[0] for m in _MUTATIONS],
)
def test_each_mutation_is_killed(monkeypatch, path, edits, test, message):
    """Each guard above must go red on the regression it names, WITH the message that names
    it. Patched in memory only — other sessions' tests read these Swift files concurrently."""
    real_read_text = pathlib.Path.read_text
    original = real_read_text(path, encoding="utf-8")
    mutated = original
    for old, new in edits:
        assert original.count(old) == 1, (
            f"mutation anchor `{old[:60]}` occurs {original.count(old)} times in {path.name}, "
            "expected exactly once — re-derive this mutation against the new source")
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
