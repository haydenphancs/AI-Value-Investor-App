//
//  RecentActivitiesSection.swift
//  ios
//
//  Organism: Complete Recent Activities section card
//  Displays recent institutional trading activities with flow bar and activity list
//

import SwiftUI

struct RecentActivitiesSection: View {
    // MARK: - Properties

    let data: RecentActivitiesData

    // MARK: - State

    @State private var selectedTab: RecentActivitiesTab = .institutions
    @State private var selectedSort: RecentActivitiesSortOption = .byValue
    @State private var showInfoSheet: Bool = false

    // MARK: - Computed Properties

    private var sortedActivities: [InstitutionalActivity] {
        data.sortedActivities(by: selectedSort)
    }

    // MARK: - Body

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header with title and info icon
            headerSection

            // Tab selector (Institutions / Insiders)
            RecentActivitiesTabSelector(
                selectedTab: $selectedTab,
                disabledTabs: [.insiders]  // Insiders tab disabled for now
            )

            // Period label
            Text("Latest Filings (\(data.flowSummary.periodDescription))")
                .font(AppTypography.footnote)
                .foregroundColor(AppColors.textMuted)
                .padding(.top, AppSpacing.xs)

            // Flow bar
            RecentActivitiesFlowBar(
                inFlowPercent: data.flowSummary.inFlowPercent,
                formattedInFlow: data.flowSummary.formattedInFlow,
                formattedOutFlow: data.flowSummary.formattedOutFlow
            )

            // Legend
            RecentActivitiesFlowLegend()

            // Net flow badge
            RecentActivitiesNetFlowBadge(summary: data.flowSummary)

            // Sort selector
            HStack {
                Text("Sort:")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)

                RecentActivitiesSortSelector(selectedSort: $selectedSort)
            }
            .padding(.top, AppSpacing.sm)

            // Activity list
            LazyVStack(spacing: AppSpacing.sm) {
                ForEach(sortedActivities) { activity in
                    InstitutionalActivityRow(activity: activity)
                }
            }
            .animation(.easeInOut(duration: 0.2), value: selectedSort)
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
        .sheet(isPresented: $showInfoSheet) {
            RecentActivitiesInfoSheet()
        }
    }

    // MARK: - Header Section

    private var headerSection: some View {
        HStack {
            HStack(spacing: AppSpacing.sm) {
                Text("Recent Activities")
                    .font(AppTypography.title3)
                    .foregroundColor(AppColors.textPrimary)

                RecentActivitiesInfoIcon {
                    showInfoSheet = true
                }
            }

            Spacer()
        }
    }
}

// MARK: - Preview

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            RecentActivitiesSection(
                data: RecentActivitiesData.sampleData
            )
            .padding()
        }
    }
}
