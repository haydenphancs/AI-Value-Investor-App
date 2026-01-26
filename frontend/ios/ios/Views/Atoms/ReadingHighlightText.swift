//
//  ReadingHighlightText.swift
//  ios
//
//  Atom: Text view that highlights words progressively as AI voice reads
//  Creates a karaoke-style effect where spoken words are highlighted
//

import SwiftUI

struct ReadingHighlightText: View {
    let text: String
    let currentWordRange: NSRange
    let isReading: Bool
    var baseColor: Color = AppColors.textSecondary
    var highlightColor: Color = AppColors.accentCyan
    var spokenColor: Color = AppColors.textPrimary
    var font: Font = .system(size: 20, weight: .regular)

    var body: some View {
        highlightedTextView
            .font(font)
            .lineSpacing(6)
    }

    @ViewBuilder
    private var highlightedTextView: some View {
        if isReading && currentWordRange.length > 0 {
            // Show progressive highlighting
            buildHighlightedText()
        } else {
            // Show base text
            Text(text)
                .foregroundColor(baseColor)
        }
    }

    private func buildHighlightedText() -> Text {
        let nsText = text as NSString
        let totalLength = nsText.length

        // Safety check
        guard currentWordRange.location < totalLength else {
            return Text(text).foregroundColor(spokenColor)
        }

        var result = Text("")

        // Text before current word (already spoken) - white
        if currentWordRange.location > 0 {
            let spokenText = nsText.substring(to: currentWordRange.location)
            result = result + Text(spokenText).foregroundColor(spokenColor)
        }

        // Current word being spoken - cyan highlight
        let endOfCurrentWord = min(currentWordRange.location + currentWordRange.length, totalLength)
        let currentWord = nsText.substring(with: NSRange(location: currentWordRange.location, length: endOfCurrentWord - currentWordRange.location))
        result = result + Text(currentWord).foregroundColor(highlightColor)

        // Remaining text (not yet spoken) - gray
        if endOfCurrentWord < totalLength {
            let remainingText = nsText.substring(from: endOfCurrentWord)
            result = result + Text(remainingText).foregroundColor(baseColor)
        }

        return result
    }
}

// MARK: - Segmented Version for pre-highlighted text

struct ReadingHighlightSegmentedText: View {
    let segments: [HighlightedTextSegment]
    let currentWordRange: NSRange
    let isReading: Bool
    var baseColor: Color = AppColors.textSecondary
    var highlightColor: Color = AppColors.accentCyan
    var spokenColor: Color = AppColors.textPrimary
    var font: Font = .system(size: 20, weight: .regular)

    // Computed full text for range calculations
    private var fullText: String {
        segments.map { $0.text }.joined()
    }

    var body: some View {
        highlightedTextView
            .font(font)
            .lineSpacing(6)
    }

    @ViewBuilder
    private var highlightedTextView: some View {
        if isReading && currentWordRange.length > 0 {
            buildHighlightedText()
        } else {
            // Show base segmented text with original highlighting
            buildBaseSegmentedText()
        }
    }

    private func buildBaseSegmentedText() -> Text {
        segments.reduce(Text("")) { result, segment in
            result + Text(segment.text)
                .foregroundColor(segment.isHighlighted ? highlightColor : baseColor)
        }
    }

    private func buildHighlightedText() -> Text {
        let nsText = fullText as NSString
        let totalLength = nsText.length

        guard currentWordRange.location < totalLength else {
            return buildFullySpokenText()
        }

        var result = Text("")
        var currentPosition = 0

        for segment in segments {
            let segmentLength = segment.text.count
            let segmentEnd = currentPosition + segmentLength

            // Determine how this segment relates to the current reading position
            let spokenEnd = currentWordRange.location
            let currentWordEnd = min(currentWordRange.location + currentWordRange.length, totalLength)

            if segmentEnd <= spokenEnd {
                // Entire segment has been spoken - white
                result = result + Text(segment.text).foregroundColor(spokenColor)
            } else if currentPosition >= currentWordEnd {
                // Segment hasn't been reached yet - use original coloring
                result = result + Text(segment.text)
                    .foregroundColor(segment.isHighlighted ? highlightColor : baseColor)
            } else {
                // Segment is partially spoken or contains current word
                let nsSegment = segment.text as NSString

                // Calculate relative positions within this segment
                let relativeSpokenEnd = max(0, spokenEnd - currentPosition)
                let relativeCurrentWordStart = max(0, currentWordRange.location - currentPosition)
                let relativeCurrentWordEnd = min(segmentLength, currentWordEnd - currentPosition)

                // Part 1: Already spoken portion
                if relativeSpokenEnd > 0 && relativeSpokenEnd <= segmentLength {
                    let spokenPortion = nsSegment.substring(to: min(relativeSpokenEnd, segmentLength))
                    result = result + Text(spokenPortion).foregroundColor(spokenColor)
                }

                // Part 2: Current word being spoken
                if relativeCurrentWordStart < segmentLength && relativeCurrentWordEnd > relativeSpokenEnd {
                    let start = max(relativeSpokenEnd, relativeCurrentWordStart)
                    let end = min(relativeCurrentWordEnd, segmentLength)
                    if end > start {
                        let currentPortion = nsSegment.substring(with: NSRange(location: start, length: end - start))
                        result = result + Text(currentPortion).foregroundColor(highlightColor)
                    }
                }

                // Part 3: Not yet spoken portion
                if relativeCurrentWordEnd < segmentLength {
                    let remainingPortion = nsSegment.substring(from: relativeCurrentWordEnd)
                    result = result + Text(remainingPortion)
                        .foregroundColor(segment.isHighlighted ? highlightColor : baseColor)
                }
            }

            currentPosition = segmentEnd
        }

        return result
    }

    private func buildFullySpokenText() -> Text {
        segments.reduce(Text("")) { result, segment in
            result + Text(segment.text).foregroundColor(spokenColor)
        }
    }
}

#Preview {
    VStack(spacing: 30) {
        // Simple text with highlighting
        ReadingHighlightText(
            text: "Price is what the market asks. Value is what the business is worth.",
            currentWordRange: NSRange(location: 18, length: 6), // "market"
            isReading: true
        )
        .padding()

        // Not reading state
        ReadingHighlightText(
            text: "Price is what the market asks. Value is what the business is worth.",
            currentWordRange: NSRange(location: 0, length: 0),
            isReading: false
        )
        .padding()

        // Segmented text
        ReadingHighlightSegmentedText(
            segments: [
                HighlightedTextSegment("Price is what the "),
                HighlightedTextSegment("market", highlighted: true),
                HighlightedTextSegment(" asks.")
            ],
            currentWordRange: NSRange(location: 5, length: 2), // "is"
            isReading: true
        )
        .padding()
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
