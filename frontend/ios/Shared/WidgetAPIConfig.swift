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
    public static let productionBaseURL = URL(
        string: "https://ai-value-investor-app-production.up.railway.app"
    )!

    static let baseURLOverrideKey = "widget.api.baseURL"

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

    /// Market mode only. Portfolio needs an identity the extension must never hold —
    /// see the header of `WidgetSnapshotStore`.
    public static var marketMoverURL: URL {
        baseURL.appendingPathComponent("api/v1/widget/market-mover")
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
