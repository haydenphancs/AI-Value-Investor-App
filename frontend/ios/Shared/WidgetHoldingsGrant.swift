//
//  WidgetHoldingsGrant.swift
//  Caydex
//
//  The Holdings widget grant on the device: the ONE home of the key the widget extension sends
//  to `/widget/holdings-mover` so the Holdings tile refreshes with the app closed.
//
//  WHAT IT IS (owner 2026-11-27: "Own key, 30-day life (Recommended)")
//  An opaque, per-device key ("cwh1_" + 43 base64url characters, 256 bits) that the server
//  minted INSIDE a refresh-token rotation and stores only as a SHA-256. It reaches exactly two
//  routes (the holdings payload and its own revocation), dies with the session that minted it
//  (logout, refresh-token reuse, sign out of all devices, a password stamp, account deletion),
//  and lives at most 30 days. It is NOT a session: the extension still holds no session and
//  cannot refresh one (`auth.md` §8, §8a). Only the APP mints, stores and revokes it.
//
//  ⚠️ WHERE THE KEY LIVES, AND WHY NOT IN THE APP GROUP DEFAULTS
//  The market widget token sits in App Group `UserDefaults`, which travels in device and iCloud
//  backups. A key that reads someone's HOLDINGS must not: it lives in ONE Keychain item,
//  `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`, never synchronizable, so a backup restored
//  to another phone carries no key (the marker comes back alone and the orphan clear removes it).
//
//  ⚠️ THE ACCESS GROUP IS THE APP GROUP, AND NO `keychain-access-groups` ENTITLEMENT IS ADDED
//  An app group can serve as a Keychain access group with no extra entitlement, and can never be
//  a target's DEFAULT group. Adding `keychain-access-groups` to reach a shared group would make
//  its first entry the default for every add that names no group — and `KeychainService` (the
//  session tokens) and `GuestIdentity` write with none, so the session's refresh token would
//  move into a group the extension can read. `test_ios_widget_holdings_grant.py` pins both.
//
//  ONE WRITER PER KEY (the v2 lesson, `WidgetSnapshotStore.swift`)
//  • the Keychain item and `widget.holdings.grant.fp` (the marker): the APP only;
//  • `widget.holdings.grant.refusedFp`: the EXTENSION only, and only while it equals the marker.
//  The App Group holds only fingerprints — the first 32 hex of SHA-256(key) — never the key.
//  A refusal names ONE fingerprint, so a newly adopted grant (a new key, a new fingerprint) is
//  never refused by an old note: the app has no reason to touch the extension's key, and
//  `removeMarker()` clears both at the end of a session.
//
//  `nonisolated`: Shared compiles into the MainActor-default app target too, and the app reads
//  and writes the Keychain OFF the main thread (`test_ios_launch_no_main_thread_keychain.py`).
//

import CryptoKit
import Foundation
import OSLog
import Security

