//
//  RecentResearchSection.swift
//  ios
//
//  Organism: Recent research carousel section
//

import SwiftUI

struct RecentResearchSection: View {
    let reports: [ResearchReport]
    var onSeeAllTapped: (() -> Void)?
    var onReportTapped: ((ResearchReport) -> Void)?
    var onAskOrReadTapped: ((ResearchReport) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Section Header
            SectionHeader(title: "Recent Research", showSeeAll: true) {
                onSeeAllTapped?()
            }
            .padding(.horizontal, AppSpacing.lg)

            // Research Cards Carousel
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: AppSpacing.lg) {
                    ForEach(reports) { report in
                        ResearchCard(report: report) {
                            onReportTapped?(report)
                        } onAskOrRead: {
                            onAskOrReadTapped?(report)
                        }
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
            }
        }
    }
}

#Preview {
    RecentResearchSection(reports: [
        ResearchReport(
            stockTicker: "MSFT",
            stockName: "Microsoft",
            companyLogoName: "icon_microsoft",
            persona: .warrenBuffett,
            headline: "Microsoft: The AI Moat Deepens",
            summary: "Azure's AI services and UX Pilot AI partnership position MSFT as a dominant force in enterprise AI. Q4 cloud growth of 28% YoY signals strong market demand.",
            rating: 4.6,
            targetPrice: 425,
            createdAt: Date().addingTimeInterval(-10800),
            gradientColors: ["0078D4", "00BCF2"]
        ),
        ResearchReport(
            stockTicker: "GOOGL",
            stockName: "Google",
            companyLogoName: "icon_google",
            persona: .peterLynch,
            headline: "Google: Gemini's Market Impact",
            summary: "Gemini AI integration across products shows promise. Search market share stable while cloud business accelerates with 26% growth.",
            rating: 4.2,
            targetPrice: 155,
            createdAt: Date().addingTimeInterval(-345600),
            gradientColors: ["4285F4", "34A853"]
        ),
        ResearchReport(
            stockTicker: "AMD",
            stockName: "AMD",
            companyLogoName: "icon_amd",
            persona: .cathieWood,
            headline: "AMD: AI Chip Wars Heat Up",
            summary: "MI300 series gaining traction in data centers. While trailing NVIDIA, AMD's competitive pricing and supply availability create opportunities.",
            rating: 3.3,
            targetPrice: 23,
            createdAt: Date().addingTimeInterval(-432000),
            gradientColors: ["ED1C24", "FF6B6B"]
        )
    ])
    .background(AppColors.background)
}
