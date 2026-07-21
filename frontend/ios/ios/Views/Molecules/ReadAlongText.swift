//
//  ReadAlongText.swift
//  ios
//
//  Molecule: renders a run of timed sentence spans as a single Text, lighting up the sentence whose
//  [start, end) contains `activeTime` (a brighter foreground + a soft highlight background). Shared
//  by the Book Library (ReadAlongBlockView) and Money Moves article sentence read-along.
//
//  Word-granularity read-along (Investor Journey) does NOT use this — it highlights a word range in
//  the original text via AIVoiceManager, so original spacing/markup is preserved.
//

import SwiftUI

struct ReadAlongText: View {
    let spans: [ReadAlongSentence]
    let activeTime: Double?
    var font: Font
    var base: Color
    var lineSpacing: CGFloat = 6

    var body: some View {
        Text(ReadAlongText.attributed(spans: spans, activeTime: activeTime, base: base))
            .font(font)
            .lineSpacing(lineSpacing)
            .fixedSize(horizontal: false, vertical: true)
    }

    /// Index of the span currently being read, if the playhead is inside this run.
    ///
    /// STICKY across inter-sentence pauses. A strict `start <= t < end` containment
    /// test leaves NOTHING highlighted whenever the playhead sits in the silence
    /// between two spans — and the shipped alignment data is full of those: 2,448
    /// intra-block gaps in the book table (median 0.60 s) and 290 in Money Moves.
    /// Since the player ticks every 0.5 s, virtually every one of them is sampled,
    /// so the highlight visibly blinked off and back on at nearly every sentence
    /// boundary for the whole of a narration.
    ///
    /// Instead: the last span that has STARTED stays lit until the next one does.
    /// Past the final span's `end` the highlight clears, so a finished (or
    /// seeked-past) run isn't left with a stale sentence lit.
    static func activeIndex(_ spans: [ReadAlongSentence], _ activeTime: Double?) -> Int? {
        guard let t = activeTime, !spans.isEmpty else { return nil }
        guard let last = spans.last, t < last.end else { return nil }
        // `lastIndex(where:)` rather than `firstIndex`: spans are ordered, so the
        // last one that has started is the one being read (or just read).
        return spans.lastIndex { t >= $0.start }
    }

    /// Build the run's text, lighting up the active span. Spans are joined with single spaces
    /// (prose reconstruction) — matches how the aligner splits sentences.
    static func attributed(
        spans: [ReadAlongSentence],
        activeTime: Double?,
        base: Color,
        highlightForeground: Color = AppColors.textPrimary,
        highlightBackground: Color = AppColors.primaryBlue.opacity(0.28)
    ) -> AttributedString {
        let active = activeIndex(spans, activeTime)
        var result = AttributedString()
        for (i, span) in spans.enumerated() {
            var piece = AttributedString(i == 0 ? span.text : " " + span.text)
            if i == active {
                piece.foregroundColor = highlightForeground
                piece.backgroundColor = highlightBackground
            } else {
                piece.foregroundColor = base
            }
            result.append(piece)
        }
        return result
    }
}
