//
//  WidgetMarketFetcher.swift
//  Caydex
//
//  The widget extension's one network call.
//

import Foundation
import OSLog

/// Fetches the Market payload directly from the widget process.
///
/// ⚠️ MARKET MODE ONLY, AND THAT IS A HARD LINE — see the header of
/// `WidgetSnapshotStore`. `/widget/market-mover` takes no identity at all: it is market
/// data about a shared universe and carries nothing about the caller, which is exactly
/// why it was made public. `/widget/portfolio-mover` resolves the caller's own holdings,
/// and an extension cannot hold a credential without breaking `auth.md` §8 (the client
/// token and the Keychain deliberately diverge during `.restoring`) and without being
/// unable to refresh it when it expires. Holdings mode keeps reading what the app wrote.
public enum WidgetMarketFetcher {
    private static let log = Logger(subsystem: "com.phan.caydex", category: "widget")

    /// The freshest Market payload, or nil.
    ///
    /// nil is an ordinary outcome — no signal, aeroplane mode, a cold radio, the request
    /// running past its budget — and the caller MUST fall back to the stored snapshot.
    /// A Home Screen tile has no error state, no spinner and no retry button, so an
    /// older-but-real reading beats anything that looks broken.
    public static func fetchMarket() async -> WidgetMoverSnapshot? {
        var request = URLRequest(url: WidgetAPIConfig.marketMoverURL)
        request.timeoutInterval = WidgetAPIConfig.requestTimeout
        // The tile is redrawn on WidgetKit's schedule, so a cached body would defeat the
        // entire point of fetching.
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                log.warning("widget fetch: non-HTTP response")
                return nil
            }
            guard (200..<300).contains(http.statusCode) else {
                // 429 is expected under a rate limit and is not an incident; the stored
                // snapshot covers it.
                log.warning("widget fetch: HTTP \(http.statusCode, privacy: .public)")
                return nil
            }
            let snapshot = try WidgetSnapshotStore.decoder.decode(
                WidgetMoverSnapshot.self, from: data
            )
            // The same refusal the app applies: a degraded 200 with no mover must not
            // displace a good stored snapshot.
            guard !snapshot.isEmpty || snapshot.marketBrief != nil else {
                log.warning("widget fetch: empty payload — keeping the stored snapshot")
                return nil
            }
            return snapshot
        } catch is CancellationError {
            return nil
        } catch {
            log.warning("widget fetch failed: \(String(describing: error))")
            return nil
        }
    }
}
