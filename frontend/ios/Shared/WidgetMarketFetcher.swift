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
/// ⚠️ MARKET MODE ONLY, AND THAT IS A HARD LINE — see the header of `WidgetSnapshotStore`.
///
/// This used to read "`/widget/market-mover` takes no identity at all… which is exactly why it
/// was made public." That stopped being true on 2026-09-07: End-User Display Rights permit FMP
/// data only through an authenticated platform, so the route now requires a credential and this
/// fetcher sends the WIDGET TOKEN — a long-lived, market-scoped credential the app publishes into
/// the App Group. It is emphatically NOT the session token: an extension cannot refresh one
/// (`auth.md` §8 — refresh is main-actor and lives in the app), so a session token would strand
/// the tile within the hour, and the Keychain deliberately diverges from the client token during
/// `.restoring`.
///
/// The line still holds for `/widget/portfolio-mover`, which resolves the caller's own holdings.
/// The widget token cannot reach it — that is the reason it is defensible at all — so holdings
/// mode keeps reading what the app wrote.
public enum WidgetMarketFetcher {
    private static let log = Logger(subsystem: "com.phan.caydex", category: "widget")

    /// The freshest Market payload, or nil.
    ///
    /// nil is an ordinary outcome — no signal, aeroplane mode, a cold radio, the request
    /// running past its budget — and the caller MUST fall back to the stored snapshot.
    /// A Home Screen tile has no error state, no spinner and no retry button, so an
    /// older-but-real reading beats anything that looks broken.
    ///
    /// ⚠️ A NON-NIL RESULT IS NOT PERMISSION TO SHOW IT. The token is checked before the
    /// request only; the session can end during the up-to-12 s wait, and the 200 still
    /// arrives. The caller re-checks `WidgetAPIConfig.widgetToken` after the await, and
    /// `WidgetSnapshotStore.writeFromExtension` re-checks it again right before storing.
    public static func fetchMarket() async -> WidgetMoverSnapshot? {
        // No credential ⇒ do not call. The route answers 401 without one, and WidgetKit grants
        // only a few dozen refreshes a day: spending one on a guaranteed rejection is a refresh
        // the tile never gets back. Signed out, the stored snapshot is cleared anyway, so the
        // correct render is the placeholder — not a stale price.
        guard let token = WidgetAPIConfig.widgetToken else {
            log.info("widget fetch: no widget token — skipping (signed out)")
            return nil
        }

        var request = URLRequest(url: WidgetAPIConfig.marketMoverURL)
        request.timeoutInterval = WidgetAPIConfig.requestTimeout
        // The tile is redrawn on WidgetKit's schedule, so a cached body would defeat the
        // entire point of fetching.
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(token, forHTTPHeaderField: WidgetAPIConfig.tokenHeader)

        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                log.warning("widget fetch: non-HTTP response")
                return nil
            }
            guard (200..<300).contains(http.statusCode) else {
                // 429 is expected under a rate limit and is not an incident; the stored
                // snapshot covers it. 401 means the widget token expired or was revoked — the
                // extension cannot renew it (only the app can, on its next foreground), so this
                // is also a fall-back-and-wait, not an incident.
                log.warning("widget fetch: HTTP \(http.statusCode, privacy: .public)")
                return nil
            }
            let snapshot = try WidgetSnapshotStore.decoder.decode(
                WidgetMoverSnapshot.self, from: data
            )
            guard snapshot.mode == "market" else {
                log.error("widget fetch: market route answered mode \(snapshot.mode, privacy: .public) — ignored")
                return nil
            }
            // The same rule the store applies (`hasContent(for: .market)`): a degraded 200
            // with nothing renderable must not displace a good stored snapshot. A brief-only
            // or assets-only payload IS content — the Market tile renders exactly those.
            guard snapshot.hasContent(for: .market) else {
                log.warning("widget fetch: payload with no market content — keeping the stored snapshot")
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