nonisolated enum WidgetHoldingsGrant {
    private static let log = Logger(subsystem: "com.phan.caydex", category: "widget")

    // MARK: - Coordinates

    /// The Keychain service. Deliberately NOT the bundle id `KeychainService` uses, so that
    /// store's `deleteAll()` can never touch the grant, and this file can never touch a session.
    static let keychainService = "com.phan.caydex.widget-holdings"
    /// The Keychain account is `grant.<fingerprint>`: one item per minted key, so a renewal never
    /// overwrites the key the extension may be using at that moment.
    static let accountPrefix = "grant."

    /// The App Group, used as the Keychain ACCESS GROUP (allowed without an entitlement).
    static var accessGroup: String { WidgetSharedConfig.appGroupIdentifier }

    /// App Group key: the fingerprint of the grant the extension should use. APP only.
    static let markerKey = "widget.holdings.grant.fp"
    /// App Group key: the fingerprint of a grant the server refused. EXTENSION only.
    static let refusedKey = "widget.holdings.grant.refusedFp"
    /// App Group key behind `requestOnNextRefresh`. APP only (the extension never reads it).
    ///
    /// ⚠️ App Group, not `UserDefaults.standard`: this file compiles into the extension binary
    /// too, and a `.standard` read anywhere in it makes the extension touch its OWN defaults
    /// domain, which its privacy manifest does not declare (CA92.1 — ITMS-91053 refuses the
    /// upload; `test_ios_privacy_manifest_reasons.py`). The value is a non-secret flag.
    static let requestKey = "widget.holdings.grant.requestOnNextRefresh"

    static let tokenPrefix = "cwh1_"
    /// `secrets.token_urlsafe(32)`: 32 bytes are 43 unpadded base64url characters.
    static let tokenTailLength = 43
    /// Hex characters of SHA-256 kept as the fingerprint (128 bits — a name, not a secret).
    static let fingerprintLength = 32
    /// The most Keychain items one sweep reads. A device holds one or two; anything past this
    /// is a bug, and a bounded loop beats an unbounded one inside a timeline or a launch.
    static let maxItemsPerSweep = 32

    // MARK: - The grant

    /// One minted key and what it is for.
    ///
    /// `description` / `debugDescription` / `customMirror` never include the key, so a log line
    /// or a `dump()` that interpolates a grant cannot leak it.
    nonisolated struct Grant: Equatable, Sendable, CustomStringConvertible,
                              CustomDebugStringConvertible, CustomReflectable {
        /// The key itself. Never logged, never written outside the Keychain item.
        let token: String
        let grantId: String
        /// The account the grant reads. The extension refreshes only a Holdings slot owned by it.
        let userId: String
        /// The session (refresh family) it was minted under: a different `sid` on the app's
        /// bearer means a password change started a new session, and the grant is dead.
        let sessionId: String
        let expiresAt: Date
        /// `WidgetHoldingsGrant.fingerprint(of: token)`. `store(_:)` refuses a grant whose
        /// fingerprint does not match its key.
        let fp: String

        init(token: String, grantId: String, userId: String, sessionId: String,
             expiresAt: Date, fp: String) {
            self.token = token
            self.grantId = grantId
            self.userId = userId
            self.sessionId = sessionId
            self.expiresAt = expiresAt
            self.fp = fp
        }

        /// The same grant with its fingerprint derived from the key (the app's usual way in).
        init(token: String, grantId: String, userId: String, sessionId: String, expiresAt: Date) {
            self.init(
                token: token, grantId: grantId, userId: userId, sessionId: sessionId,
                expiresAt: expiresAt, fp: WidgetHoldingsGrant.fingerprint(of: token)
            )
        }

        var description: String {
            "Grant(fp: \(fp), grantId: \(grantId), userId: \(userId), expiresAt: \(expiresAt))"
        }

        var debugDescription: String { description }

        var customMirror: Mirror {
            Mirror(self, children: [
                "fp": fp, "grantId": grantId, "userId": userId,
                "sessionId": sessionId, "expiresAt": expiresAt,
            ])
        }
    }

    /// What the device holds, as the extension needs to tell it apart.
    nonisolated enum Lookup: Sendable {
        /// No marker: this device has no grant (an older app, no Movers widget at mint time, or
        /// signed out). The tile behaves exactly as before the grant existed.
        case absent
        /// A usable grant.
        case live(Grant)
        /// The grant's own `expiresAt` has passed. The server would refuse it; the extension
        /// treats it as refused WITHOUT spending a request on the answer.
        case expired(Grant)
        /// The marker names an item that cannot be read now — before the first unlock, missing
        /// after a backup restore, or corrupt. Never "refused": nothing is hidden on its account.
        case unreadable
    }

    nonisolated enum StoreError: Error, Sendable, CustomStringConvertible {
        case malformed(String)
        case encode(String)
        case keychain(OSStatus)

        var description: String {
            switch self {
            case .malformed(let what): return "malformed grant: \(what)"
            case .encode(let detail): return "encode failed: \(detail)"
            case .keychain(let status): return "Keychain status \(status)"
            }
        }
    }

    /// The Keychain item's value: everything the grant needs, in ONE value, so the owner can
    /// never be torn from the key.
    private nonisolated struct StoredGrant: Codable {
        let token: String
        let grantId: String
        let userId: String
        let sessionId: String
        /// Seconds since 1970 — a plain number, not a formatted date, so no date strategy can
        /// fail to read it back.
        let expiresAt: Double

        enum CodingKeys: String, CodingKey {
            case token
            case grantId = "grant_id"
            case userId = "user_id"
            case sessionId = "session_id"
            case expiresAt = "expires_at"
        }
    }

    // MARK: - Reads (both processes)

    /// The usable grant, or nil — none, expired, or unreadable (each logged by `lookup()`).
    ///
    /// The app reads nil as "urgent" (mint at the next foreground refresh); the extension
    /// reads it as "render what the app last wrote".
    static func current() -> Grant? {
        if case .live(let grant) = lookup() { return grant }
        return nil
    }

    /// The marker, resolved through the Keychain: see `Lookup`.
    static func lookup(now: Date = Date()) -> Lookup {
        guard let fp = markerFingerprint else { return .absent }
        switch readItem(fingerprint: fp) {
        case .found(let grant):
            if grant.expiresAt > now { return .live(grant) }
            log.info("widget holdings grant \(fp, privacy: .public) has expired")
            return .expired(grant)
        case .missing:
            // A marker with no item: a backup restored to another phone (the item is
            // ThisDeviceOnly, the marker is not), or the app deleted the item first.
            log.warning("widget holdings grant: the marker \(fp, privacy: .public) has no Keychain item")
            return .unreadable
        case .failed(let status):
            if status == errSecInteractionNotAllowed {
                // Before the first unlock after a reboot: AfterFirstUnlock items are sealed.
                log.info("widget holdings grant unreadable before the first unlock")
            } else {
                log.error("widget holdings grant: Keychain read failed, status \(status, privacy: .public)")
            }
            return .unreadable
        case .corrupt(let why):
            log.error("widget holdings grant \(fp, privacy: .public) is corrupt: \(why, privacy: .public)")
            return .unreadable
        }
    }

    /// The fingerprint the app last adopted, or nil. Not a secret; also the extension's fence:
    /// a write or a refusal applies only while the marker still names the grant it fetched with.
    static var markerFingerprint: String? {
        guard let raw = WidgetSharedDefaults.store?.string(forKey: markerKey),
              isFingerprint(raw) else { return nil }
        return raw
    }

    /// Whether the extension already recorded a refusal of THIS fingerprint.
    static func isRefused(_ fp: String) -> Bool {
        WidgetSharedDefaults.store?.string(forKey: refusedKey) == fp
    }

    // MARK: - Extension only

    /// Records that the server refused `fp` (or that it expired). EXTENSION only, and only while
    /// the marker still names it: a grant the app adopted meanwhile must not be marked dead.
    static func noteRefused(_ fp: String) {
        guard isFingerprint(fp) else {
            log.error("widget holdings grant: refusal of a malformed fingerprint ignored")
            return
        }
        guard markerFingerprint == fp else {
            log.info("widget holdings grant: refusal not recorded — the app replaced the grant")
            return
        }
        WidgetSharedDefaults.store?.set(fp, forKey: refusedKey)
    }

    // MARK: - App only (always off the main thread for the Keychain calls)

    /// Stores a newly minted grant in its own Keychain item. Throws on any malformed field or
    /// Keychain failure — the caller logs and leaves the marker where it was.
    static func store(_ g: Grant) throws {
        guard isWellFormedToken(g.token) else { throw StoreError.malformed("token") }
        guard g.fp == fingerprint(of: g.token) else { throw StoreError.malformed("fingerprint") }
        for (name, value) in [("grant_id", g.grantId), ("user_id", g.userId), ("session_id", g.sessionId)]
        where UUID(uuidString: value) == nil {
            throw StoreError.malformed(name)
        }
        guard g.expiresAt.timeIntervalSince1970.isFinite else { throw StoreError.malformed("expires_at") }

        let data: Data
        do {
            data = try JSONEncoder().encode(StoredGrant(
                token: g.token, grantId: g.grantId, userId: g.userId,
                sessionId: g.sessionId, expiresAt: g.expiresAt.timeIntervalSince1970
            ))
        } catch {
            throw StoreError.encode(String(describing: error))
        }

        let item = baseQuery(account: account(for: g.fp))
        var add = item
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        var status = SecItemAdd(add as CFDictionary, nil)
        if status == errSecDuplicateItem {
            let update: [String: Any] = [
                kSecValueData as String: data,
                kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            ]
            status = SecItemUpdate(item as CFDictionary, update as CFDictionary)
        }
        guard status == errSecSuccess else {
            log.error("widget holdings grant \(g.fp, privacy: .public): Keychain store failed, status \(status, privacy: .public)")
            throw StoreError.keychain(status)
        }
    }

    /// Points the extension at a stored grant. APP only, synchronous (an App Group write).
    static func setMarker(_ fp: String) {
        guard isFingerprint(fp) else {
            log.error("widget holdings grant: refused to set a malformed marker")
            return
        }
        WidgetSharedDefaults.store?.set(fp, forKey: markerKey)
    }

    /// Stops the extension using any grant, at once, and returns the fingerprint it named (nil
    /// when there was none). APP only, SYNCHRONOUS — the session-end path calls it before it
    /// clears the widget token and the snapshots. Also clears the extension's refusal note: a
    /// fingerprint of a dead key is nobody's state once the session is over.
    @discardableResult
    static func removeMarker() -> String? {
        let named = markerFingerprint
        WidgetSharedDefaults.store?.removeObject(forKey: markerKey)
        WidgetSharedDefaults.store?.removeObject(forKey: refusedKey)
        return named
    }

    /// Every stored grant except `fp` — the keys the app should revoke, then `delete`. Items that
    /// can never be used (a value that does not decode, or whose key does not match its
    /// fingerprint) are deleted here and logged: there is nothing to revoke them with.
    static func pendingKeys(excluding fp: String?) -> [Grant] {
        var query = baseQuery(account: nil)
        query[kSecReturnAttributes as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitAll
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return [] }
        guard status == errSecSuccess, let rows = result as? [[String: Any]] else {
            log.error("widget holdings grant: Keychain listing failed, status \(status, privacy: .public)")
            return []
        }
        if rows.count > maxItemsPerSweep {
            log.error("widget holdings grant: \(rows.count, privacy: .public) Keychain items — reading the first \(maxItemsPerSweep, privacy: .public)")
        }
        var grants: [Grant] = []
        for row in rows.prefix(maxItemsPerSweep) {
            guard let account = row[kSecAttrAccount as String] as? String,
                  account.hasPrefix(accountPrefix) else { continue }
            let itemFp = String(account.dropFirst(accountPrefix.count))
            guard itemFp != fp else { continue }
            guard isFingerprint(itemFp) else {
                log.error("widget holdings grant: an item with a malformed account — deleted")
                deleteItem(account: account)
                continue
            }
            switch readItem(fingerprint: itemFp) {
            case .found(let grant):
                grants.append(grant)
            case .corrupt(let why):
                log.error("widget holdings grant \(itemFp, privacy: .public) is corrupt (\(why, privacy: .public)) — deleted")
                deleteItem(account: account)
            case .missing:
                continue
            case .failed(let status):
                log.warning("widget holdings grant \(itemFp, privacy: .public) unreadable, status \(status, privacy: .public) — kept for the next sweep")
            }
        }
        return grants
    }

    /// Deletes one grant's Keychain item. APP only — after its revoke answered, or when the
    /// item can never be used. A missing item is not an error.
    static func delete(fingerprint fp: String) {
        guard isFingerprint(fp) else {
            log.error("widget holdings grant: refused to delete a malformed fingerprint")
            return
        }
        deleteItem(account: account(for: fp))
    }

    /// Set by the app when the next `POST /auth/refresh` should ask the server for a grant.
    /// APP only; stored in the App Group (see `requestKey` for why not `.standard`).
    static var requestOnNextRefresh: Bool {
        get { WidgetSharedDefaults.store?.bool(forKey: requestKey) ?? false }
        set {
            if newValue {
                WidgetSharedDefaults.store?.set(true, forKey: requestKey)
            } else {
                WidgetSharedDefaults.store?.removeObject(forKey: requestKey)
            }
        }
    }

    // MARK: - Pure helpers

    /// The first 32 hex characters of SHA-256(key): a stable, non-secret NAME for a key.
    static func fingerprint(of token: String) -> String {
        let digest = SHA256.hash(data: Data(token.utf8))
        let hex = digest.map { byte -> String in
            let s = String(byte, radix: 16)
            return s.count == 1 ? "0" + s : s
        }.joined()
        return String(hex.prefix(fingerprintLength))
    }

    /// `cwh1_` + exactly 43 base64url characters — the only shape the server mints.
    static func isWellFormedToken(_ token: String) -> Bool {
        guard token.hasPrefix(tokenPrefix) else { return false }
        let tail = token.utf8.dropFirst(tokenPrefix.utf8.count)
        guard tail.count == tokenTailLength else { return false }
        return tail.allSatisfy(isBase64URLByte)
    }

    /// A-Z, a-z, 0-9, "-" and "_". A `switch` over ranges rather than one long `||` chain: the
    /// type checker reads it in linear time (CLAUDE.md, Machine safety rule 6).
    private static func isBase64URLByte(_ byte: UInt8) -> Bool {
        switch byte {
        case 0x41...0x5A, 0x61...0x7A, 0x30...0x39, 0x2D, 0x5F: return true
        default: return false
        }
    }

    private static func isLowerHexByte(_ byte: UInt8) -> Bool {
        switch byte {
        case 0x30...0x39, 0x61...0x66: return true
        default: return false
        }
    }

    /// Exactly `fingerprintLength` lower-case hex characters.
    static func isFingerprint(_ value: String) -> Bool {
        let bytes = value.utf8
        guard bytes.count == fingerprintLength else { return false }
        return bytes.allSatisfy(isLowerHexByte)
    }

    static func account(for fp: String) -> String { accountPrefix + fp }

    // MARK: - Keychain plumbing

    private nonisolated enum ReadResult {
        case found(Grant)
        case missing
        case failed(OSStatus)
        case corrupt(String)
    }

    /// The attributes every grant query and add shares. `kSecAttrSynchronizable` false: the item
    /// never leaves this device through iCloud Keychain.
    private static func baseQuery(account: String?) -> [String: Any] {
        var query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: keychainService,
            kSecAttrAccessGroup as String: accessGroup,
            kSecAttrSynchronizable as String: false,
        ]
        if let account { query[kSecAttrAccount as String] = account }
        return query
    }

    private static func readItem(fingerprint fp: String) -> ReadResult {
        var query = baseQuery(account: account(for: fp))
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return .missing }
        guard status == errSecSuccess else { return .failed(status) }
        guard let data = result as? Data else { return .corrupt("no data") }
        let stored: StoredGrant
        do {
            stored = try JSONDecoder().decode(StoredGrant.self, from: data)
        } catch {
            return .corrupt("undecodable value")
        }
        guard isWellFormedToken(stored.token) else { return .corrupt("malformed key") }
        guard fingerprint(of: stored.token) == fp else { return .corrupt("key does not match its fingerprint") }
        guard UUID(uuidString: stored.grantId) != nil,
              UUID(uuidString: stored.userId) != nil,
              UUID(uuidString: stored.sessionId) != nil
        else { return .corrupt("malformed id") }
        guard stored.expiresAt.isFinite else { return .corrupt("malformed expiry") }
        return .found(Grant(
            token: stored.token, grantId: stored.grantId, userId: stored.userId,
            sessionId: stored.sessionId,
            expiresAt: Date(timeIntervalSince1970: stored.expiresAt), fp: fp
        ))
    }

    private static func deleteItem(account: String) {
        let status = SecItemDelete(baseQuery(account: account) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            log.error("widget holdings grant: Keychain delete failed, status \(status, privacy: .public)")
            return
        }
    }
}
