//
//  DeviceProofStore.swift
//  ios
//
//  The install's "this device has signed into this account before" proofs.
//
//  The backend rate-limits sign-in, forgot-password and reset-password PER EMAIL as well as
//  per IP — the per-email bucket is the brute-force control an address pool cannot get
//  around. Its cost was a trivial lockout: anyone who knew an address could post ten wrong
//  passwords every fifteen minutes and the real owner's correct password answered 429 from
//  every device for as long as that loop ran. The server therefore hands every VERIFIED
//  sign-in an opaque, signed `device_token` bound to the address, and a later request that
//  presents it as `X-Device-Token` is judged on its own bucket instead of the per-email one.
//
//  It is not a session: it carries no user id and unlocks nothing by itself, which is why it
//  is deliberately NOT cleared on sign-out — signing out and back in during an attack is
//  exactly when the owner needs it. Keyed per address so a shared device keeps one proof per
//  account. Never logged, never sent anywhere but the three auth routes.
//

import Foundation

nonisolated enum DeviceProofStore {

    private static let key = "device-proofs-v1"
    private static let maxEntries = 5
    private static let lock = NSLock()

    /// The proof for `email`, if this install has ever signed into it.
    static func proof(for email: String) -> String? {
        lock.lock(); defer { lock.unlock() }
        return load()[normalise(email)]
    }

    /// Remember the proof the server minted for `email` on a verified sign-in.
    static func remember(_ proof: String?, for email: String?) {
        guard let proof, !proof.isEmpty, let email else { return }
        let normalised = normalise(email)
        guard !normalised.isEmpty else { return }
        lock.lock(); defer { lock.unlock() }
        var proofs = load()
        proofs[normalised] = proof
        save(bounded(proofs, keeping: normalised))
    }

    /// Bound the map AFTER the insert: a device that cycles through many accounts keeps the
    /// newest few, oldest ISSUE first (CC-08).
    ///
    /// This used to drop `keys.sorted()` — the alphabetically FIRST addresses — and skip the
    /// drop when the new address sorted first, so the map could sit one over its limit. On a
    /// shared iPad, the account whose email sorts first lost its exemption every time any other
    /// account signed in: during a lockout attack on that address, its own device got 429.
    ///
    /// The address just remembered is never evicted. Ties (and unreadable proofs, which sort
    /// oldest) break on the address so the result does not depend on dictionary order.
    static func bounded(_ proofs: [String: String], keeping kept: String) -> [String: String] {
        let overflow = proofs.count - maxEntries
        guard overflow > 0 else { return proofs }
        var out = proofs
        let oldestFirst = proofs
            .filter { $0.key != kept }
            .map { (issuedAt: issuedAt(of: $0.value), email: $0.key) }
            .sorted { ($0.issuedAt, $0.email) < ($1.issuedAt, $1.email) }
        for entry in oldestFirst.prefix(overflow) {
            out.removeValue(forKey: entry.email)
        }
        return out
    }

    /// The proof's issue time — the `<iat>` of `v1.<iat>.<digest>.<sig>`
    /// (`core/security.py::create_device_proof`). The format is otherwise opaque here; a proof
    /// this cannot read counts as the oldest, so it is the first to go.
    static func issuedAt(of proof: String) -> Int {
        let parts = proof.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count >= 2, let iat = Int(parts[1]) else { return Int.min }
        return iat
    }

    static func normalise(_ email: String) -> String {
        email.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    private static func load() -> [String: String] {
        guard let raw = KeychainService.shared.get(key), let data = raw.data(using: .utf8),
              let map = try? JSONDecoder().decode([String: String].self, from: data) else {
            return [:]
        }
        return map
    }

    private static func save(_ proofs: [String: String]) {
        guard let data = try? JSONEncoder().encode(proofs),
              let raw = String(data: data, encoding: .utf8) else { return }
        KeychainService.shared.set(raw, forKey: key)
    }
}
