"""Source-scan guards: an Updates feed never outlives its ET day (pre-commit review 2026-11-13).

A web-only Insights card is shown only on the ET day it was written (`web_only_card_visible`).
`UpdatesViewModel.feedCache` had no TTL and `loadIfNeeded` latches once per process, so a
suspended app returning after ET midnight — or one left open on this tab across it, then a chip
tap — kept day D's "24h" card on D+1 while Ask Cay AI, grounded on the server's view, called it
off screen. These pin the fix:

- `feedCacheDay[scope]` is the ET day the scope's page 0 came off the network, written ONLY by
  `loadFeed`'s network success;
- `loadFeed` never serves an entry not dated today (nor any entry on a `fresh` load), so a chip
  tap after midnight and a late writer that put an old entry back both go to the network;
- `reloadFeedIfDayRolledOver` reloads the scope ON SCREEN when its day is past, through
  `selectTab(tab, fresh: true)`, on app foreground (`UpdatesView`) and on a tab return;
- a `fresh` load keeps the scope's rows on screen (no skeleton), like a pull-to-refresh;
- the widget focus drops the scope's entry and reloads an already-open chip fresh;
- an identity change forgets every day.

Comments are stripped and each check is bound to its own declaration (testing.md §3). No Swift
is compiled or run.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_VM = _ROOT / "frontend/ios/ios/ViewModels/UpdatesViewModel.swift"
_VIEW = _ROOT / "frontend/ios/ios/Views/Screens/UpdatesView.swift"
_LOAD_FEED = "private func loadFeed(for tab: NewsFilterTab, force: Bool, fresh: Bool = false) async"


def _strip_comments(src: str) -> str:
    """Drop // and /* */ comments, leaving string literals alone."""
    out, i, n = [], 0, len(src)
    while i < n:
        if src.startswith('"', i):
            j = i + 1
            while j < n and src[j] != '"':
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1])
            i = j + 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j == -1 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


def _read(path: Path) -> str:
    return _strip_comments(path.read_text(encoding="utf-8"))


