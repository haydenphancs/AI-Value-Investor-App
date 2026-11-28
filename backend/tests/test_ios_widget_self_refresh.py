"""Source-scan guards: the widget refreshes ITSELF, on a market-hours cadence.

TestFlight, build 1.0 (3): *"Check widget, it doesn't automatically update new
information."* Correct, and there were TWO independent causes — fixing either alone
leaves the tile frozen, which is why both are pinned here.

**1. The extension never fetched.** Verified in git history, not just the tree:
`URLSession` had never appeared under `CaydexWidgets/` or `Shared/`, and `BGTaskScheduler`
has never existed anywhere in this repo. The widget was a pure renderer of an App Group
blob that only the app writes, on cold launch / foreground / sign-in. Miss a day of
opening the app and the tile showed yesterday's numbers for a day.

**2. The reload policy said "not until tomorrow".** `Timeline(policy: .after(next))` where
`next` was the next 00:01. WidgetKit was explicitly told not to ask again for the rest of
the day, and the last scheduled entry was only +180m — so past three hours even the
session LABEL stopped ageing. This one hid behind a comment about label-ageing that read
like the whole story.

**3. 🔴 THE PREMISE OF THIS FILE WAS FALSE FOR A DAY.** It used to read: *"`/widget/
market-mover` takes no identity at all, so the extension may call it."* The account-only
redesign (2026-09-07) put `Depends(get_current_user_id)` on the widget router, and from
that moment every extension fetch answered 401 — cause 1 was back, exactly as described
above. **All sixteen tests here kept passing**, because every one is a pure source scan:
they proved the CALL SITE existed and could not see that the call could never succeed.
`test_ios_auth_policy_parity.py` could not see it either — the extension bypasses
`APIClient`, so this request has no `APIEndpoint` case to pair with a backend dependency.

That is the shape of vacuity `.claude/rules/testing.md` §3 warns about, arrived at from
the other direction: not a scan that stopped matching, but a scan whose match stopped
meaning anything. `tests/test_widget_token_auth.py` now covers it behaviourally — it
issues the real request and asserts the status code — and this file keeps only the
questions a scan can honestly answer.

**4. HOLDINGS NOW FETCHES TOO (2026-11-27) — WITHOUT A SESSION.** The owner reported the
Holdings tile out of step with the Updates card until the app was opened: only Market
fetched. The extension authenticates market mode with a WIDGET TOKEN (long-lived, scoped to
one market-wide route) and holdings mode with a per-device HOLDINGS GRANT — an opaque key
minted only inside a refresh-token rotation, revoked server-side with its session, held in a
ThisDeviceOnly Keychain item, good for `/widget/holdings-mover` alone. The reasons the
extension must never hold a SESSION all still stand — `auth.md` §8 (the client token and the
Keychain deliberately diverge during `.restoring`), no refresh from an extension, and that
giving `GuestIdentity` a Keychain access group would orphan that install's data — so the
grant is not one, and `portfolio-mover` (the app's session route) is still never called from
here. The grant's own guards are `tests/test_ios_widget_holdings_grant.py`; these scans pin
only that both fetches are WIRED and GATED.

The CADENCE arithmetic is proven separately and properly by
`frontend/ios/scripts/widget-refresh-schedule-check.sh`, which compiles the real
`WidgetRefreshSchedule.swift` and asserts every session boundary, the weekend skip, that
no minute of four days yields a past date, and that a trading day stays inside WidgetKit's
refresh allowance. These scans pin only that it is WIRED UP.

Comments are stripped before every assertion — the comments beside this change quote
`URLSession`, `reloadTimelines` and "does not fetch" verbatim (`.claude/rules/testing.md`
§3). `test_the_scanners_are_not_vacuous` proves the helpers bite.
"""

import base64
import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_IOS = _ROOT / "frontend/ios"
_WIDGET = _IOS / "CaydexWidgets/MoversWidget.swift"
_INTENT = _IOS / "CaydexWidgets/MoversConfigurationIntent.swift"
_FETCHER = _IOS / "Shared/WidgetMarketFetcher.swift"
_HOLDINGS_FETCHER = _IOS / "Shared/WidgetHoldingsFetcher.swift"
_SCHEDULE = _IOS / "Shared/WidgetRefreshSchedule.swift"
_STORE = _IOS / "Shared/WidgetSnapshotStore.swift"
_APICONFIG = _IOS / "Shared/WidgetAPIConfig.swift"
_APPSTATE = _IOS / "ios/Core/State/AppState.swift"
_SCHEDULE_HARNESS = _IOS / "scripts/widget-refresh-schedule-check.sh"


