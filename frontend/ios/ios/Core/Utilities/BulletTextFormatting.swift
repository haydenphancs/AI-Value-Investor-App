//
//  BulletTextFormatting.swift
//  ios
//
//  Small display normalization for AI summary bullets.
//

import Foundation

extension String {
    /// Rewrites a short lead-in transition that ends in a colon into a
    /// comma-led sentence, e.g. `"The takeaway: This policy…"` →
    /// `"The takeaway, This policy…"` and `"In short: X"` → `"In short, X"`.
    ///
    /// Applied to the FINAL summary bullet (the "why investors care" line) so it
    /// reads like the other transitions ("Ultimately, …") instead of a bold
    /// label. Only a colon within the first `maxLeadIn` characters is touched, so
    /// a legitimate mid-sentence colon (e.g. "watch two things: X and Y") is left
    /// alone. This is a display-time safeguard for already-cached bullets; new
    /// enrichments are prompted to emit the comma directly.
    func normalizingLeadInColon(maxLeadIn: Int = 40) -> String {
        guard let colon = firstIndex(of: ":") else { return self }
        guard distance(from: startIndex, to: colon) <= maxLeadIn else { return self }
        let lead = self[..<colon]
        // Keep the sentence readable: drop spaces right after the colon so we
        // don't produce ",  " (double space).
        let rest = self[index(after: colon)...].drop(while: { $0 == " " })
        return "\(lead), \(rest)"
    }
}
