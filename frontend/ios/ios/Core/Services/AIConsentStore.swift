//
//  AIConsentStore.swift
//  ios
//
//  Consent for sending user-typed content to a third-party AI provider.
//
//  Why this exists: App Review guideline 5.1.2(i), added 13 November 2025, requires that
//  you "clearly disclose where personal data will be shared with third parties, including
//  with third-party AI, and obtain explicit permission before doing so." Ask Cay AI sends
//  the user's typed message to an external AI provider for processing, and there was no
//  consent step — only a privacy-policy mention, which is not "explicit permission".
//
//  Scope note: this gates USER-TYPED content (chat). It does not gate the AI-generated
//  research reports, because those are produced from public market data with no user
//  content in the request — see the audit: no user id, email, watchlist, or holdings are
//  ever sent to the provider.
//

import Foundation
import Combine

@MainActor
final class AIConsentStore: ObservableObject {
    static let shared = AIConsentStore()

    private enum Keys {
        static let granted = "ai_processing_consent_granted"
        static let grantedAt = "ai_processing_consent_granted_at"
    }

    /// True once the user has explicitly allowed sending chat content for AI processing.
    @Published private(set) var hasConsented: Bool

    /// When consent was granted, for the audit trail shown in Settings.
    @Published private(set) var grantedAt: Date?

    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        self.hasConsented = defaults.bool(forKey: Keys.granted)
        let ts = defaults.double(forKey: Keys.grantedAt)
        self.grantedAt = ts > 0 ? Date(timeIntervalSince1970: ts) : nil
    }

    func grant() {
        let now = Date()
        defaults.set(true, forKey: Keys.granted)
        defaults.set(now.timeIntervalSince1970, forKey: Keys.grantedAt)
        hasConsented = true
        grantedAt = now
    }

    /// Withdraw consent. 5.1.2(ii)/5.1.1(ii) require an accessible way to withdraw, so
    /// this is exposed in Settings. Chat stops working until it is granted again — that is
    /// the honest consequence, and the UI says so.
    func withdraw() {
        defaults.set(false, forKey: Keys.granted)
        defaults.removeObject(forKey: Keys.grantedAt)
        hasConsented = false
        grantedAt = nil
    }
}
