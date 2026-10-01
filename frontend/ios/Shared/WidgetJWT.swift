//
//  WidgetJWT.swift
//  Caydex
//
//  Reads the two claims the widget plumbing needs out of a compact JWT — and nothing else.
//
//  WHY ITS OWN FILE
//  It used to live inside `WidgetAPIConfig`, which reaches the App Group and WidgetKit, so no
//  harness could compile it alone — and nothing tested it. A slip in it is silent and costly:
//    • `subject` nil ⇒ every Holdings snapshot is stored with owner nil ⇒ `settleWidgetSession`
//      reads every launch as an account switch, blanks the Holdings tile and forces an extra
//      refresh;
//    • `expiry` nil ⇒ the app cannot tell its stored widget token is still good, and mints a
//      fresh, unrevocable 90-day token on every launch.
//  Dependency-free (Foundation + OSLog), so `scripts/widget-jwt-check.sh` compiles THIS file
//  with a `main.swift` and nothing else.
//
//  ⚠️ IT VERIFIES NOTHING. It reads claims; the signature is never checked and cannot be (the
//  key is server-side). Never use it to decide what the server will accept.
//

import Foundation
import OSLog

/// `nonisolated`: pure functions over a string, callable from the app, the extension and the
/// harness alike.
nonisolated public enum WidgetJWT {
    private static let log = Logger(subsystem: "com.phan.caydex", category: "widget")

    /// The decoded payload segment of a compact JWT (`header.payload.signature`, base64url,
    /// unpadded), or nil when it is not exactly three segments of a base64url JSON object.
    public static func claims(of token: String) -> [String: Any]? {
        let parts = token.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 3 else {
            log.warning("widget: JWT has \(parts.count, privacy: .public) segments, not 3 — treating its claims as unknown")
            return nil
        }
        // base64url → base64: the two URL-safe characters back, then the padding the
        // compact form strips. A length ≡ 1 (mod 4) is impossible for base64 and stays
        // invalid after padding, so `Data(base64Encoded:)` rejects it below.
        var b64 = String(parts[1])
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        let remainder = b64.count % 4
        if remainder > 0 { b64 += String(repeating: "=", count: 4 - remainder) }
        guard let data = Data(base64Encoded: b64) else {
            log.warning("widget: JWT payload is not base64url — treating its claims as unknown")
            return nil
        }
        do {
            guard let claims = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                log.warning("widget: JWT payload is not a JSON object — treating its claims as unknown")
                return nil
            }
            return claims
        } catch {
            log.warning("widget: JWT payload unreadable — treating its claims as unknown: \(String(describing: error), privacy: .public)")
            return nil
        }
    }

    /// The `exp` claim as a `Date`, or nil when it is absent, not a number, not finite, or
    /// not after the epoch. nil means "unknown", and callers treat unknown as "expired".
    public static func expiry(of token: String) -> Date? {
        guard let claims = claims(of: token) else { return nil }
        // A JSON boolean also bridges to NSNumber (true ⇒ 1, i.e. 1970): not a timestamp.
        guard let number = claims["exp"] as? NSNumber, !isBoolean(number) else { return nil }
        let exp = number.doubleValue
        guard exp.isFinite, exp > 0 else { return nil }
        return Date(timeIntervalSince1970: exp)
    }

    /// The `sub` claim — the user id the backend signed the token for — or nil when it is
    /// absent, not a string, or empty.
    public static func subject(of token: String) -> String? {
        guard let sub = claims(of: token)?["sub"] as? String, !sub.isEmpty else { return nil }
        return sub
    }

    private static func isBoolean(_ number: NSNumber) -> Bool {
        CFGetTypeID(number) == CFBooleanGetTypeID()
    }
}
