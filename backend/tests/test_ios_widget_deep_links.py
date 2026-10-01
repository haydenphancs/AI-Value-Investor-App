"""Source-scan guards for the widget → app deep link (`caydex://ticker/<SYMBOL>[?type=…]`).

There is no XCTest target, so these read the Swift source. They pin four things:

1. ONE GRAMMAR. `Shared/CaydexDeepLink.swift` compiles into both targets. The widget builds
   links only through `CaydexDeepLink.tickerURL` and the app parses them only through
   `CaydexDeepLink.parse`, so the two sides cannot drift. The parser is exact: an ASCII symbol
   grammar, a `type` allow-list, and no unknown query keys. Any app or web page can open a
   `caydex://` URL, so this is untrusted input.
2. THE OAUTH CALLBACK IS LEFT ALONE. `caydex` is also `APIConfig.oauthCallbackScheme`
   (`caydex://auth-callback`). The parser recognises only the `ticker` host, and the scheme is
   one value in all three places that name it.
3. SIGN-IN GATED (auth.md §1a). `.onOpenURL` only PARKS the link. `ContentView`, which exists
   only past the sign-in wall, opens it, and only once `DeepLinkRouter.canPresent` allows it
   plus the disclaimer and onboarding gates. A signed-out tap therefore lands on `SignInView`
   and never draws FMP data.
4. THE WIDGET LINKS what it says it links: `.widgetURL` on the Small headline, a `Link` per row
   on Medium/Large, market assets only when the server named their class.

Every scan strips comments first and brace-bounds the declaration it means (testing.md §3).
The explanatory comments beside this code name every token asserted here.
"""

from __future__ import annotations

import plistlib
import re
from pathlib import Path

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


def test_the_parser_recognises_only_the_ticker_host():
    src = _read(_GRAMMAR)
    assert _string_constant(src, "tickerHost") == "ticker"
    parse = _block(src, "public static func parse(_ url: URL) -> ParseResult")
    assert "components.host?.lowercased() == tickerHost" in parse, (
        "parse no longer pins the host: an OAuth `caydex://auth-callback` could be read as a ticker"
    )
    guard = parse[parse.index("components.host?.lowercased() == tickerHost"):]
    assert guard.index("else { return .notTickerLink }") < guard.index("return .ticker("), (
        "a non-ticker host no longer short-circuits to .notTickerLink before a ticker is built"
    )
    assert "auth-callback" not in src, (
        "the grammar special-cases the OAuth callback. It must not know about it at all: "
        "anything that is not the ticker host is simply not ours"
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
                   "invalid_symbol", "unexpected_component", "url_too_long"):
        assert f'.malformed(reason: "{reason}")' in parse, (
            f"parse no longer refuses `{reason}`: a link outside the grammar would be coerced "
            "into the nearest valid one"
        )
    assert "maxURLLength" in parse.split("URLComponents(url:")[0], (
        "the length cap no longer runs before parsing"
    )


def test_links_are_built_only_by_the_grammar():
    """Nobody hand-writes `caydex://…`: a second spelling is how builder and parser drift."""
    build = _block(_read(_GRAMMAR), "public static func tickerURL(")
    assert "URLComponents()" in build and "normalizedSymbol(symbol)" in build, (
        "tickerURL no longer validates the symbol or no longer percent-encodes via URLComponents "
        "(`^GSPC` needs `%5E` in a path)"
    )
    offenders = []
    for root in (_APP, _SHARED, _WIDGETS):
        for path in _swift_files(root):
            if path == _GRAMMAR:
                continue
            code = _strip_comments(path.read_text(encoding="utf-8"))
            if re.search(r'"caydex://ticker', code) or re.search(r'URL\(string:\s*"caydex://', code):
                offenders.append(str(path.relative_to(_ROOT)))
    assert not offenders, f"hand-built caydex:// ticker URLs (use CaydexDeepLink.tickerURL): {offenders}"


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