def _block(src: str, opener: str, *, first: bool = False) -> str:
    """The brace-balanced body that follows ``opener`` (exactly once in ``src``, or its first
    occurrence with ``first``)."""
    if not first:
        assert src.count(opener) == 1, f"{opener!r} appears {src.count(opener)} times"
    assert opener in src, f"{opener!r} not found"
    start = src.index("{", src.index(opener))
    depth = 0
    for k in range(start, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[start:k + 1]
    raise AssertionError(f"unbalanced braces after {opener!r}")


def _squash(src: str) -> str:
    return re.sub(r"\s+", "", src)


def test_only_a_network_feed_dates_its_cache_entry():
    vm = _read(_VM)
    flat = _squash(_block(vm, _LOAD_FEED))
    capture = _squash("let requestDay = SentimentTrendDayParser.etTodayKey()")
    request = _squash("try await apiClient.requestReturningBody(")
    write = _squash("feedCache[scope] = (articles, insightSummary, loadedOffset, hasMorePages)")
    stamp = _squash("feedCacheDay[scope] = requestDay")
    shown = _squash("shownFeedDay = requestDay")
    order = [flat.find(x) for x in (capture, request, write, stamp, shown)]
    assert -1 not in order and order == sorted(order), (
        "loadFeed no longer dates the scope's cache entry, and the feed on screen, with the ET "
        "day taken BEFORE its request (an answer that lands after midnight is the old day's): "
        f"a cached feed from another day cannot be told from today's. Found order {order}"
    )
    assert len(re.findall(r"\bfeedCacheDay\[[^\]]*\]\s*=", vm)) == 1, (
        "feedCacheDay is written outside loadFeed's network success (a page, an enrichment, an "
        "insight re-poll or a cache paint would date an old entry today)"
    )


def test_a_cached_feed_is_served_only_on_its_own_et_day():
    flat = _squash(_block(_read(_VM), _LOAD_FEED))
    drop = _squash(
        "if fresh || (feedCache[scope] != nil && "
        "feedCacheDay[scope] != SentimentTrendDayParser.etTodayKey()) {"
    )
    serve = _squash("if !force, let cached = feedCache[scope] {")
    assert drop in flat and serve in flat and flat.index(drop) < flat.index(serve), (
        "loadFeed no longer drops, BEFORE its cached branch, an entry that is not dated today "
        "(or any entry on a fresh load): a chip tapped after ET midnight serves yesterday's "
        "Insights card from memory"
    )
    between = flat[flat.index(drop):flat.index(serve)]
    assert "feedCache.removeValue(forKey:scope)" in between and \
        "feedCacheDay.removeValue(forKey:scope)" in between, (
            "the stale or fresh entry is no longer removed (with its day) before the cached branch"
        )
    assert "letkeepsShownRows=(force||fresh)&&shownFeedScope==scope" in flat, (
        "a fresh reload of the scope on screen blanks its timeline to a skeleton (it must keep "
        "the rows until the answer replaces them, like a pull-to-refresh)"
    )


def test_select_tab_carries_fresh_to_the_load():
    body = _squash(_block(_read(_VM), "func selectTab(_ tab: NewsFilterTab, fresh: Bool = false)"))
    assert "loadFeed(for:tab,force:false,fresh:fresh)" in body, (
        "selectTab drops its `fresh` flag: a widget focus or a new ET day reloads from memory"
    )


def test_a_new_et_day_reloads_the_feed_on_screen():
    flat = _squash(_block(_read(_VM), "func reloadFeedIfDayRolledOver()"))
    steps = [
        "guardisTabActive,lettab=selectedTab,letshownDay=shownFeedDayelse{return}",
        "lettoday=SentimentTrendDayParser.etTodayKey()",
        "guardshownDay!=todayelse{return}",
        "feedCache.removeAll()",
        "feedCacheDay.removeAll()",
        "deferredPostPaint=nil",
        "selectTab(tab,fresh:true)",
    ]
    positions = [flat.find(s) for s in steps]
    assert -1 not in positions and positions == sorted(positions), (
        "reloadFeedIfDayRolledOver no longer: on the active tab, reads the ET day of the feed on "
        "screen, and when it is past, drops every cached scope and its day, drops the superseded "
        f"load's post-paint work, and reloads the open scope fresh through selectTab. Found:\n  {flat}"
    )
    assert "shownFeedDay=" not in flat, (
        "the rollover moves the on-screen day itself: a failed reload would then read as today's "
        "and never be retried (only a network success may move it)"
    )
    assert "hasLoadedOnce" not in flat, (
        "the rollover waits for hasLoadedOnce, which a failed first load fixed by Try Again never "
        "sets — the rollover would then never run"
    )
    assert "selectionLoad" not in flat and "force:true" not in flat, (
        "the rollover starts its own load: selectTab is the one place a selection load starts"
    )


def test_the_rollover_runs_on_foreground_and_on_a_tab_return():
    view = _read(_VIEW)
    marker = "UIApplication.didBecomeActiveNotification"
    assert view.count(marker) == 1, "the didBecomeActive handler moved or was duplicated"
    handler = _block(view[view.index(marker):], ") { _ in", first=True)
    assert "viewModel.reloadFeedIfDayRolledOver()" in handler, (
        "UpdatesView no longer reloads a feed from an earlier ET day when the app comes back"
    )
    flat = _squash(_block(_read(_VM), "func setTabActive(_ active: Bool)"))
    assert "ifactive{reloadFeedIfDayRolledOver()}" in flat, (
        "a tab return no longer reloads a feed from an earlier ET day"
    )
    # After the flag flips: the rollover guards on isTabActive, so a call before it is a no-op.
    assert flat.index("isTabActive=active") < flat.index("ifactive{reloadFeedIfDayRolledOver()}"), (
        "setTabActive calls the rollover before isTabActive is set: every tab return is a no-op"
    )


def test_the_widget_focus_never_serves_the_chip_from_memory():
    flat = _squash(_block(_read(_VM), "func focusTicker(_ symbol: String) async -> TickerFocusOutcome"))
    steps = [
        "feedCache.removeValue(forKey:tab.scope)",
        "feedCacheDay.removeValue(forKey:tab.scope)",
        "ifselectedTab?.scope==tab.scope{",
        "selectTab(tab,fresh:true)",
        "selectedTab=tab",
    ]
    positions = [flat.find(s) for s in steps]
    assert -1 not in positions and positions == sorted(positions), (
        "focusTicker no longer drops the chip's cached feed before selecting it, or no longer "
        "reloads an already-open chip fresh: a widget tap lands on a feed cached before the "
        f"card that backs the row was written. Found:\n  {flat}"
    )


def test_an_identity_change_forgets_every_day():
    flat = _squash(_block(_read(_VM), "func handleIdentityChange(isActiveTab: Bool) async"))
    assert "feedCache.removeAll()feedCacheDay.removeAll()" in flat, (
        "an identity change keeps the ended account's cache days"
    )


def test_the_on_screen_day_moves_only_with_what_is_painted():
    vm = _read(_VM)
    writes = re.findall(r"\bshownFeedDay\s*=(?!=)\s*([^\n]+)", vm)
    assert sorted(w.strip() for w in writes) == sorted(
        ["requestDay", "feedCacheDay[scope]", "nil", "nil", "nil"]
    ), (
        "shownFeedDay is set somewhere other than: a network paint (requestDay), a cached paint "
        "(that entry's day), a cleared timeline, dropped rows and an identity change (nil). "
        f"Found {writes}"
    )
    load = _block(vm, _LOAD_FEED)
    clear = _block(load, "if !keepsShownRows && (snapshotSavedAt == nil || scope != UpdatesScope.market) {")
    assert "shownFeedDay = nil" in clear, "clearing the rows no longer forgets the on-screen day"
    cached = _block(load, "if !force, let cached = feedCache[scope] {")
    assert "shownFeedDay = feedCacheDay[scope]" in cached, (
        "a cached paint no longer dates the screen with its entry's day"
    )


def test_dropped_rows_forget_the_on_screen_day():
    body = _squash(_block(_read(_VM), "private func dropShownRows()"))
    assert "shownFeedDay=nil" in body, (
        "dropping the rows (a sign-in refusal) keeps the old day, so each later foreground "
        "starts a reload for a feed that is no longer on screen"
    )


def test_ask_cay_never_opens_on_a_feed_from_an_earlier_et_day():
    vm = _read(_VM)
    prop = _squash(_block(vm, "var feedIsFromEarlierETDay: Bool"))
    assert "guardletday=shownFeedDayelse{returnfalse}" in prop and \
        "returnday!=SentimentTrendDayParser.etTodayKey()" in prop, (
            "feedIsFromEarlierETDay no longer compares the on-screen day with today's ET day"
        )
    chat = _squash(_block(_read(_VIEW), "private func openUpdatesChat(focus: UpdatesChatFocus)"))
    gate = "guard!viewModel.feedIsFromEarlierETDayelse{askAfterReloadingFeed(focus:focus,scope:tab.scope)return}"
    assert gate in chat and chat.index(gate) < chat.index("updatesChat.prepareGroundedConversation("), (
        "Ask Cay AI opens on yesterday's card (possibly a web-only card the server no longer "
        "shows; the turn is charged and the chat calls it off screen) instead of reloading first"
    )


# ── C13 (round 5): the ask waits for the reload, visibly, and then opens ─────────────────────


_ASK = "private func askAfterReloadingFeed(focus: UpdatesChatFocus, scope: String)"


def test_an_ask_on_an_old_feed_reloads_then_opens_the_chat_once():
    """The tap after ET midnight used to start a silent reload (the rows stay, so no skeleton)
    and open nothing; from the detail sheet it only closed the sheet."""
    flat = _squash(_block(_read(_VIEW), _ASK))
    steps = [
        "guarddayRolloverAskID==nilelse{return}",
        "letid=UUID()",
        "dayRolloverAskID=id",
        "letisToday=awaitviewModel.reloadFeedBeforeAskCay(scope:scope)",
        "guarddayRolloverAskID==idelse{return}",
        "dayRolloverAskID=nil",
        "guardisTodayelse{return}",
        "guardviewModel.insightSummary!=nilelse{showNoTodayCardNotice(scope:scope)return}",
        "openUpdatesChat(focus:focus)",
    ]
    positions = [flat.find(x) for x in steps]
    assert -1 not in positions and positions == sorted(positions), (
        "askAfterReloadingFeed no longer: refuses a second ask while one waits, records the "
        "ask, awaits the reload, drops an ask that was reset meanwhile, and opens the chat "
        f"only on today's feed that still has its card. Found:\n  {flat}"
    )
    assert flat.count("openUpdatesChat(") == 1, "the ask opens the chat on more than one path"


def test_the_reload_before_an_ask_is_the_pull_to_refresh_and_checks_what_landed():
    flat = _squash(_block(_read(_VM), "func reloadFeedBeforeAskCay(scope: String) async -> Bool"))
    steps = [
        "letgeneration=identityGeneration",
        "awaitrefresh()",
        "returngeneration==identityGeneration",
        "&&isTabActive",
        "&&selectedTab?.scope==scope",
        "&&shownFeedScope==scope",
        "&&snapshotSavedAt==nil",
        "&&shownFeedDay==SentimentTrendDayParser.etTodayKey()",
    ]
    positions = [flat.find(x) for x in steps]
    assert -1 not in positions and positions == sorted(positions), (
        "reloadFeedBeforeAskCay no longer reloads through refresh() (joins a running refresh, "
        "never skipped as a duplicate of a load still on the wire) and reports a feed of the same "
        "scope, identity and active tab, dated today, as the only success — a chat could open "
        f"on another chip, another account, a snapshot or yesterday's card. Found:\n  {flat}"
    )
    assert "!feedIsFromEarlierETDay" not in flat, (
        "success read as 'not from an earlier day', which is also true with NO feed on screen"
    )


def test_a_waiting_ask_is_shown_and_dropped_with_the_screen():
    view = _read(_VIEW)
    body = _block(_block(view, "struct UpdatesView: View"), "var body: some View", first=True)
    flat = _squash(body)
    notice = _squash(
        'if dayRolloverAskID != nil { InlineRetryNotice( message: "Updating the news for today…",'
    )
    failure = _squash("} else if viewModel.showsSnapshotRefreshFailure {")
    assert notice in flat and failure in flat and flat.index(notice) < flat.index(failure), (
        "a waiting ask shows nothing (the tap looks dead), or its notice left the notice slot"
    )
    shown = _block(body, "if dayRolloverAskID != nil {")
    assert "onRetry" not in shown, "the waiting notice offers a retry over a reload already running"
    reset = _squash(_block(body, ".onPresentationReset"))
    assert "dayRolloverAskID=nil" in reset, (
        "a presentation reset (a widget tap) leaves a waiting ask that opens a chat over it"
    )
    identity = _squash(_block(body, ".reloadOnIdentityChange { isActive in"))
    assert "dayRolloverAskID=nil" in identity, (
        "an identity change leaves the previous account's waiting ask on screen"
    )
    assert len(re.findall(r"\bdayRolloverAskID\s*=\s*id\b", view)) == 1, (
        "an ask is recorded somewhere other than askAfterReloadingFeed"
    )


def test_the_detail_sheet_ask_takes_the_same_path():
    pending = _squash(_block(_read(_VIEW), "private func presentPendingChat()"))
    assert "openUpdatesChat(focus:focus)" in pending, (
        "the detail sheet's ask bypasses openUpdatesChat, and with it the day-rollover wait"
    )


# ── C14 (round 5): a failed reload of an earlier day's rows hides their Insights card ────────


def test_a_failed_reload_of_an_earlier_day_hides_the_card_but_keeps_the_rows():
    load = _block(_read(_VM), _LOAD_FEED)
    catch = load[load.index("} catch {"):]
    other = _block(catch, "} else {", first=True)
    flat = _squash(other)
    guard = _squash("if keepsShownRows, let shownDay = shownFeedDay, shownDay != requestDay {")
    assert guard in flat, (
        "a failed reload no longer recognises rows from an earlier ET day: a web-only card the "
        "server stopped showing stays on D+1 beside them"
    )
    hide = _squash(_block(other, "if keepsShownRows, let shownDay = shownFeedDay, shownDay != requestDay {"))
    for token in ("insightSummary=nil", "feedCache.removeValue(forKey:scope)",
                  "feedCacheDay.removeValue(forKey:scope)"):
        assert token in hide, f"the earlier day's card is not fully dropped: missing `{token}`"
    for token in ("allNewsArticles=", "groupedNews=", "dropShownRows()", "shownFeedDay=",
                  "self.error=nil", "error=nil"):
        assert token not in hide, (
            f"the earlier day's failed reload does more than hide the card (`{token}`): the rows, "
            "the failure note and the old day (so the next foreground retries) must stay"
        )
    # The error is recorded first, so the kept rows still say the refresh failed.
    assert flat.index("self.error=appError.message") < flat.index(guard)


def test_the_delayed_ask_never_opens_the_chat_over_another_presentation():
    view = _read(_VIEW)
    ask = _squash(_block(view, "private func askAfterReloadingFeed(focus: UpdatesChatFocus, scope: String)"))
    defer = ("ifsourcesSheetUp{pendingChatFocus=focus"
             "sheetAskFeed=(scope:scope,day:viewModel.shownFeedDay)return}")
    guard = "guard!isPresentingSomethingelse{return}"
    opened = "openUpdatesChat(focus:focus)"
    assert defer in ask and guard in ask and opened in ask, (
        "the delayed Ask no longer defers to the sources sheet or refuses to open over another "
        f"presentation. Found:\n  {ask}"
    )
    assert ask.index(defer) < ask.index(guard) < ask.index(opened)
    presenting = _squash(_block(view, "private var isPresentingSomething: Bool"))
    for flag in ("sourcesSheetUp", "browserLink!=nil", "showSearch", "showProfile", "showManageAssetsSheet",
                 "viewModel.showPaywall", "viewModel.showFilterSheet", "showUpdatesChat"):
        assert flag in presenting, f"isPresentingSomething no longer checks {flag}"


# ── Round 6: the sources sheet's ask, the no-card answer, the identity change ────────────────


_PENDING = "private func presentPendingChat()"
_OPEN_SOURCES = "private func openInsightSources(_ summary: NewsInsightSummary)"


def _view_body() -> str:
    view = _read(_VIEW)
    return _block(_block(view, "struct UpdatesView: View"), "var body: some View", first=True)


def test_the_sources_sheet_records_the_feed_its_card_belongs_to():
    """Item 6: the sheet's card cannot be compared (`NewsInsightSummary.id` is a fresh UUID per
    decode), so the scope and ET day on screen are recorded when the sheet opens."""
    view = _read(_VIEW)
    assert "onOpenSources:{openInsightSources(summary)}" in _squash(_view_body()), (
        "the Insights card opens its sources sheet without recording the feed its card belongs to"
    )
    opener = _squash(_block(view, _OPEN_SOURCES))
    record = "sheetAskFeed=viewModel.selectedTab.map{(scope:$0.scope,day:viewModel.shownFeedDay)}"
    assert record in opener and "insightSources=summary" in opener and \
        opener.index(record) < opener.index("insightSources=summary"), (
            f"openInsightSources no longer records the scope and day before opening. Found:\n  {opener}"
        )
    opens = [v for v in re.findall(r"\binsightSources\s*=(?!=)\s*(\w+)", view) if v != "nil"]
    assert opens == ["summary"], (
        f"the sources sheet opens somewhere other than openInsightSources: {opens}"
    )
    writes = sorted(w.strip() for w in re.findall(r"\bsheetAskFeed\s*=(?!=)\s*([^\n]+)", view))
    assert writes == sorted([
        "viewModel.selectedTab.map { (scope: $0.scope, day: viewModel.shownFeedDay) }",
        "(scope: scope, day: viewModel.shownFeedDay)",
        "nil", "nil",
    ]), f"sheetAskFeed is written somewhere unexpected: {writes}"
    vm = _read(_VM)
    assert re.search(r"\bprivate\(set\)\s+var\s+shownFeedDay:\s*String\?", vm), (
        "shownFeedDay is not read-only outside the ViewModel"
    )
    assert not re.search(r"viewModel\.shownFeedDay\s*=(?!=)", view), "the view writes shownFeedDay"


def test_a_sheet_ask_opens_only_on_the_feed_it_was_made_against():
    """Item 6: open D's sheet, cross ET midnight, the rollover reload removes or replaces the
    card, tap Ask in the still-open sheet — the chat used to open about a card that was gone."""
    flat = _squash(_block(_read(_VIEW), _PENDING))
    steps = [
        "letaskedFeed=sheetAskFeed",
        "sheetAskFeed=nil",
        "guardletfocus=pendingChatFocuselse{return}",
        "pendingChatFocus=nil",
        "guarddayRolloverAskID==nilelse{return}",
        "guardlettab=viewModel.selectedTab,letasked=askedFeed,asked.scope==tab.scopeelse{return}",
        "guardviewModel.insightSummary!=nil,!viewModel.feedIsFromEarlierETDay,"
        "letaskedDay=asked.day,viewModel.shownFeedDay==askedDayelse{"
        "askAfterReloadingFeed(focus:focus,scope:tab.scope)return}",
        "openUpdatesChat(focus:focus)",
    ]
    positions = [flat.find(x) for x in steps]
    assert -1 not in positions and positions == sorted(positions), (
        "presentPendingChat no longer: forgets the recorded feed on every dismissal, drops a sheet "
        "ask while another ask waits on a reload (one at a time), drops one made against another "
        "scope or with nothing recorded, and sends an ask on a card that is gone, from an earlier "
        "day, or made on another day than the one on screen through the reload path. "
        f"Found:\n  {flat}"
    )
    # The honest twin: a same-day ask on a feed that still has its card opens the chat at once —
    # the reload path is the guard's else-branch only, never every sheet ask.
    assert flat.count("askAfterReloadingFeed(") == 1 and flat.count("openUpdatesChat(") == 1
    tail = flat[flat.index(steps[-2]) + len(steps[-2]):]
    assert tail.startswith("openUpdatesChat(focus:focus)"), (
        "a same-day sheet ask on a feed with its card no longer opens the chat directly"
    )


def test_a_pill_ask_that_lands_under_the_sheet_records_the_feed_it_verified():
    """Item 6, single flight: a pill ask that waits on a reload while the sheet opens queues its
    chat for the sheet's dismissal, against the feed its reload verified (not the day the sheet
    opened on, which would send it through a second reload)."""
    ask = _squash(_block(_read(_VIEW), _ASK))
    restamp = ("ifsourcesSheetUp{pendingChatFocus=focus"
               "sheetAskFeed=(scope:scope,day:viewModel.shownFeedDay)return}")
    verified = "guardisTodayelse{return}"
    assert verified in ask and restamp in ask and ask.index(verified) < ask.index(restamp), (
        "the pill ask queues its chat under the sheet without recording the feed it verified"
    )


def test_an_ask_whose_reload_finds_no_card_says_so():
    """Item 7: the "Updating…" notice disappeared, the card disappeared, and nothing said why."""
    view = _read(_VIEW)
    ask = _squash(_block(view, _ASK))
    assert "noTodayCardNotice=nil" in ask and \
        ask.index("noTodayCardNotice=nil") < ask.index("letisToday="), (
            "a new ask does not clear the previous no-card line"
        )
    failed = "guardisTodayelse{return}"
    nocard = "guardviewModel.insightSummary!=nilelse{showNoTodayCardNotice(scope:scope)return}"
    assert failed in ask and nocard in ask and ask.index(failed) < ask.index(nocard), (
        "an ask whose reload landed today's feed with no card is silent again"
    )
    # Honest twin: a FAILED reload shows the refresh's failure notice, never "no card today".
    assert ask.count("showNoTodayCardNotice(") == 1

    body = _squash(_view_body())
    waiting = _squash('if dayRolloverAskID != nil { InlineRetryNotice( message: "Updating the news for today…",')
    branch = _squash(
        "} else if let notice = noTodayCardNotice, notice.scope == viewModel.selectedTab?.scope, "
        "viewModel.insightSummary == nil {"
    )
    failure = _squash("} else if viewModel.showsSnapshotRefreshFailure {")
    order = [body.find(x) for x in (waiting, branch, failure)]
    assert -1 not in order and order == sorted(order), (
        "the no-card line left the notice slot, or shows on another scope or beside a card "
        f"that has since landed. Found order {order}"
    )
    shown = _squash(_block(_view_body(), "} else if let notice = noTodayCardNotice,"))
    assert "InlineRetryNotice(message:noTodayCardMessage(scope:notice.scope)" in shown, (
        "the no-card line no longer renders the code-owned message through InlineRetryNotice"
    )
    assert "onRetry" not in shown and "iconColor:AppColors.textMuted" in shown, (
        "the no-card line reads as a failure (retry button or caution icon)"
    )

    message = _block(view, "private func noTodayCardMessage(scope: String) -> String")
    literals = re.findall(r'"((?:[^"\\]|\\.)*)"', message)
    assert literals == ["No Insights card for the market today yet.",
                        r"No Insights card for \(scope) today yet."], (
        f"the no-card line is not the code-owned sentence (never a backend string): {literals}"
    )

    timer = _squash(_block(view, "private func showNoTodayCardNotice(scope: String)"))
    steps = ["letid=UUID()", "noTodayCardNotice=(id:id,scope:scope)", "Task.sleep(",
             "ifnoTodayCardNotice?.id==id{noTodayCardNotice=nil}"]
    positions = [timer.find(x) for x in steps]
    assert -1 not in positions and positions == sorted(positions), (
        f"the no-card line no longer clears itself (only its own instance). Found:\n  {timer}"
    )
    writes = re.findall(r"\bnoTodayCardNotice\s*=(?!=)\s*([^\n]+)", view)
    assert sorted(w.strip() for w in writes) == sorted(
        ["(id: id, scope: scope)", "nil", "nil", "nil"]
    ), f"noTodayCardNotice is written somewhere unexpected: {writes}"


def test_an_identity_change_drops_every_pending_ask():
    """Item 8: the sources sheet could stay up across a sign-out with its ask pending."""
    identity = _squash(_block(_view_body(), ".reloadOnIdentityChange { isActive in"))
    run = _squash(_block(identity, "awaitMainActor.run"))
    for token in ("dayRolloverAskID=nil", "noTodayCardNotice=nil", "pendingChatFocus=nil",
                  "sheetAskFeed=nil", "insightSources=nil"):
        assert token in run, f"an identity change keeps the previous account's `{token.split('=')[0]}`"
    assert run.index("pendingChatFocus=nil") < run.index("insightSources=nil"), (
        "the sheet is closed before its pending ask is dropped: its onDismiss opens a chat"
    )
    assert identity.index("awaitMainActor.run") < identity.index("awaitviewModel.handleIdentityChange("), (
        "the pending asks are dropped after the new identity's load starts"
    )


def test_the_sources_sheet_counts_as_up_until_its_on_dismiss_runs():
    """The binding is nil while the sheet is still animating closed: a chat presented in that
    window could land on a sheet still on screen. Only `presentPendingChat` (the onDismiss)
    clears the flag, and only `openInsightSources` sets it."""
    view = _read(_VIEW)
    sets = re.findall(r"(?<!var )\bsourcesSheetUp\s*=\s*(true|false)", view)   # not the declaration
    assert sorted(sets) == ["false", "true"], sets
    opener = _squash(_block(view, "private func openInsightSources(_ summary: NewsInsightSummary)"))
    assert "sourcesSheetUp=true" in opener
    dismiss = _squash(_block(view, "private func presentPendingChat()"))
    assert dismiss.startswith("{sourcesSheetUp=false"), dismiss[:80]
    assert ".sheet(item:$insightSources,onDismiss:presentPendingChat)" in _squash(view)
