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
            stockTicker: "ORCL",
            stockName: "Oracle Corporation",
            companyLogoName: "icon_oracle",
            persona: .warrenBuffett,
            headline: "Oracle: Strong Quality",
            summary: "Enterprise software giant with deep moat in cloud infrastructure and database services. Consistent earnings growth and long-term competitive advantages.",
            rating: 82,
            fairValue: 190,
            createdAt: Calendar.current.date(from: DateComponents(year: 2025, month: 2, day: 7)) ?? Date(),
            gradientColors: ["C74634", "F80000"]
        ),
        ResearchReport(
            stockTicker: "AAPL",
            stockName: "Apple Inc.",
            companyLogoName: "icon_apple",
            persona: .warrenBuffett,
            headline: "Apple: Excellent Quality",
            summary: "Unmatched ecosystem and brand loyalty create a powerful moat. Services revenue continues to grow, driving recurring income and higher margins.",
            rating: 90,
            fairValue: 213,
            createdAt: Calendar.current.date(from: DateComponents(year: 2024, month: 12, day: 24)) ?? Date(),
            gradientColors: ["A2AAAD", "555555"]
        ),
        ResearchReport(
            stockTicker: "NVDA",
            stockName: "NVIDIA Corp.",
            companyLogoName: "icon_nvidia",
            persona: .peterLynch,
            headline: "NVIDIA: Excellent Quality",
            summary: "Dominant position in AI accelerators with data center revenue surging. GPU demand from AI training and inference workloads continues to outpace supply.",
            rating: 95,
            fairValue: 220,
            createdAt: Calendar.current.date(from: DateComponents(year: 2024, month: 12, day: 23)) ?? Date(),
            gradientColors: ["76B900", "1A1A1A"]
        )
    ])
    .background(AppColors.background)
}
