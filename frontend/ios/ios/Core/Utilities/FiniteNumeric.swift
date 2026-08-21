//
//  FiniteNumeric.swift
//  ios
//
//  Non-finite guards for values that arrive from the backend.
//
//  WHY THIS EXISTS
//  ---------------
//  Financial figures reach the app through FMP → a Python service → JSON, and every stage can
//  produce a non-finite double: a division by a zero denominator, a ratio against a missing
//  prior period, an aggregate over an empty set. The backend guards most of these now, but the
//  guard has had to be re-added on the asset-detail, financials, holders and analysis paths in
//  turn, so treating "finite" as a property to assert at the decode boundary — rather than a
//  property to hope for — is the cheaper posture.
//
//  On the wire this matters twice over. `NaN` and `Infinity` are NOT valid JSON, so a single one
//  anywhere in a response makes `JSONDecoder` reject the WHOLE body: one bad row takes out the
//  entire list. And a NaN that does make it into the view layer propagates silently — it formats
//  as "nan", compares false against everything (so `min`/`max`/sort quietly misbehave), and
//  poisons any chart geometry computed from it.
//
//  Preferring nil over a substituted number is deliberate and matches how this app already
//  handles absent financial data: an honest "—" rather than a fabricated figure.
//

import Foundation

extension Double {

    /// `self` when finite, otherwise nil — so a NaN or ±Infinity degrades to "unknown"
    /// instead of rendering as a number or silently corrupting a comparison.
    var finiteOrNil: Double? {
        isFinite ? self : nil
    }
}

extension Optional where Wrapped == Double {

    /// Collapses both "absent" and "present but non-finite" into nil.
    var finiteOrNil: Double? {
        self?.finiteOrNil
    }
}


// MARK: - Type-tolerant integer

/// An `Int` that also decodes from a JSON **string** or a floating-point number.
///
/// A Swift optional does NOT absorb a type mismatch: `decodeIfPresent(Int.self, …)`
/// returns nil only when the key is absent or explicitly null, and THROWS when the value
/// is the right key but the wrong type. Because `Codable` is all-or-nothing, one such
/// field fails the entire struct.
///
/// That is not hypothetical: FMP `/stable/profile` returns `fullTimeEmployees` as the
/// string `"166000"` (verified live), while `StockDetail.fullTimeEmployees` is declared
/// `Int?` — so the whole `StockDetail` decode threw on every ticker, silently gutting the
/// fallback the `/overview` failure path depends on. The backend now coerces the field
/// too; this is the other half, so neither side is a single point of failure.
struct LenientInt: Codable, Equatable, Sendable {
    let value: Int?

    init(_ value: Int?) { self.value = value }

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { value = nil; return }
        if let i = try? c.decode(Int.self) { value = i; return }
        if let d = try? c.decode(Double.self) {
            // `Int(d)` TRAPS on NaN/±Infinity and on anything outside Int's range.
            value = (d.isFinite && d >= -9.2e18 && d <= 9.2e18) ? Int(d) : nil
            return
        }
        if let s = try? c.decode(String.self) {
            value = Int(s.replacingOccurrences(of: ",", with: "")
                         .trimmingCharacters(in: .whitespaces))
            return
        }
        // Unknown shape (an object, an array): degrade to nil rather than throwing and
        // taking the whole response down with it.
        value = nil
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        if let value { try c.encode(value) } else { try c.encodeNil() }
    }
}
