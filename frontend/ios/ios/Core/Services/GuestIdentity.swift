//
//  GuestIdentity.swift
//  ios
//
//  A stable per-INSTALL identifier, sent as `X-Guest-Id` on every API request.
//
//  WHY THIS EXISTS
//  ---------------
//  The iOS login UI isn't built yet, so no request carries a Bearer token and the
//  backend attributes every single install to one shared `GUEST_USER_ID`. The
//  Learn stores (Books / Journey / Money Moves / Bookmarks) union-merge the
//  server's completed set into their local one — which meant one person's
//  finished lessons and saved books were merged into EVERY other person's app,
//  permanently and in both directions.
//
//  This gives each install its own partition. The backend hashes the value into a
//  UUID5 (`dependencies.guest_user_id_for`), so it is never used as a user id
//  directly and a client cannot impersonate a real account by sending its uuid.
//
//  KEYCHAIN, NOT UserDefaults: Keychain items survive app deletion, so a
//  reinstall keeps the user's progress instead of silently starting over.
//  `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly` — needed by background
//  refresh after a reboot, never synced to iCloud (this is a device-local id, and
//  syncing it would re-merge two devices into one identity).
//
//  When real auth ships this becomes a no-op: the backend prefers a valid Bearer
//  token and ignores the header.
//

import Foundation
import Security

enum GuestIdentity {

    private static let service = "com.phan.caydex.guest"
    private static let account = "install-id"

    /// Cached so the Keychain is touched once per launch, not once per request.
    nonisolated(unsafe) private static var cached: String?
    private static let lock = NSLock()

    /// The stable id for this install. Created on first access.
    ///
    /// Never throws and never returns empty: if the Keychain is somehow
    /// unavailable, a process-lifetime fallback is used so requests still carry a
    /// consistent id for this session (degrading to "progress doesn't persist
    /// across launches" rather than "progress pools with strangers").
    static var current: String {
        lock.lock()
        defer { lock.unlock() }

        if let cached { return cached }

        if let existing = read() {
            cached = existing
            return existing
        }

        let fresh = UUID().uuidString
        if !write(fresh) {
            // Keychain write failed (device locked at first launch, entitlement
            // issue). Still return a stable-for-this-process value — and say so,
            // because silently regenerating per launch would look like progress
            // loss and be very hard to diagnose from a bug report.
            print("⚠️ GuestIdentity: Keychain write failed — using a session-only id")
        }
        cached = fresh
        return fresh
    }

    // MARK: - Keychain

    private static func read() -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data,
              let value = String(data: data, encoding: .utf8),
              !value.isEmpty
        else { return nil }
        return value
    }

    @discardableResult
    private static func write(_ value: String) -> Bool {
        guard let data = value.data(using: .utf8) else { return false }
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        // Delete-then-add: SecItemAdd fails with errSecDuplicateItem otherwise,
        // and a partially-written item would be worse than none.
        SecItemDelete(query as CFDictionary)

        var attributes = query
        attributes[kSecValueData as String] = data
        attributes[kSecAttrAccessible as String] =
            kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        return SecItemAdd(attributes as CFDictionary, nil) == errSecSuccess
    }
}
