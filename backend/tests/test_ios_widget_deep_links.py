"""Source-scan guards for the widget → app deep links: `caydex://ticker/<SYMBOL>[?type=…]` (a
Holdings tap → that asset's detail) and `caydex://updates/market` (a Market tap → the Updates tab
on its Market chip; owner, 2026-10-22).

There is no XCTest target, so these read the Swift source. They pin five things:

1. ONE GRAMMAR. `Shared/CaydexDeepLink.swift` compiles into both targets. The widget builds
   links only through `CaydexDeepLink.tickerURL` and `CaydexDeepLink.updatesMarketURL`, and the
   app parses them only through `CaydexDeepLink.parse`, so the two sides cannot drift. The parser
   is exact: an ASCII symbol grammar, a `type` allow-list, no unknown query keys, and one updates
   path. Any app or web page can open a `caydex://` URL, so this is untrusted input.
2. THE OAUTH CALLBACK IS LEFT ALONE. `caydex` is also `APIConfig.oauthCallbackScheme`
   (`caydex://auth-callback`). The parser recognises only the `ticker` and `updates` hosts, and
   the scheme is one value in all three places that name it.
3. SIGN-IN GATED (auth.md §1a). `.onOpenURL` only PARKS the link. `ContentView`, which exists
   only past the sign-in wall, opens it, and only once `DeepLinkRouter.canPresent` allows it
   plus the disclaimer and onboarding gates. A signed-out tap therefore lands on `SignInView`
   and never draws FMP data.
4. THE WIDGET LINKS what it says it links: `.widgetURL` once, on the root — the Holdings
   headline, or the Updates Market chip in Market mode, and nothing on WidgetKit's placeholder
   render — and a `Link` per Holdings row on Medium/Large. Market rows carry no `Link`: the
   whole Market tile opens Updates → Market.
5. NOTHING LEFT UP (section 3). A tap tears down every presentation before it opens, so every
   view a tab root reaches — found by a walk from `RootView`, not by a list — clears each
   presentation it owns in its `.onPresentationReset`: headers, atoms, the pushes Learn and
   Tracking keep, and RootView's own Buy Credits / sign-in sheets. Shared by push taps and the
   AI Deep Research handoff, which run the same teardown.

Every Swift scan strips comments first and brace-bounds the declaration it means (testing.md
§3), except the deliberate whole-file ones: the one-`.widgetURL` and `TapThrough(` counts, the
`isPlaceholderRender` mention count (previews removed), and the no-hand-built-URL tree scan. The
one Python scan (the widget schema) reads the AST. The explanatory comments beside this code name
every token asserted here.

The small functions a Market tap runs through are PINNED WHOLE (`_assert_pinned`): their
statements, compared with all whitespace removed, so a reflow passes and a changed token fails.
A substring check passed on every condition wrapped around a pinned call (a review of 34be3e7c
kept the whole file green while it inverted a guard, swapped a destination or put the
already-fixed bugs back). Editing one of those functions means editing its pin here, on purpose.
"""

from __future__ import annotations

import ast
import functools
import plistlib
import re
from pathlib import Path
from typing import Callable

import pytest

_ROOT = Path(__file__).resolve().parents[2] / "frontend" / "ios"
_APP = _ROOT / "ios"
_SHARED = _ROOT / "Shared"
_WIDGETS = _ROOT / "CaydexWidgets"

_GRAMMAR = _SHARED / "CaydexDeepLink.swift"
_ROUTER = _APP / "Core" / "Services" / "DeepLinkRouter.swift"
_API_CONFIG = _APP / "Core" / "Services" / "APIConfig.swift"
_INFO_PLIST = _APP / "Info.plist"
_IOS_APP = _APP / "iosApp.swift"
_CONTENT = _APP / "ContentView.swift"
_APP_STATE = _APP / "Core" / "State" / "AppState.swift"
_SOCIAL = _APP / "Core" / "Services" / "SocialSignInService.swift"


# ── helpers ──────────────────────────────────────────────────────────────────


