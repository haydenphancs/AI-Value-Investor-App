//
//  TickerDetailSnapshotsSection.swift
//  ios
//
//  Organism: Snapshots section for Ticker Detail with expandable cards
//

import SwiftUI

struct TickerDetailSnapshotsSection: View {
    let snapshots: [SnapshotItem]
    var onDeepResearchTap: (() -> Void)?
    @State private var showInfoSheet: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section title with info button inside card styling
            HStack {
                Text("Snapshots")
                    .font(AppTypography.title3)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                // What's Snapshots? link
                Button(action: {
                    showInfoSheet = true
                }) {
                    Text("What's Snapshots?")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
                .buttonStyle(PlainButtonStyle())
            }

            // Snapshot cards
            VStack(spacing: 0) {
                ForEach(snapshots) { snapshot in
                    SnapshotCard(snapshot: snapshot)
                }
            }

            // AI Deep Research button
            AIDeepResearchButton(title: "AI Deep Research") {
                onDeepResearchTap?()
            }
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
        .sheet(isPresented: $showInfoSheet) {
            SnapshotsInfoSheet()
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
        }
    }
}

// MARK: - Snapshots Info Sheet
struct SnapshotsInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xl) {
                    // What are Snapshots?
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "camera.viewfinder")
                                .font(.system(size: 18))
                                .foregroundColor(AppColors.neutral)
                            Text("What are Snapshots?")
                                .font(AppTypography.headline)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        Text("Snapshots provide a quick, comprehensive view of a stock's health across multiple dimensions. Each snapshot evaluates different aspects of the company's performance, giving you an instant understanding of its strengths and weaknesses.")
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    // Why Snapshots Matter
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "lightbulb.fill")
                                .font(.system(size: 18))
                                .foregroundColor(AppColors.neutral)
                            Text("Why Snapshots Matter")
                                .font(AppTypography.headline)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        Text("Instead of analyzing hundreds of metrics separately, Snapshots synthesize complex data into easy-to-understand ratings. This helps you:")
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)

                        VStack(alignment: .leading, spacing: AppSpacing.md) {
                            SnapshotBulletPoint(
                                icon: "clock.fill",
                                title: "Save Time",
                                description: "Make faster investment decisions by quickly identifying key strengths and risks."
                            )

                            SnapshotBulletPoint(
                                icon: "chart.bar.fill",
                                title: "Compare Stocks",
                                description: "Easily compare companies across standardized categories and ratings."
                            )

                            SnapshotBulletPoint(
                                icon: "target",
                                title: "Focus on What Matters",
                                description: "Identify which areas require deeper research based on ratings."
                            )

                            SnapshotBulletPoint(
                                icon: "checkmark.shield.fill",
                                title: "Reduce Risk",
                                description: "Spot potential red flags before making investment decisions."
                            )
                        }
                    }

                    // Understanding Ratings
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "star.fill")
                                .font(.system(size: 18))
                                .foregroundColor(AppColors.neutral)
                            Text("Understanding Ratings")
                                .font(AppTypography.headline)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        VStack(alignment: .leading, spacing: AppSpacing.sm) {
                            RatingExplanation(
                                rating: "High",
                                color: AppColors.bullish,
                                stars: 5,
                                description: "Outstanding performance, significantly above industry standards."
                            )

                            RatingExplanation(
                                rating: "Solid",
                                color: AppColors.bullish,
                                stars: 4,
                                description: "Strong performance, meets or exceeds most expectations."
                            )

                            RatingExplanation(
                                rating: "Moderate",
                                color: AppColors.neutral,
                                stars: 3,
                                description: "Average performance, some strengths and weaknesses."
                            )

                            RatingExplanation(
                                rating: "Soft",
                                color: AppColors.alertOrange,
                                stars: 2,
                                description: "Below-average performance, may require attention."
                            )

                            RatingExplanation(
                                rating: "Low",
                                color: AppColors.bearish,
                                stars: 1,
                                description: "Serious concerns, significant underperformance or risk."
                            )
                        }
                    }

                    // Pro Tips
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "sparkles")
                                .font(.system(size: 18))
                                .foregroundColor(AppColors.neutral)
                            Text("Pro Tips")
                                .font(AppTypography.headline)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        VStack(alignment: .leading, spacing: AppSpacing.md) {
                            ProTipCard(
                                icon: "arrow.triangle.2.circlepath",
                                tip: "Don't rely on a single snapshot. A stock with excellent growth but poor financial health may still be risky."
                            )

                            ProTipCard(
                                icon: "calendar",
                                tip: "Snapshots reflect recent data. Check the financial tab for trends over time to see if ratings are improving or declining."
                            )

                            ProTipCard(
                                icon: "building.2",
                                tip: "Compare snapshots with competitors in the same sector to understand relative performance."
                            )

                            ProTipCard(
                                icon: "book.fill",
                                tip: "Use snapshots as a starting point, not the final word. Always combine them with your own research and investment strategy."
                            )
                        }
                    }
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("About Snapshots")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                    .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
        .preferredColorScheme(.dark)
    }
}

// MARK: - Snapshot Bullet Point
struct SnapshotBulletPoint: View {
    let icon: String
    let title: String
    let description: String

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            Image(systemName: icon)
                .font(.system(size: 16))
                .foregroundColor(AppColors.primaryBlue)
                .frame(width: 24)

            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(title)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textPrimary)

                Text(description)
                    .font(AppTypography.footnote)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

// MARK: - Snapshot Usage Step
struct SnapshotUsageStep: View {
    let number: Int
    let title: String
    let description: String

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            // Step number circle
            ZStack {
                Circle()
                    .fill(AppColors.primaryBlue)
                    .frame(width: 28, height: 28)

                Text("\(number)")
                    .font(AppTypography.calloutBold)
                    .foregroundColor(.white)
            }

            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(title)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textPrimary)

                Text(description)
                    .font(AppTypography.footnote)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

// MARK: - Rating Explanation
struct RatingExplanation: View {
    let rating: String
    let color: Color
    let stars: Int
    let description: String

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            // Stars
            HStack(spacing: 2) {
                ForEach(0..<stars, id: \.self) { _ in
                    Image(systemName: "star.fill")
                        .font(.system(size: 12))
                        .foregroundColor(color)
                }
                ForEach(stars..<5, id: \.self) { _ in
                    Image(systemName: "star.fill")
                        .font(.system(size: 12))
                        .foregroundColor(AppColors.textMuted.opacity(0.3))
                }
            }

            // Rating title
            Text(rating)
                .font(AppTypography.calloutBold)
                .foregroundColor(color)

            // Description
            Text(description)
                .font(AppTypography.footnote)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.medium)
    }
}

// MARK: - Pro Tip Card
struct ProTipCard: View {
    let icon: String
    let tip: String

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            Image(systemName: icon)
                .font(.system(size: 16))
                .foregroundColor(AppColors.primaryBlue)
                .frame(width: 24)

            Text(tip)
                .font(AppTypography.footnote)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.medium)
    }
}

#Preview {
    ScrollView {
        TickerDetailSnapshotsSection(snapshots: SnapshotItem.sampleData)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
#Preview("Info Sheet") {
    SnapshotsInfoSheet()
}

