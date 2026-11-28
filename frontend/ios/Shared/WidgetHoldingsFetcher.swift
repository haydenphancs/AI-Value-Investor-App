//
//  WidgetHoldingsFetcher.swift
//  Caydex
//
//  The Holdings tile's own request: the payload `/widget/portfolio-mover` gives the app,
//  fetched from the widget process with the device's Holdings grant.
//

import Foundation
import OSLog

/// Fetches the Holdings payload, and revokes a grant by its key.
///
/// The extension calls `fetchHoldings`; the APP calls `revoke` (after a renewal, at the end of a
/// session, when the last Movers widget is removed). Both go around `APIClient` on purpose: the
/// key is not a session, the routes are not `APIEndpoint` cases, and the pinned eleven-case
/// `.public` list therefore does not grow (`auth.md` §1).
///
/// ⚠️ THE KEY TRAVELS ONLY TO THE PRODUCTION HOST, OVER HTTPS. `WidgetAPIConfig.baseURL` honours
/// an App Group override of ANY scheme, written by the app for local debugging. A key that reads
/// someone's holdings must not follow such an override to plain HTTP, nor — in a shipped build —
/// to any host but production. A refused destination is `.unavailable`, logged, never sent.
///
/// ⚠️ "KEY REFUSED" IS AN ALLOW-LIST. Only a 401 carrying one of `refusalCodes` means the server
/// looked the key up and found it dead; the tile then hides the holdings. Every other answer —
/// `AUTH_REQUIRED` (the header never arrived: a proxy, or the route wired to the session router),
/// a 401 with no readable code, 403, 404/405 (a backend rolled back past the route), 429, 5xx,
/// a timeout, a decode failure — is `.unavailable` and keeps the stored snapshot. A deny-list
/// would wipe every Holdings tile on one misshapen deploy, and each would stay dark until its app
/// was opened (`auth.md` §3: `AUTH_REQUIRED` never clears anything).
///
/// Main-actor isolated like `WidgetMarketFetcher` (the target default): the network wait
/// suspends, it does not block, and the app calls `revoke` with `await` from any task.
enum WidgetHoldingsFetcher {
    private static let log = Logger(subsystem: "com.phan.caydex", category: "widget")

    /// The 401 codes that mean "this key is dead" — `get_holdings_grant_caller`'s three refusals.
    nonisolated static let refusalCodes: Set<String> = [
        "AUTH_TOKEN_INVALID", "AUTH_SESSION_EXPIRED", "AUTH_ACCOUNT_NOT_FOUND",
    ]

    /// The revoke reasons the server accepts (its body enum).
    nonisolated static let revokeReasons: Set<String> = ["app_revoked", "widget_removed"]

    nonisolated enum Outcome: Sendable {
        /// A portfolio-scoped payload with something to render.
        case payload(WidgetMoverSnapshot)
        /// The server refused the key, with the code it gave (one of `refusalCodes`).
        case refused(code: String)
        /// Anything else: keep the stored snapshot, try again at the next reload.
        case unavailable
    }

