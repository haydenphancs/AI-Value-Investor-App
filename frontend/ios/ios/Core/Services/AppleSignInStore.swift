//
//  AppleSignInStore.swift
//  ios
//
//  Two small Sign in with Apple facts this install has to remember (sign-in audit, 2026-11-26).
//
//  1. THE ACCOUNT LINK (SOC-04) — which Apple ID the CURRENT session was signed in with, and which
//     Caydex account that session belongs to. `AppState.checkAppleCredentialState` asks Apple
//     whether that Apple ID still authorises Caydex (`getCredentialState(forUserID:)`), and signs
//     out when the user has revoked it ("Stop Using Apple ID" in Settings) — our own refresh tokens
//     never notice that. Its lifetime is the session's: `AuthService.clearToken()` drops it with the
//     session tokens (sign-out, a dead credential, an undone adoption, the fresh-install purge), and
//     every NON-Apple sign-in drops it too (`AuthService.recordAppleIdentity`), so a link can never
//     outlive the session it describes and sign out someone who came in another way.
//
//  2. THE PENDING NAME (SOC-05) — Apple sends the user's name ONLY on the first authorization. If
//     that first `/auth/oauth` failed (a 429, a 5xx, a dropped connection) the name was gone for
//     good and the account was named after its relay address. It is kept here, keyed by Apple's
//     user id, from the moment Apple hands it over until an `/auth/oauth` succeeds, and resent with
//     every attempt in between. Cleared on success and when a session ends (auth.md §7: a store
//     with no user id in its key must not hand one person's data to the next).
//
//  Keychain, `AfterFirstUnlockThisDeviceOnly` (`KeychainService`), like the session tokens. Every
//  function here is SYNCHRONOUS: a caller on the main actor reads through `Task.detached` (the
//  launch rule pinned by `test_ios_launch_no_main_thread_keychain.py`); writes happen at sign-in /
//  sign-out, next to the session-token writes. Never logged: the name is personal data and the
//  Apple user id is an identifier.
//

import Foundation

nonisolated enum AppleSignInStore {

    /// Which Apple ID the current session was signed in with, and whose session that is.
    struct AccountLink: Codable, Equatable, Sendable {
        let appleUserID: String
        let accountId: String
    }

    /// A name Apple sent once, waiting for an `/auth/oauth` that succeeds.
    struct PendingName: Codable, Equatable, Sendable {
        let appleUserID: String
        let name: String
    }

    static let accountLinkKey = "apple-account-link-v1"
    static let pendingNameKey = "apple-pending-name-v1"

    /// The backend's `DISPLAY_NAME_MAX_LENGTH` (backend/app/schemas/user.py), counted the way
    /// Python counts a `str`: Unicode SCALARS, not grapheme clusters. A longer name used to fail
    /// the whole `/auth/oauth` request with a 422 — and the retry, with no name, lost it.
    static let maxNameScalars = 64

    // MARK: - Account link (SOC-04)

    /// Record the identity the session that was just adopted came in with: an Apple ID, or `nil`
    /// for any other way in (which clears a link left over from an earlier session).
    static func setAccountLink(_ link: AccountLink?, keychain: KeychainService = .shared) {
        guard let link, !link.appleUserID.isEmpty, !link.accountId.isEmpty else {
            keychain.delete(accountLinkKey)
            return
        }
        guard let data = try? JSONEncoder().encode(link),
              let raw = String(data: data, encoding: .utf8) else {
            keychain.delete(accountLinkKey)
            return
        }
        keychain.set(raw, forKey: accountLinkKey)
    }

    /// The Apple ID the current session came in with — only if that session belongs to
    /// `accountId`. A link for any other account is ignored: it describes a session that is not
    /// this one. Synchronous Keychain read: call it off the main actor.
    static func appleUserID(linkedTo accountId: String, keychain: KeychainService = .shared) -> String? {
        guard !accountId.isEmpty,
              let raw = keychain.get(accountLinkKey), let data = raw.data(using: .utf8),
              let link = try? JSONDecoder().decode(AccountLink.self, from: data),
              link.accountId == accountId, !link.appleUserID.isEmpty else {
            return nil
        }
        return link.appleUserID
    }

    static func clearAccountLink(keychain: KeychainService = .shared) {
        keychain.delete(accountLinkKey)
    }

    // MARK: - Pending name (SOC-05)

    /// The name to send with this Apple sign-in.
    ///
    /// - Apple sent a name (`received`): keep it until an `/auth/oauth` succeeds, and send it.
    /// - Apple sent none (every authorization after the first): send the name kept from the first
    ///   one — but only if it was kept for THIS Apple ID.
    static func nameForSignIn(
        received: String?, appleUserID: String?, keychain: KeychainService = .shared
    ) -> String? {
        guard let appleUserID, !appleUserID.isEmpty else { return normalisedName(received) }
        if let name = normalisedName(received) {
            if let data = try? JSONEncoder().encode(PendingName(appleUserID: appleUserID, name: name)),
               let raw = String(data: data, encoding: .utf8) {
                keychain.set(raw, forKey: pendingNameKey)
            }
            return name
        }
        guard let raw = keychain.get(pendingNameKey), let data = raw.data(using: .utf8),
              let pending = try? JSONDecoder().decode(PendingName.self, from: data),
              pending.appleUserID == appleUserID else {
            return nil
        }
        return normalisedName(pending.name)
    }

    /// The backend accepted the sign-in (and with it, the name): nothing is pending any more.
    static func clearPendingName(keychain: KeychainService = .shared) {
        keychain.delete(pendingNameKey)
    }

    /// A session ended (auth.md §7): forget both facts.
    static func clearForEndedSession(keychain: KeychainService = .shared) {
        keychain.delete(pendingNameKey)
        keychain.delete(accountLinkKey)
    }

    /// Trimmed, empty → nil, capped at the backend's limit in Unicode scalars.
    static func normalisedName(_ raw: String?) -> String? {
        guard let trimmed = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !trimmed.isEmpty else {
            return nil
        }
        guard trimmed.unicodeScalars.count > maxNameScalars else { return trimmed }
        var scalars = String.UnicodeScalarView()
        scalars.append(contentsOf: trimmed.unicodeScalars.prefix(maxNameScalars))
        let capped = String(scalars).trimmingCharacters(in: .whitespacesAndNewlines)
        return capped.isEmpty ? nil : capped
    }
}