def _strip_comments(src: str) -> str:
    out = []
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _decl_block(src: str, header: str) -> str:
    """The brace-balanced body after `header`, comments stripped FIRST.

    Stripping before counting matters: a `{` or `}` inside a `//` comment used to be
    counted, so a comment could end (or extend) the block the scan believed it was reading.
    """
    src = _strip_comments(src)
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace : i + 1]
    pytest.fail(f"unbalanced braces after {header!r}")


def _timeline() -> str:
    return _decl_block(_WIDGET.read_text(), "func timeline(for configuration:")


# ── 1. The extension actually fetches ─────────────────────────────────


def test_the_timeline_fetches_instead_of_only_reading_the_stored_blob():
    body = _timeline()
    assert "await WidgetMarketFetcher.fetchMarket()" in body, (
        "the timeline no longer fetches — the tile is back to changing only when the "
        "app is foregrounded, which is the reported bug"
    )


# The gate, pinned STRUCTURALLY. Anchored on `if mode == .market ,` running straight into
# `let fresh = await …fetchMarket()`, so no `||`, `&&` or `!(` can sit between them.
_FETCH_GATE = re.compile(
    r"\bif\s+mode\s*==\s*\.market\s*,\s*let\s+fresh\s*=\s*await\s+"
    r"WidgetMarketFetcher\.fetchMarket\(\)"
)


def _fetch_gate_problems(body: str) -> list[str]:
    problems = []
    calls = body.count("WidgetMarketFetcher.fetchMarket()")
    if calls != 1:
        problems.append(f"expected exactly ONE fetchMarket() call in timeline(), found {calls}")
    if not _FETCH_GATE.search(body):
        problems.append(
            "the call is not `if mode == .market, let fresh = await "
            "WidgetMarketFetcher.fetchMarket()` (a guard/switch refactor must update this scan)"
        )
    return problems


def test_the_fetch_is_gated_on_market_mode():
    """The widget token reaches the market route only; Holdings has its own fetch, below.

    This used to assert only that `mode == .market` appeared somewhere BEFORE the call, which
    passed with the gate widened to `|| mode == .portfolio`, made a tautology, or joined by a
    second, ungated call. The mutations below prove the rewrite catches all of those.
    """
    body = _timeline()
    assert _fetch_gate_problems(body) == [], (
        "the market fetch is no longer restricted to market mode — a Holdings tile would "
        f"render market data under its header: {_fetch_gate_problems(body)}"
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.replace("if mode == .market,", "if mode == .market || mode == .portfolio,"),
        lambda b: b.replace("if mode == .market,", "if true || mode == .market,"),
        lambda b: b.replace("if mode == .market,", "if !(mode == .market),"),
        lambda b: b.replace("if mode == .market,", "if mode == .portfolio,"),
        lambda b: b + "\nlet extra = await WidgetMarketFetcher.fetchMarket()\n",
    ],
    ids=["or-portfolio", "or-true", "negated", "wrong-mode", "second-ungated-call"],
)
def test_the_fetch_gate_scan_bites(mutate):
    """MUTATION_LOG, executable: each plausible widening of the gate must turn the scan red."""
    body = _timeline()
    mutated = mutate(body)
    assert mutated != body, "the mutation did not apply — the source moved under this test"
    assert _fetch_gate_problems(mutated), "a widened fetch gate passed the scan"


# The Holdings fetch, pinned the same way: ONE call, inside `if mode == .portfolio {`, behind a
# LIVE grant read off the main thread, not already refused, whose account owns the stored slot —
# all of it captured before the await, and running straight into the call.
_HOLDINGS_GATE = re.compile(
    r"\bif\s+mode\s*==\s*\.portfolio\s*\{\s*"
    r"let\s+held\s*=\s*await\s+Task\.detached\([^)]*\)\s*\{\s*WidgetHoldingsGrant\.lookup\(\)\s*\}\.value\s*"
    r"if\s+case\s+\.live\(let\s+grantAtFetch\)\s*=\s*held\s*,"
    r"\s*!WidgetHoldingsGrant\.isRefused\(grantAtFetch\.fp\)\s*,"
    r"\s*let\s+ownerAtFetch\s*=\s*WidgetSnapshotStore\.portfolioOwner\(\)\s*,"
    r"\s*ownerAtFetch\.lowercased\(\)\s*==\s*grantAtFetch\.userId\.lowercased\(\)\s*\{\s*"
    r"switch\s+await\s+WidgetHoldingsFetcher\.fetchHoldings\(grantAtFetch\)"
)


