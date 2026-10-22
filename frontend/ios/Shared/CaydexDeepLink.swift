//
//  CaydexDeepLink.swift
//  Caydex
//
//  The ONE grammar for the `caydex://` links the widget builds and the app opens.
//

import Foundation

/// The `caydex://` links a Home Screen widget tap opens. Exactly two shapes:
/// - `caydex://ticker/<SYMBOL>[?type=<class>]` → that asset's detail screen (a Holdings tap);
/// - `caydex://updates/market` → the Updates tab on its Market chip, where the Market Insight
///   card leads (a Market tap; owner, 2026-10-22).
///
/// Lives in `Shared/`, so it compiles into BOTH targets. The widget that BUILDS a link and the
/// app that PARSES it share one grammar, and neither side can drift from the other.
///
/// ⚠️ `caydex` IS ALSO THE OAUTH CALLBACK SCHEME (`APIConfig.oauthCallbackScheme`, redirect
/// `caydex://auth-callback`). `ASWebAuthenticationSession` consumes that callback itself, so it
/// never reaches `.onOpenURL`. The parser still recognises ONLY the `ticker` and `updates`
/// hosts, and answers `.notOurLink` for everything else. A callback that did arrive is
/// therefore left alone, never misread as a link of ours. `test_widget_deep_links.py` pins the
/// scheme equal in all three places (here, `APIConfig`, Info.plist).
///
/// ⚠️ UNTRUSTED INPUT. Any app or web page can open a `caydex://` URL, not just our widget. So
/// the grammar is exact, and anything outside it is `.malformed`, never coerced into the
/// nearest valid link:
/// - the symbol is ASCII `[A-Z0-9]` segments joined by `.` or `-`, with an optional leading
///   `^`, at most `maxSymbolLength` characters;
/// - `type` is one of `AssetClass`, given at most once, and is the only query key allowed;
/// - an `updates` link is exactly the path `/market`, with no query at all;
/// - there are no credentials, port, fragment or extra path segments.
///
/// What a link may do is decided by the app (`DeepLinkRouter`), not here. That is: open a
/// detail screen or the Updates tab, behind the sign-in wall, and nothing that spends credits.
nonisolated public enum CaydexDeepLink {

    /// Pinned equal to `APIConfig.oauthCallbackScheme` and Info.plist's `CFBundleURLSchemes`.
    public static let scheme = "caydex"
    static let tickerHost = "ticker"
    static let updatesHost = "updates"
    /// The one path the `updates` host accepts. Lower-case and exact: no other chip is a link.
    static let updatesMarketPath = "/market"
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
            // `query` (not `queryItems`) so a bare trailing `?` is refused too.
            guard components.query == nil else { return .malformed(reason: "unknown_query_key") }
            guard components.path == updatesMarketPath else {
                return .malformed(reason: "unknown_updates_path")
            }
            return .updatesMarket
        }

        // `path` is percent-DECODED, so an encoded `%2F` shows up here as `/` and is refused.
        let path = components.path
        guard path.hasPrefix("/") else { return .malformed(reason: "missing_symbol") }
        let rawSymbol = String(path.dropFirst())
        guard !rawSymbol.contains("/") else { return .malformed(reason: "extra_path") }
        guard let symbol = normalizedSymbol(rawSymbol) else {
            return .malformed(reason: "invalid_symbol")
        }

        var assetClass: AssetClass?
        var sawType = false
        for item in components.queryItems ?? [] {
            guard item.name == typeKey else { return .malformed(reason: "unknown_query_key") }
            guard !sawType else { return .malformed(reason: "duplicate_type") }
            sawType = true
            guard let parsed = AssetClass(wire: item.value) else {
                return .malformed(reason: "unknown_type")
            }
            assetClass = parsed
        }
        return .ticker(Ticker(symbol: symbol, assetClass: assetClass))
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
