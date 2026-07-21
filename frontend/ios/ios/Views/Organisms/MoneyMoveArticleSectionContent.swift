//
//  MoneyMoveArticleSectionContent.swift
//  ios
//
//  Organism: Article section with title, icon, and content blocks
//

import SwiftUI
import Charts
import Foundation

/// One-shot reporter for stale read-along spans. `body` re-renders on every narration tick
/// (~2 Hz while audio plays), so an unguarded log would bury the real defect under thousands of
/// duplicates. Locked rather than actor-isolated so it stays callable from the plain (non-isolated)
/// view helpers below.
private final class StaleReadAlongLog: @unchecked Sendable {
    static let shared = StaleReadAlongLog()
    private let lock = NSLock()
    private var reported = Set<String>()

    func once(_ key: String, _ message: () -> String) {
        lock.lock()
        let isNew = reported.insert(key).inserted
        lock.unlock()
        if isNew { print(message()) }
    }
}

struct MoneyMoveArticleSectionContent: View {
    let section: ArticleSection
    /// Narration playhead (seconds) when this article's audio is active, else nil (no highlight).
    var activeTime: Double? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section header
            HStack(spacing: AppSpacing.md) {
                if let icon = section.icon {
                    ArticleSectionIcon(icon: icon)
                }

                Text(section.title)
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)
            }

            // Content blocks
            VStack(alignment: .leading, spacing: AppSpacing.lg) {
                ForEach(Array(section.content.enumerated()), id: \.offset) { index, content in
                    renderContent(content, readAlong: verifiedReadAlong(at: index, for: content))
                }
            }
        }
        .padding(AppSpacing.lg)
        .background(Color(hex: "171B26"))
    }

    /// Read-along timings for the block at `index` (nil when none / not yet aligned).
    private func readAlongGroup(at index: Int) -> ReadAlongGroup? {
        index < section.readAlong.count ? section.readAlong[index] : nil
    }

    /// Timings for the block at `index` — but only once they are PROVEN to reconstruct the
    /// authored prose.
    ///
    /// `ReadAlongText` builds the paragraph FROM the spans (it joins their `text`), so the spans
    /// are the copy the reader actually sees, not a highlight overlay on top of `text`. Timings
    /// come from align_money_moves_audio.py and describe the text AS IT WAS AT ALIGNMENT TIME, so
    /// rewording a paragraph and reseeding WITHOUT re-running alignment doesn't merely drift the
    /// highlight (all the authoring playbook warns about) — it SUBSTITUTES the old sentences for
    /// the authored ones, silently publishing copy nobody wrote. Verify first; on mismatch fall
    /// back to plain text: right words, no highlight.
    private func verifiedReadAlong(at index: Int, for content: ArticleSectionContent) -> ReadAlongGroup? {
        guard let group = readAlongGroup(at: index) else { return nil }
        switch group {
        case .sentences(let spans):
            // No authored text to compare against (bulletList uses `.items`, chart narrates
            // nothing) => unverifiable => no read-along.
            guard let authored = Self.authoredText(of: content) else { return nil }
            guard Self.spans(spans, reconstruct: authored) else {
                logStaleSpans(at: "\(section.title)#\(index)", authored: authored, spans: spans)
                return nil
            }
            return group

        case .items(let itemSpans):
            guard case .bulletList(let items) = content else { return nil }
            // Verified PER ITEM so one stale bullet doesn't cost its neighbours their highlight.
            // A rejected item becomes an empty span list, which the bulletList branch already
            // treats as "no timings" and renders as plain `Text(item)`.
            var verified: [[ReadAlongSentence]] = []
            for (i, spans) in itemSpans.enumerated() {
                let authored = i < items.count ? items[i] : nil
                if let authored, Self.spans(spans, reconstruct: authored) {
                    verified.append(spans)
                } else {
                    logStaleSpans(at: "\(section.title)#\(index).item\(i)",
                                  authored: authored ?? "<no item at this index>", spans: spans)
                    verified.append([])
                }
            }
            return .items(verified)
        }
    }

    /// The prose a `.sentences` run is supposed to reconstruct; nil when the block renders none.
    private static func authoredText(of content: ArticleSectionContent) -> String? {
        switch content {
        case .paragraph(let text), .subheading(let text): return text
        case .quote(let text, _): return text
        case .callout(_, let text, _): return text
        case .bulletList, .chart: return nil
        }
    }

    /// Whitespace-normalised equality against ReadAlongText's OWN single-space join, so the
    /// comparison matches exactly what would be rendered. Normalising whitespace keeps a harmless
    /// re-wrap / double-space edit from disabling read-along on otherwise-identical prose.
    private static func spans(_ spans: [ReadAlongSentence], reconstruct text: String) -> Bool {
        normalizedWhitespace(spans.map(\.text).joined(separator: " ")) == normalizedWhitespace(text)
    }

    private static func normalizedWhitespace(_ s: String) -> String {
        s.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
    }

    /// Stale timings are a CONTENT defect (edited + reseeded without re-alignment), diagnosed later
    /// from logs alone — so name the section, the block, and BOTH strings.
    private func logStaleSpans(at location: String, authored: String, spans: [ReadAlongSentence]) {
        let rebuilt = spans.map(\.text).joined(separator: " ")
        StaleReadAlongLog.shared.once("\(location)|\(authored.prefix(48))") {
            """
            [MoneyMoveArticleSectionContent] stale read-along at \(location): spans do not \
            reconstruct the authored text — rendering plain text (no highlight). Re-run \
            align_money_moves_audio.py, then reseed. authored="\(authored.prefix(120))" \
            spans="\(rebuilt.prefix(120))"
            """
        }
    }

    @ViewBuilder
    private func renderContent(_ content: ArticleSectionContent, readAlong: ReadAlongGroup?) -> some View {
        switch content {
        case .paragraph(let text):
            if case let .sentences(spans) = readAlong, !spans.isEmpty {
                ReadAlongText(spans: spans, activeTime: activeTime, font: AppTypography.body, base: AppColors.textPrimary)
            } else {
                Text(text)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textPrimary)
                    .lineSpacing(6)
                    .fixedSize(horizontal: false, vertical: true)
            }

        case .bulletList(let items):
            let itemSpans: [[ReadAlongSentence]]? = { if case let .items(s) = readAlong { return s } else { return nil } }()
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                ForEach(Array(items.enumerated()), id: \.offset) { i, item in
                    HStack(alignment: .top, spacing: AppSpacing.md) {
                        Circle()
                            .fill(AppColors.primaryBlue)
                            .frame(width: 6, height: 6)
                            .padding(.top, 7)

                        if let spans = itemSpans, i < spans.count, !spans[i].isEmpty {
                            ReadAlongText(spans: spans[i], activeTime: activeTime, font: AppTypography.body, base: AppColors.textPrimary)
                        } else {
                            Text(item)
                                .font(AppTypography.body)
                                .foregroundColor(AppColors.textPrimary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
            .padding(.leading, AppSpacing.sm)

        case .subheading(let text):
            if case let .sentences(spans) = readAlong, !spans.isEmpty {
                ReadAlongText(spans: spans, activeTime: activeTime, font: AppTypography.headingSmall, base: AppColors.textPrimary)
                    .padding(.top, AppSpacing.sm)
            } else {
                Text(text)
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
                    .padding(.top, AppSpacing.sm)
            }

        case .quote(let text, let attribution):
            ArticleQuoteBlock(text: text, attribution: attribution,
                              readAlong: sentenceSpans(readAlong), activeTime: activeTime)

        case .callout(let icon, let text, let style):
            ArticleCalloutBox(icon: icon, text: text, style: style,
                              readAlong: sentenceSpans(readAlong), activeTime: activeTime)

        case .chart(let data):
            ArticleChartView(chartData: data)
        }
    }

    /// Unwrap a `.sentences` group (text blocks); nil otherwise (incl. an empty span list, so the
    /// quote/callout falls back to plain `text` instead of rendering blank).
    private func sentenceSpans(_ group: ReadAlongGroup?) -> [ReadAlongSentence]? {
        if case let .sentences(spans) = group, !spans.isEmpty { return spans }
        return nil
    }
}

// MARK: - Article Chart View
struct ArticleChartView: View {
    let chartData: ChartData

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text(chartData.title)
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textSecondary)

            Chart {
                ForEach(chartData.dataPoints) { point in
                    switch chartData.type {
                    case .bar:
                        BarMark(
                            x: .value("Label", point.label),
                            y: .value("Value", point.value)
                        )
                        .foregroundStyle(point.color ?? AppColors.primaryBlue)

                    case .line:
                        LineMark(
                            x: .value("Label", point.label),
                            y: .value("Value", point.value)
                        )
                        .foregroundStyle(point.color ?? AppColors.primaryBlue)

                    case .area:
                        AreaMark(
                            x: .value("Label", point.label),
                            y: .value("Value", point.value)
                        )
                        .foregroundStyle(
                            LinearGradient(
                                colors: [
                                    (point.color ?? AppColors.primaryBlue).opacity(0.5),
                                    (point.color ?? AppColors.primaryBlue).opacity(0.1)
                                ],
                                startPoint: .top,
                                endPoint: .bottom
                            )
                        )
                    }
                }
            }
            .chartXAxis {
                AxisMarks(values: .automatic) { _ in
                    AxisValueLabel()
                        .foregroundStyle(AppColors.textSecondary)
                }
            }
            .chartYAxis {
                AxisMarks(values: .automatic) { _ in
                    AxisValueLabel()
                        .foregroundStyle(AppColors.textSecondary)
                    AxisGridLine()
                        .foregroundStyle(AppColors.cardBackgroundLight)
                }
            }
            .frame(height: 200)
        }
        .padding(AppSpacing.md)
        .background(AppColors.cardBackgroundLight)
        .cornerRadius(AppCornerRadius.medium)
    }
}

#Preview {
    ScrollView {
        VStack(spacing: AppSpacing.lg) {
            MoneyMoveArticleSectionContent(
                section: ArticleSection(
                    title: "The Rise of Decentralized Finance",
                    icon: "chart.bar.fill",
                    content: [
                        .paragraph("Decentralized finance, or DeFi, represents a fundamental shift in how financial services are delivered."),
                        .callout(
                            icon: "lightbulb.fill",
                            text: "DeFi protocols have processed over $180B in total value locked.",
                            style: .highlight
                        ),
                        .bulletList([
                            "Permissionless lending and borrowing",
                            "Automated market makers (AMMs)",
                            "Yield farming and liquidity mining"
                        ])
                    ],
                    hasGlowEffect: true
                )
            )

            MoneyMoveArticleSectionContent(
                section: ArticleSection(
                    title: "AI in Banking",
                    icon: "cpu.fill",
                    content: [
                        .paragraph("Traditional banks are not standing still."),
                        .subheading("Enhanced Security"),
                        .paragraph("Biometric authentication has reduced fraud rates by 45%."),
                        .quote(
                            text: "The future of finance isn't about going to the bank.",
                            attribution: "Industry Analyst"
                        )
                    ]
                )
            )
        }
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