def _holdings_gate_problems(body: str) -> list[str]:
    problems = []
    calls = body.count("WidgetHoldingsFetcher.fetchHoldings(")
    if calls != 1:
        problems.append(f"expected exactly ONE fetchHoldings( call in timeline(), found {calls}")
    if not _HOLDINGS_GATE.search(body):
        problems.append(
            "the Holdings fetch is not `if mode == .portfolio {` → an off-main grant lookup → "
            "`if case .live(let grantAtFetch) = held, !isRefused, <slot owner == grant user> {` → "
            "`switch await WidgetHoldingsFetcher.fetchHoldings(grantAtFetch)`"
        )
    return problems


def test_the_holdings_fetch_is_gated_on_holdings_mode_and_a_live_grant():
    """The extension fetches Holdings only with a grant for the account the APP wrote the slot
    for. Without the owner gate a grant left from an earlier account would refresh into the
    current account's tile; without the refusal gate a dead key spends a request per reload."""
    body = _timeline()
    assert _holdings_gate_problems(body) == [], _holdings_gate_problems(body)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.replace("if mode == .portfolio {", "if true || mode == .portfolio {"),
        lambda b: b.replace("if mode == .portfolio {", "if mode == .market {"),
        lambda b: b.replace("!WidgetHoldingsGrant.isRefused(grantAtFetch.fp),", ""),
        lambda b: b.replace("ownerAtFetch.lowercased() == grantAtFetch.userId.lowercased() {", "true {"),
        lambda b: b + "\nlet extra = await WidgetHoldingsFetcher.fetchHoldings(grant)\n",
    ],
    ids=["or-true", "swapped-mode", "refusal-not-checked", "owner-gate-dropped", "second-ungated-call"],
)
def test_the_holdings_gate_scan_bites(mutate):
    body = _timeline()
    mutated = mutate(body)
    assert mutated != body, "the mutation did not apply — the source moved under this test"
    assert _holdings_gate_problems(mutated), "a widened Holdings fetch gate passed the scan"


def test_only_the_market_route_is_ever_called():
    """Renamed from `test_only_the_public_route_is_ever_called` — there is no public route
    any more, and the old name is what made the false premise above read as settled fact."""
    src = (
        _strip_comments(_APICONFIG.read_text()) + _strip_comments(_FETCHER.read_text())
        + _strip_comments(_HOLDINGS_FETCHER.read_text()) + _strip_comments(_WIDGET.read_text())
    )
    assert "market-mover" in src and "holdings-mover" in src
    assert "portfolio-mover" not in src, (
        "the extension now references the portfolio route, which requires a session "
        "it cannot hold — and which neither of its credentials can reach"
    )
    assert '"Authorization"' not in src, (
        "the extension sets Authorization — neither the widget token nor the grant is a bearer"
    )


def test_the_extension_fetch_is_authenticated():
    """The half a source scan CAN still prove: the request carries a credential.

    Whether that credential is ACCEPTED is a behavioural question and lives in
    `tests/test_widget_token_auth.py`. Both halves are needed — this one fails if someone
    strips the header, that one fails if the backend stops honouring it.
    """
    body = _decl_block(_strip_comments(_FETCHER.read_text()), "func fetchMarket()")
    assert "forHTTPHeaderField: WidgetAPIConfig.tokenHeader" in body, (
        "the extension's request goes out with no credential — the route answers 401 and the "
        "tile silently freezes on whatever the app last wrote, with no error surface anywhere"
    )
    holdings = _decl_block(_HOLDINGS_FETCHER.read_text(), "private static func holdingsRequest(")
    assert "forHTTPHeaderField: WidgetAPIConfig.holdingsTokenHeader" in holdings, (
        "the Holdings request goes out without the grant — every refresh answers 401 AUTH_REQUIRED"
    )


