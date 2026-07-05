//
//  CompanyNameFormatter.swift
//  ios
//
//  Display-only normalisation of company names. Strips legal-entity suffixes
//  (Inc, Corp, Co, Ltd, plc, N.V., S.A., AG, LLC, LP, GmbH, SE, …) and trailing
//  exchange security-type / ADR descriptors ("American Depositary Shares",
//  "Common Stock", "Class A", …), while KEEPING meaningful name words such as
//  "Holdings", "Group", "International", "Technology" and internal tokens like
//  ".com".
//
//  Presentation-only: the RAW legal name is left untouched in the data layer
//  (DTOs / models keep the full string), so any matching/search sees the
//  original. Apply this only at the DTO→display-model boundary.
//
//  There is no iOS XCTest target in this project (see the Home-redesign notes);
//  this file is intentionally Foundation-only so it can be exercised by a
//  standalone `swiftc` harness that compiles it alongside a `main.swift` of
//  assertions.
//
//  Examples:
//    "NVIDIA Corporation"                                  -> "NVIDIA"
//    "Amazon.com, Inc."                                    -> "Amazon.com"
//    "Restaurant Brands International Inc."                 -> "Restaurant Brands International"
//    "ASML Holding N.V."                                   -> "ASML Holding"
//    "Arm Holdings plc American Depositary Shares"          -> "Arm Holdings"
//    "Taiwan Semiconductor Manufacturing Company Limited"   -> "Taiwan Semiconductor Manufacturing"
//    "Tiffany & Co."                                       -> "Tiffany"
//

import Foundation

enum CompanyNameFormatter {

    /// Legal-entity suffix tokens, compared case-insensitively after removing
    /// '.' and a surrounding ','. Deliberately EXCLUDES "holdings"/"group" —
    /// those read as part of the brand and are kept ("ASML Holding" stays).
    private static let legalSuffixes: Set<String> = [
        "inc", "incorporated", "corp", "corporation", "co", "company",
        "cos", "companies", "ltd", "limited", "llc", "llp", "lllp", "lp",
        "plc", "nv", "sa", "sab", "cv", "ag", "gmbh", "se", "ab", "oyj",
        "asa", "spa", "srl", "bv", "pte", "pty", "bhd", "kk", "kgaa", "nl",
    ]

    /// Trailing "connector" tokens that can be left orphaned once a suffix that
    /// followed them is removed (e.g. the "de" in a Mexican "S.A.B. de C.V.",
    /// or the "&" in "Tiffany & Co.").
    private static let orphanConnectors: Set<String> = ["de", "of", "and", "&"]

    /// Whole-word phrases that begin an exchange security-type / share-class
    /// descriptor. Everything from the first match onward is dropped. Matching is
    /// per-token (so "American Airlines" is NOT cut — only "American Depositary").
    private static let descriptorPhrases: [[String]] = [
        ["american", "depositary"],
        ["sponsored", "adr"],
        ["common", "stock"], ["common", "shares"],
        ["ordinary", "shares"], ["ordinary", "share"],
        ["class", "a"], ["class", "b"], ["class", "c"],
        ["depositary", "shares"], ["depositary", "receipt"],
        ["depositary", "receipts"], ["depositary", "units"],
        ["subordinate", "voting"],
        ["adr"], ["ads"], ["units"], ["warrant"], ["warrants"],
        ["rights"], ["redeemable"], ["preferred"],
    ]

    /// Clean a raw company name for display. Never returns empty for non-empty
    /// input — falls back to the trimmed original if the transform would strip
    /// everything.
    static func clean(_ raw: String) -> String {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return trimmed }

        var tokens = trimmed
            .split(whereSeparator: { $0 == " " || $0 == "\t" || $0 == "\n" })
            .map(String.init)
        guard !tokens.isEmpty else { return trimmed }

        // Pass 1 — drop a trailing security-type / share-class descriptor
        // ("… plc American Depositary Shares" -> "… plc"). cut > 0 guards
        // against nuking a name that IS entirely a descriptor.
        if let cut = firstDescriptorIndex(in: tokens), cut > 0 {
            tokens = Array(tokens[0..<cut])
        }

        // Pass 2 — iteratively strip trailing legal suffixes + orphan
        // connectors. count > 1 guarantees we never strip the last token.
        while tokens.count > 1 {
            let last = norm(tokens[tokens.count - 1])
            if legalSuffixes.contains(last) || orphanConnectors.contains(last) {
                tokens.removeLast()
            } else {
                break
            }
        }

        // Rejoin and trim any trailing comma/space left on the final token
        // ("Micron Technology," -> "Micron Technology"). Internal '.' is kept
        // (".com" survives).
        var result = tokens.joined(separator: " ")
        while let last = result.last, last == "," || last == " " {
            result.removeLast()
        }
        result = result.trimmingCharacters(in: .whitespaces)
        return result.isEmpty ? trimmed : result
    }

    // MARK: - Helpers

    /// Case-insensitive normal form used ONLY for matching: lowercased, all '.'
    /// removed, surrounding ',' trimmed. Output always keeps the original token.
    private static func norm(_ token: String) -> String {
        var t = token.lowercased()
        t.removeAll { $0 == "." }
        return t.trimmingCharacters(in: CharacterSet(charactersIn: ","))
    }

    /// Index of the first token that begins any descriptor phrase, else nil.
    private static func firstDescriptorIndex(in tokens: [String]) -> Int? {
        let normed = tokens.map(norm)
        for i in 0..<normed.count {
            for phrase in descriptorPhrases where i + phrase.count <= normed.count {
                var match = true
                for k in 0..<phrase.count where normed[i + k] != phrase[k] {
                    match = false
                    break
                }
                if match { return i }
            }
        }
        return nil
    }
}
