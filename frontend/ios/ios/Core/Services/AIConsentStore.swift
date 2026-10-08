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
        /// Which version of the consent TEXT the user accepted (`currentVersion`).
        static let version = "ai_processing_consent_version"
    }

    /// The version of the consent sheet's text (`AIDataConsentView`). Bump it whenever the
    /// sheet starts disclosing a NEW flow, so everyone who accepted the older text is asked once
    /// more before that flow can run for them — a consent covers only what it said.
    ///   1 — the original sheet. No version key was stored, so every pre-1.1 grant reads as 1.
    ///   2 — 1.1: adds report chat's web search (Brave), whose query is derived from the message.
    ///       Without this, everyone who tapped Allow on 1.0 would reach the search on 1.1
    ///       having never seen the row that discloses it.
    static let currentVersion = 2

    /// True once the user has explicitly allowed sending chat content for AI processing,
    /// under the CURRENT consent text.
    @Published private(set) var hasConsented: Bool

    /// When the current consent was granted, for the audit trail shown in Settings. Nil while
    /// no current consent is held, so Settings never shows "Allowed <date>" for an older text.
    @Published private(set) var grantedAt: Date?

    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let granted = defaults.bool(forKey: Keys.granted)
        let accepted = (defaults.object(forKey: Keys.version) as? Int) ?? 1
        let current = granted && accepted >= Self.currentVersion
        self.hasConsented = current
        let ts = defaults.double(forKey: Keys.grantedAt)
        self.grantedAt = (current && ts > 0) ? Date(timeIntervalSince1970: ts) : nil
    }

    func grant() {
        let now = Date()
        defaults.set(true, forKey: Keys.granted)
        defaults.set(now.timeIntervalSince1970, forKey: Keys.grantedAt)
        defaults.set(Self.currentVersion, forKey: Keys.version)
        hasConsented = true
        grantedAt = now
    }

    /// Withdraw consent. 5.1.2(ii)/5.1.1(ii) require an accessible way to withdraw, so
    /// this is exposed in Settings. Chat stops working until it is granted again — that is
    /// the honest consequence, and the UI says so.
    func withdraw() {
        defaults.set(false, forKey: Keys.granted)
        defaults.removeObject(forKey: Keys.grantedAt)
        defaults.removeObject(forKey: Keys.version)
        hasConsented = false
        grantedAt = nil
    }

    /// Drop consent because the SESSION that granted it ended — not because the user withdrew.
    ///
    /// Both keys are device-global, so without this the next account to sign in on this phone
    /// inherits the previous user's "Allow". `ChatViewModel` gates the consent sheet on
    /// `hasConsented`, so it would not present, and the new user's first message would be sent
    /// for AI processing having never been asked. Consent is per person, and it cannot be
    /// inherited from whoever held the phone before.
    ///
    /// Separate from `withdraw()` on purpose: that is a deliberate user action with its own
    /// meaning in Settings, and conflating the two would misreport why consent went away.
    func resetForEndedSession() {
        defaults.removeObject(forKey: Keys.granted)
        defaults.removeObject(forKey: Keys.grantedAt)
        defaults.removeObject(forKey: Keys.version)
        hasConsented = false
        grantedAt = nil
    }
}