def test_a_failed_fetch_falls_back_to_the_stored_snapshot():
    """A Home Screen tile has no error state, no spinner and no retry button."""
    body = _timeline()
    assert "var snap = snapshot(for: mode)" in body, (
        "the stored snapshot is no longer the starting value, so a failed fetch would "
        "render an empty tile instead of an older-but-real one"
    )
    fetcher = _decl_block(_FETCHER.read_text(), "public static func fetchMarket()")
    assert "return nil" in fetcher and "catch" in fetcher, (
        "the fetcher no longer degrades to nil on error"
    )


# ── 2. The cadence is wired, and cannot loop ──────────────────────────


# The ONE reload call, with the final snapshot's phase hint (2026-11-27: a holiday or a half day
# the server has already called closed puts the rest of the ET day on the 90-minute cadence).
_RELOAD_CALL = re.compile(
    r"let\s+reload\s*=\s*WidgetRefreshSchedule\.nextRefresh\(\s*after:\s*now,\s*"
    r"marketSession:\s*snap\?\.marketSession,\s*builtAt:\s*snap\?\.asOf\s*\)"
)


def _reload_policy_problems(body: str) -> list[str]:
    problems = []
    calls = body.count("WidgetRefreshSchedule.nextRefresh(")
    if calls != 1:
        problems.append(f"expected exactly ONE nextRefresh( call in timeline(), found {calls}")
    m = _RELOAD_CALL.search(body)
    if not m:
        problems.append(
            "the reload is not `let reload = WidgetRefreshSchedule.nextRefresh(after: now, "
            "marketSession: snap?.marketSession, builtAt: snap?.asOf)` — the closed-for-the-day "
            "hint no longer reaches the schedule"
        )
    else:
        # Decided only once `snap` is final: after BOTH fetches, before the entry dates.
        for fetch in ("WidgetMarketFetcher.fetchMarket()", "WidgetHoldingsFetcher.fetchHoldings("):
            at = body.find(fetch)
            if at == -1 or at > m.start():
                problems.append(f"the reload is decided before (or without) {fetch}")
        dates = body.find("WidgetRefreshSchedule.renderDates(now: now, reload: reload)")
        if dates == -1 or dates < m.end():
            problems.append("the entry dates are built before the reload is known")
    if "policy: .after(reload)" not in body:
        problems.append(
            "the policy no longer uses the computed reload date. It used to be the last "
            "ENTRY's date, which was the next 00:01 — WidgetKit was told to go away until "
            "tomorrow, and nothing could wake the extension during the day."
        )
    return problems


def test_the_reload_policy_is_the_market_hours_schedule():
    """Re-anchored 2026-11-27: it used to look for the literal `nextRefresh(after: now)`, which
    the hint-carrying call no longer contains (and which a signed-out return could satisfy on
    its own). The schedule's own arithmetic is pinned by `widget-refresh-schedule-check.sh` and
    `tests/test_ios_widget_schedule_footer.py`."""
    body = _timeline()
    assert _reload_policy_problems(body) == [], _reload_policy_problems(body)


@pytest.mark.parametrize(
    "mutate",
    [
        # The hint dropped: the holiday runs on the 20-minute cadence again.
        lambda b: b.replace(
            "marketSession: snap?.marketSession, builtAt: snap?.asOf", "marketSession: nil, builtAt: nil", 1
        ),
        # Decided before the fetches, from the stale stored snapshot.
        lambda b: b.replace(
            "var snap = snapshot(for: mode)",
            "var snap = snapshot(for: mode)\n"
            "let early = WidgetRefreshSchedule.nextRefresh(after: now, marketSession: nil, builtAt: nil)",
            1,
        ),
        # The one call MOVED above the fetches: it reads the stale stored snapshot's phase.
        lambda b: _RELOAD_CALL.sub("let reload = now", b, 1).replace(
            "var snap = snapshot(for: mode)",
            "var snap = snapshot(for: mode)\nlet reload = WidgetRefreshSchedule.nextRefresh("
            "after: now, marketSession: snap?.marketSession, builtAt: snap?.asOf)",
            1,
        ),
        # The policy back on an entry date.
        lambda b: b.replace("policy: .after(reload)", "policy: .after(entryDates.last ?? now)", 1),
    ],
    ids=["hint-dropped", "second-early-call", "moved-above-the-fetches", "policy-on-an-entry"],
)
def test_the_reload_policy_scan_bites(mutate):
    body = _timeline()
    mutated = mutate(body)
    assert mutated != body, "the mutation did not apply — the source moved under this test"
    assert _reload_policy_problems(mutated), "a regressed reload policy passed the scan"


