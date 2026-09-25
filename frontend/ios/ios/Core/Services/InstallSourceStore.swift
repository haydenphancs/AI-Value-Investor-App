//
//  InstallSourceStore.swift
//  ios
//
//  The device half of `InstallSourcePolicy`: reads where this copy of the app came from, once
//  per process, for the synchronous readers (the share sheet, the "Rate the App" row).
//
//  ⚠️ NO STOREKIT HERE — not `AppTransaction.shared`, not `AppTransaction.refresh()`. Measured
//  2026-09-24: `.shared` with no cached app transaction put an interactive "Sign in to Apple
//  Account" sheet on screen at launch (storekitd "receipt renewal"). Choosing which public
//  link to show never justifies a sign-in prompt. See `InstallSourcePolicy`'s header.
//

import Foundation
import OSLog

@MainActor
enum InstallSourceStore {

    private static let log = Logger(subsystem: "com.phan.caydex", category: "install-source")

    /// Where this copy came from, or nil when nothing can say. Synchronous by design: the
    /// callers are a share sheet and a button.
    static var current: InstallSource? {
        #if DEBUG
        // A DEBUG build always reads as `.development`, so this is the only way to see each
        // branch of the Rate row and the share link on the Simulator:
        // `SIMCTL_CHILD_CAYDEX_INSTALL_SOURCE=appStore|preRelease|development|unknown`.
        if let raw = ProcessInfo.processInfo.environment["CAYDEX_INSTALL_SOURCE"] {
            if let parsed = InstallSourcePolicy.parseOverride(raw) { return parsed }
            log.error("CAYDEX_INSTALL_SOURCE=\(raw, privacy: .public) is not a known value — ignored")
        }
        #endif
        return resolved
    }

    /// Computed ONCE — nothing it reads can change while the process runs — and logged once,
    /// so a device log shows which branch the Rate row and shares are on.
    private static let resolved: InstallSource? = {
        let receipt = receiptFileName()
        let source = InstallSourcePolicy.classify(receiptFileName: receipt,
                                                  isDevelopmentBuild: isDevelopmentBuild)
        log.info("install source: \(String(describing: source), privacy: .public) (receipt=\(receipt ?? "none", privacy: .public), developmentBuild=\(isDevelopmentBuild, privacy: .public))")
        return source
    }()

    /// A DEBUG build or the Simulator: never an App Store install.
    private static var isDevelopmentBuild: Bool {
        #if DEBUG || targetEnvironment(simulator)
        return true
        #else
        return false
        #endif
    }

    /// The receipt path's file name, or nil when iOS gives no path.
    ///
    /// `appStoreReceiptURL` is deprecated as of iOS 18 in favour of `AppTransaction` — which is
    /// exactly what must NOT be used here (see the header). It is read through
    /// `ReceiptURLReading` so the build stays warning-free: a requirement witnessed by the
    /// deprecated property is not itself deprecated. (`@available(iOS, deprecated: 100000)`
    /// does NOT silence it — measured on Swift 6.3.3 with an iOS 18.0 target.)
    private static func receiptFileName() -> String? {
        (Bundle.main as ReceiptURLReading).appStoreReceiptURL?.lastPathComponent
    }
}

/// See `InstallSourceStore.receiptFileName()`.
private protocol ReceiptURLReading {
    var appStoreReceiptURL: URL? { get }
}

extension Bundle: ReceiptURLReading {}
