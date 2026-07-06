//
//  MarkdownText.swift
//  ios
//
//  Atom: lightweight Markdown renderer for Cay AI chat messages.
//
//  The backend writes "clean markdown" (## headers, **bold**, - bullets, 1.
//  lists). This atom splits the text into block elements and renders inline
//  spans (**bold** / *italic* / `code`) via AttributedString(markdown:) — no
//  SPM dependency. Every inline parse is wrapped in `try?` with a plain-text
//  fallback, because streamed text arrives in PARTIAL fragments (an unclosed
//  `**` mid-token would otherwise throw and blank the row).
//

import SwiftUI

struct MarkdownText: View {
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            ForEach(Array(Self.blocks(from: text).enumerated()), id: \.offset) { _, block in
                block.view
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Block model

    fileprivate enum Block {
        case heading(String, level: Int)
        case bullet(String)
        case numbered(String, marker: String)
        case paragraph(String)

        @ViewBuilder var view: some View {
            switch self {
            case .heading(let s, let level):
                MarkdownInline(raw: s)
                    .font(level <= 1 ? AppTypography.heading : AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
                    .padding(.top, AppSpacing.xxs)

            case .bullet(let s):
                HStack(alignment: .top, spacing: AppSpacing.sm) {
                    Text("•")
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.primaryBlue)
                    MarkdownInline(raw: s)
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textPrimary)
                }

            case .numbered(let s, let marker):
                HStack(alignment: .top, spacing: AppSpacing.sm) {
                    Text(marker)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.primaryBlue)
                    MarkdownInline(raw: s)
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textPrimary)
                }

            case .paragraph(let s):
                MarkdownInline(raw: s)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textPrimary)
            }
        }
    }

    // MARK: - Block parsing

    fileprivate static func blocks(from text: String) -> [Block] {
        var out: [Block] = []
        let lines = text
            .replacingOccurrences(of: "\r\n", with: "\n")
            .components(separatedBy: "\n")
        for rawLine in lines {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            if line.isEmpty { continue }
            if let level = headingLevel(line) {
                out.append(.heading(stripHeadingMarks(line), level: level))
            } else if line.hasPrefix("- ") || line.hasPrefix("* ") {
                out.append(.bullet(String(line.dropFirst(2))))
            } else if let (marker, rest) = numberedItem(line) {
                out.append(.numbered(rest, marker: marker))
            } else {
                out.append(.paragraph(line))
            }
        }
        return out
    }

    private static func headingLevel(_ line: String) -> Int? {
        if line.hasPrefix("#### ") { return 4 }
        if line.hasPrefix("### ") { return 3 }
        if line.hasPrefix("## ") { return 2 }
        if line.hasPrefix("# ") { return 1 }
        return nil
    }

    private static func stripHeadingMarks(_ line: String) -> String {
        var s = Substring(line)
        while s.first == "#" { s = s.dropFirst() }
        return String(s).trimmingCharacters(in: .whitespaces)
    }

    /// Matches "1. text" or "12) text"; returns ("1.", "text"). nil otherwise.
    private static func numberedItem(_ line: String) -> (marker: String, rest: String)? {
        var digits = ""
        var i = line.startIndex
        while i < line.endIndex, line[i].isNumber {
            digits.append(line[i])
            i = line.index(after: i)
        }
        guard !digits.isEmpty, i < line.endIndex else { return nil }
        let sep = line[i]
        guard sep == "." || sep == ")" else { return nil }
        let afterSep = line.index(after: i)
        guard afterSep < line.endIndex, line[afterSep] == " " else { return nil }
        let rest = String(line[line.index(after: afterSep)...])
        return ("\(digits).", rest)
    }
}

// MARK: - Inline renderer (tolerant of malformed streamed fragments)

private struct MarkdownInline: View {
    let raw: String

    var body: some View {
        Text(attributed)
            .fixedSize(horizontal: false, vertical: true)
    }

    /// Parse inline markdown; on ANY failure fall back to the raw string so a
    /// half-streamed fragment (e.g. an unclosed `**`) never crashes/blanks the row.
    private var attributed: AttributedString {
        (try? AttributedString(
            markdown: raw,
            options: AttributedString.MarkdownParsingOptions(
                interpretedSyntax: .inlineOnlyPreservingWhitespace
            )
        )) ?? AttributedString(raw)
    }
}

// MARK: - Preview

#Preview {
    ScrollView {
        VStack(alignment: .leading, spacing: 16) {
            MarkdownText(text: """
            ## Quality
            **Apple** scores high on durability with a *wide* moat.

            Key drivers:
            - Gross margin 46% (sector 38%)
            - ROIC 31%
            1. Ecosystem lock-in
            2. Services growth

            Watch the `net_margin` trend into next quarter.
            """)

            // Malformed / partial streamed fragment must not crash.
            MarkdownText(text: "Apple's margins are **expanding into")
        }
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