def test_a_signed_out_tile_asks_for_little():
    """Signed out there is nothing to age and nothing to fetch: ONE entry and a six-hour
    reload. The app's sign-in publishes the widget token and reloads the tiles at once — that,
    not this timer, is what ends the signed-out state."""
    body = _timeline()
    early = _decl_block(body, "guard let tokenAtFetch = WidgetAPIConfig.widgetToken else")
    assert "entries: [signedOutEntry(at: now, mode: mode, configuration: configuration)]" in early
    assert ".after(now.addingTimeInterval(Self.signedOutReloadInterval))" in early
    assert "renderDates" not in early and "nextRefresh" not in early
    assert re.search(
        r"private static let signedOutReloadInterval: TimeInterval = 6 \* 60 \* 60",
        _strip_comments(_WIDGET.read_text()),
    ), "the signed-out reload is no longer six hours"


def test_the_extension_never_asks_for_a_reload_while_building_a_timeline():
    """`reloadTimelines()` from inside `timeline()` is a loop that eats the allowance."""
    body = _timeline()
    assert "reloadTimelines" not in body
    assert "WidgetSnapshotStore.writeFromExtension(" in body, (
        "the extension no longer stores its fetch, so the next FAILED fetch falls back "
        "to whenever the app was last opened rather than to the last good response"
    )
    assert "WidgetSnapshotStore.writeHoldingsFromExtension(" in body, (
        "the extension no longer stores its Holdings fetch"
    )
    write = _decl_block(_STORE.read_text(), "public static func writeFromExtension(")
    assert "reloading: false" in write, (
        "writeFromExtension now reloads, which is exactly the loop it exists to avoid"
    )
    for fn in ("static func writeHoldingsFromExtension(", "static func dropPortfolioFromExtension("):
        assert "reloadTimelines" not in _decl_block(_STORE.read_text(), fn), (
            f"{fn} now reloads — a loop inside timeline()"
        )


def test_the_schedule_harness_exists_and_is_executable():
    """These scans pin the wiring; that harness pins the arithmetic."""
    assert _SCHEDULE_HARNESS.exists(), "the cadence harness is gone"
    assert _SCHEDULE_HARNESS.stat().st_mode & 0o111, "the cadence harness is not executable"
    src = _SCHEDULE_HARNESS.read_text()
    assert "WidgetRefreshSchedule.swift" in src, "the harness no longer compiles the real source"


_JWT = _IOS / "Shared/WidgetJWT.swift"
_JWT_HARNESS = _IOS / "scripts/widget-jwt-check.sh"
_LABEL_HARNESS = _IOS / "scripts/widget-session-label-check.sh"


def _b64url_claims(token: str) -> dict:
    """The Swift reader's algorithm, in Python: base64url → padding → JSON object."""
    parts = token.split(".")
    assert len(parts) == 3, "not a compact JWT"
    payload = parts[1].replace("-", "+").replace("_", "/")
    payload += "=" * (-len(payload) % 4)
    claims = json.loads(base64.b64decode(payload, validate=True))
    assert isinstance(claims, dict)
    return claims


def test_the_jwt_harness_exists_and_compiles_only_the_reader():
    """The claim reader has its own harness; it must compile the REAL file, and only it —
    `WidgetJWT.swift` is dependency-free precisely so it can (see the guard in
    test_ios_widget_extension_guards.py)."""
    assert _JWT.exists() and _JWT_HARNESS.exists(), "the JWT reader or its harness is gone"
    assert _JWT_HARNESS.stat().st_mode & 0o111, "the JWT harness is not executable"
    src = _JWT_HARNESS.read_text()
    assert 'SRC="$ROOT/frontend/ios/Shared/WidgetJWT.swift"' in src
    compile_line = next(line for line in src.splitlines() if line.startswith("swiftc "))
    assert compile_line.count(".swift") == 1 and '"$WORK/main.swift" "$SRC"' in compile_line, (
        f"the harness compiles more than WidgetJWT.swift + its main: {compile_line!r}"
    )


