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

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section header
            HStack(spacing: AppSpacing.md) {
                if let icon = section.icon {
                    ArticleSectionIcon(icon: icon)
                }

                Text(section.title)
                    .font(AppTypography.title3)
                    .foregroundColor(AppColors.textPrimary)
            }

            // Content blocks
            VStack(alignment: .leading, spacing: AppSpacing.lg) {
                ForEach(section.content) { content in
                    renderContent(content)
                }
            }
        }
        .padding(AppSpacing.lg)
        .background(Color(hex: "171B26"))
    }

    @ViewBuilder
    private func renderContent(_ content: ArticleSectionContent) -> some View {
        switch content {
        case .paragraph(let text):
            Text(text)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textPrimary)
                .lineSpacing(6)
                .fixedSize(horizontal: false, vertical: true)

        case .bulletList(let items):
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                ForEach(items, id: \.self) { item in
                    HStack(alignment: .top, spacing: AppSpacing.md) {
                        Circle()
                            .fill(AppColors.primaryBlue)
                            .frame(width: 6, height: 6)
                            .padding(.top, 7)

                        Text(item)
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .padding(.leading, AppSpacing.sm)

        case .subheading(let text):
            Text(text)
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)
                .padding(.top, AppSpacing.sm)

        case .quote(let text, let attribution):
            ArticleQuoteBlock(text: text, attribution: attribution)

        case .callout(let icon, let text, let style):
            ArticleCalloutBox(icon: icon, text: text, style: style)

        case .chart(let data):
            ArticleChartView(chartData: data)
        }
    }
}

// MARK: - Article Chart View
struct ArticleChartView: View {
    let chartData: ChartData

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text(chartData.title)
                .font(AppTypography.calloutBold)
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
