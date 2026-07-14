//
//  MoneyMoveArticleSectionContent.swift
//  ios
//
//  Organism: Article section with title, icon, and content blocks
//

import SwiftUI
import Charts

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
                    renderContent(content, readAlong: readAlongGroup(at: index))
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
