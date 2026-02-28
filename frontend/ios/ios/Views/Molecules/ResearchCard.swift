//
//  ResearchCard.swift
//  ios
//
//  Molecule: Research report card for horizontal carousel
//

import SwiftUI

struct ResearchCard: View {
    let report: ResearchReport
    var onTap: (() -> Void)?
    var onAskOrRead: (() -> Void)?

    private var gradient: LinearGradient {
        LinearGradient(
            colors: report.gradientColors.map { Color(hex: $0) },
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Header with gradient and logo
            ZStack {
                gradient
                    .frame(height: 80)

                // Company Logo Placeholder
                companyLogoPlaceholder
            }

            // Content
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                // Persona badge and time
                HStack {
                    PersonaBadge(persona: report.persona)
                    Spacer()
                    Text(report.timeAgo)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                // Headline
                Text(report.headline)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)
                    .multilineTextAlignment(.leading)

                // Summary
                Text(report.summary)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                    .lineLimit(3)
                    .multilineTextAlignment(.leading)

                Spacer()

                // Footer
                HStack {
                    RatingBadge(rating: report.rating, maxRating: 100)

                    Text("Fair Value: \(report.formattedFairValue)")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)

                    Spacer()

                    Button(action: {
                        onAskOrRead?()
                    }) {
                        Text("Ask or Read")
                            .font(AppTypography.bodySmallEmphasis)
                            .foregroundColor(AppColors.primaryBlue)
                    }
                }
            }
            .padding(AppSpacing.md)
            .background(AppColors.cardBackground)
        }
        .frame(width: 260, height: 280)
        .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.large))
    }

    @ViewBuilder
    private var companyLogoPlaceholder: some View {
        // Using system symbols as placeholders for company logos
        switch report.stockTicker {
        case "ORCL":
            Image(systemName: "server.rack")
                .font(AppTypography.iconDisplay).fontWeight(.bold)
                .foregroundColor(.white.opacity(0.9))
        case "AAPL":
            Image(systemName: "apple.logo")
                .font(AppTypography.iconJumbo).fontWeight(.bold)
                .foregroundColor(.white.opacity(0.9))
        case "NVDA":
            Image(systemName: "gpu")
                .font(AppTypography.iconDisplay).fontWeight(.bold)
                .foregroundColor(.white.opacity(0.9))
        default:
            Text(String(report.stockTicker.prefix(1)))
                .font(AppTypography.iconXXL).fontWeight(.bold)
                .foregroundColor(.white.opacity(0.9))
        }
    }
}

#Preview {
    ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: 16) {
            ResearchCard(report: ResearchReport(
                stockTicker: "ORCL",
                stockName: "Oracle Corporation",
                companyLogoName: "icon_oracle",
                persona: .warrenBuffett,
                headline: "Oracle: Strong Quality",
                summary: "Enterprise software giant with deep moat in cloud infrastructure and database services.",
                rating: 82,
                fairValue: 190,
                createdAt: Calendar.current.date(from: DateComponents(year: 2025, month: 2, day: 7)) ?? Date(),
                gradientColors: ["C74634", "F80000"]
            ))

            ResearchCard(report: ResearchReport(
                stockTicker: "AAPL",
                stockName: "Apple Inc.",
                companyLogoName: "icon_apple",
                persona: .warrenBuffett,
                headline: "Apple: Excellent Quality",
                summary: "Unmatched ecosystem and brand loyalty create a powerful moat. Services revenue continues to grow.",
                rating: 90,
                fairValue: 213,
                createdAt: Calendar.current.date(from: DateComponents(year: 2024, month: 12, day: 24)) ?? Date(),
                gradientColors: ["A2AAAD", "555555"]
            ))
        }
        .padding()
    }
    .background(AppColors.background)
}