def test_the_jwt_fixture_still_matches_what_the_backend_mints():
    """The harness's REAL-token fixture was minted by `create_widget_token` (with a dummy
    key). If the backend renames or retypes `sub` / `exp`, the Swift reader silently returns
    nil — owner-less Holdings snapshots and a token re-mint on every launch. Re-mint here and
    hold the fixture's claim shape to it, so the two sides cannot drift."""
    from app.core.security import create_widget_token

    fixture = re.search(r'let real = "([^"]+)"', _JWT_HARNESS.read_text())
    assert fixture, "the harness lost its real-token fixture"
    pinned = _b64url_claims(fixture.group(1))
    fresh = _b64url_claims(create_widget_token("3f2b8c1e-7a4d-4e0b-9c55-0d1e2f3a4b5c"))

    assert set(fresh) == set(pinned), f"claim names drifted: backend {sorted(fresh)} vs fixture {sorted(pinned)}"
    for claim in fresh:
        assert type(fresh[claim]) is type(pinned[claim]), f"`{claim}` changed type"
    assert isinstance(fresh["sub"], str) and fresh["sub"] == "3f2b8c1e-7a4d-4e0b-9c55-0d1e2f3a4b5c"
    assert isinstance(fresh["exp"], int) and not isinstance(fresh["exp"], bool)
    # The harness's expectations are the fixture's own claims, not hand-typed guesses.
    harness = _JWT_HARNESS.read_text()
    assert f'"{pinned["sub"]}"' in harness and f'"{pinned["exp"]}.0"' in harness


def test_the_label_harness_covers_the_new_label_rules():
    """The pure helpers behind the inline fallback, the 24/7 ageing, the pre-market rule and
    the age-boundary entry are asserted in `widget-session-label-check.sh` (main session runs
    it — a swiftc compile); this pins that the cases are still there."""
    src = _LABEL_HARNESS.read_text()
    for token in ("compactAgedLabel(", "isPriorETDay(", "ageBoundary(", '"premarket"',
                  '"afterhours"', "agedOff(", "for tz in "):
        assert token in src, f"the label harness lost its {token!r} cases"


def test_the_cadence_spends_its_budget_in_market_hours():
    """2026-11-27 (owner: "Every 90 min (Recommended)"): the quiet hours reload every 90 minutes
    instead of sleeping to the next weekday's 04:00 ET, so this no longer asserts that
    `nextPremarketOpen` exists — it was deleted with the sleep. The quiet branch, its 04:00
    clamp, the closed-for-the-day hint and the always-kept ET 00:01 rollover are pinned, with an
    executable mutation table, by `tests/test_ios_widget_schedule_footer.py`."""
    src = _strip_comments(_SCHEDULE.read_text())

    def _interval(name: str) -> float:
        m = re.search(rf"static let {name}: TimeInterval = ([0-9 *]+)", src)
        assert m, f"{name} is gone from WidgetRefreshSchedule"
        return eval(m.group(1))  # a literal arithmetic expression from our own source

    regular, extended = _interval("regularInterval"), _interval("extendedInterval")
    quiet = _interval("quietInterval")
    assert regular < extended < quiet, (
        f"the cadences collapsed (regular={regular}s, extended={extended}s, quiet={quiet}s). A "
        "flat interval around the clock asks for more refreshes than WidgetKit grants, gets "
        "throttled, and can leave the tile STALER than a modest cadence would."
    )
    # All three must actually be USED, or one of them is decoration.
    body = _decl_block(_SCHEDULE.read_text(), "public static func nextRefresh(")
    for name in ("regularInterval", "extendedInterval", "quietInterval"):
        assert name in body, f"nextRefresh no longer uses {name}"
    assert "premarketStart" in body, (
        "the quiet branch no longer stops at a weekday's 04:00 ET pre-market open"
    )
    assert "nextPremarketOpen" not in src, (
        "the sleep-to-the-next-weekday helper is back — the night and the weekend sleep again"
    )


# ── 3. Config the extension can actually reach ────────────────────────


def test_the_app_publishes_its_base_url_for_the_extension():
    """APIConfig is app-target only and DEBUG-probes localhost."""
    src = _strip_comments(_APPSTATE.read_text())
    assert "WidgetAPIConfig.publishBaseURL(" in src, (
        "the app no longer publishes its base URL, so a debug build's widget would call "
        "production while the app calls localhost — or keep calling a dead local port"
    )


