//
//  WidgetAPIConfig.swift
//  Caydex
//
//  Where the widget extension sends its one request.
//

import Foundation

/// The backend base URL, resolvable from BOTH processes.
///
/// The app's own `APIConfig` cannot be used here. It lives in the app target, and in
/// DEBUG it asks `ServerEnvironmentManager` for a localhost probe result — state that
/// exists only in the app process. An extension reading it would either fail to compile
/// or, worse, resolve a URL that is right for the simulator and wrong for a device.
///
/// So: a production constant that is always correct in a shipped build, plus an override
/// the APP writes into the App Group whenever it resolves something different. That
/// keeps `USE_LOCAL=1` and the localhost auto-probe working for the widget too, without
/// the extension knowing anything about how the app decided.
public enum WidgetAPIConfig {
    /// Must stay in lockstep with `APIConfig.baseURL` — this is a deliberate SECOND copy
    /// (see the note above), so a hostname change has to be made in both places or the
    /// widget silently keeps calling the old host long after the app moved.
    public static let productionBaseURL = URL(
        string: "https://caydexinvest.com"
    )!

    static let baseURLOverrideKey = "widget.api.baseURL"
    static let widgetTokenKey = "widget.api.token"

    /// What the extension should call. The override is only ever set by the app.
    public static var baseURL: URL {
        if let raw = WidgetSharedDefaults.store?.string(forKey: baseURLOverrideKey),
           let url = URL(string: raw), url.scheme != nil {
            return url
        }
        return productionBaseURL
    }

    /// Called by the APP once it knows which environment it is talking to.
    ///
    /// Writing the production URL is not a no-op — it CLEARS a stale localhost override
    /// left by a debug run, which would otherwise strand the widget on a dead port.
    public static func publishBaseURL(_ url: URL) {
        WidgetSharedDefaults.store?.set(url.absoluteString, forKey: baseURLOverrideKey)
    }

    /// Market mode only. Portfolio needs a SESSION the extension must never hold —
    /// see the header of `WidgetSnapshotStore`.
    public static var marketMoverURL: URL {
        baseURL.appendingPathComponent("api/v1/widget/market-mover")
    }

    // MARK: - The widget's credential

    /// The header `/widget/market-mover` authenticates with. Deliberately NOT `Authorization`:
    /// the widget token and a session bearer are not interchangeable, and giving the widget one
    /// its own header means no route can start accepting it just because it reads a bearer.
    ///
    /// Must match `WIDGET_TOKEN_HEADER` in `backend/app/dependencies.py`.
    public static let tokenHeader = "X-Caydex-Widget-Token"

    /// The extension's market-data credential, or nil when there is none.
    ///
    /// nil is the normal signed-out state and the fetcher treats it as "do not call" rather
    /// than "call and get a 401" — a WidgetKit refresh spent on a guaranteed rejection is one
    /// the tile does not get back.
    public static var widgetToken: String? {
        guard let raw = WidgetSharedDefaults.store?.string(forKey: widgetTokenKey),
              !raw.isEmpty else { return nil }
        return raw
    }

    /// Called by the APP when it mints or renews the token. The app is the only writer.
    ///
    /// ⚠️ This is the one credential that crosses into the extension, and it is safe ONLY
    /// because of what it can reach: `/widget/market-mover` returns a market-wide roll-up and
    /// nothing about the caller. It is not a session — the backend refuses it as a bearer on
    /// every authenticated route (`_decode_access_token` allow-lists `type == "access"`). Do
    /// not publish the session token here; it would expire in an hour and the extension cannot
    /// refresh one.
    public static func publishWidgetToken(_ token: String) {
        WidgetSharedDefaults.store?.set(token, forKey: widgetTokenKey)
    }

    /// Called by the APP when the session ends. Without this the tile keeps refreshing FMP
    /// prices onto a signed-out device, which End-User Display Rights do not permit.
    public static func clearWidgetToken() {
        WidgetSharedDefaults.store?.removeObject(forKey: widgetTokenKey)
    }

    /// Short on purpose. WidgetKit gives a timeline provider a limited budget, and a
    /// slow request is worse than no request: the fallback (the stored snapshot) is
    /// already correct, just older.
    public static let requestTimeout: TimeInterval = 12
}

/// The one App Group suite, shared by every store in this file's neighbourhood.
enum WidgetSharedDefaults {
    static var store: UserDefaults? {
        UserDefaults(suiteName: WidgetSharedConfig.appGroupIdentifier)
    }
}
