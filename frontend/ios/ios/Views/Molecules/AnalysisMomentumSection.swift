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

// MARK: - Remembered period

/// The 6M/1Y choice is a device display preference (TestFlight 1.0 (9): "set up once and
/// permanently keep them"). It used to be a `@Published` copy on each detail ViewModel with
/// a hard 6M default, so every pushed ticker and every relaunch reset it. The screens now
/// hold it in `@AppStorage` under this key and bind it down; every screen in the stack reads
/// the same value, so going back never shows a stale period.
///
/// Device-only, and deliberately NOT cleared by `AppState.discardDataForEndedSession()`: a
/// display choice of this phone, not account data (same standing as
/// `caydex_preferred_chart_type`).
extension AnalystMomentumPeriod {
    static let storageKey = "caydex_analyst_momentum_period"
    static let defaultChoice: AnalystMomentumPeriod = .sixMonths

    /// What is stored — NOT `rawValue`, which is the toggle LABEL ("6M"); relabelling the
    /// pill would silently reset everyone's choice. A storage contract: never change an id.
    var storageID: String {
        switch self {
        case .sixMonths: return "six_months"
        case .oneYear:   return "one_year"
        }
    }

    /// The period a stored id names; the default for a missing or unknown id. Display-only —
    /// never written back, so a garbage value cannot overwrite anything.
    static func stored(_ id: String) -> AnalystMomentumPeriod {
        allCases.first { $0.storageID == id } ?? defaultChoice
    }

    /// `@AppStorage`'s string as the toggle's binding: reads resolve through `stored(_:)`,
    /// and only a tap (the setter) writes.
    static func binding(_ id: Binding<String>) -> Binding<AnalystMomentumPeriod> {
        Binding(get: { Self.stored(id.wrappedValue) }, set: { id.wrappedValue = $0.storageID })
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
