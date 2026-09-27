//
//  BulletTextFormatting.swift
//  ios
//
//  Small display normalization for AI summary bullets.
//

import Foundation

/// Opening clauses that are pure scaffolding in front of a conclusion.
///
/// Kept in ONE place because two separate backend prompts used to ask for these
/// (`news_insight_service._build_prompt` for the Insights card,
/// `news_cache_service._batch_enrich_articles` for per-article bullets). Both now
/// forbid them, but see `strippingConclusionLeadIn()` for why that is not enough.
///
/// TWO CLASSES, and the split is load-bearing:
///
/// `exact` stems must BE the whole clause. "So," is a lead-in; "So the Fed cut
/// rates," is a sentence that happens to start with the same word, and allowing a
/// continuation here would delete it.
///
/// `openNoun` stems may be followed by a few more words, because their head noun
/// cannot begin an ordinary clause that means anything else — which is what makes
/// "The takeaway for everyday investors," (the form actually observed in
/// production) safe to match while "So the Fed…" is not.
///
/// `phrase` stems (below) need no punctuation at all.
///
/// ⚠️ The backend strips with the SAME stems (`app/services/conclusion_lead_in.py`)
/// and `backend/tests/test_conclusion_lead_in.py` parses these arrays and fails on
/// any drift. Change both together.
private let conclusionLeadInExactStems: [[String]] = [
    ["so"],
    ["so", "what"],
    ["in", "short"],
    ["in", "summary"],
    ["in", "brief"],
    ["ultimately"],
    ["overall"],
    ["bottom", "line"],
    ["the", "bottom", "line"],
    ["net-net"],
    ["what", "this", "means"],
    ["what", "it", "means"],
    ["why", "it", "matters"],
    ["why", "this", "matters"],
    ["for", "investors"],
    ["for", "everyday", "investors"],
    ["what", "this", "means", "for", "investors"],
    ["what", "it", "means", "for", "investors"],
    ["the", "bottom", "line", "for", "investors"],
]

private let conclusionLeadInOpenNounStems: [[String]] = [
    ["the", "takeaway"],
    ["takeaway"],
    ["key", "takeaway"],
    ["the", "key", "takeaway"],
    ["the", "upshot"],
    ["upshot"],
]

/// Lead-ins that end in a conjunction, so they have no clause punctuation to find:
/// "Investors should care because Oracle's backlog…" (TestFlight, 2026-09-10 — the
/// prompt itself used to ask the final bullet to explain "why an everyday investor
/// should care", and the model echoed it). Matched on leading WORDS.
private let conclusionLeadInPhraseStems: [[String]] = [
    ["investors", "should", "care", "because"],
    ["everyday", "investors", "should", "care", "because"],
    ["why", "should", "investors", "care", "because"],
    ["this", "matters", "because"],
    ["this", "matters", "for", "investors", "because"],
    ["it", "matters", "because"],
    ["why", "it", "matters", "is", "that"],
    ["why", "this", "matters", "is", "that"],
]

/// A remainder that opens with one of these is the middle of a sentence, not its
/// start: "For investors, especially retirees, the cut…" must not become
/// "Especially retirees, the cut…", nor "This matters because of the debt" "Of the debt".
private let conclusionLeadInContinuationWords: Set<String> = [
    "and", "or", "but", "nor", "especially", "particularly", "notably",
    "including", "though", "however", "of", "is", "are", "was", "were",
    "which", "according", "because", "beyond",
]

/// After a PHRASE only: the stripped words held the pronoun's antecedent, ANYWHERE in the
/// remainder ("…should care because rising yields raise their borrowing costs").
private let conclusionLeadInPhrasePronouns: Set<String> = ["they", "their", "them", "theirs"]

/// A remainder must START like a sentence: a letter, a digit, an opening quote or a
/// currency sign — never "– unlike peers –" or a closing quote.
private let conclusionSentenceStartExtras: Set<Character> = ["\"", "\u{201C}", "'", "\u{2018}", "$", "\u{20AC}", "\u{00A3}", "\u{00A5}"]

