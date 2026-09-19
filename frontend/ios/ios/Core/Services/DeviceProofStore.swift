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
        // Bound the map: a device that cycles through many accounts keeps the newest few.
        if proofs.count > maxEntries {
            for dropped in proofs.keys.sorted().prefix(proofs.count - maxEntries) where dropped != normalised {
                proofs.removeValue(forKey: dropped)
            }
        }
        save(proofs)
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
