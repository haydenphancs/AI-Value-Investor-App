//
//  AnalysisMomentumSection.swift
//  ios
//
//  Complete momentum section with header, chart, legend, and actions
//

import SwiftUI

struct AnalysisMomentumSection: View {
    let momentumData: [AnalystMomentumMonth]
    let netPositive: Int
    let netNegative: Int
    let actionsSummary: AnalystActionsSummary
    let actions: [AnalystAction]
    @Binding var selectedPeriod: AnalystMomentumPeriod
    var onActionsTapped: (() -> Void)?

    /// Filter momentum data based on selected period
    private var filteredMomentumData: [AnalystMomentumMonth] {
        switch selectedPeriod {
        case .sixMonths:
            return Array(momentumData.suffix(6))
        case .oneYear:
            return momentumData
        }
    }

    /// Compute actions summary filtered by selected period
    private var filteredActionsSummary: AnalystActionsSummary {
        let cutoff: Date
        switch selectedPeriod {
        case .sixMonths:
            cutoff = Calendar.current.date(byAdding: .month, value: -6, to: Date()) ?? Date()
        case .oneYear:
            cutoff = Calendar.current.date(byAdding: .year, value: -1, to: Date()) ?? Date()
        }

        let filtered = actions.filter { $0.date >= cutoff }
        var upgrades = 0
        var maintains = 0
        var downgrades = 0
        for action in filtered {
            switch action.actionType {
            case .upgrade:
                upgrades += 1
            case .downgrade:
                downgrades += 1
            case .maintain, .initiated, .reiterated:
                maintains += 1
            }
        }
        return AnalystActionsSummary(upgrades: upgrades, maintains: maintains, downgrades: downgrades)
    }

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Header with toggle
            HStack {
                Text("Analyst Momentum")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Button(action: {
                    onActionsTapped?()
                }) {
                    Text("Actions")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.primaryBlue)
                }
            }

            // Period toggle - centered
            HStack {
                Spacer()
                MomentumPeriodToggle(selectedPeriod: $selectedPeriod)
                Spacer()
            }

            // Bar chart — filtered by selected period
            MomentumBarChart(data: filteredMomentumData)

            // Actions row — filtered by selected period
            AnalystActionsRow(actionsSummary: filteredActionsSummary)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        AnalysisMomentumSection(
            momentumData: AnalystMomentumMonth.sampleData,
            netPositive: 17,
            netNegative: 7,
            actionsSummary: AnalystActionsSummary.sampleData,
            actions: AnalystAction.sampleData,
            selectedPeriod: .constant(.sixMonths)
        )
        .padding()
    }
}
