//
//  CaydexDeepLink.swift
//  Caydex
//
//  The ONE grammar for the `caydex://` links the widget builds and the app opens.
//

import Foundation

/// The `caydex://` links a Home Screen widget tap opens. Exactly three shapes:
/// - `caydex://ticker/<SYMBOL>[?type=<class>]` → that asset's detail screen (a Holdings tap);
/// - `caydex://updates/market` → the Updates tab on its Market chip, where the Market Insight
///   card leads (a Market tap; owner, 2026-10-22);
/// - `caydex://updates/<SYMBOL>[?type=<class>]` → the Updates tab on that ticker's chip, where its
///   Insights card and the card's sources are (a Holdings row whose cause came from that card, the
///   payload's `cause_card`; 2026-11-12). The app falls back to the ticker's detail screen when the
///   ticker is not one of the user's chips; `type` picks that fallback screen, as on a ticker link
///   (an ETF holding must not fall back to the equity screen).
///
/// Lives in `Shared/`, so it compiles into BOTH targets. The widget that BUILDS a link and the
/// app that PARSES it share one grammar, and neither side can drift from the other.
///
/// ⚠️ `caydex` IS ALSO THE OAUTH CALLBACK SCHEME (`APIConfig.oauthCallbackScheme`, redirect
/// `caydex://auth-callback`). `ASWebAuthenticationSession` consumes that callback itself, so it
/// never reaches `.onOpenURL`. The parser still recognises ONLY the `ticker` and `updates`
/// hosts, and answers `.notOurLink` for everything else. A callback that did arrive is
/// therefore left alone, never misread as a link of ours. `test_ios_widget_deep_links.py` pins the
/// scheme equal in all three places (here, `APIConfig`, Info.plist).
///
/// ⚠️ UNTRUSTED INPUT. Any app or web page can open a `caydex://` URL, not just our widget. So
/// the grammar is exact, and anything outside it is `.malformed`, never coerced into the
/// nearest valid link:
/// - the symbol is ASCII `[A-Z0-9]` segments joined by `.` or `-`, with an optional leading
///   `^`, at most `maxSymbolLength` characters;
/// - `type` is one of `AssetClass`, given at most once, and is the only query key allowed
///   (`typeQuery`, ONE reader shared by the ticker and `updates/<SYMBOL>` links);
/// - an `updates` link's path is exactly `/market`, with no query at all, or ONE symbol in its
///   canonical form (already uppercased and trimmed, as `updatesTickerURL` builds it) with the
///   ticker link's optional `type`, never `MARKET` (reserved: that is the chip, not a ticker) — so
///   `/research`, `/MARKET` and `/market?type=etf` are refused;
/// - there are no credentials, port, fragment or extra path segments.
///
/// What a link may do is decided by the app (`DeepLinkRouter`), not here. That is: open a
/// detail screen or the Updates tab (its Market chip or one ticker's chip), behind the sign-in
/// wall, and nothing that spends credits.
nonisolated public enum CaydexDeepLink {

    /// Pinned equal to `APIConfig.oauthCallbackScheme` and Info.plist's `CFBundleURLSchemes`.
    public static let scheme = "caydex"
    static let tickerHost = "ticker"
    static let updatesHost = "updates"
    /// The Market chip's path on the `updates` host. Lower-case and exact.
    static let updatesMarketPath = "/market"
    /// The one symbol an `updates/<SYMBOL>` link may never name: in any case-folding reader its
    /// path is the Market chip's. Neither built nor parsed as a ticker.
    static let reservedUpdatesSymbol = "MARKET"
    static let typeKey = "type"
    /// Longer than any listed symbol (`SHOP.TO`, `BRK-B`, `^GSPC`, `BTCUSD`) with headroom.
    static let maxSymbolLength = 20
    /// Checked before any parsing, so a pathological URL costs nothing.
    static let maxURLLength = 256

    /// Which detail screen a link asks for. The raw values are the backend's lowercase wire
    /// spellings (`asset_type` on the widget payload).
    public enum AssetClass: String, CaseIterable, Sendable {
        case stock, etf, crypto, index, commodity

        /// The backend's spelling, case-insensitively. The search route's "fund" is the ETF
        /// screen, the same as `MarketTickerType.resolve`. Anything else is nil.
        public init?(wire raw: String?) {
            guard let raw else { return nil }
            let normalized = raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
            self.init(rawValue: normalized == "fund" ? "etf" : normalized)
        }
    }

    /// A parsed ticker link. `assetClass` nil means "resolve from the symbol".
    public struct Ticker: Equatable, Hashable, Sendable {
        public let symbol: String
        public let assetClass: AssetClass?
    }

    /// What `parse` decided, with a reason worth logging when it declined.
    public enum ParseResult: Equatable, Sendable {
        case ticker(Ticker)
        /// `caydex://updates/market`: the Updates tab on its Market chip.
        case updatesMarket
        /// `caydex://updates/<SYMBOL>[?type=<class>]`: the Updates tab on that ticker's chip
        /// (canonical symbol). `assetClass` is for the fallback detail screen only; nil means
        /// "resolve from the symbol", as on a ticker link.
        case updatesTicker(symbol: String, assetClass: AssetClass?)
        /// Not a link of ours at all: another scheme, or another `caydex` host such as the
        /// OAuth `auth-callback`. The caller must leave it alone.
        case notOurLink
        /// A `caydex://ticker` or `caydex://updates` link that breaks the grammar. Declined,
        /// never repaired.
        case malformed(reason: String)
    }

    // MARK: - Build (widget)

    /// The link for one asset, or nil when `symbol` is not a valid symbol.
    ///
    /// Built with `URLComponents`, which percent-encodes `^` (not legal in a URL path), so
    /// `^GSPC` travels as `/%5EGSPC` and parses back to `^GSPC`.
    public static func tickerURL(symbol: String, assetClass: AssetClass? = nil) -> URL? {
        guard let normalized = normalizedSymbol(symbol) else { return nil }
        var components = URLComponents()
        components.scheme = scheme
        components.host = tickerHost
        components.path = "/" + normalized
        if let assetClass {
            components.queryItems = [URLQueryItem(name: typeKey, value: assetClass.rawValue)]
        }
        return components.url
    }

    /// The Market widget's link: the Updates tab on its Market chip. Built here, like every
    /// link, so the widget never spells a `caydex://` URL by hand.
    public static var updatesMarketURL: URL? {
        var components = URLComponents()
        components.scheme = scheme
        components.host = updatesHost
        components.path = updatesMarketPath
        return components.url
    }

    /// A Holdings row whose cause came from the ticker's Insights card (`cause_card`): the
    /// Updates tab on that ticker's chip, where the card's sources are. nil for a symbol outside
    /// the grammar, and for the reserved `MARKET`. Built like `tickerURL`: `URLComponents`
    /// percent-encodes `^`, and the only query is the optional `type` of the fallback screen.
    public static func updatesTickerURL(symbol: String, assetClass: AssetClass? = nil) -> URL? {
        guard let normalized = normalizedSymbol(symbol), normalized != reservedUpdatesSymbol else {
            return nil
        }
        var components = URLComponents()
        components.scheme = scheme
        components.host = updatesHost
        components.path = "/" + normalized
        if let assetClass {
            components.queryItems = [URLQueryItem(name: typeKey, value: assetClass.rawValue)]
        }
        return components.url
    }

    // MARK: - Parse (app)

    public static func parse(_ url: URL) -> ParseResult {
        guard url.absoluteString.count <= maxURLLength else {
            return url.scheme?.lowercased() == scheme
                ? .malformed(reason: "url_too_long") : .notOurLink
        }
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              components.scheme?.lowercased() == scheme,
              let host = components.host?.lowercased(),
              host == tickerHost || host == updatesHost
        else { return .notOurLink }

        guard components.user == nil, components.password == nil,
              components.port == nil, components.fragment == nil
        else { return .malformed(reason: "unexpected_component") }

        if host == updatesHost {
            if components.path == updatesMarketPath {
                // `query` (not `queryItems`) so a bare trailing `?` is refused too.
                guard components.query == nil else { return .malformed(reason: "unknown_query_key") }
                return .updatesMarket
            }
            // `caydex://updates/<SYMBOL>`: one symbol, exactly as `updatesTickerURL` builds it.
            // The canonical-form check refuses a lowercase, padded or otherwise repaired
            // spelling, so `/research` can never become RESEARCH.
            guard components.path.hasPrefix("/") else {
                return .malformed(reason: "unknown_updates_path")
            }
            let rawUpdatesSymbol = String(components.path.dropFirst())
            guard !rawUpdatesSymbol.contains("/"),
                  let symbol = normalizedSymbol(rawUpdatesSymbol),
                  symbol == rawUpdatesSymbol,
                  symbol != reservedUpdatesSymbol
            else { return .malformed(reason: "unknown_updates_path") }
            // The ticker link's optional `type`, read by the SAME reader: it only picks the
            // fallback detail screen.
            switch typeQuery(components.queryItems) {
            case .refused(let reason): return .malformed(reason: reason)
            case .accepted(let assetClass): return .updatesTicker(symbol: symbol, assetClass: assetClass)
            }
        }

        // `path` is percent-DECODED, so an encoded `%2F` shows up here as `/` and is refused.
        let path = components.path
        guard path.hasPrefix("/") else { return .malformed(reason: "missing_symbol") }
        let rawSymbol = String(path.dropFirst())
        guard !rawSymbol.contains("/") else { return .malformed(reason: "extra_path") }
        guard let symbol = normalizedSymbol(rawSymbol) else {
            return .malformed(reason: "invalid_symbol")
        }

        switch typeQuery(components.queryItems) {
        case .refused(let reason): return .malformed(reason: reason)
        case .accepted(let assetClass): return .ticker(Ticker(symbol: symbol, assetClass: assetClass))
        }
    }

    /// What `typeQuery` decided about a link's query.
    enum TypeQuery: Equatable {
        /// No query, or exactly one known `type`. nil means "resolve from the symbol".
        case accepted(AssetClass?)
        /// Outside the grammar, with the reason `parse` reports.
        case refused(String)
    }

    /// The ONE reader of the optional `type=<class>` query, shared by `ticker/<SYMBOL>` and
    /// `updates/<SYMBOL>` so the two can never accept different queries: `type` is the only key,
    /// given at most once, and one of `AssetClass`.
    static func typeQuery(_ items: [URLQueryItem]?) -> TypeQuery {
        var assetClass: AssetClass?
        var sawType = false
        for item in items ?? [] {
            guard item.name == typeKey else { return .refused("unknown_query_key") }
            guard !sawType else { return .refused("duplicate_type") }
            sawType = true
            guard let parsed = AssetClass(wire: item.value) else {
                return .refused("unknown_type")
            }
            assetClass = parsed
        }
        return .accepted(assetClass)
    }

    // MARK: - Symbol grammar

    /// The uppercased symbol, or nil when it is outside the grammar described above.
    ///
    /// Checks ASCII BEFORE uppercasing. `"ß".uppercased()` is `"SS"`, so uppercasing first
    /// would turn a non-ASCII string into a different, valid-looking symbol.
    public static func normalizedSymbol(_ raw: String) -> String? {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, trimmed.count <= maxSymbolLength,
              trimmed.unicodeScalars.allSatisfy({ $0.isASCII })
        else { return nil }
        let upper = trimmed.uppercased()

        var body = Substring(upper)
        if body.first == "^" { body = body.dropFirst() }
        // At most three segments: `BRK-B`, `SHOP.TO`, `RDS-A.L`. An empty segment means a
        // leading, trailing or doubled separator, and is refused.
        let segments = body.split(
            omittingEmptySubsequences: false, whereSeparator: { $0 == "." || $0 == "-" }
        )
        guard (1...3).contains(segments.count) else { return nil }
        for segment in segments {
            guard !segment.isEmpty,
                  segment.unicodeScalars.allSatisfy({
                      ("A"..."Z").contains($0) || ("0"..."9").contains($0)
                  })
            else { return nil }
        }
        return upper
    }
}