def _strip_comments(src: str) -> str:
    """Remove `//` and (nested) `/* */` comments, leaving string literals intact.

    A scanner, not a regex, because the strings asserted on here contain `//`
    (`"caydex://…"`), and a naive strip would cut them.
    """
    out: list[str] = []
    i, n = 0, len(src)
    in_str = False
    multiline = False
    while i < n:
        if in_str:
            if multiline and src.startswith('"""', i):
                out.append('"""')
                i += 3
                in_str = multiline = False
                continue
            ch = src[i]
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if ch == '"' and not multiline:
                in_str = False
            i += 1
            continue
        if src.startswith('"""', i):
            out.append('"""')
            i += 3
            in_str = multiline = True
            continue
        if src[i] == '"':
            out.append('"')
            i += 1
            in_str = True
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        if src.startswith("/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if src.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif src.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    if src[i] == "\n":
                        out.append("\n")
                    i += 1
            continue
        out.append(src[i])
        i += 1
    return "".join(out)


def _read(path: Path) -> str:
    # Never pytest.skip: a guard whose subject vanished must fail, not go quiet.
    assert path.exists(), f"{path} is missing — this guard would otherwise pass vacuously"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _block(src: str, opener: str) -> str:
    """The brace-matched block starting at the first `{` at or after `opener`."""
    assert opener in src, f"{opener!r} not found"
    at = src.index(opener)
    start = src.index("{", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced braces after {opener!r}")


def _squash(src: str) -> str:
    """`src` with every whitespace run removed: a reflow or a one-line chain compares equal,
    a changed token does not."""
    return re.sub(r"\s+", "", src)


def _assert_pinned(actual: str, expected: str, why: str) -> None:
    """`actual` (a brace-matched block) holds exactly `expected`'s statements."""
    assert _squash(actual) == _squash(expected), (
        f"{why}\n\nexpected (whitespace removed):\n  {_squash(expected)}\n"
        f"found:\n  {_squash(actual)}"
    )


def _enum_cases(block: str) -> list[str]:
    """Every case NAME declared in an enum block, comma-joined ones included
    (`case a, b(x: Int)` → `a`, `b`). Associated values are dropped first, so a multi-line
    payload cannot hide a name."""
    text = block
    while True:
        stripped = re.sub(r"\([^()]*\)", "", text)
        if stripped == text:
            break
        text = stripped
    names: list[str] = []
    for m in re.finditer(r"^\s*case\s+([^\n]+)$", text, re.M):
        names += [part.split("=")[0].strip() for part in m.group(1).split(",")]
    return names


def _arm(switch: str, label: str) -> str:
    """One switch arm: from `label` to the next `case`/`default` label, or the switch's end.
    Order-free, so reordering the arms changes nothing here."""
    assert label in switch, f"switch arm {label!r} not found"
    start = switch.index(label) + len(label)
    m = re.search(r"\n\s*(?:case\s|default\s*:)", switch[start:])
    return switch[start:start + m.start()] if m else switch[start:]


def _swift_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.swift") if "Preview Content" not in p.parts]


def _string_constant(src: str, name: str) -> str:
    m = re.search(rf'static let {name}\s*=\s*"([^"]*)"', src)
    assert m, f"`static let {name} = \"…\"` not found"
    return m.group(1)


# ── 1. One grammar, one scheme ───────────────────────────────────────────────


def test_the_scheme_is_one_value_in_all_three_places():
    grammar = _string_constant(_read(_GRAMMAR), "scheme")
    oauth = _string_constant(_read(_API_CONFIG), "oauthCallbackScheme")
    with _INFO_PLIST.open("rb") as fh:
        plist = plistlib.load(fh)
    registered = {
        s for url_type in plist.get("CFBundleURLTypes", []) for s in url_type.get("CFBundleURLSchemes", [])
    }
    assert grammar == oauth == "caydex", (
        f"deep-link scheme {grammar!r} and OAuth callback scheme {oauth!r} drifted apart. "
        "They share one registered scheme, so they must agree."
    )
    assert grammar in registered, (
        f"{grammar!r} is not in Info.plist CFBundleURLSchemes ({sorted(registered)}): iOS would "
        "never deliver a widget tap to the app"
    )


def test_the_parser_recognises_only_its_two_hosts():
    src = _read(_GRAMMAR)
    assert _string_constant(src, "tickerHost") == "ticker"
    assert _string_constant(src, "updatesHost") == "updates"
    assert _string_constant(src, "updatesMarketPath") == "/market"
    results = _enum_cases(_block(src, "public enum ParseResult: Equatable, Sendable"))
    assert sorted(results) == ["malformed", "notOurLink", "ticker", "updatesMarket"], (
        f"ParseResult is {results}: the grammar can name a ticker, the Updates Market chip, "
        "'not ours' and 'malformed', nothing else (a new case is a new door — a report, a chat)"
    )
    parse = _block(src, "public static func parse(_ url: URL) -> ParseResult")
    # The condition must END there: `… || host == updatesHost || true` would still hold the pin.
    assert re.search(
        r"components\.scheme\?\.lowercased\(\) == scheme,\s*"
        r"let host = components\.host\?\.lowercased\(\),\s*"
        r"host == tickerHost \|\| host == updatesHost\s*else \{ return \.notOurLink \}",
        parse,
    ), (
        "parse no longer pins the host to exactly its two: an OAuth `caydex://auth-callback` "
        "could be read as a link of ours"
    )
    guard = parse[parse.index("host == tickerHost || host == updatesHost"):]
    not_ours = guard.index("else { return .notOurLink }")
    assert not_ours < guard.index("return .ticker(") and not_ours < guard.index("return .updatesMarket"), (
        "a foreign host no longer short-circuits to .notOurLink before any destination is built"
    )
    assert "auth-callback" not in src, (
        "the grammar special-cases the OAuth callback. It must not know about it at all: "
        "anything that is not one of its hosts is simply not ours"
    )


# From the host guard's `else` to the ticker path's first line, at the top level of `parse`:
# the credentials/port/fragment check right after the host check (so it covers BOTH hosts, not
# a branch of one), then the whole updates branch.
_PARSE_UPDATES = '''
        else { return .notOurLink }

        guard components.user == nil, components.password == nil,
              components.port == nil, components.fragment == nil
        else { return .malformed(reason: "unexpected_component") }

        if host == updatesHost {
            guard components.query == nil else { return .malformed(reason: "unknown_query_key") }
            guard components.path == updatesMarketPath else {
                return .malformed(reason: "unknown_updates_path")
            }
            return .updatesMarket
        }

        let path = components.path
'''


def test_an_updates_link_is_exactly_the_market_chip():
    """`caydex://updates/market` and nothing near it: any app can open a caydex:// URL."""
    parse = _block(_read(_GRAMMAR), "public static func parse(_ url: URL) -> ParseResult")
    assert _squash(_PARSE_UPDATES) in _squash(parse), (
        "the updates link is no longer exactly `caydex://updates/market`. Pinned in order: the "
        "credentials/port/fragment check straight after the host check, for both hosts "
        "(`caydex://u:p@updates/market#x`); no query at all, not even a bare `?`; the one path, "
        "compared exactly (`/MARKET`, `/market/`, `/research` refused); and the branch answers "
        ".updatesMarket only after both checks, then hands every other host to the ticker path"
    )
    hits = re.findall(r"\bupdatesMarket\b", parse)
    assert len(hits) == 1, (
        f"parse names .updatesMarket {len(hits)} times: a second answer (e.g. a lowercased or "
        "prefix match) would open Updates for a link outside the grammar"
    )


def test_the_symbol_grammar_checks_ascii_before_uppercasing():
    body = _block(_read(_GRAMMAR), "public static func normalizedSymbol(_ raw: String) -> String?")
    assert "isASCII" in body and "uppercased()" in body
    assert body.index("isASCII") < body.index("uppercased()"), (
        "uppercasing runs before the ASCII check: \"ß\".uppercased() == \"SS\", so a non-ASCII "
        "string would become a different, valid-looking symbol"
    )
    assert '("A"..."Z").contains($0)' in body and '("0"..."9").contains($0)' in body, (
        "segments are no longer restricted to ASCII letters and digits"
    )
    assert "maxSymbolLength" in body
    limit = int(re.search(r"static let maxSymbolLength = (\d+)", _read(_GRAMMAR)).group(1))
    assert 10 <= limit <= 32, f"maxSymbolLength {limit} is outside any real symbol's range"
    assert "omittingEmptySubsequences: false" in body and "!segment.isEmpty" in body, (
        "empty segments are dropped instead of refused, so `BRK--B` or `.AAPL` would pass"
    )


def test_the_query_is_an_allow_list():
    src = _read(_GRAMMAR)
    asset_class = _block(src, "public enum AssetClass: String, CaseIterable, Sendable")
    cases = set(re.search(r"case ([a-z, ]+)\n", asset_class).group(1).replace(" ", "").split(","))
    assert cases == {"stock", "etf", "crypto", "index", "commodity"}, (
        f"AssetClass cases {sorted(cases)} no longer match MarketTickerType's five screens"
    )
    parse = _block(src, "public static func parse(_ url: URL) -> ParseResult")
    for reason in ("unknown_query_key", "duplicate_type", "unknown_type", "extra_path",
                   "invalid_symbol", "unexpected_component", "url_too_long",
                   "unknown_updates_path"):
        assert f'.malformed(reason: "{reason}")' in parse, (
            f"parse no longer refuses `{reason}`: a link outside the grammar would be coerced "
            "into the nearest valid one"
        )
    assert "maxURLLength" in parse.split("URLComponents(url:")[0], (
        "the length cap no longer runs before parsing"
    )


_UPDATES_MARKET_URL = """
{
    var components = URLComponents()
    components.scheme = scheme
    components.host = updatesHost
    components.path = updatesMarketPath
    return components.url
}
"""


def test_links_are_built_only_by_the_grammar():
    """Nobody hand-writes `caydex://…`: a second spelling is how builder and parser drift."""
    build = _block(_read(_GRAMMAR), "public static func tickerURL(")
    assert "URLComponents()" in build and "normalizedSymbol(symbol)" in build, (
        "tickerURL no longer validates the symbol or no longer percent-encodes via URLComponents "
        "(`^GSPC` needs `%5E` in a path)"
    )
    # Exactly what `parse` accepts (pinned above): the scheme, the updates host and the one
    # path, with NO query — a `?src=widget` tag or a trailing `/` would make every Market tap
    # a `.malformed` link the router declines, so the tile would just open the app.
    _assert_pinned(
        _block(_read(_GRAMMAR), "public static var updatesMarketURL: URL?"), _UPDATES_MARKET_URL,
        "updatesMarketURL no longer builds exactly the URL parse accepts (scheme, updates host, "
        "the one path, no query)",
    )
    offenders = []
    for root in (_APP, _SHARED, _WIDGETS):
        for path in _swift_files(root):
            if path == _GRAMMAR:
                continue
            code = _strip_comments(path.read_text(encoding="utf-8"))
            if re.search(r'"caydex://(?:ticker|updates)', code) or re.search(r'URL\(string:\s*"caydex://', code):
                offenders.append(str(path.relative_to(_ROOT)))
    assert not offenders, (
        f"hand-built caydex:// URLs (use CaydexDeepLink.tickerURL / .updatesMarketURL): {offenders}"
    )


# ── 2. The app parks, then opens behind the wall ─────────────────────────────


def test_open_url_only_parks_the_link():
    app = _read(_IOS_APP)
    handler = _block(app, ".onOpenURL { url in")
    assert "DeepLinkRouter.pendingLink(for: url)" in handler
    assert "appState.pendingDeepLink = link" in handler
    for navigation in ("openedPushDestination", "TickerDetailView", "selectedTab",
                       "fullScreenCover", "NotificationRouteContent", "requestSignIn"):
        assert navigation not in handler, (
            f".onOpenURL does `{navigation}` itself: it must only park. Navigating here would "
            "skip the sign-in gate, and on a cold launch it runs before the shell exists"
        )


def test_there_is_exactly_one_open_url_handler():
    """A second `.onOpenURL` would be a door that skips the router and its gate."""
    hits = [
        str(p.relative_to(_ROOT))
        for p in _swift_files(_APP)
        for _ in re.finditer(r"\.onOpenURL\b", _strip_comments(p.read_text(encoding="utf-8")))
    ]
    assert hits == ["ios/iosApp.swift"], f"expected one .onOpenURL in iosApp.swift, found {hits}"


_ROUTER_ARMS = ("case .ticker(let ticker):", "case .updatesMarket:", "case .notOurLink:",
                "case .malformed(let reason):")


def test_the_router_parks_only_its_two_destinations():
    router = _read(_ROUTER)
    cases = _enum_cases(_block(router, "enum Destination: Equatable"))
    assert cases == ["ticker", "updatesMarket"], (
        f"PendingDeepLink.Destination is {cases}: a link may open a ticker detail or the Updates "
        "Market chip, nothing else (never a report, a chat or a credit)"
    )
    body = _block(router, "static func pendingLink(for url: URL")
    switch = _block(body, "switch CaydexDeepLink.parse(url)")
    labels = re.findall(r"^\s*(case\s[^\n]*?:|default\s*:)\s*$", switch, re.M)
    assert sorted(labels) == sorted(_ROUTER_ARMS), (
        f"pendingLink's switch arms are {labels}: one arm per ParseResult, no `default:`"
    )
    assert _squash(body).count("PendingDeepLink(") == 2, (
        "pendingLink builds a parked link outside its ticker and updates/market arms"
    )

    ticker = _arm(switch, "case .ticker(let ticker):")
    assert _squash(
        "let assetType = MarketTickerType.resolve(ticker.assetClass?.rawValue, symbol: ticker.symbol)"
    ) in _squash(ticker), (
        "the router picks the screen by its own rule. It must use the resolver search results "
        "use, which sends `BTCUSD` to the crypto screen even when typed `stock`"
    )
    assert _squash(
        "return PendingDeepLink(destination: .ticker(symbol: ticker.symbol, assetType: assetType), receivedAt: now)"
    ) in _squash(ticker) and len(re.findall(r"\breturn\b", ticker)) == 1, (
        "a ticker link no longer parks THAT ticker with the resolved screen: a Holdings tap would "
        "open something else (another destination, or an equity screen for BTCUSD)"
    )
    assert ".updatesMarket" not in ticker, "the ticker arm parks the Updates Market chip"

    market = _arm(switch, "case .updatesMarket:")
    assert _squash("return PendingDeepLink(destination: .updatesMarket, receivedAt: now)") in _squash(market) \
        and len(re.findall(r"\breturn\b", market)) == 1 and "log.info(" in market, (
        "an updates/market link is no longer parked (and logged) like a ticker link"
    )
    assert ".ticker(" not in market, "the updates/market arm parks a ticker"

    for label, level in (("case .notOurLink:", "log."), ("case .malformed(let reason):", "log.warning(")):
        arm = _arm(switch, label)
        returns = re.findall(r"\breturn\b\s*(\w+)", arm)
        assert returns == ["nil"] and "PendingDeepLink(" not in arm and level in arm, (
            f"`{label}` no longer declines (logged, `{level}`) with nothing parked: a URL that is "
            "not ours (the OAuth callback) or breaks the grammar must never open anything"
        )


def test_the_auth_gate_is_exhaustive_and_holds_at_the_wall():
    body = _block(_read(_ROUTER), "static func canPresent(status: AuthStatus) -> Bool")
    assert "default" not in body, "canPresent grew a `default:` — a new AuthStatus must be decided"
    arms = {}
    for m in re.finditer(r"case ([^:]+):\s*return (true|false)", body):
        for status in m.group(1).split(","):
            arms[status.strip()] = m.group(2)
    assert arms.get(".unauthenticated") == "false", (
        "a signed-out open is presented — FMP data behind no credential (auth.md §1a). "
        "Signed out, RootView shows SignInView, and the link must wait behind it"
    )
    assert arms.get(".unknown") == "false" and arms.get(".loading") == "false", (
        "a link presents over the splash, before auth is resolved"
    )
    assert arms.get(".authenticated") == "true"
    # `.restoring` presents ON PURPOSE (the token is armed before the status says
    # authenticated; APIClient refuses every request pre-flight when it is not).
    assert arms.get(".restoring") == "true"
    statuses = set(re.findall(r"^\s*case (\w+)", _block(_read(_APP_STATE), "enum AuthStatus: Equatable"), re.M))
    assert {s.lstrip(".") for s in arms} == statuses, (
        f"canPresent decides {sorted(arms)} but AuthStatus has {sorted(statuses)}"
    )


def test_content_view_consumes_the_link_once_every_gate_is_open():
    src = _read(_CONTENT)
    ready = _block(src, "private var isReadyForDeepLink: Bool")
    for gate in ("DeepLinkRouter.canPresent(status: appState.auth.status)",
                 "hasAcknowledgedDisclaimers", "hasCompletedOnboarding"):
        assert gate in ready, (
            f"the widget-tap gate lost `{gate}`: a cover presents ABOVE RootView's overlays, so "
            "the ticker would open over the sign-in wall, the disclaimer or onboarding"
        )
    for key in ("has_acknowledged_disclaimers", "has_completed_onboarding"):
        assert f'@AppStorage("{key}")' in src, f"ContentView no longer reads RootView's `{key}` key"
        assert f'@AppStorage("{key}")' in _read(_IOS_APP), f"RootView renamed `{key}` — the gates diverged"

    opener = ".onChange(\n            of: DeepLinkTrigger(link: appState.pendingDeepLink, ready: isReadyForDeepLink),"
    assert opener in src, "the handler no longer observes the link AND its readiness together"
    head = src[src.index(opener):]
    assert "initial: true" in head[:head.index("{")], (
        "`initial: true` is gone: a cold launch parks the link before ContentView renders, and "
        "a plain onChange never sees it"
    )
    _assert_pinned(
        _block(src, opener), _CONSUME_LINK,
        "the handler no longer, every time: waits for readiness, CLEARS the link before presenting "
        "it (one tap must not open twice), drops an expired one, and presents every fresh link — "
        "whatever its destination, whatever tab is showing",
    )


_CONSUME_LINK = """
{ _, trigger in
    guard trigger.ready, let parked = trigger.link else { return }
    appState.pendingDeepLink = nil
    guard let link = DeepLinkRouter.freshLink(parked) else { return }
    presentDeepLink(link)
}
"""


_PRESENT_DEEP_LINK = """
{
    switch link.destination {
    case .ticker(let symbol, let assetType):
        presentTickerLink(symbol: symbol, assetType: assetType)
    case .updatesMarket:
        openUpdatesMarket()
    }
}
"""


def test_present_dispatches_every_destination_exhaustively():
    _assert_pinned(
        _block(_read(_CONTENT), "private func presentDeepLink(_ link: PendingDeepLink)"), _PRESENT_DEEP_LINK,
        "presentDeepLink no longer sends each destination to its one opener, exhaustively and "
        "with nothing else on the way (never a report, a chat or a credit)",
    )


def test_present_tears_down_then_opens_a_ticker_destination():
    body = _block(_read(_CONTENT), "private func presentTickerLink(symbol: String, assetType: MarketTickerType)")
    assert "destination: .default" in body and "target: .ticker(" in body, (
        "a widget tap opens something other than the ticker detail"
    )
    assert ".report(" not in body, "a deep link must never reach a report (the paid surface)"
    assert "ModalPresentationProbe.isAnythingPresented" in body
    teardown = body.index("appState.dismissAllPresentations()")
    wait = body.index("waitUntilNothingPresented()")
    last = body.rindex("openedPushDestination = destination")
    assert teardown < wait < last, (
        "the destination is assigned before the teardown finishes — ContentView's own "
        "onPresentationReset nils it on the same bump"
    )
    assert ".alertDestinationCover($openedPushDestination)" in _read(_CONTENT), (
        "the shell's destination cover is gone: the deep link would assign into nothing"
    )


_UPDATES_VIEW = _APP / "Views" / "Screens" / "UpdatesView.swift"
_UPDATES_VM = _APP / "ViewModels" / "UpdatesViewModel.swift"
_UPDATES_STRIP = _APP / "Views" / "Organisms" / "UpdatesTabBar.swift"

# `OPEN` stands for the local closure's name, whatever it is.
_OPEN_UPDATES_MARKET = """
{
    if AudioManager.shared.showFullScreenPlayer {
        AudioManager.shared.collapsePlayer()
    }
    let OPEN = {
        selectedTab = .updates
        updatesMarketFocusSeq &+= 1
    }
    guard ModalPresentationProbe.isAnythingPresented else {
        OPEN()
        return
    }
    appState.dismissAllPresentations()
    Task { @MainActor in
        _ = await ModalPresentationProbe.waitUntilNothingPresented()
        OPEN()
    }
}
"""


def test_a_market_link_tears_down_then_opens_updates_on_the_market_chip():
    content = _read(_CONTENT)
    body = _block(content, "private func openUpdatesMarket()")
    name = re.search(r"\blet (\w+) = \{", body)
    assert name, "openUpdatesMarket no longer opens through one local closure"
    _assert_pinned(
        re.sub(rf"\b{re.escape(name.group(1))}\b", "OPEN", body), _OPEN_UPDATES_MARKET,
        "a Market widget tap no longer runs, in order and unconditionally: (1) collapse the "
        "expanded audio player FIRST — it is a ZStack layer the presentation probe never sees, "
        "and collapsed only after the wait its root copy showed through the dismissed cover and "
        "slid away a second time; (2) open — the Updates tab AND a fresh focus request, every "
        "time, even with Updates already on screen; (3) straight away when nothing is presented, "
        "else only after the teardown and the wait, so Updates never changes UNDER an open cover",
    )
    tabs = _block(content, "var body: some View")
    assert tabs.count("UpdatesView(") == 1 and \
        "UpdatesView(selectedTab: $selectedTab, marketFocusSeq: updatesMarketFocusSeq)" in tabs, (
        "ContentView no longer hands the focus request to its one Updates tab"
    )


# The selection handler, in each spelling of "bind the new tab, then selectTab it".
_SELECTION_HANDLERS = (
    r"\{\w+,(\w+)iniflet(\w+)=\1\{viewModel\.selectTab\(\2\)\}\}",
    r"\{\w+,(\w+)iniflet\1\{viewModel\.selectTab\(\1\)\}\}",
    r"\{\w+,(\w+)inguardlet(\w+)=\1else\{return\}viewModel\.selectTab\(\2\)\}",
)


def test_a_market_focus_selects_the_chip_and_scrolls_to_the_insight_card():
    view = _read(_UPDATES_VIEW)
    reader = _block(view, "ScrollViewReader { scrollProxy in")
    _assert_pinned(
        _block(reader, ".onChange(of: marketFocusSeq)"),
        "{ _, _ in viewModel.focusMarket() scrollProxy.scrollTo(Self.feedTopID, anchor: .top) }",
        "a Market widget tap no longer selects the Market chip and scrolls the feed to the top, "
        "where the Market Insight card leads — every time, whatever the feed is showing",
    )
    feed = _block(reader, "ScrollView(showsIndicators: false)")
    assert "marketFocusSeq" not in feed and \
        ".onChange(of: marketFocusSeq)" in reader[reader.index(feed) + len(feed):], (
        "the focus handler no longer sits on the feed's ScrollView: on a child of the LazyVStack "
        "(the anchor, say) it is released once the user scrolls deep, and a Market tap then "
        "neither selects the chip nor scrolls back up"
    )
    stack = _block(feed, "LazyVStack(spacing: 0, pinnedViews: [.sectionHeaders])")
    assert _squash(stack).startswith("{" + _squash("Color.clear .frame(height: 0) .id(Self.feedTopID)")), (
        "the scroll anchor is no longer the LazyVStack's FIRST child, zero-height and never "
        "resized (a resizing LazyVStack child is the 100%-CPU hang): the tap would land below "
        "the Insight card"
    )
    assert stack.count(".id(Self.feedTopID)") == 1 and \
        stack.index(".id(Self.feedTopID)") < stack.index("InsightsSummaryCard("), (
        "the scroll anchor is no longer above the Insights card, so the tap would land below it"
    )
    # focusMarket only selects; THIS is what loads the selection. Without it a Market tap from a
    # ticker chip selects Market and never fetches it.
    selection = _squash(_block(view, ".onChange(of: viewModel.selectedTab)"))
    assert any(re.fullmatch(form, selection) for form in _SELECTION_HANDLERS), (
        "a selection change no longer starts its feed load through viewModel.selectTab (or the "
        f"handler does something else besides). Found:\n  {selection}"
    )


# Every modifier that presents something over Updates. A `$binding` handed to any OTHER modifier
# fails the test until it is added here or to `_NOT_PRESENTATIONS` — a cover nobody resets
# covers the Insight card on a Market tap.
_PRESENTATIONS = ("sheet", "fullScreenCover", "popover", "alert", "confirmationDialog",
                  "inAppBrowser", "aiChatCover", "alertDestinationCover", "navigationDestination",
                  "narratedCoreReader")
# Modifiers that take a `$binding` and present nothing: a focus binding, and the Alerts inbox's
# row builder (`NotificationInboxSection.rows`), whose bindings are the selection that
# `AlertsTabContent` presents and resets itself.
_NOT_PRESENTATIONS: frozenset[str] = frozenset({"focused", "rows"})


def _call_args(src: str, open_paren: int) -> str:
    """The text between the parenthesis at `open_paren` and its match."""
    depth = 0
    for i in range(open_paren, len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return src[open_paren + 1:i]
    raise AssertionError("unbalanced parentheses")


def _block_end(src: str, open_brace: int) -> int:
    """The index just past the `}` matching the `{` at `open_brace`."""
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    raise AssertionError("unbalanced braces")


def _presentations(view: str, where: str) -> list[tuple[str, str, int, int]]:
    """`(modifier, state, start, end)` for every presentation in `view`: the state behind it, and
    the span of the call with its trailing closures (`} message: {` included). Fails on a
    presentation it cannot read and on a `$binding` given to a modifier it does not know, instead
    of skipping either."""
    found = []
    for call in re.finditer(r"\.(\w+)\(", view):
        modifier, args = call.group(1), _call_args(view, call.end() - 1)
        binding = re.search(r"\$([A-Za-z_][\w.]*)", args)
        if modifier in _PRESENTATIONS:
            # `Binding(get: { x != nil }, …)` (a Bool) or `Binding(get: { x }, …)` (an item).
            optional = re.search(r"get:\s*\{\s*([\w.]+)\s*(?:!=\s*nil\s*)?\}", args)
            assert binding or optional, (
                f"`.{modifier}(` in {where} presents from state this guard cannot read "
                f"({args.strip()[:80]!r}): teach it, do not skip it"
            )
            end = call.end() + len(args) + 1
            first = True
            while True:
                rest = view[end:]
                closure = re.match(r"\s*\{", rest) if first else re.match(r"\s*\w+\s*:\s*\{", rest)
                if not closure:
                    break
                end = _block_end(view, end + closure.end() - 1)
                first = False
            found.append((modifier, binding.group(1) if binding else optional.group(1), call.start(), end))
        else:
            assert not binding or modifier in _NOT_PRESENTATIONS, (
                f"`.{modifier}(` in {where} takes `${binding.group(1)}`: if it presents "
                "anything, add it to _PRESENTATIONS (and reset it); if not, to _NOT_PRESENTATIONS"
            )
    return found


def _presented_bindings(view: str) -> set[str]:
    """The state behind every presentation in UpdatesView (see `_presentations`)."""
    return {state for _, state, _, _ in _presentations(view, "UpdatesView")}


def _cleared_by_reset(view: str, where: str) -> list[str]:
    """What `view`'s `.onPresentationReset` blocks clear, in order. Each statement must be an
    unconditional `x = false` / `x = nil`."""
    cleared = []
    for at in [m.start() for m in re.finditer(r"\.onPresentationReset\s*\{", view)]:
        block = _block(view[at:], ".onPresentationReset")
        statements = [s.strip() for s in re.split(r"[\n;]", block[1:-1]) if s.strip()]
        for statement in statements:
            m = re.fullmatch(r"([\w.]+)\s*=\s*(false|nil)", statement)
            assert m, (
                f"{where}'s presentation reset runs `{statement}`: it must be nothing but "
                "unconditional clears — a condition or early return leaves a presentation up over "
                "the widget or push tap"
            )
            cleared.append(m.group(1))
    return cleared


def test_updates_presentation_reset_clears_every_presentation_it_owns():
    """A Market widget tap lands on THIS tab: anything Updates left up would cover it."""
    view = _block(_read(_UPDATES_VIEW), "struct UpdatesView: View")
    names = _presented_bindings(view)
    assert view.count(".onPresentationReset {") == 1
    cleared = _cleared_by_reset(view, "UpdatesView")
    missing = sorted(names - set(cleared))
    assert not missing, (
        f"Updates' presentation reset no longer clears {missing}: a Market widget tap lands on "
        "this tab, and the probe then times out while that presentation covers the Insight card"
    )
    assert cleared.index("pendingChatFocus") < cleared.index("insightSources"), (
        "the sources sheet is cleared before its pending chat: its onDismiss would reopen the chat"
    )


_STRIP_REVEAL = """
{
    if scope == UpdatesScope.market, tabs.first?.scope == scope {
        proxy.scrollTo(Self.stripID, anchor: .leading)
    } else {
        proxy.scrollTo(scope)
    }
}
"""


def test_the_chip_strip_reveals_a_code_made_selection():
    strip = _read(_UPDATES_STRIP)
    assert ".id(tab.scope)" in _block(strip, "ForEach(tabs) { tab in"), "the chips carry no scroll id by scope"
    content = _block(strip, "ScrollView(.horizontal, showsIndicators: false)")
    chips = _block(content, "HStack(spacing: AppSpacing.sm)")
    after_chips = _squash(content[content.index(chips) + len(chips):])
    assert after_chips.startswith(_squash(".padding(.horizontal, AppSpacing.lg) .id(Self.stripID)")) \
        and strip.count(".id(Self.stripID)") == 1, (
        "the strip's scroll id no longer sits on the PADDED content: a Market reveal would stop "
        "with the chip flush against the screen edge, its leading inset scrolled away"
    )
    strip_id = re.search(r'static let stripID\s*=\s*"([^"]*)"', strip)
    assert strip_id and strip_id.group(1) == "updates.tabbar.strip", (
        "the strip's scroll id changed: it must stay a constant no chip scope (a ticker or "
        "`__MARKET__`) can ever equal, or `reveal` scrolls to that chip instead"
    )
    _assert_pinned(
        _block(strip, ".onChange(of: selectedTab?.scope)"),
        "{ _, scope in guard let scope else { return } "
        "withAnimation(.easeInOut(duration: 0.2)) { reveal(scope, with: proxy) } }",
        "a selection made from code (a Market widget tap, /tabs re-selecting) is no longer "
        "brought into view, every time: the chip could stay scrolled off-screen",
    )
    _assert_pinned(
        _block(strip, ".onChange(of: marketRevealSeq)"),
        "{ _, _ in withAnimation(.easeInOut(duration: 0.2)) { reveal(UpdatesScope.market, with: proxy) } }",
        "a Market widget tap no longer reveals the Market chip when it was ALREADY selected (no "
        "selection change, so the scope handler never fires)",
    )
    _assert_pinned(
        _block(strip, "private func reveal(_ scope: String, with proxy: ScrollViewProxy)"), _STRIP_REVEAL,
        "reveal no longer takes the strip back to rest for the Market chip (first, so the whole "
        "padded strip from its leading edge) and the least scroll that shows any other chip",
    )
    view = _read(_UPDATES_VIEW)
    assert view.count("UpdatesTabBar(") == 1 and \
        "marketRevealSeq: marketFocusSeq" in _call_args(view, view.index("UpdatesTabBar(") + len("UpdatesTabBar")), (
        "UpdatesView no longer hands the tap to its chip strip"
    )


_FOCUS_MARKET = """
{
    if pendingScope != nil {
        pendingScope = UpdatesScope.market
    }
    let market = filterTabs.first(where: { $0.isMarketTab }) ?? Self.marketTabFallback
    guard selectedTab?.scope != market.scope else { return }
    selectedTab = market
}
"""


def test_focus_market_repoints_the_stash_then_selects():
    _assert_pinned(
        _block(_read(_UPDATES_VM), "func focusMarket()"), _FOCUS_MARKET,
        "focusMarket no longer (1) re-points a session-heal stash to Market FIRST, before any "
        "early return — the stash outranks the selection, so /tabs would carry the user straight "
        "back to the chip they left — then (2) selects the Market chip unless it already is, "
        "loading nothing itself: the view's .onChange(of: selectedTab) → selectTab loads it",
    )


_SELECT_TAB = """
{
    selectedTab = tab
    selectionLoad = (tab.scope, Task { await loadFeed(for: tab, force: false) })
}
"""

_JOIN_SELECTION_LOAD = """
{
    guard let running = selectionLoad, running.scope == inFlightScope,
          running.scope == selectedTab?.scope
    else { return }
    await running.task.value
}
"""


def test_the_first_load_joins_a_selection_load_already_in_flight():
    """A Market tap before the tab's first load: the selection's load can claim the scope first,
    so the first load's own is skipped as a duplicate. The latch must wait for that load."""
    vm = _read(_UPDATES_VM)
    _assert_pinned(
        _block(vm, "func selectTab(_ tab: NewsFilterTab)"), _SELECT_TAB,
        "selectTab no longer records the load it starts, so the first load cannot join it",
    )
    _assert_pinned(
        _block(vm, "private func joinSelectionLoad() async"), _JOIN_SELECTION_LOAD,
        "joinSelectionLoad no longer waits for exactly the selection's load that still owns the "
        "selected scope",
    )
    owned = _block(_block(vm, "func loadIfNeeded() async"), "let task = Task {")
    order = [owned.find(step) for step in ("await self.loadInitialData()", "await self.joinSelectionLoad()",
                                           "if !Task.isCancelled, self.error == nil, self.snapshotSavedAt == nil {")]
    assert -1 not in order and order == sorted(order), (
        "the first load no longer joins the selection's feed load BEFORE it latches hasLoadedOnce: "
        "a feed that then failed is never retried on the next activation. Found:\n  "
        f"{_squash(owned)}"
    )
    latches = [m.start() for m in re.finditer(r"\bhasLoadedOnce\s*=\s*true", vm)]
    latch_if = _block(owned, "if !Task.isCancelled, self.error == nil, self.snapshotSavedAt == nil {")
    assert len(latches) == 1 and re.search(r"\bhasLoadedOnce\s*=\s*true", latch_if), (
        f"hasLoadedOnce is latched in {len(latches)} places: only the owned first load's live-"
        "completion check may set it (latching when /tabs answers is the bug the join fixes)"
    )
    identity = _block(vm, "func handleIdentityChange(isActiveTab: Bool) async")
    first_await = re.search(r"\bawait\b", identity)
    assert re.findall(r"\bselectionLoad\s*=", vm) == ["selectionLoad =", "selectionLoad ="] and \
        "selectionLoad = nil" in identity and \
        (first_await is None or identity.index("selectionLoad = nil") < first_await.start()), (
        "selectionLoad is set outside selectTab, or no longer dropped on an identity change "
        "BEFORE its first suspension (a Market tap during that await would be dropped)"
    )


def test_a_parked_link_expires_and_is_kept_across_a_session_end():
    router = _read(_ROUTER)
    pending = _block(router, "struct PendingDeepLink: Equatable")
    assert "let id = UUID()" in pending, (
        "without a per-tap id, a second tap on the same ticker is not a change onChange sees"
    )
    minutes = re.search(r"static let maxAge: TimeInterval = (\d+) \* 60", pending)
    assert minutes and 1 <= int(minutes.group(1)) <= 30, "maxAge missing or unreasonable"
    fresh = _block(router, "static func freshLink(_ link: PendingDeepLink")
    assert "link.isExpired(now: now)" in fresh and "log.info(" in fresh

    state = _read(_APP_STATE)
    assert "var pendingDeepLink: PendingDeepLink?" in state
    ended = _block(state, "private func discardDataForEndedSession()")
    assert "pendingDeepLink" not in ended, (
        "pendingDeepLink is cleared at session end again. The commonest path into that funnel "
        "with a parked tap is a cold launch on an expired session (restore → dead credential → "
        "wall), so clearing it drops the tap in exactly the flow parking exists for. "
        "PendingDeepLink.maxAge bounds it instead"
    )


# ── 3. Every view a tab root reaches takes its presentations down ────────────
#
# A widget or push tap runs `dismissAllPresentations()`, waits for UIKit to report nothing
# presented (1.5 s at most), then opens. Only views observing `.onPresentationReset` take their
# presentations down. A tab root's reset unwinds every cover it presents, with everything inside
# it — but not what its ON-SCREEN children present (a header's logo cover, an atom's sheet, a
# popover on a Home card), nor the covers of a push the tab keeps (Learn keeps Money Moves, the
# Library and the Journey; Tracking keeps "see all whales"). Any of those left up and the tap
# lands underneath it: nothing changes until the user closes the screen by hand.
#
# So the reach is COMPUTED, never listed (a list misses the next organism): a walk from
# `RootView` through every view the tree draws, stepping over the content of every presentation
# its owner's reset clears, and into the destination of every push its owner keeps. Each view it
# reaches must clear every presentation it owns. A view is a struct conforming to `View` or
# `ViewModifier` (plus its extensions); `extension View` helpers are followed into the modifier
# they apply. Previews are left out.

_ROOT_VIEW = "RootView"
# Drawn by RootView only while a widget link must wait, so no link ever tears down over them.
# Each still has to be drawn by RootView, or its entry is stale.
_GATES = {
    "SplashView": "auth is still resolving: ContentView, the only caller of the teardown, does "
                  "not exist yet",
    "SignInView": "the sign-in wall (auth.md §1a): ContentView does not exist behind it, and a "
                  "link waits for DeepLinkRouter.canPresent",
    "DisclaimerAcknowledgementView": "first-run gate: a link waits for it (isReadyForDeepLink)",
    "OnboardingView": "first-run gate: a link waits for it (isReadyForDeepLink)",
}

_TYPE_DECL = re.compile(
    r"\b(struct|extension)\s+([A-Z]\w*)(?:<[^>{]*>)?\s*(?::\s*([^{]*?))?(?:\s*\bwhere\b[^{]*)?\s*\{"
)
_TYPE_REF = re.compile(r"\b([A-Z]\w*)(?:\s*[({]|\.init\b)")
_UNDOTTED_PRESENTATION = re.compile(rf"(?<![\w.])(?:{'|'.join(_PRESENTATIONS)})\(")


@functools.lru_cache(maxsize=None)
def _clean_swift(raw: str) -> str:
    return _without_previews(_strip_comments(raw))


@functools.lru_cache(maxsize=None)
def _file_types(src: str) -> tuple[tuple[str, str, str, bool, str], ...]:
    """`(kind, name, conformances, is_private, body)` for every struct and extension in `src`
    (comments and previews already gone). A type nested in another is cut out of the outer
    body: it is a type of its own."""
    found = []
    for m in _TYPE_DECL.finditer(src):
        open_brace = m.end() - 1
        line = src[src.rfind("\n", 0, m.start()) + 1:m.start()]
        found.append((m.group(1), m.group(2), m.group(3) or "", bool(re.search(r"\b(?:file)?private\b", line)),
                      m.start(), open_brace, _block_end(src, open_brace)))
    out = []
    for kind, name, conformances, private, _, open_brace, end in found:
        inner = [(s, e) for _, _, _, _, s, _, e in found if open_brace < s and e <= end]
        outermost = [(s, e) for s, e in inner if not any(s2 < s and e <= e2 for s2, e2 in inner)]
        body = src[open_brace:end]
        for s, e in sorted(outermost, reverse=True):
            body = body[:s - open_brace] + body[e - open_brace:]
        out.append((kind, name, conformances, private, body))
    return tuple(out)


def _view_index() -> tuple[dict[str, list[dict]], dict[str, list[tuple[Path, str]]]]:
    """Every View / ViewModifier struct by name (its extensions folded in), and every
    `extension View` function's body by name."""
    types: dict[str, list[dict]] = {}
    extensions: list[tuple[str, Path, str]] = []
    for path in sorted(_swift_files(_APP)):
        for kind, name, conformances, private, body in _file_types(_clean_swift(path.read_text(encoding="utf-8"))):
            if kind == "extension":
                extensions.append((name, path, body))
            else:
                types.setdefault(name, []).append({
                    "name": name, "path": path, "body": body, "private": private,
                    "view": bool(re.search(r"\b(?:View|ViewModifier)\b", conformances)),
                })
    helpers: dict[str, list[tuple[Path, str]]] = {}
    for name, path, body in extensions:
        if name == "View":
            for f in re.finditer(r"\bfunc\s+(\w+)\s*(?:<[^>]*>)?\(", body):
                params = _call_args(body, f.end() - 1)
                brace = body.index("{", f.end() + len(params))
                helpers.setdefault(f.group(1), []).append((path, body[brace:_block_end(body, brace)]))
            continue
        owners = types.get(name, [])
        for decl in [d for d in owners if d["path"] == path] or owners:
            decl["body"] += "\n" + body
    return types, helpers


def _resolve(types: dict[str, list[dict]], name: str, from_path: Path) -> list[dict]:
    """The view a reference names: one declared in the same file first, else a non-private one."""
    views = [d for d in types.get(name, []) if d["view"]]
    return [d for d in views if d["path"] == from_path] or [d for d in views if not d["private"]]


def _where(view: dict) -> str:
    return f"{view['name']} ({view['path'].relative_to(_ROOT)})"


def _drawn_and_owned(view: dict) -> tuple[str, set[str]]:
    """What `view` keeps drawn — its source minus the content of every presentation, except a
    push it keeps (`navigationDestination` its reset does not clear), whose destination stays
    in the tree — and the state of every presentation it owns: all but pushes, and all but those
    nested inside another presentation's content, which go down with it."""
    where = _where(view)
    body = view["body"]
    cleared = set(_cleared_by_reset(body, where))
    found = _presentations(body, where)
    cut = sorted((start, end) for modifier, state, start, end in found
                 if modifier != "navigationDestination" or state in cleared)
    owned = {state for modifier, state, start, end in found
             if modifier != "navigationDestination"
             and not any(s < start and end <= e for s, e in cut)}
    drawn, at = [], 0
    for start, end in cut:
        if start >= at:  # a span nested in one already cut goes with it
            drawn.append(body[at:start])
            at = end
    drawn.append(body[at:])
    return "".join(drawn), owned


def _reachable_views() -> list[tuple[dict, str]]:
    """`(view, how it was reached)` for every view drawn without crossing a presentation its
    owner's reset clears, walking from RootView."""
    types, helpers = _view_index()
    roots = _resolve(types, _ROOT_VIEW, _IOS_APP)
    assert len(roots) == 1, f"{_ROOT_VIEW} not found exactly once in {_IOS_APP.name}: {len(roots)}"
    reached: dict[tuple[str, Path], tuple[dict, str]] = {}
    stack = [(roots[0], "the app root")]
    while stack:
        view, via = stack.pop()
        key = (view["name"], view["path"])
        if key in reached or view["name"] in _GATES:
            continue
        reached[key] = (view, via)
        drawn, _ = _drawn_and_owned(view)
        refs = [(m.group(1), view["path"]) for m in _TYPE_REF.finditer(drawn)]
        for helper in {m.group(1) for m in re.finditer(r"\.(\w+)\(", drawn)} & set(helpers):
            if helper in _PRESENTATIONS:
                continue  # its content IS the presentation, owned by the caller's `$binding`
            for path, body in helpers[helper]:
                assert not _UNDOTTED_PRESENTATION.search(body), (
                    f"`extension View.{helper}` presents from its parameters: add `{helper}` to "
                    "_PRESENTATIONS so its callers' bindings are checked"
                )
                refs += [(m.group(1), path) for m in _TYPE_REF.finditer(body)]
        for name, from_path in refs:
            for child in _resolve(types, name, from_path):
                stack.append((child, f"drawn by {view['name']}"))
    return list(reached.values())


def test_every_view_a_tab_root_reaches_resets_what_it_presents():
    """Every presentation a reached view owns is cleared by its own reset. Pushes it keeps are
    exempt (they do not cover the window; their destinations are walked instead)."""
    problems = []
    for view, via in _reachable_views():
        where = _where(view)
        _, owned = _drawn_and_owned(view)
        missing = sorted(owned - set(_cleared_by_reset(view["body"], where)))
        if missing:
            problems.append(f"{where}, {via}: {missing}")
    assert not problems, (
        "a presentation reset no longer clears every presentation its view owns — a widget or "
        "push tap would land underneath it, and nothing changes until the user closes it by "
        "hand. Add `.onPresentationReset { … }` clearing each one in that view:\n  "
        + "\n  ".join(problems)
    )


# The walk must reach these: each owns a presentation no tab root's reset unwinds. A parser that
# stopped seeing them would leave the test above green over every one of the old gaps.
#
# `GlobalAudioOverlay` left this list when Money Moves, the one KEPT push that carried it, stopped
# (that overlay drew a second mini player over the root's — test_ios_audio_player_single_render).
# Every host it has now is a cover, or a push its tab's reset pops, so its reader cover goes down
# with its host; the tab tree's reader cover is RootContainerView's, which is on this list.
_MUST_REACH = {
    "RootView", "RootContainerView", "ContentView", "HomeDashboardView", "UpdatesView",
    "ResearchViewWithBinding", "TrackingContentViewWithBinding", "LearnContentView",
    "GlobalHeaderView", "ResearchHeader", "WhalesTabContent", "AllWhalesView",
    "MoneyMovesDetailView", "BookLibraryView", "InvestorJourneyView",
    "FullScreenAudioPlayer", "LearnAudioPaywallModifier", "InlineDisclaimerNotice", "ScannerCard",
    "AlertsTabContent",
}


def test_the_walk_reaches_every_view_it_must_guard():
    reached = {view["name"] for view, _ in _reachable_views()}
    lost = sorted(_MUST_REACH - reached)
    assert not lost and len(reached) >= 150, (
        f"the presentation walk no longer reaches {lost} ({len(reached)} views in all): a view "
        "it cannot see is a view whose presentations nobody checks"
    )
    root = _block(_read(_IOS_APP), "struct RootView: View")
    for gate, why in _GATES.items():
        assert f"{gate}()" in root, (
            f"_GATES exempts {gate} ({why}), but RootView no longer draws it: drop the entry, or "
            "the walk skips a view the user can now reach"
        )
    assert not reached & set(_GATES), f"the walk reached a gate: {sorted(reached & set(_GATES))}"


_JOURNEY_VIEW = _APP / "Views" / "Screens" / "InvestorJourneyView.swift"
_JOURNEY_VM = _APP / "ViewModels" / "InvestorPathViewModel.swift"
# The views the mutation rows below break (each owns a presentation no tab root unwinds).
_HEADER = _APP / "Views" / "Organisms" / "GlobalHeaderView.swift"
_RESEARCH_HEADER = _APP / "Views" / "Organisms" / "ResearchHeader.swift"
_TRACKING = _APP / "Views" / "Screens" / "TrackingView.swift"
_ALL_WHALES = _APP / "Views" / "Screens" / "AllWhalesView.swift"
_LEARN = _APP / "Views" / "Screens" / "LearnView.swift"
_MONEY_MOVES = _APP / "Views" / "Screens" / "MoneyMovesDetailView.swift"
_LIBRARY = _APP / "Views" / "Screens" / "BookLibraryView.swift"
_ROOT_CONTAINER = _APP / "Views" / "Screens" / "RootContainerView.swift"
_PLAYER = _APP / "Views" / "Screens" / "FullScreenAudioPlayer.swift"
_AUDIO_PAYWALL = _APP / "Views" / "Modifiers" / "LearnAudioPaywall.swift"
_DISCLAIMER_ATOM = _APP / "Views" / "Atoms" / "InlineDisclaimerNotice.swift"
_SCANNER = _APP / "Views" / "Molecules" / "ScannerCard.swift"


def test_the_journey_story_refreshes_its_grid_however_it_closes():
    """The reset only flips `showLessonStory`, so the rebuild after a lesson completed inside the
    story must hang off the cover's dismissal, not off the story's own close button."""
    view = _read(_JOURNEY_VIEW)
    at = view.index(".fullScreenCover(isPresented: $viewModel.showLessonStory")
    args = _call_args(view, view.index("(", at))
    assert re.search(r"onDismiss:\s*\{\s*viewModel\.lessonStoryDidClose\(\)\s*\}", args), (
        "the lesson story cover no longer rebuilds the Journey on every dismissal: a widget or "
        "push tap that closes it would leave a just-completed lesson unticked"
    )
    _assert_pinned(
        _block(_read(_JOURNEY_VM), "func lessonStoryDidClose()"),
        "{ selectedLesson = nil rebuildJourney() }",
        "lessonStoryDidClose no longer clears the lesson and rebuilds the grid",
    )


# ── 4. The widget links what it shows ────────────────────────────────────────

_WIDGET = _WIDGETS / "MoversWidget.swift"
_SNAPSHOT = _SHARED / "WidgetSnapshotStore.swift"
_WIDGET_SCHEMA = Path(__file__).resolve().parents[1] / "app" / "schemas" / "widget.py"

_WIDGET_BODY = """
{
    Group {
        switch family {
        case .accessoryInline:      InlineView(entry: entry)
        case .accessoryRectangular: RectangularView(entry: entry)
        default:                    homeScreen { homeContent }
        }
    }
    .widgetURL(tileURL)
}
"""

_TILE_URL = """
{
    guard !entry.isSignedOut, !entry.isPlaceholderRender else { return nil }
    if entry.mode == .market {
        return CaydexDeepLink.updatesMarketURL
    }
    guard entry.mode == .portfolio,
          let snap = entry.snapshot, snap.mode == "portfolio",
          let headline = snap.headlineMover
    else { return nil }
    return headline.deepLink
}
"""


def test_one_widget_url_on_the_root_holdings_headline_or_updates_market():
    src = _read(_WIDGET)
    assert src.count(".widgetURL(") == 1, (
        "a widget gets ONE .widgetURL. A second one deeper in the tree is undefined behaviour, "
        "and on Small it silently decides where every tap goes"
    )
    _assert_pinned(
        _block(_block(src, "struct MoversWidgetView: View"), "var body: some View"), _WIDGET_BODY,
        "the tile-wide URL is no longer set ONCE, on the root around every family: moved into one "
        "arm, the Lock Screen (or Home Screen) tiles would link nothing",
    )
    _assert_pinned(
        _block(src, "private var tileURL: URL?"), _TILE_URL,
        "tileURL no longer: links nothing when signed out or on WidgetKit's placeholder render, "
        "FIRST; sends the whole Market tile to the Updates Market chip (owner, 2026-10-22); and "
        "links a Holdings tile to its headline mover only from a Holdings payload (a Small tile "
        "would otherwise link a mover the user does not hold)",
    )
    _assert_pinned(
        _block(src, "func placeholder(in context: Context) -> MoversEntry"),
        "{ MoversEntry(date: Date(), snapshot: .preview, isPlaceholderRender: true) }",
        "the placeholder render is no longer marked: its sample entry has the DEFAULT mode, "
        "Market, so a signed-out or Holdings tile tapped while loading would open Updates → Market",
    )
    assert "var isPlaceholderRender: Bool = false" in _block(src, "struct MoversEntry: TimelineEntry"), (
        "MoversEntry no longer defaults isPlaceholderRender to false: every real entry would link nothing"
    )
    mentions = len(re.findall(r"\bisPlaceholderRender\b", _without_previews(src)))
    assert mentions == 3, (
        f"isPlaceholderRender is named {mentions} times outside #Preview, not 3 (its declaration, "
        "placeholder(in:) and tileURL's guard): an entry other than the placeholder render is "
        "marked — by a literal or an expression such as `snap == nil` — and its tap links nothing"
    )


def _without_previews(src: str) -> str:
    """`src` minus every `#Preview { … }` block and its `timeline: { … }` closure."""
    while "#Preview" in src:
        at = src.index("#Preview")
        body = _block(src, "#Preview")
        end = src.index(body, at) + len(body)
        rest = src[end:]
        if rest.lstrip().startswith("timeline:"):
            timeline = _block(rest, "timeline:")
            end += rest.index(timeline) + len(timeline)
        src = src[:at] + src[end:]
    return src


def test_holdings_rows_link_and_market_rows_do_not():
    src = _read(_WIDGET)
    side = _block(src, "private struct SidePanel: View")
    for who in ("riser", "faller"):
        assert side.count(f".tapThrough({who}.deepLink)") == 2, (
            f"a Medium {who} row no longer links in BOTH layouts (captioned and glyph). "
            "ViewThatFits picks either one, so a tap would open the app root half the time"
        )
    assert ".tapThrough($0.deepLink)" in _block(src, "private struct MoverColumn: View"), (
        "a Large rising/falling row no longer links to its ticker"
    )
    holdings = side.count(".tapThrough(") + _block(src, "private struct MoverColumn: View").count(".tapThrough(")
    assert src.count(".tapThrough(") == holdings == 5, (
        f"{src.count('.tapThrough(')} `.tapThrough(` calls, {holdings} of them in the Holdings rows: a "
        "row link appeared outside SidePanel/MoverColumn. The whole Market tile opens Updates → "
        "Market (owner, 2026-10-22); on Medium/Large its rows cover most of the tile"
    )
    assert src.count("TapThrough(") == 1 and "TapThrough(" in _block(src, "func tapThrough(_ url: URL?)"), (
        "TapThrough is applied outside the one `.tapThrough(` helper (e.g. "
        "`.modifier(TapThrough(url:))`), which the row count above cannot see"
    )
    for m in re.finditer(r"(?:private |fileprivate )?struct (\w+)[^{\n]*\{", src):
        decl = _block(src[m.start():], m.group(0))
        if "WidgetIndex" in decl:
            for link in ("tapThrough(", "TapThrough(", "tickerURL(", "Link(destination"):
                assert link not in decl, (
                    f"{m.group(1)} draws a market asset and links it (`{link}`): a Market row would "
                    "take the tap from Updates → Market, and an unclassified SPY opens the STOCK screen"
                )


def test_links_are_drawn_only_where_widgetkit_honours_them():
    src = _read(_WIDGET)
    assert src.count("Link(destination:") == 1, (
        "a Link outside TapThrough: it would draw on Small or Lock Screen tiles, where WidgetKit "
        "ignores it and `.widgetURL` is the only target"
    )
    tap = _block(src, "private struct TapThrough: ViewModifier")
    honours = _block(tap, "private var familyHonoursLinks: Bool")
    assert ".systemMedium" in honours and ".systemLarge" in honours
    for inert in (".systemSmall", ".accessory"):
        assert inert not in honours, f"TapThrough draws a Link on {inert}, where it is inert"
    body = _block(tap, "func body(content: Content) -> some View")
    assert "if let url, familyHonoursLinks {" in body and "Link(destination: url)" in body
    assert re.search(r"\}\s*else\s*\{\s*content\s*\}\s*\}\s*$", body), (
        "with no URL, or on a family without links, the row must render unchanged"
    )


def _optional_none_field(cls: ast.ClassDef, name: str) -> bool:
    """`name: Optional[<x>] = None` declared in `cls`'s own body."""
    for node in cls.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            ann = node.annotation
            optional = (isinstance(ann, ast.Subscript) and isinstance(ann.value, ast.Name)
                        and ann.value.id == "Optional")
            none_default = isinstance(node.value, ast.Constant) and node.value.value is None
            return optional and none_default
    return False


def test_asset_classes_come_from_the_server():
    src = _read(_WIDGET)
    for path in (_WIDGET, _SNAPSHOT):
        assert not re.search(r"\bextension\s+(?:\w+\.)?WidgetIndex\b", _read(path)), (
            f"{path.name} extends WidgetIndex: a market asset links again (a `deepLink` on it is all "
            "a row needs). If market rows ever link, the old rule returns with them: ONLY with a "
            "server-named class — an unclassified SPY resolves to the STOCK screen"
        )
    mover_link = _block(_block(src, "private extension WidgetMover"), "var deepLink: URL?")
    assert "CaydexDeepLink.tickerURL(symbol: ticker, assetClass: CaydexDeepLink.AssetClass(wire: assetType))" in mover_link

    shared = _read(_SNAPSHOT)
    for struct in ("public struct WidgetIndex", "public struct WidgetMover:"):
        decl = _block(shared, struct)
        assert "public let assetType: String?" in decl, f"{struct} lost its optional assetType"
        assert 'case assetType = "asset_type"' in decl, f"{struct} no longer decodes `asset_type`"
    classes = {
        node.name: node
        for node in ast.parse(_WIDGET_SCHEMA.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ClassDef)
    }
    for model in ("WidgetIndexResponse", "WidgetMoverResponse"):
        assert model in classes, f"{model} is gone from app/schemas/widget.py"
        assert _optional_none_field(classes[model], "asset_type"), (
            f"{model} no longer declares `asset_type: Optional[...] = None` for the widget's links"
        )


def test_the_oauth_web_flow_still_owns_its_callback():
    """`.onOpenURL` must not have replaced the session-owned callback."""
    flow = _block(_read(_SOCIAL), "private func signInWithGoogleWeb() async throws -> SocialSignInResult")
    assert "ASWebAuthenticationSession(" in flow and "callbackURLScheme: callbackScheme" in flow
    assert "let callbackScheme = APIConfig.oauthCallbackScheme" in flow
    assert '"\\(callbackScheme)://auth-callback"' in flow, (
        "the OAuth redirect host changed. If it ever became `ticker`, the deep-link "
        "parser would claim the callback"
    )


# ── Mutations: each guard must fail on the bug it names ──────────────────────
#
# A guard that stays green on its bug proves nothing (testing.md §3). Each row breaks the REAL
# source in memory (only `Path.read_text` of that one file is patched, never the file on disk),
# then runs one guard, which must fail with its own message — so an unrelated breakage
# (a renamed anchor) cannot pass for a kill. The first rows are the mutants the review of
# 34be3e7c kept green.


def _replace(old: str, new: str) -> Callable[[str], str]:
    def mutate(src: str) -> str:
        assert src.count(old) >= 1, f"mutation anchor {old!r} not in source"
        return src.replace(old, new, 1)
    return mutate


def _resub(pattern: str, repl: str, flags: int = re.S) -> Callable[[str], str]:
    def mutate(src: str) -> str:
        out, n = re.subn(pattern, repl, src, count=1, flags=flags)
        assert n == 1, f"mutation pattern {pattern!r} did not match"
        return out
    return mutate


def _append(text: str) -> Callable[[str], str]:
    return lambda src: src + text


def _chain(*mutations: Callable[[str], str]) -> Callable[[str], str]:
    def mutate(src: str) -> str:
        for m in mutations:
            src = m(src)
        return src
    return mutate


def _drop_line(line: str) -> Callable[[str], str]:
    """Delete one whole line, matched with its indentation."""
    return _replace(f"\n{line}\n", "\n")


_REACH = "test_every_view_a_tab_root_reaches_resets_what_it_presents"


_MUTATIONS: list[tuple[str, Path, Callable[[str], str], str, str]] = [
    # ── the grammar ──
    ("parse-second-answer", _GRAMMAR,
     _replace("            guard components.path == updatesMarketPath else {",
              "            if components.path.lowercased().hasPrefix(updatesMarketPath) { return ParseResult.updatesMarket }\n"
              "            guard components.path == updatesMarketPath else {"),
     "test_an_updates_link_is_exactly_the_market_chip", "no longer exactly"),
    ("parse-credentials-only-for-tickers", _GRAMMAR,
     _resub(r"(        guard components\.user == nil.*?unexpected_component\"\) \}\n)",
            r"        if host == tickerHost {\n\1        }\n"),
     "test_an_updates_link_is_exactly_the_market_chip", "no longer exactly"),
    ("parse-accepts-a-query", _GRAMMAR,
     _replace('            guard components.query == nil else { return .malformed(reason: "unknown_query_key") }\n', ""),
     "test_an_updates_link_is_exactly_the_market_chip", "no longer exactly"),
    ("parse-path-case-folded", _GRAMMAR,
     _replace("guard components.path == updatesMarketPath else {",
              "guard components.path.lowercased() == updatesMarketPath else {"),
     "test_an_updates_link_is_exactly_the_market_chip", "no longer exactly"),
    ("parse-result-comma-case", _GRAMMAR,
     _replace("        case updatesMarket\n", "        case updatesMarket, report(id: String)\n"),
     "test_the_parser_recognises_only_its_two_hosts", "ParseResult is"),
    ("builder-query-tag", _GRAMMAR,
     _replace("        components.path = updatesMarketPath\n",
              '        components.path = updatesMarketPath\n'
              '        components.queryItems = [URLQueryItem(name: "src", value: "widget")]\n'),
     "test_links_are_built_only_by_the_grammar", "updatesMarketURL no longer builds"),
    ("builder-trailing-slash", _GRAMMAR,
     _replace("components.path = updatesMarketPath\n", 'components.path = updatesMarketPath + "/"\n'),
     "test_links_are_built_only_by_the_grammar", "updatesMarketURL no longer builds"),
    # ── the router ──
    ("router-ticker-parks-updates", _ROUTER,
     _replace("destination: .ticker(symbol: ticker.symbol, assetType: assetType), receivedAt: now",
              "destination: .updatesMarket, receivedAt: now"),
     "test_the_router_parks_only_its_two_destinations", "no longer parks THAT ticker"),
    ("router-ticker-drops-resolver", _ROUTER,
     _replace("destination: .ticker(symbol: ticker.symbol, assetType: assetType)",
              "destination: .ticker(symbol: ticker.symbol, assetType: .stock)"),
     "test_the_router_parks_only_its_two_destinations", "no longer parks THAT ticker"),
    ("router-destination-comma-case", _ROUTER,
     _replace("        case updatesMarket\n", "        case updatesMarket, report(id: String)\n"),
     "test_the_router_parks_only_its_two_destinations", "PendingDeepLink.Destination is"),
    ("router-not-ours-parks", _ROUTER,
     _resub(r"(case \.notOurLink:.*?)(            return nil)",
            r'\1            if url.path == "/x" { return PendingDeepLink(destination: .updatesMarket, receivedAt: now) }\n\2'),
     "test_the_router_parks_only_its_two_destinations", "parked link outside"),
    ("router-default-arm", _ROUTER,
     _replace("case .malformed(let reason):", "default:"),
     "test_the_router_parks_only_its_two_destinations", "switch arms are"),
    # ── ContentView ──
    ("present-reaches-chat", _CONTENT,
     _resub(r"(case \.updatesMarket:\s*)openUpdatesMarket\(\)", r"\1appState.isAIChatPresented = true"),
     "test_present_dispatches_every_destination_exhaustively", "presentDeepLink no longer"),
    ("collapse-inverted", _CONTENT,
     _replace("        if AudioManager.shared.showFullScreenPlayer {\n            AudioManager.shared.collapsePlayer()",
              "        if !AudioManager.shared.showFullScreenPlayer {\n            AudioManager.shared.collapsePlayer()"),
     "test_a_market_link_tears_down_then_opens_updates_on_the_market_chip", "no longer runs"),
    ("collapse-after-the-wait", _CONTENT,
     _resub(r"        if AudioManager\.shared\.showFullScreenPlayer \{\n            AudioManager\.shared\.collapsePlayer\(\)\n        \}\n"
            r"        let open = \{\n",
            "        let open = {\n            if AudioManager.shared.showFullScreenPlayer {\n"
            "                AudioManager.shared.collapsePlayer()\n            }\n"),
     "test_a_market_link_tears_down_then_opens_updates_on_the_market_chip", "no longer runs"),
    ("focus-bump-conditional", _CONTENT,
     _replace("            updatesMarketFocusSeq &+= 1\n",
              "            if selectedTab != .updates { updatesMarketFocusSeq &+= 1 }\n"),
     "test_a_market_link_tears_down_then_opens_updates_on_the_market_chip", "no longer runs"),
    ("open-before-teardown", _CONTENT,
     _resub(r"(private func openUpdatesMarket\(\).*?)(        appState\.dismissAllPresentations\(\)\n)",
            r"\1        open()\n\2"),
     "test_a_market_link_tears_down_then_opens_updates_on_the_market_chip", "no longer runs"),
    ("open-without-the-wait", _CONTENT,
     _resub(r"(private func openUpdatesMarket\(\).*?)            _ = await ModalPresentationProbe\.waitUntilNothingPresented\(\)\n",
            r"\1"),
     "test_a_market_link_tears_down_then_opens_updates_on_the_market_chip", "no longer runs"),
    # ── UpdatesView ──
    ("focus-scroll-only-off-market", _UPDATES_VIEW,
     _replace("                            scrollProxy.scrollTo(Self.feedTopID, anchor: .top)\n",
              "                            if viewModel.selectedTab?.scope != UpdatesScope.market {\n"
              "                                scrollProxy.scrollTo(Self.feedTopID, anchor: .top)\n"
              "                            }\n"),
     "test_a_market_focus_selects_the_chip_and_scrolls_to_the_insight_card", "no longer selects"),
    ("focus-skipped-on-an-empty-feed", _UPDATES_VIEW,
     _replace("                            viewModel.focusMarket()\n",
              "                            guard !viewModel.groupedNews.isEmpty else { return }\n"
              "                            viewModel.focusMarket()\n"),
     "test_a_market_focus_selects_the_chip_and_scrolls_to_the_insight_card", "no longer selects"),
    ("anchor-resizes", _UPDATES_VIEW,
     _resub(r"(Color\.clear\s*)\.frame\(height: 0\)(\s*\.id\(Self\.feedTopID\))", r"\1.frame(height: 1)\2"),
     "test_a_market_focus_selects_the_chip_and_scrolls_to_the_insight_card", "FIRST child"),
    ("selection-never-loads", _UPDATES_VIEW,
     _replace("viewModel.selectTab(newTab)", "_ = newTab"),
     "test_a_market_focus_selects_the_chip_and_scrolls_to_the_insight_card", "no longer starts its feed load"),
    ("reset-keeps-manage-assets", _UPDATES_VIEW,
     _replace("                showManageAssetsSheet = false\n", ""),
     "test_updates_presentation_reset_clears_every_presentation_it_owns", "no longer clears"),
    ("reset-keeps-the-paywall", _UPDATES_VIEW,
     _replace("                viewModel.showPaywall = false\n", ""),
     "test_updates_presentation_reset_clears_every_presentation_it_owns", "no longer clears"),
    ("new-sheet-never-reset", _UPDATES_VIEW,
     _replace("            .onPresentationReset {\n",
              "            .sheet(isPresented: $showNewThing) { EmptyView() }\n            .onPresentationReset {\n"),
     "test_updates_presentation_reset_clears_every_presentation_it_owns", "no longer clears"),
    # ── the chip strip ──
    ("reveal-skipped-when-selected", _UPDATES_STRIP,
     _replace("                .onChange(of: marketRevealSeq) { _, _ in\n",
              "                .onChange(of: marketRevealSeq) { _, _ in\n"
              "                    guard selectedTab?.scope != UpdatesScope.market else { return }\n"),
     "test_the_chip_strip_reveals_a_code_made_selection", "ALREADY selected"),
    ("reveal-market-flush", _UPDATES_STRIP,
     _replace("proxy.scrollTo(Self.stripID, anchor: .leading)", "proxy.scrollTo(scope)"),
     "test_the_chip_strip_reveals_a_code_made_selection", "back to rest"),
    ("strip-id-off-the-padding", _UPDATES_STRIP,
     _replace("                    .id(Self.stripID)\n", ""),
     "test_the_chip_strip_reveals_a_code_made_selection", "PADDED content"),
    ("selection-follow-anchored", _UPDATES_STRIP,
     _replace("reveal(scope, with: proxy)", "proxy.scrollTo(scope, anchor: .center)"),
     "test_the_chip_strip_reveals_a_code_made_selection", "brought into view"),
    # ── the view model ──
    ("focus-guard-inverted", _UPDATES_VM,
     _replace("guard selectedTab?.scope != market.scope else { return }",
              "guard selectedTab?.scope == market.scope else { return }"),
     "test_focus_market_repoints_the_stash_then_selects", "focusMarket no longer"),
    ("focus-stash-inverted", _UPDATES_VM,
     _replace("        if pendingScope != nil {\n            pendingScope = UpdatesScope.market",
              "        if pendingScope == nil {\n            pendingScope = UpdatesScope.market"),
     "test_focus_market_repoints_the_stash_then_selects", "focusMarket no longer"),
    ("focus-early-return-first", _UPDATES_VM,
     _resub(r"(    func focusMarket\(\) \{\n)(        if pendingScope != nil \{\n.*?\n        \}\n)"
            r"(        let market = [^\n]*\n        guard selectedTab\?\.scope != market\.scope else \{ return \}\n)",
            r"\1\3\2"),
     "test_focus_market_repoints_the_stash_then_selects", "focusMarket no longer"),
    ("focus-loads-itself", _UPDATES_VM,
     _replace("        selectedTab = market\n    }\n",
              "        selectedTab = market\n        Task { await loadFeed(for: market, force: false) }\n    }\n"),
     "test_focus_market_repoints_the_stash_then_selects", "focusMarket no longer"),
    ("latch-without-the-join", _UPDATES_VM,
     _replace("            await self.joinSelectionLoad()\n", ""),
     "test_the_first_load_joins_a_selection_load_already_in_flight", "latches hasLoadedOnce"),
    ("select-tab-unrecorded", _UPDATES_VM,
     _replace("selectionLoad = (tab.scope, Task { await loadFeed(for: tab, force: false) })",
              "Task { await loadFeed(for: tab, force: false) }"),
     "test_the_first_load_joins_a_selection_load_already_in_flight", "no longer records"),
    ("join-any-scope", _UPDATES_VM,
     _replace("guard let running = selectionLoad, running.scope == inFlightScope,",
              "guard let running = selectionLoad,"),
     "test_the_first_load_joins_a_selection_load_already_in_flight", "joinSelectionLoad no longer"),
    ("identity-keeps-the-selection-load", _UPDATES_VM,
     _replace("        selectionLoad = nil\n", ""),
     "test_the_first_load_joins_a_selection_load_already_in_flight", "no longer dropped"),
    # ── the widget ──
    ("widget-url-in-one-arm", _WIDGET,
     _chain(_replace("        }\n        .widgetURL(tileURL)\n", "        }\n"),
            _replace("default:                    homeScreen { homeContent }",
                     "default:                    homeScreen { homeContent }.widgetURL(tileURL)")),
     "test_one_widget_url_on_the_root_holdings_headline_or_updates_market", "no longer set ONCE"),
    ("market-link-before-signed-out", _WIDGET,
     _resub(r"(    private var tileURL: URL\? \{\n)(        guard !entry\.isSignedOut[^\n]*\n)"
            r"(        if entry\.mode == \.market \{\n            return CaydexDeepLink\.updatesMarketURL\n        \}\n)",
            r"\1\3\2"),
     "test_one_widget_url_on_the_root_holdings_headline_or_updates_market", "tileURL no longer"),
    ("market-link-conditional", _WIDGET,
     _replace("        if entry.mode == .market {\n            return CaydexDeepLink.updatesMarketURL",
              "        if entry.mode == .market, entry.configuredMode == .market {\n"
              "            return CaydexDeepLink.updatesMarketURL"),
     "test_one_widget_url_on_the_root_holdings_headline_or_updates_market", "tileURL no longer"),
    ("placeholder-links-again", _WIDGET,
     _replace("guard !entry.isSignedOut, !entry.isPlaceholderRender else { return nil }",
              "guard !entry.isSignedOut else { return nil }"),
     "test_one_widget_url_on_the_root_holdings_headline_or_updates_market", "tileURL no longer"),
    ("placeholder-unmarked", _WIDGET,
     _replace("MoversEntry(date: Date(), snapshot: .preview, isPlaceholderRender: true)",
              "MoversEntry(date: Date(), snapshot: .preview)"),
     "test_one_widget_url_on_the_root_holdings_headline_or_updates_market", "placeholder render is no longer marked"),
    ("market-row-via-modifier", _WIDGET,
     _append("\nprivate struct MarketRowLink: View {\n    let index: WidgetIndex\n"
             "    var body: some View { Text(index.symbol).modifier(TapThrough(url: nil)) }\n}\n"),
     "test_holdings_rows_link_and_market_rows_do_not", "TapThrough is applied outside"),
    ("widget-index-extension", _WIDGET,
     _append("\nextension WidgetIndex {\n    var deepLink: URL? { CaydexDeepLink.tickerURL(symbol: symbol) }\n}\n"),
     "test_asset_classes_come_from_the_server", "extends WidgetIndex"),
    ("schema-field-commented-out", _WIDGET_SCHEMA,
     _replace("    asset_type: Optional[str] = None\n", "    # asset_type: Optional[str] = None  (moved)\n"),
     "test_asset_classes_come_from_the_server", "no longer declares `asset_type"),
    # ── found by the review of the fix pass (2026-10-22) ──
    ("consume-only-tickers", _CONTENT,
     _replace("guard let link = DeepLinkRouter.freshLink(parked) else { return }",
              "guard let link = DeepLinkRouter.freshLink(parked), case .ticker = link.destination else { return }"),
     "test_content_view_consumes_the_link_once_every_gate_is_open", "the handler no longer"),
    ("consume-skips-on-updates", _CONTENT,
     _replace("            presentDeepLink(link)\n",
              "            if selectedTab != .updates { presentDeepLink(link) }\n"),
     "test_content_view_consumes_the_link_once_every_gate_is_open", "the handler no longer"),
    ("consume-without-expiry", _CONTENT,
     _replace("guard let link = DeepLinkRouter.freshLink(parked) else { return }",
              "_ = DeepLinkRouter.freshLink(parked)\n            let link = parked"),
     "test_content_view_consumes_the_link_once_every_gate_is_open", "the handler no longer"),
    ("consume-presents-before-clearing", _CONTENT,
     _resub(r"(            appState\.pendingDeepLink = nil\n)(            guard let link = DeepLinkRouter\.freshLink\(parked\) else \{ return \}\n"
            r"            presentDeepLink\(link\)\n)", r"\2\1"),
     "test_content_view_consumes_the_link_once_every_gate_is_open", "the handler no longer"),
    ("parse-answer-in-the-ticker-path", _GRAMMAR,
     _replace("        let path = components.path\n",
              "        let path = components.path\n"
              '        if path.lowercased() == "/market" { return .updatesMarket }\n'),
     "test_an_updates_link_is_exactly_the_market_chip", "names .updatesMarket"),
    ("updates-tab-loses-the-focus-seq", _CONTENT,
     _replace("UpdatesView(selectedTab: $selectedTab, marketFocusSeq: updatesMarketFocusSeq)",
              "UpdatesView(selectedTab: $selectedTab)"),
     "test_a_market_link_tears_down_then_opens_updates_on_the_market_chip", "its one Updates tab"),
    ("focus-handler-on-the-anchor", _UPDATES_VIEW,
     _chain(_resub(r"\n\s*\.onChange\(of: marketFocusSeq\) \{ _, _ in\n\s*viewModel\.focusMarket\(\)\n"
                   r"\s*scrollProxy\.scrollTo\(Self\.feedTopID, anchor: \.top\)\n\s*\}", ""),
            _replace("                                    .id(Self.feedTopID)\n",
                     "                                    .id(Self.feedTopID)\n"
                     "                                    .onChange(of: marketFocusSeq) { _, _ in\n"
                     "                                        viewModel.focusMarket()\n"
                     "                                        scrollProxy.scrollTo(Self.feedTopID, anchor: .top)\n"
                     "                                    }\n")),
     "test_a_market_focus_selects_the_chip_and_scrolls_to_the_insight_card", "no longer sits on the feed"),
    ("anchor-below-the-card", _UPDATES_VIEW,
     _replace("                                    .id(summary.id)\n",
              "                                    .id(summary.id)\n"
              "                                Color.clear.frame(height: 0).id(Self.feedTopID)\n"),
     "test_a_market_focus_selects_the_chip_and_scrolls_to_the_insight_card", "above the Insights card"),
    ("selection-handler-filters", _UPDATES_VIEW,
     _replace("viewModel.selectTab(newTab)", "if newTab.isMarketTab { viewModel.selectTab(newTab) }"),
     "test_a_market_focus_selects_the_chip_and_scrolls_to_the_insight_card", "no longer starts its feed load"),
    ("reset-conditional", _UPDATES_VIEW,
     _replace("                showProfile = false\n", "                if !isActiveTab { showProfile = false }\n"),
     "test_updates_presentation_reset_clears_every_presentation_it_owns", "must be nothing but unconditional"),
    ("reset-early-return", _UPDATES_VIEW,
     _replace("            .onPresentationReset {\n",
              "            .onPresentationReset {\n                guard !viewModel.isLoading else { return }\n"),
     "test_updates_presentation_reset_clears_every_presentation_it_owns", "must be nothing but unconditional"),
    ("reset-shadowed-name", _UPDATES_VIEW,
     _replace(".sheet(isPresented: $viewModel.showPaywall)", ".sheet(isPresented: $showPaywall)"),
     "test_updates_presentation_reset_clears_every_presentation_it_owns", "no longer clears"),
    ("unknown-presentation-modifier", _UPDATES_VIEW,
     _replace("            .onPresentationReset {\n",
              "            .alertDestinationCover($insightDestination)\n            .onPresentationReset {\n"),
     "test_updates_presentation_reset_clears_every_presentation_it_owns", "no longer clears"),
    ("unknown-binding-modifier", _UPDATES_VIEW,
     _replace("            .onPresentationReset {\n",
              "            .storyReader(item: $openedStory)\n            .onPresentationReset {\n"),
     "test_updates_presentation_reset_clears_every_presentation_it_owns", "add it to _PRESENTATIONS"),
    ("reset-order-reopens-chat", _UPDATES_VIEW,
     _resub(r"(                pendingChatFocus = nil\n)(                insightSources = nil\n)", r"\2\1"),
     "test_updates_presentation_reset_clears_every_presentation_it_owns", "reopen the chat"),
    ("strip-id-before-the-padding", _UPDATES_STRIP,
     _resub(r"(\n\s*\.padding\(\.horizontal, AppSpacing\.lg\))((?:\n\s*//[^\n]*)*)(\n\s*\.id\(Self\.stripID\))",
            r"\2\3\1"),
     "test_the_chip_strip_reveals_a_code_made_selection", "PADDED content"),
    ("strip-id-value-collides", _UPDATES_STRIP,
     _replace('private static let stripID = "updates.tabbar.strip"', "private static let stripID = UpdatesScope.market"),
     "test_the_chip_strip_reveals_a_code_made_selection", "no chip scope"),
    ("strip-loses-the-reveal-seq", _UPDATES_VIEW,
     _replace("marketRevealSeq: marketFocusSeq", "marketRevealSeq: 0"),
     "test_the_chip_strip_reveals_a_code_made_selection", "hands the tap to its chip strip"),
    ("latch-on-tabs", _UPDATES_VM,
     _replace("            tabsAreLive = true\n", "            tabsAreLive = true\n            hasLoadedOnce = true\n"),
     "test_the_first_load_joins_a_selection_load_already_in_flight", "latched in 2 places"),
    ("join-after-the-latch", _UPDATES_VM,
     _resub(r"(            await self\.joinSelectionLoad\(\)\n)(.*?self\.hasLoadedOnce = true\n\s*\}\n)", r"\2\1"),
     "test_the_first_load_joins_a_selection_load_already_in_flight", "BEFORE it latches"),
    ("selection-load-dropped-after-an-await", _UPDATES_VM,
     _chain(_replace("        selectionLoad = nil\n", ""),
            _replace("        await prepareSnapshot()\n", "        await prepareSnapshot()\n        selectionLoad = nil\n")),
     "test_the_first_load_joins_a_selection_load_already_in_flight", "BEFORE its first suspension"),
    ("timeline-entry-marked", _WIDGET,
     _replace("return MoversEntry(date: date, snapshot: snap, mode: mode, configuredMode: configuration.mode)",
              "return MoversEntry(date: date, snapshot: snap, mode: mode, configuredMode: configuration.mode, "
              "isPlaceholderRender: snap == nil)"),
     "test_one_widget_url_on_the_root_holdings_headline_or_updates_market", "named 4 times"),
    ("placeholder-default-flipped", _WIDGET,
     _replace("var isPlaceholderRender: Bool = false", "var isPlaceholderRender: Bool = true"),
     "test_one_widget_url_on_the_root_holdings_headline_or_updates_market", "no longer defaults"),
    # ── the teardown gaps: every view a tab root reaches (2026-10-22) ──
    ("header-logo-cover-kept", _HEADER, _drop_line("            showSloganSheet = false"),
     _REACH, "no longer clears every presentation"),
    ("research-header-cover-kept", _RESEARCH_HEADER, _drop_line("            showSloganSheet = false"),
     _REACH, "no longer clears every presentation"),
    ("root-sign-in-prompt-kept", _IOS_APP, _drop_line("            appState.signInPrompt = nil"),
     _REACH, "no longer clears every presentation"),
    ("root-buy-credits-kept", _IOS_APP, _drop_line("            showPaywallFromError = false"),
     _REACH, "no longer clears every presentation"),
    ("research-delete-alert-kept", _CONTENT, _drop_line("            viewModel.showDeleteConfirm = false"),
     _REACH, "no longer clears every presentation"),
    ("research-error-alert-kept", _CONTENT, _drop_line("            viewModel.error = nil"),
     _REACH, "no longer clears every presentation"),
    ("tracking-manage-tickers-kept", _TRACKING, _drop_line("                viewModel.showManageTickersSheet = false"),
     _REACH, "no longer clears every presentation"),
    ("whales-paywall-unreset", _TRACKING,
     _replace("        .onPresentationReset {\n            viewModel.showWhalePaywall = false\n        }\n", ""),
     _REACH, "no longer clears every presentation"),
    ("all-whales-paywall-kept", _ALL_WHALES, _drop_line("            viewModel.showWhalePaywall = false"),
     _REACH, "no longer clears every presentation"),
    ("money-moves-article-kept", _MONEY_MOVES, _drop_line("            selectedArticle = nil"),
     _REACH, "no longer clears every presentation"),
    ("library-chat-kept", _LIBRARY, _drop_line("            showBookChat = false"),
     _REACH, "no longer clears every presentation"),
    ("journey-story-kept", _JOURNEY_VIEW, _drop_line("            viewModel.showLessonStory = false"),
     _REACH, "no longer clears every presentation"),
    ("audio-reader-kept", _ROOT_CONTAINER, _drop_line("            readerRoute = nil"),
     _REACH, "no longer clears every presentation"),
    ("player-share-sheet-kept", _PLAYER, _drop_line("            showShareSheet = false"),
     _REACH, "no longer clears every presentation"),
    ("narration-paywall-kept", _AUDIO_PAYWALL, _drop_line("                showPaywall = false"),
     _REACH, "no longer clears every presentation"),
    ("disclaimer-atom-kept", _DISCLAIMER_ATOM, _drop_line("            showDisclaimers = false"),
     _REACH, "no longer clears every presentation"),
    ("scanner-popover-kept", _SCANNER, _drop_line("                            showInfo = false"),
     _REACH, "no longer clears every presentation"),
    ("new-header-organism-with-a-sheet", _HEADER,
     _chain(_replace("            AskCayAIButton { appState.requestAIChat() }\n",
                     "            AskCayAIButton { appState.requestAIChat() }\n            HeaderProbeBadge()\n"),
            _append("\nstruct HeaderProbeBadge: View {\n    @State private var showNote = false\n"
                    "    var body: some View {\n        Button(\"i\") { showNote = true }\n"
                    "            .sheet(isPresented: $showNote) { Text(\"note\") }\n    }\n}\n")),
     _REACH, "HeaderProbeBadge"),
    ("new-kept-push-with-a-cover", _LEARN,
     _chain(_replace("        .navigationDestination(isPresented: $showingBookLibrary) {\n",
                     "        .navigationDestination(isPresented: $showingProbe) {\n            ProbeDetailScreen()\n        }\n"
                     "        .navigationDestination(isPresented: $showingBookLibrary) {\n"),
            _append("\nprivate struct ProbeDetailScreen: View {\n    @State private var showCover = false\n"
                    "    var body: some View {\n        Text(\"probe\")\n"
                    "            .fullScreenCover(isPresented: $showCover) { Text(\"cover\") }\n    }\n}\n")),
     _REACH, "ProbeDetailScreen"),
    ("push-reset-conditional", _MONEY_MOVES,
     _replace("\n            selectedArticle = nil\n", "\n            if featured == nil { selectedArticle = nil }\n"),
     _REACH, "must be nothing but unconditional"),
    ("push-binding-modifier-unknown", _LIBRARY,
     _replace("        .aiChatCover(isPresented: $showBookChat, viewModel: bookChatViewModel)\n",
              "        .aiChatCover(isPresented: $showBookChat, viewModel: bookChatViewModel)\n"
              "        .storyReader(item: $openedStory)\n"),
     _REACH, "add it to _PRESENTATIONS"),
    # On `miniPlayerSafeArea()`, the audio helper the kept pushes call now that none of them
    # carries `globalAudioOverlay` (see `_MUST_REACH`).
    ("helper-presents-from-its-parameters", _ROOT_CONTAINER,
     _replace("        modifier(MiniPlayerSafeAreaModifier())\n",
              "        sheet(isPresented: .constant(true)) { EmptyView() }.modifier(MiniPlayerSafeAreaModifier())\n"),
     _REACH, "presents from its parameters"),
    ("walk-loses-a-kept-push", _LEARN,
     _replace("            MoneyMovesDetailView()\n", "            EmptyView()\n"),
     "test_the_walk_reaches_every_view_it_must_guard", "no longer reaches"),
    ("gate-entry-stale", _IOS_APP,
     _replace("                OnboardingView()\n", "                OnboardingFlowView()\n"),
     "test_the_walk_reaches_every_view_it_must_guard", "RootView no longer draws it"),
    ("journey-rebuild-only-on-its-button", _JOURNEY_VIEW,
     _replace(", onDismiss: {\n            viewModel.lessonStoryDidClose()\n        }) {", ") {"),
     "test_the_journey_story_refreshes_its_grid_however_it_closes", "no longer rebuilds"),
    ("journey-close-skips-the-rebuild", _JOURNEY_VM,
     _replace("        selectedLesson = nil\n        rebuildJourney()\n    }", "        selectedLesson = nil\n    }"),
     "test_the_journey_story_refreshes_its_grid_however_it_closes", "lessonStoryDidClose no longer"),
]


@pytest.mark.parametrize("label,path,mutate,guard,message", _MUTATIONS, ids=[m[0] for m in _MUTATIONS])
def test_every_guard_kills_its_mutation(label, path, mutate, guard, message, monkeypatch):
    real_read = Path.read_text
    original = real_read(path, encoding="utf-8")
    mutated = mutate(original)
    if path.suffix == ".swift":
        assert _strip_comments(mutated) != _strip_comments(original), (
            f"{label}: the mutation only touched a comment, so it tests nothing"
        )

    def read_text(self, *args, **kwargs):
        return mutated if self == path else real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    with pytest.raises(AssertionError) as caught:
        globals()[guard]()
    assert message in str(caught.value), (
        f"{label}: {guard} failed, but not with its own message — the mutation broke something "
        f"else:\n{caught.value}"
    )


# The guards the Market tap depends on; each must be shown failing on its bug at least once.
_MUTATION_COVERED = {
    "test_content_view_consumes_the_link_once_every_gate_is_open",
    "test_the_parser_recognises_only_its_two_hosts",
    "test_an_updates_link_is_exactly_the_market_chip",
    "test_links_are_built_only_by_the_grammar",
    "test_the_router_parks_only_its_two_destinations",
    "test_present_dispatches_every_destination_exhaustively",
    "test_a_market_link_tears_down_then_opens_updates_on_the_market_chip",
    "test_a_market_focus_selects_the_chip_and_scrolls_to_the_insight_card",
    "test_updates_presentation_reset_clears_every_presentation_it_owns",
    "test_the_chip_strip_reveals_a_code_made_selection",
    "test_focus_market_repoints_the_stash_then_selects",
    "test_the_first_load_joins_a_selection_load_already_in_flight",
    "test_one_widget_url_on_the_root_holdings_headline_or_updates_market",
    "test_holdings_rows_link_and_market_rows_do_not",
    "test_asset_classes_come_from_the_server",
    "test_every_view_a_tab_root_reaches_resets_what_it_presents",
    "test_the_walk_reaches_every_view_it_must_guard",
    "test_the_journey_story_refreshes_its_grid_however_it_closes",
}


def test_every_market_tap_guard_has_a_mutation():
    rows = {m[3] for m in _MUTATIONS}
    assert _MUTATION_COVERED <= rows, f"guards with no mutation row: {sorted(_MUTATION_COVERED - rows)}"
    assert rows <= set(globals()), f"mutation rows name guards that do not exist: {sorted(rows - set(globals()))}"
    labels = [m[0] for m in _MUTATIONS]
    assert len(labels) == len(set(labels)), "mutation labels are not unique"


# ── Harmless refactors: each guard must stay GREEN ───────────────────────────
#
# The other side of the table above. A guard that goes red on a change with no behaviour in it
# sends the next engineer hunting for a bug that is not there (the review of the fix pass found
# a reflowed guard, a log line and a shorthand `if let` each did exactly that).

_HARMLESS: list[tuple[str, Path, Callable[[str], str], str]] = [
    ("shorthand-if-let", _UPDATES_VIEW,
     _chain(_replace("if let newTab = newValue {", "if let newValue {"),
            _replace("viewModel.selectTab(newTab)", "viewModel.selectTab(newValue)")),
     "test_a_market_focus_selects_the_chip_and_scrolls_to_the_insight_card"),
    ("guard-let-selection", _UPDATES_VIEW,
     _resub(r"if let newTab = newValue \{\n(\s*)viewModel\.selectTab\(newTab\)\n\s*\}",
            r"guard let newTab = newValue else { return }\n\1viewModel.selectTab(newTab)"),
     "test_a_market_focus_selects_the_chip_and_scrolls_to_the_insight_card"),
    ("log-line-in-the-first-load", _UPDATES_VM,
     _replace("            await self.loadInitialData()\n",
              '            await self.loadInitialData()\n            Self.log.info("first load answered")\n'),
     "test_the_first_load_joins_a_selection_load_already_in_flight"),
    ("modifier-after-the-strip-id", _UPDATES_STRIP,
     _replace("                    .id(Self.stripID)\n",
              "                    .id(Self.stripID)\n                    .accessibilityElement(children: .contain)\n"),
     "test_the_chip_strip_reveals_a_code_made_selection"),
    ("placeholder-preview", _WIDGET,
     _append('\n#Preview("Placeholder", as: .systemSmall) {\n    MoversWidget()\n} timeline: {\n'
             "    MoversEntry(date: .now, snapshot: .preview, isPlaceholderRender: true)\n}\n"),
     "test_one_widget_url_on_the_root_holdings_headline_or_updates_market"),
    ("renamed-open-closure", _CONTENT,
     _chain(_replace("        let open = {\n", "        let land = {\n"),
            _replace("            open()\n            return\n", "            land()\n            return\n"),
            _replace("waitUntilNothingPresented()\n            open()\n", "waitUntilNothingPresented()\n            land()\n")),
     "test_a_market_link_tears_down_then_opens_updates_on_the_market_chip"),
    ("one-line-reset", _HEADER,
     _replace(".onPresentationReset {\n            showSloganSheet = false\n        }",
              ".onPresentationReset { showSloganSheet = false }"),
     _REACH),
    ("semicolon-reset", _LIBRARY,
     _replace("            selectedBook = nil\n            showBookChat = false\n",
              "            selectedBook = nil; showBookChat = false\n"),
     _REACH),
    ("comment-in-a-reset", _ALL_WHALES,
     _replace("        .onPresentationReset {\n            viewModel.showWhalePaywall = false\n",
              "        .onPresentationReset {\n            // the plan sheet\n            viewModel.showWhalePaywall = false\n"),
     _REACH),
    ("reset-split-in-two", _PLAYER,
     _replace("            showSleepTimer = false\n            showShareSheet = false\n        }",
              "            showSleepTimer = false\n        }\n        .onPresentationReset {\n"
              "            showShareSheet = false\n        }"),
     _REACH),
    ("cover-with-on-dismiss", _HEADER,
     _replace(".fullScreenCover(isPresented: $showSloganSheet) {",
              ".fullScreenCover(isPresented: $showSloganSheet, onDismiss: { }) {"),
     _REACH),
    ("reflowed-query-guard", _GRAMMAR,
     _replace('            guard components.query == nil else { return .malformed(reason: "unknown_query_key") }\n',
              "            guard components.query == nil else {\n"
              '                return .malformed(reason: "unknown_query_key")\n            }\n'),
     "test_an_updates_link_is_exactly_the_market_chip"),
]


@pytest.mark.parametrize("label,path,mutate,guard", _HARMLESS, ids=[m[0] for m in _HARMLESS])
def test_a_harmless_refactor_keeps_its_guard_green(label, path, mutate, guard, monkeypatch):
    real_read = Path.read_text
    original = real_read(path, encoding="utf-8")
    changed = mutate(original)
    assert changed != original, f"{label}: the refactor changed nothing"

    def read_text(self, *args, **kwargs):
        return changed if self == path else real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    globals()[guard]()