    /// EPHEMERAL, unlike `WidgetMarketFetcher`'s `.shared`: no URL cache, no cookies, no
    /// credential store. A holdings body must not land in the extension's on-disk URL cache,
    /// which no session-end clear can reach.
    private static let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.urlCache = nil
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.httpCookieStorage = nil
        config.httpShouldSetCookies = false
        config.urlCredentialStorage = nil
        config.timeoutIntervalForRequest = WidgetAPIConfig.requestTimeout
        config.timeoutIntervalForResource = WidgetAPIConfig.requestTimeout
        config.waitsForConnectivity = false
        return URLSession(configuration: config)
    }()

    // MARK: - The Holdings payload (extension)

    /// The freshest Holdings payload for `grant`'s account, or why there is none.
    ///
    /// ⚠️ A `.payload` IS NOT PERMISSION TO SHOW IT. The session or the grant can end during the
    /// up-to-12 s wait; the caller re-checks the widget token and the grant marker after the
    /// await, and `WidgetSnapshotStore.writeHoldingsFromExtension` checks them again.
    static func fetchHoldings(_ grant: WidgetHoldingsGrant.Grant) async -> Outcome {
        guard let request = holdingsRequest(
            url: WidgetAPIConfig.holdingsMoverURL, method: "GET", grant: grant
        ) else {
            return .unavailable
        }
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                log.warning("widget holdings fetch: non-HTTP response")
                return .unavailable
            }
            if http.statusCode == 401 {
                return classifyUnauthorized(data, route: "holdings-mover")
            }
            guard (200..<300).contains(http.statusCode) else {
                // 429 is expected under the per-grant limit; 503 is the switch off or the grant
                // store down; 404/405 a backend rolled back past the route. All keep the snapshot.
                log.warning("widget holdings fetch: HTTP \(http.statusCode, privacy: .public)")
                return .unavailable
            }
            let snapshot = try WidgetSnapshotStore.decoder.decode(WidgetMoverSnapshot.self, from: data)
            guard snapshot.mode == "portfolio" else {
                log.error("widget holdings fetch: the holdings route answered mode \(snapshot.mode, privacy: .public) — ignored")
                return .unavailable
            }
            // The store's own rule (`hasContent(for: .portfolio)`): a degraded 200 says nothing
            // about the group and must not displace a good stored snapshot.
            guard snapshot.hasContent(for: .portfolio) else {
                log.warning("widget holdings fetch: a degraded payload — keeping the stored snapshot")
                return .unavailable
            }
            return .payload(snapshot)
        } catch is CancellationError {
            return .unavailable
        } catch {
            log.warning("widget holdings fetch failed: \(String(describing: error), privacy: .public)")
            return .unavailable
        }
    }

    // MARK: - Revoke by key (app)

    /// Asks the server to revoke `grant`. True when the key is certainly dead: a 2xx — whose body
    /// `{"revoked": true}` (this call ended it) and `{"revoked": false}` (already revoked) mean the
    /// same thing here, so it is not read — or a 401 refusal (already revoked, expired or unknown).
    /// False otherwise — 503 AUTH_UNAVAILABLE (the grant store down), 429, AUTH_REQUIRED, a
    /// timeout: the caller keeps the Keychain item and tries again at its next refresh. Never
    /// retried here.
    ///
    /// Works while the server's grant switch is off: revocation is never gated.
    static func revoke(_ grant: WidgetHoldingsGrant.Grant, reason: String) async -> Bool {
        guard revokeReasons.contains(reason) else {
            log.error("widget holdings revoke: unknown reason \(reason, privacy: .public) — not sent")
            return false
        }
        guard var request = holdingsRequest(
            url: WidgetAPIConfig.holdingsGrantRevokeURL, method: "POST", grant: grant
        ) else {
            return false
        }
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        do {
            request.httpBody = try JSONSerialization.data(withJSONObject: ["reason": reason])
        } catch {
            log.error("widget holdings revoke: body encode failed: \(String(describing: error), privacy: .public)")
            return false
        }
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                log.warning("widget holdings revoke: non-HTTP response")
                return false
            }
            if (200..<300).contains(http.statusCode) {
                log.info("widget holdings revoke: grant \(grant.fp, privacy: .public) revoked (\(reason, privacy: .public))")
                return true
            }
            if http.statusCode == 401, case .refused = classifyUnauthorized(data, route: "holdings-grant/revoke") {
                // The key was already dead — the outcome a revoke wants.
                return true
            }
            log.warning("widget holdings revoke: HTTP \(http.statusCode, privacy: .public) — kept for a retry")
            return false
        } catch is CancellationError {
            return false
        } catch {
            log.warning("widget holdings revoke failed: \(String(describing: error), privacy: .public)")
            return false
        }
    }

    // MARK: - Shared plumbing

    /// Whether the holdings key may be attached to a request for `url`: HTTPS always, and in a
    /// shipped build only the production host. Checked BEFORE the header is set.
    static func mayCarryHoldingsKey(_ url: URL) -> Bool {
        guard url.scheme?.lowercased() == "https" else { return false }
        #if !DEBUG
        guard let host = url.host?.lowercased(),
              host == WidgetAPIConfig.productionBaseURL.host?.lowercased()
        else { return false }
        #endif
        return true
    }

    /// The request, with the key attached — or nil (logged) when the key must not go there.
    private static func holdingsRequest(
        url: URL, method: String, grant: WidgetHoldingsGrant.Grant
    ) -> URLRequest? {
        guard mayCarryHoldingsKey(url) else {
            log.error("widget holdings: refused to send the grant to \(url.scheme ?? "?", privacy: .public)://\(url.host ?? "?", privacy: .public) — production HTTPS only")
            return nil
        }
        guard WidgetHoldingsGrant.isWellFormedToken(grant.token) else {
            log.error("widget holdings: grant \(grant.fp, privacy: .public) holds a malformed key — not sent")
            return nil
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = WidgetAPIConfig.requestTimeout
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        // Its OWN header, never a bearer: the grant and a session are not interchangeable, and
        // no route can start accepting it just because it reads `Authorization`.
        request.setValue(grant.token, forHTTPHeaderField: WidgetAPIConfig.holdingsTokenHeader)
        // As `WidgetMarketFetcher` does: the server gates linkable causes on the build.
        if let version = WidgetMarketFetcher.appVersion {
            request.setValue(version, forHTTPHeaderField: "X-App-Version")
        }
        return request
    }

    /// A 401, read through the allow-list. Only `refusalCodes` is a refusal.
    private static func classifyUnauthorized(_ data: Data, route: String) -> Outcome {
        let code = errorCode(in: data)
        if let code, refusalCodes.contains(code) {
            log.warning("widget holdings \(route, privacy: .public): key refused (\(code, privacy: .public))")
            return .refused(code: code)
        }
        // AUTH_REQUIRED means the header never reached the route (stripped on the way, or the
        // route moved to a router that reads a different credential). Never a reason to hide
        // anything — and worth an ERROR, because it would be happening to every device.
        log.error("widget holdings \(route, privacy: .public): 401 with \(code ?? "no readable error_code", privacy: .public) — treated as unavailable")
        return .unavailable
    }

    /// The `error_code` of an `APIErrorResponse` body (`auth_error` emits it at the top level).
    nonisolated static func errorCode(in data: Data) -> String? {
        guard data.count <= 16_384,
              let object = try? JSONSerialization.jsonObject(with: data),
              let body = object as? [String: Any],
              let code = body["error_code"] as? String,
              !code.isEmpty, code.count <= 64
        else { return nil }
        return code
    }
}
