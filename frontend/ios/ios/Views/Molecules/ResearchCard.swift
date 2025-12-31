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
        Button(action: {
            onTap?()
        }) {
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
                        .font(AppTypography.bodyBold)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)

                    // Summary
                    Text(report.summary)
                        .font(AppTypography.callout)
                        .foregroundColor(AppColors.textSecondary)
                        .lineLimit(3)
                        .multilineTextAlignment(.leading)

                    Spacer()

                    // Footer
                    HStack {
                        RatingBadge(rating: report.rating)

                        Text("Target: \(report.formattedTargetPrice)")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textSecondary)

                        Spacer()

                        Button(action: {
                            onAskOrRead?()
                        }) {
                            Text("Ask or Read")
                                .font(AppTypography.calloutBold)
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
        .buttonStyle(PlainButtonStyle())
    }

    @ViewBuilder
    private var companyLogoPlaceholder: some View {
        // Using system symbols as placeholders for company logos
        switch report.stockTicker {
        case "MSFT":
            Image(systemName: "square.grid.2x2.fill")
                .font(.system(size: 32, weight: .bold))
                .foregroundColor(.white.opacity(0.9))
        case "GOOGL":
            Text("G")
                .font(.system(size: 40, weight: .bold))
                .foregroundColor(.white.opacity(0.9))
        case "AMD":
            Image(systemName: "cpu.fill")
                .font(.system(size: 32, weight: .bold))
                .foregroundColor(.white.opacity(0.9))
        default:
            Text(String(report.stockTicker.prefix(1)))
                .font(.system(size: 40, weight: .bold))
                .foregroundColor(.white.opacity(0.9))
        }
    }
}

#Preview {
    ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: 16) {
            ResearchCard(report: ResearchReport(
                stockTicker: "MSFT",
                stockName: "Microsoft",
                companyLogoName: "icon_microsoft",
                persona: .warrenBuffett,
                headline: "Microsoft: The AI Moat Deepens",
                summary: "Azure's AI services and UX Pilot AI partnership position MSFT as a dominant force in enterprise AI.",
                rating: 4.6,
                targetPrice: 425,
                createdAt: Date().addingTimeInterval(-10800),
                gradientColors: ["0078D4", "00BCF2"]
            ))

            ResearchCard(report: ResearchReport(
                stockTicker: "GOOGL",
                stockName: "Google",
                companyLogoName: "icon_google",
                persona: .peterLynch,
                headline: "Google: Gemini's Market Impact",
                summary: "Gemini AI integration across products shows promise. Search market share stable.",
                rating: 4.2,
                targetPrice: 155,
                createdAt: Date().addingTimeInterval(-345600),
                gradientColors: ["4285F4", "34A853"]
            ))
        }
        .padding()
    }
    .background(AppColors.background)
}