/// A real lead-in is short. Past this the clause is carrying content.
private let conclusionLeadInMaxWords = 6

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
        // Never a colon between digits — "at 4:05 p.m." is a time, not a label.
        if colon > startIndex, index(after: colon) < endIndex,
           self[index(before: colon)].isNumber, self[index(after: colon)].isNumber {
            return self
        }
        let lead = self[..<colon]
        // Keep the sentence readable: drop spaces right after the colon so we
        // don't produce ",  " (double space).
        let rest = self[index(after: colon)...].drop(while: { $0 == " " })
        return "\(lead), \(rest)"
    }

    /// Removes a conclusion lead-in ("The takeaway for everyday investors, …",
    /// "In short, …") so only the point itself remains, re-capitalised.
    ///
    /// The conclusion bullet is marked with an icon now (`SummaryBulletGlyph`), so
    /// saying it in words is redundant. Both backend prompts have been changed to
    /// stop writing them — **and that is not sufficient**, which is the whole
    /// reason this exists:
    ///
    /// - the Insights card is regenerated by a `PROMPT_VERSION` bump, so it heals
    ///   on the next sweep;
    /// - per-article bullets have **no invalidation mechanism at all**. They are
    ///   re-enriched only when `ai_processed` is false, and `expires_at` is
    ///   re-stamped on every refresh, so an article that keeps circulating in the
    ///   feed keeps its old text indefinitely. Without this, the new icon would
    ///   land directly in front of the words it replaces.
    ///
    /// Falls back to `normalizingLeadInColon()` when no stem matches, so an
    /// unrecognised "Bottom line: X" still reads as a sentence rather than a label.
    ///
    /// - Warning: stems match on WORD boundaries, never as a raw prefix.
    ///   `"Sony, the electronics maker, …"` opens with the letters of the stem
    ///   `so`; a prefix test would eat the company's name, and the result would
    ///   read like a model error rather than a formatting bug.
    func strippingConclusionLeadIn(maxLeadIn: Int = 48) -> String {
        // PHRASE stems first: they end in a conjunction, so the clause test below
        // can never see them — "Investors should care because the Fed cut rates."
        // has no `,` `:` or `—` at all.
        if let rest = conclusionRemainderAfterLeadingPhrase(maxLeadIn: maxLeadIn) {
            return rest.capitalisingConclusionStart()
        }

        let clauseEnd = firstIndex(where: { $0 == "," || $0 == ":" || $0 == "\u{2014}" })
        guard let end = clauseEnd,
              distance(from: startIndex, to: end) <= maxLeadIn else {
            return normalizingLeadInColon()
        }
        // Only whitespace before the first word: a QUOTED lead-in is content, and
        // stripping it left an unbalanced quote.
        guard self[..<end].prefix(while: { !($0.isLetter || $0 == "-") }).allSatisfy({ $0.isWhitespace }) else {
            return normalizingLeadInColon()
        }

        // Compare on words, lowercased, with punctuation dropped so "So what?" and
        // "so what" are the same opener.
        let words = self[..<end]
            .lowercased()
            .split(whereSeparator: { !$0.isLetter && $0 != "-" })
            .map(String.init)
        guard !words.isEmpty, words.count <= conclusionLeadInMaxWords else {
            return normalizingLeadInColon()
        }
        let isExact = conclusionLeadInExactStems.contains(words)
        let isOpenNoun = conclusionLeadInOpenNounStems.contains { stem in
            words.count >= stem.count && Array(words.prefix(stem.count)) == stem
        }
        guard isExact || isOpenNoun else { return normalizingLeadInColon() }

        let rest = self[index(after: end)...]
            .trimmingCharacters(in: .whitespacesAndNewlines)
        // A lead-in with nothing behind it is not a lead-in. Returning "" here
        // would blank the one line the reader most needs.
        guard rest.count >= 20 else { return normalizingLeadInColon() }
        // "What this means, in practice, is higher rates" and "Ultimately — and this
        // is the key point — the Fed decides" are sentences whose SUBJECT is the
        // lead-in; cutting it leaves a fragment.
        guard rest.isPlausibleConclusionRemainder(afterDash: self[end] == "\u{2014}") else {
            return normalizingLeadInColon()
        }

        return rest.capitalisingConclusionStart()
    }

    /// The text after a leading PHRASE stem, or nil when none applies (or when what
    /// follows would not stand on its own).
    private func conclusionRemainderAfterLeadingPhrase(maxLeadIn: Int) -> String? {
        let window = prefix(maxLeadIn + 1)
        let tokens = window.split(whereSeparator: { !$0.isLetter && $0 != "-" })
        guard let first = tokens.first,
              window[window.startIndex..<first.startIndex].allSatisfy({ $0.isWhitespace })
        else { return nil }
        for stem in conclusionLeadInPhraseStems where tokens.count >= stem.count {
            guard zip(tokens, stem).allSatisfy({ $0.0.lowercased() == $0.1 }) else { continue }
            let last = tokens[stem.count - 1]
            guard distance(from: startIndex, to: last.endIndex) <= maxLeadIn else { continue }
            let tail = self[last.endIndex...]
            let dropped = tail.prefix(while: { $0 == " " || $0 == "," || $0 == ":" || $0 == "?" || $0 == "\u{2014}" })
            let rest = tail.dropFirst(dropped.count)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            guard rest.count >= 20 else { return nil }
            let letterWords = rest.split(whereSeparator: { !$0.isLetter }).map { $0.lowercased() }
            guard !letterWords.contains(where: { conclusionLeadInPhrasePronouns.contains($0) }) else { return nil }
            guard rest.isPlausibleConclusionRemainder(afterDash: dropped.contains("\u{2014}")) else { return nil }
            return rest
        }
        return nil
    }

    /// The first whitespace-delimited token, edge punctuation trimmed, lowercased. A
    /// TOKEN, not the first run of letters: "80% of Oracle's revenue…" starts with "80",
    /// and reading "of" there refused an honest strip.
    fileprivate var firstConclusionWord: String {
        guard let token = split(whereSeparator: { $0.isWhitespace }).first else { return "" }
        let keep: (Character) -> Bool = { $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" }
        let head = token.drop(while: { !keep($0) })
        let trimmed = String(head.reversed().drop(while: { !keep($0) }).reversed())
        return trimmed.lowercased()
    }

    fileprivate func isPlausibleConclusionRemainder(afterDash: Bool) -> Bool {
        guard let head = first,
              head.isLetter || head.isNumber || conclusionSentenceStartExtras.contains(head) else {
            return false
        }
        if conclusionLeadInContinuationWords.contains(firstConclusionWord) { return false }
        if afterDash && contains("\u{2014}") { return false }
        if range(
            of: "^[^,:\u{2014}]{1,40}[,\u{2014}]\\s*(is|are|was|were)\\b",
            options: [.regularExpression, .caseInsensitive]
        ) != nil {
            return false
        }
        return true
    }

    /// Upper-cases the first letter unless the first word is already cased inside
    /// ("iPhone demand…" must not become "IPhone demand…").
    fileprivate func capitalisingConclusionStart() -> String {
        guard let head = first else { return self }
        let firstWord = prefix(while: { $0.isLetter || $0 == "-" })
        if firstWord.dropFirst().contains(where: { $0.isUppercase }) { return self }
        return String(head).uppercased() + dropFirst()
    }
}

extension Array where Element == String {
    /// The first `limit - 1` bullets plus the LAST one, when there are too many.
    ///
    /// The last bullet is the conclusion (the ↳ row). `prefix(limit)` used to cut it
    /// off whenever a card carried more bullets than the "why it moved" layout had
    /// room for, and the arrow landed on a plain fact instead.
    func keepingConclusion(limit: Int) -> [String] {
        guard limit > 0 else { return [] }
        guard count > limit, let conclusion = last else { return self }
        return Array(prefix(limit - 1)) + [conclusion]
    }
}
