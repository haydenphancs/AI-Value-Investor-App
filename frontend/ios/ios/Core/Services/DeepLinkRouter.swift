//
//  DeepLinkRouter.swift
//  ios
//
//  Turns an opened `caydex://` URL into a parked destination, and decides when the shell may
//  show it. No UI: `iosApp` parks, `ContentView` presents.
//

import Foundation
import OSLog

/// A widget tap's destination, parked on `AppState.pendingDeepLink` until the shell can show it.
///
/// PARKED rather than presented on arrival, because the URL usually arrives before anything
/// could present it. A cold launch from a widget tap delivers it while the splash is up (auth
/// `.unknown` / `.loading`). A signed-out tap delivers it behind the sign-in wall. The
/// consumer, `ContentView`, only exists once the user is past the wall. So parking it here is
/// what turns "signed out" into "sign in, then land on the ticker you tapped".
struct PendingDeepLink: Equatable {
    /// So a second tap on the same ticker is still a CHANGE that `onChange` observes.
    let id = UUID()
    let symbol: String
    let assetType: MarketTickerType
    let receivedAt: Date

    /// How long a parked tap stays meaningful. Past this the link is dropped, not opened: a
    /// sign-in that took the whole afternoon must not end on a screen the user no longer
    /// remembers asking for.
    static let maxAge: TimeInterval = 10 * 60

    func isExpired(now: Date = Date()) -> Bool {
        now.timeIntervalSince(receivedAt) > Self.maxAge
    }
}

enum DeepLinkRouter {
    private static let log = Logger(subsystem: "com.phan.caydex", category: "deeplink")

    /// `.onOpenURL` → the link to park, or nil (always logged) for anything that is not one.
    ///
    /// Only ever opens a ticker DETAIL screen. No link can generate a report, start a chat or
    /// spend a credit, because the grammar has nowhere to say so.
    static func pendingLink(for url: URL, now: Date = Date()) -> PendingDeepLink? {
        switch CaydexDeepLink.parse(url) {
        case .ticker(let ticker):
            // The SAME resolver search results use (`AssetDetailRouter`). A specific class is
            // trusted, while "stock" or no class falls back to the symbol, so a `BTCUSD` link
            // still opens the crypto screen rather than the equity one.
            let assetType = MarketTickerType.resolve(
                ticker.assetClass?.rawValue, symbol: ticker.symbol
            )
            log.info("deep link parked: ticker=\(ticker.symbol, privacy: .public) type=\(assetType.rawValue, privacy: .public)")
            return PendingDeepLink(symbol: ticker.symbol, assetType: assetType, receivedAt: now)
        case .notTickerLink:
            // Another scheme, or another `caydex` host such as the OAuth `auth-callback`.
            // Not ours: left for whoever owns it, and never navigated on.
            log.info("open URL ignored: not a ticker link (scheme=\(url.scheme ?? "-", privacy: .public) host=\(url.host ?? "-", privacy: .public))")
            return nil
        case .malformed(let reason):
            log.warning("deep link declined: \(reason, privacy: .public) (host=\(url.host ?? "-", privacy: .public))")
            return nil
        }
    }

    /// The parked link if it is still worth opening, else nil (logged). See `maxAge`.
    static func freshLink(_ link: PendingDeepLink, now: Date = Date()) -> PendingDeepLink? {
        guard link.isExpired(now: now) else { return link }
        let waited = Int(now.timeIntervalSince(link.receivedAt))
        log.info("deep link dropped: parked \(waited, privacy: .public)s, past maxAge (ticker=\(link.symbol, privacy: .public))")
        return nil
    }

    /// Whether the shell may present a parked link in this auth state.
    ///
    /// EXHAUSTIVE ON PURPOSE, with no `default:`. A new `AuthStatus` must be decided here, not
    /// swept into whichever answer a default happened to give.
    ///
    /// `.restoring` presents, deliberately. The stored token is ARMED during a cold-launch
    /// restore before the status says `.authenticated`. So a gate on `.authenticated` alone
    /// would hold a tap that is about to succeed, then open the screen out of nowhere a few
    /// seconds later. The detail screen is the same one Home opens in that state, and every
    /// request it makes is `.signInRequired`. `APIClient` refuses those before any network I/O
    /// when no token is armed, so nothing licensed is ever drawn without a credential.
    ///
    /// The wall itself (`.unauthenticated`) holds the link. `RootView` is showing `SignInView`
    /// then, which IS the sign-in route, and the parked link opens once the user is through it.
    static func canPresent(status: AuthStatus) -> Bool {
        switch status {
        case .authenticated, .restoring:
            return true
        case .unknown, .loading, .unauthenticated:
            return false
        }
    }
}
