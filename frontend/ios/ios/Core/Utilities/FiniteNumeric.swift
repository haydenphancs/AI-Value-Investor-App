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
