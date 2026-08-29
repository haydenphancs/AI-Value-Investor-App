//
//  InsightsDetailView.swift
//  ios
//
//  Screen: the Insights card expanded — the AI summary on top, then the source
//  stories it was built from, each tappable to the publisher (in-app browser).
//  Presented as a sheet from the Updates screen when the card is tapped.
//

import SwiftUI

struct InsightsDetailView: View {
    let summary: NewsInsightSummary

    @Environment(\.dismiss) private var dismiss
    /// The source article currently open in the in-app browser (same wrapper the
    /// timeline uses — see InAppBrowser.swift).
    @State private var browserLink: BrowserLink?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.lg) {
                    summarySection
                    if !summary.sources.isEmpty {
                        sourcesSection
                    }
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background.ignoresSafeArea())
            .navigationTitle("Insights")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
            // Same in-app browser the timeline uses, so a source link never ejects
            // the user out of Caydex into Safari.
            .inAppBrowser(link: $browserLink)
        }
    }

    // MARK: - Summary

    /// Mirrors `InsightsSummaryCard` so the card and its tap-through cannot show
    /// a different body for the same summary.
    private var catalyst: InsightPriceMove? {
        summary.isAIGenerated ? summary.priceMove : nil
    }

    private var visibleBullets: ArraySlice<String> {
        summary.bulletPoints.prefix(catalyst == nil ? 5 : 4)
    }

    private var summarySection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack {
                if summary.isAIGenerated {
                    AIInsightLabel(
                        text: "Insights",
                        font: AppTypography.bodyEmphasis,
                        iconFont: AppTypography.iconSmall
                    )
                }
                Spacer()
                if summary.isAIGenerated {
                    SentimentBadge(sentiment: summary.sentiment)
                }
            }

            Text(summary.headline)
                .font(AppTypography.titleCompact)
                .foregroundColor(AppColors.textPrimary)
                .fixedSize(horizontal: false, vertical: true)

            // Same one-body composition as the card: catalyst first, then the
            // bullets. The tap-through used to render NO price move at all, so
            // opening Sources silently dropped the "why it moved" explanation —
            // the one piece of content on this screen that actually has cited
            // sources to open.
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                if let move = catalyst {
                    InsightCatalystBullet(move: move)
                }

                ForEach(Array(visibleBullets.enumerated()), id: \.offset) { index, point in
                    let isLast = index == visibleBullets.count - 1
                    HStack(alignment: .top, spacing: AppSpacing.sm) {
                        SummaryBulletGlyph(isConclusion: isLast)
                        Text(isLast ? point.strippingConclusionLeadIn() : point)
                            .font(AppTypography.bodySmall)
                            .foregroundColor(AppColors.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
        .padding(AppSpacing.lg)
        .cardSurface(cornerRadius: AppCornerRadius.large)
    }

    // MARK: - Sources

    private var sourcesSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text("SOURCES")
                .font(AppTypography.caption)
                .fontWeight(.semibold)
                .foregroundColor(AppColors.textMuted)
                .padding(.leading, AppSpacing.xs)

            VStack(spacing: 0) {
                ForEach(summary.sources) { source in
                    sourceRow(source)
                    if source.id != summary.sources.last?.id {
                        Divider().overlay(AppColors.divider)
                    }
                }
            }
            .cardSurface(cornerRadius: AppCornerRadius.large)
        }
    }

    @ViewBuilder
    private func sourceRow(_ source: InsightSource) -> some View {
        let openable = source.url != nil
        Button {
            if let url = source.url { openExternal(url, into: &browserLink) }
        } label: {
            HStack(alignment: .top, spacing: AppSpacing.sm) {
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    Text(source.title)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textPrimary)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                    // The OUTLET, with the URL host only as a fallback. A bare
                    // host mislabels broadcast news: feeds link TV segments to
                    // their video upload, so this row read "youtube.com" when the
                    // source was Bloomberg or CNBC Television.
                    if let name = source.displayName {
                        HStack(spacing: AppSpacing.xxs) {
                            if source.isVideo {
                                Image(systemName: "play.rectangle.fill")
                                    .font(AppTypography.iconTiny)
                                    .foregroundColor(AppColors.textMuted)
                            }
                            Text(name)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textMuted)
                                .lineLimit(1)
                        }
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel(source.isVideo ? "\(name), video" : name)
                    }
                }
                Spacer(minLength: AppSpacing.sm)
                if openable {
                    Image(systemName: "arrow.up.right")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .padding(.top, 2)
                }
            }
            .padding(AppSpacing.md)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(PlainButtonStyle())
        .disabled(!openable)
    }
}

#Preview {
    InsightsDetailView(
        summary: NewsInsightSummary(
            headline: "AI, Geopolitics and Earnings Drive Mixed Market Signals",
            bulletPoints: [
                "Artificial intelligence investment is boosting markets, with AI-stock valuations more attractive.",
                "Geopolitical tensions are driving oil prices up and benefiting energy companies.",
                "The takeaway, investors should watch upcoming Big Tech earnings as growth broadens."
            ],
            sentiment: .neutral,
            updatedAt: Date().addingTimeInterval(-3600),
            summaryType: "24h",
            sources: [
                InsightSource(title: "Nvidia leads AI rally as valuations stretch",
                              url: URL(string: "https://www.reuters.com/tech/ai"),
                              publisher: "Reuters"),
                // A broadcast segment: video host, real outlet. The case this screen got wrong.
                InsightSource(title: "Fed Chair's full remarks at the Jackson Hole symposium",
                              url: URL(string: "https://www.youtube.com/watch?v=abc123"),
                              publisher: "CNBC Television"),
                // A card stored before `publisher` shipped — falls back to the host.
                InsightSource(title: "Oil climbs on Middle East tensions",
                              url: URL(string: "https://www.cnbc.com/oil"),
                              publisher: nil),
                InsightSource(title: "Big Tech earnings preview", url: nil, publisher: nil)
            ]
        )
    )
}
