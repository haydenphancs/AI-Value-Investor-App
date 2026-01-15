//
//  FinancialHealthCheckSection.swift
//  ios
//
//  Organism: Health Check section with horizontally scrollable metric cards
//

import SwiftUI

struct FinancialHealthCheckSection: View {
    let data: HealthCheckData
    var onDetailTap: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section Header with score badge
            FinancialSectionHeader(
                title: "Health Check",
                subtitle: data.formattedScore,
                infoTitle: "Health Check",
                infoDescription: FinancialInfoContent.healthCheck,
                showDetailLink: true,
                onDetailTap: onDetailTap
            )
            .padding(.horizontal, AppSpacing.lg)

            // Horizontally scrollable cards
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: AppSpacing.md) {
                    ForEach(data.metrics) { metric in
                        HealthCheckCard(metric: metric)
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
            }
        }
    }
}

// MARK: - Preview
#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            FinancialHealthCheckSection(
                data: HealthCheckData.sampleData
            )
            .padding(.vertical)
        }
    }
}
