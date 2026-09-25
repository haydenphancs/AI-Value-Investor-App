//
//  InstallSourceStore.swift
//  ios
//
//  The StoreKit half of `InstallSourcePolicy`: reads where this copy of the app came from and
//  keeps the answer for the synchronous readers (the share sheet, the "Rate the App" row).
//
//  Two sources, in order of trust:
//
//  1. `AppTransaction.shared` — signed by the App Store, but ASYNC, may need the network the
//     first time, and has been seen to throw on TestFlight installs. Resolved once at launch.
//  2. The receipt's file name — synchronous and local: `sandboxReceipt` on TestFlight and App
//     Review, `receipt` on an App Store install. It answers until (1) lands, and whenever (1)
//     fails.
//
//  ⚠️ NEVER call `AppTransaction.refresh()` here. It prompts the user to sign in to the App
//  Store — a launch-time sign-in sheet to decide which link a share carries.
//

import Foundation
import OSLog
import StoreKit

@MainActor
enum InstallSourceStore {

    private static let log = Logger(subsystem: "com.phan.caydex", category: "install-source")

    /// What `AppTransaction` said, once it has said anything. `nil` = not resolved (yet).
    private static var fromAppTransaction: InstallSource?
    private static var resolveTask: Task<Void, Never>?

    /// Where this copy came from, or nil when nothing can say. Synchronous by design: the
    /// callers are a share sheet and a button, and neither can wait on the network.
    static var current: InstallSource? {
        #if DEBUG
        // The Simulator is always "development" (or unknown) and cannot be made an App Store
        // install, so this is the only way to see each branch of the Rate row and the share
        // link: `SIMCTL_CHILD_CAYDEX_INSTALL_SOURCE=appStore|preRelease|development|unknown`.
        if let raw = ProcessInfo.processInfo.environment["CAYDEX_INSTALL_SOURCE"] {
            if let parsed = InstallSourcePolicy.parseOverride(raw) { return parsed }
            log.error("CAYDEX_INSTALL_SOURCE=\(raw, privacy: .public) is not a known value — ignored")
        }
        #endif
        if let resolved = fromAppTransaction { return resolved }
        return InstallSourcePolicy.classify(store: nil, receiptFileName: receiptFileName())
    }

    /// Reads `AppTransaction` once, in the background. Single-flight, and a no-op once it has
    /// an answer; after a FAILURE it may run again (the receipt answers in the meantime).
    static func resolve() {
        guard fromAppTransaction == nil, resolveTask == nil else { return }
        resolveTask = Task {
            defer { resolveTask = nil }
            do {
                let result = try await AppTransaction.shared
                let environment: AppStore.Environment
                switch result {
                case .verified(let transaction):
                    environment = transaction.environment
                case .unverified(let transaction, let error):
                    // Still used: the answer only chooses which public link to show, and the
                    // receipt — the fallback — is not signed at all.
                    log.warning("AppTransaction unverified (\(type(of: error), privacy: .public): \(error.localizedDescription, privacy: .public)) — using its environment anyway")
                    environment = transaction.environment
                }
                let receipt = receiptFileName()
                let source = InstallSourcePolicy.classify(store: storeEnvironment(environment),
                                                          receiptFileName: receipt)
                fromAppTransaction = source
                log.info("install source: \(String(describing: source), privacy: .public) (AppTransaction environment=\(environment.rawValue, privacy: .public), receipt=\(receipt ?? "none", privacy: .public))")
            } catch {
                let receipt = receiptFileName()
                let fallback = InstallSourcePolicy.classify(store: nil, receiptFileName: receipt)
                log.warning("AppTransaction.shared failed (\(type(of: error), privacy: .public): \(error.localizedDescription, privacy: .public)) — using the receipt: \(String(describing: fallback), privacy: .public) (receipt=\(receipt ?? "none", privacy: .public))")
            }
        }
    }

    /// `AppStore.Environment` is a struct of constants, not an enum, so this needs `default`.
    private static func storeEnvironment(_ environment: AppStore.Environment) -> StoreEnvironment {
        switch environment {
        case .production: return .production
        case .sandbox:    return .sandbox
        case .xcode:      return .xcode
        default:          return .unrecognised
        }
    }

    /// The receipt's file name, or nil when there is no receipt.
    ///
    /// `appStoreReceiptURL` is deprecated as of iOS 18 in favour of `AppTransaction`, which
    /// is exactly what it backs up here. It is read through `ReceiptURLReading` so the build
    /// stays warning-free: a requirement witnessed by the deprecated property is not itself
    /// deprecated. (The `@available(iOS, deprecated: 100000)` trick does NOT work — measured on
    /// Swift 6.3.3 with an iOS 18.0 target, the call inside still warns.)
    private static func receiptFileName() -> String? {
        (Bundle.main as ReceiptURLReading).appStoreReceiptURL?.lastPathComponent
    }
}

/// See `InstallSourceStore.receiptFileName()`.
private protocol ReceiptURLReading {
    var appStoreReceiptURL: URL? { get }
}

extension Bundle: ReceiptURLReading {}