def test_the_fetcher_has_a_bounded_timeout():
    src = _strip_comments(_APICONFIG.read_text())
    assert "requestTimeout" in src
    for path in (_FETCHER, _HOLDINGS_FETCHER):
        fetcher = _strip_comments(path.read_text())
        assert "timeoutInterval = WidgetAPIConfig.requestTimeout" in fetcher, (
            f"{path.name}: an unbounded request in a timeline callback burns the extension's "
            "budget and still ends with the stored snapshot"
        )


# ── 4. The toggle ─────────────────────────────────────────────────────


def test_the_toggle_writes_an_override_the_provider_prefers():
    """The choice is recorded per CONFIGURED mode, and only Home Screen tiles follow it.

    It used to be one global value: one tap flipped every tile (Lock Screen included, which
    has no button to tap back with), beat Edit Widget forever, and outlived the account.
    """
    intent = _decl_block(_INTENT.read_text(), "func perform() async throws")
    assert "WidgetModeOverride.set(mode, for: base)" in intent, (
        "the toggle no longer records its choice under the tapped tile's configured mode"
    )
    resolve = _decl_block(_WIDGET.read_text(), "private func effectiveMode(")
    assert "WidgetModeOverride.current(for: configuration.mode) ?? configuration.mode" in resolve, (
        "the provider ignores the override, or reads it for the wrong base. An untouched "
        "install must keep behaving exactly as it did before the toggle existed."
    )
    home_arm = resolve[resolve.index("case .systemSmall") : resolve.index("default:")]
    assert "WidgetModeOverride" in home_arm, "the override is not applied to Home Screen tiles"
    lock_arm = resolve[resolve.index("default:") :]
    assert "WidgetModeOverride" not in lock_arm and "return configuration.mode" in lock_arm, (
        "the Lock Screen families follow the Home Screen toggle again — they cannot tap back"
    )
    toggle = _decl_block(_WIDGET.read_text(), "private struct ModeToggle: View")
    assert "ToggleMoversModeIntent(mode: other, base: base)" in toggle, (
        "the in-tile button no longer tells the intent which tile was tapped"
    )


def test_the_override_lives_in_the_shared_config_and_dies_with_the_session():
    store = _strip_comments(_STORE.read_text())
    assert re.search(r'static let modeOverrideKey = "[^"]+"', store), (
        "the override key is not in WidgetSharedConfig, so the app cannot clear it"
    )
    clear_all = _decl_block(_STORE.read_text(), "public static func clearAll()")
    assert "removeObject(forKey: WidgetSharedConfig.modeOverrideKey)" in clear_all, (
        "the toggle's choice survives sign-out and carries over to the next account"
    )
    intent = _strip_comments(_INTENT.read_text())
    assert "WidgetSharedConfig.modeOverrideKey" in intent and '"widget.movers.modeOverride"' not in intent


def _gallery_preview_problems(widget_src: str) -> list[str]:
    body = _decl_block(
        widget_src, "func snapshot(for configuration: MoversConfigurationIntent, in context: Context)"
    )
    preview = _decl_block(body, "if context.isPreview")
    problems = []
    if "effectiveMode(" in preview or "WidgetModeOverride" in preview:
        problems.append("the gallery preview follows the in-tile toggle again")
    if "configuration.mode" not in preview:
        problems.append("the gallery preview no longer shows the CONFIGURED mode")
    after = body[body.index("if context.isPreview") + len(preview) :]
    if "effectiveMode(for: configuration, family: context.family)" not in after:
        problems.append("a placed tile's snapshot no longer honours the toggle")
    return problems


def test_the_gallery_preview_shows_the_configured_mode():
    """2026-09-30 review: one toggle tap made the "add widget" gallery advertise the Holdings
    sample under the default (Market) configuration. The override is about placed tiles."""
    assert _gallery_preview_problems(_WIDGET.read_text()) == []


def test_the_gallery_preview_scan_bites():
    src = _WIDGET.read_text()
    old = "            let previewMode: MoversMode = configuration.mode\n"
    assert old in src, "the preview's mode line moved — update this mutation"
    mutated = src.replace(
        old,
        "            let previewMode: MoversMode = effectiveMode(for: configuration, family: context.family)\n",
        1,
    )
    assert _gallery_preview_problems(mutated)


def test_edit_widget_says_the_toggle_outranks_it():
    """The override still beats Edit Widget for its base until tapped back (kept on purpose —
    an expiry would make the tile flip by itself). The one place to say so is the sheet."""
    intent = _decl_block(_INTENT.read_text(), "static var description: IntentDescription")
    assert "until you tap it back" in intent, (
        "Edit Widget no longer says the in-tile switch wins — re-choosing a mode there then "
        "silently does nothing"
    )