def test_the_router_declines_everything_but_a_ticker_link():
    body = _block(_read(_ROUTER), "static func pendingLink(for url: URL")
    assert "CaydexDeepLink.parse(url)" in body
    ticker = body[body.index("case .ticker(let ticker):"):body.index("case .notTickerLink:")]
    assert "MarketTickerType.resolve(" in ticker, (
        "the router picks the screen by its own rule. It must use the resolver search results "
        "use, which sends `BTCUSD` to the crypto screen even when typed `stock`"
    )
    not_ours = body[body.index("case .notTickerLink:"):body.index("case .malformed")]
    assert "return nil" in not_ours and "log." in not_ours, (
        "a non-ticker URL (e.g. the OAuth callback) is no longer ignored-and-logged"
    )
    malformed = body[body.index("case .malformed"):]
    assert "log.warning(" in malformed and "return nil" in malformed, (
        "a malformed link is no longer declined LOUDLY: a silent drop is undiagnosable"
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
    handler = _block(src, opener)
    assert "guard trigger.ready, let parked = trigger.link else { return }" in handler
    assert handler.index("appState.pendingDeepLink = nil") < handler.index("presentDeepLink("), (
        "the link is presented before it is cleared, so one tap could open twice"
    )
    assert "DeepLinkRouter.freshLink(parked)" in handler, "an expired parked link would still open"


def test_present_tears_down_then_opens_a_ticker_destination():
    body = _block(_read(_CONTENT), "private func presentDeepLink(_ link: PendingDeepLink)")
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


# ── 4. The widget links what it shows ────────────────────────────────────────

_WIDGET = _WIDGETS / "MoversWidget.swift"
_SNAPSHOT = _SHARED / "WidgetSnapshotStore.swift"
_WIDGET_SCHEMA = Path(__file__).resolve().parents[1] / "app" / "schemas" / "widget.py"


def test_one_widget_url_set_on_the_root_to_the_holdings_headline():
    src = _read(_WIDGET)
    assert src.count(".widgetURL(") == 1, (
        "a widget gets ONE .widgetURL. A second one deeper in the tree is undefined behaviour, "
        "and on Small it silently decides where every tap goes"
    )
    body = _block(_block(src, "struct MoversWidgetView: View"), "var body: some View")
    assert ".widgetURL(tileURL)" in body, "the tile-wide URL is no longer set on the root view"
    tile = _block(src, "private var tileURL: URL?")
    for cond in ("!entry.isSignedOut", "entry.mode == .portfolio", 'snap.mode == "portfolio"',
                 "let headline = snap.headlineMover"):
        assert cond in tile, (
            f"tileURL lost `{cond}`: a Small tile would link a mover the user does not hold "
            "(a market payload standing in for Holdings), or link while signed out"
        )
    assert "return headline.deepLink" in tile


def test_rows_link_through_on_medium_and_large():
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
    assert ".tapThrough(assets[i].deepLink)" in _block(src, "private struct AssetGrid: View"), (
        "a Medium market cell no longer links to its asset"
    )
    assert ".tapThrough($0.deepLink)" in _block(src, "private struct AssetPriceList: View"), (
        "a Large market row no longer links to its asset"
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


def test_asset_classes_come_from_the_server_and_markets_never_guess():
    src = _read(_WIDGET)
    index_link = _block(_block(src, "private extension WidgetIndex"), "var deepLink: URL?")
    assert "guard let assetClass = CaydexDeepLink.AssetClass(wire: assetType) else { return nil }" in index_link, (
        "a market asset links with no server-named class: SPY/ONEQ/DIA would resolve to the "
        "STOCK screen. No link beats a wrong screen"
    )
    mover_link = _block(_block(src, "private extension WidgetMover"), "var deepLink: URL?")
    assert "CaydexDeepLink.tickerURL(symbol: ticker, assetClass: CaydexDeepLink.AssetClass(wire: assetType))" in mover_link

    shared = _read(_SNAPSHOT)
    for struct in ("public struct WidgetIndex", "public struct WidgetMover:"):
        decl = _block(shared, struct)
        assert "public let assetType: String?" in decl, f"{struct} lost its optional assetType"
        assert 'case assetType = "asset_type"' in decl, f"{struct} no longer decodes `asset_type`"
    schema = _WIDGET_SCHEMA.read_text(encoding="utf-8")
    for model in ("class WidgetIndexResponse(BaseModel):", "class WidgetMoverResponse(BaseModel):"):
        start = schema.index(model)
        nxt = schema.find("\nclass ", start + 1)
        assert "asset_type: Optional[str] = None" in schema[start:nxt if nxt != -1 else None], (
            f"{model} no longer sends an optional asset_type for the widget's links"
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