def test_the_toggle_does_not_open_the_app():
    intent = _strip_comments(_INTENT.read_text())
    assert "static var openAppWhenRun: Bool { false }" in intent, (
        "the toggle now launches the app, which defeats the point of an in-tile control"
    )


def test_the_toggle_never_covers_the_session_footer():
    """The rendered Small tile read 'As of 2:14 PM E⇆ Holdings' when this was an overlay.

    That footer is the widget's honesty mechanism — the only thing telling the reader
    whether a number is from today — so nothing may be positioned over it.
    """
    block = _decl_block(_WIDGET.read_text(), "private func homeScreen<Content: View>")
    assert ".overlay(" not in block, (
        "the mode toggle is an overlay again; on Small it draws straight through the "
        "session footer"
    )
    assert "bottomRow(" in block, "the Home Screen families lost their shared bottom row"
    row = _decl_block(_WIDGET.read_text(), "private func bottomRow(compactToggle: Bool)")
    assert ".overlay(" not in row
    assert "ModeToggle(current:" in row and "SessionFooter(" in row, (
        "the footer and the toggle no longer share ONE row — Small runs out of lines and "
        "Large loses its footer whenever the market band is absent"
    )
    assert row.index("SessionFooter(") < row.index("ModeToggle(current:"), (
        "the toggle comes first: it would take the width the honesty label needs"
    )


# ── 5. Market mode is a market summary, not a mover list ──────────────


def test_market_mode_renders_the_market_view():
    root = _decl_block(_WIDGET.read_text(), "private var homeContent: some View")
    assert "MarketView(entry: entry)" in root, "the Market tile is back to being a mover list"
    market_arm = root[root.index("entry.mode == .market") :]
    market_arm = market_arm[: market_arm.index("} else {")]
    assert "MarketView(" in market_arm and "HoldingsView(" not in market_arm, (
        "the market branch is no longer gated on the tile's EFFECTIVE mode — a toggled tile "
        "would render the wrong layout"
    )
    assert root.index("entry.isSignedOut") < root.index("entry.mode == .market"), (
        "signed out must win over either mode — the stored data is not showable"
    )


def test_a_missing_brief_still_renders_the_market_numbers():
    """Replaces `test_a_missing_brief_falls_back_to_the_mover_layout`.

    The backend session-gates the brief, so absent is ORDINARY. The old tile fell back to the
    mover layout; the Market tile never shows a mover now, so the numbers must render on their
    own — they cannot live inside the brief's `if`.
    """
    content = _decl_block(_WIDGET.read_text(), "private func content(_ snap: WidgetMoverSnapshot)")
    brief = _decl_block(content, "if let brief = snap.marketBrief")
    for view in ("AssetGrid(", "AssetColumn(", "AssetPriceList("):
        assert view in content, f"{view} is gone from the Market tile"
        assert view not in brief, (
            f"{view} renders only when there is a brief — a tile whose brief expired "
            "would show a header and nothing else"
        )
    assert "marketRows" in content, (
        "the asset rows no longer fall back to the index band for an old backend"
    )


# ── 6. Anti-vacuity ───────────────────────────────────────────────────


def test_the_scanners_are_not_vacuous():
    assert _strip_comments("// WidgetMarketFetcher.fetchMarket()\ncode()") == "code()"
    assert _strip_comments("code() // reloadTimelines") == "code()"

    fake = "struct X {\n  func timeline(for configuration: A) {\n    A()\n  }\n}\nfunc o() { B() }"
    block = _decl_block(fake, "func timeline(for configuration:")
    assert "A()" in block and "B()" not in block, "_decl_block leaked past the declaration"

    # A stray `}` in a comment must not end the block early, nor a `{` extend it.
    tricky = "func timeline(for configuration: A) {\n    // closes here }\n    A()\n    // opens {\n}\nB()"
    block = _decl_block(tricky, "func timeline(for configuration:")
    assert "A()" in block and "B()" not in block, "_decl_block counted braces inside comments"

    for path in (_WIDGET, _INTENT, _FETCHER, _HOLDINGS_FETCHER, _SCHEDULE, _STORE, _APICONFIG, _APPSTATE):
        assert path.exists(), f"{path} moved — every scan above would silently pass"
