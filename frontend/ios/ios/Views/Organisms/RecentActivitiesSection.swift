//
//  RecentActivitiesSection.swift
//  ios
//
//  Organism: Complete Recent Activities section card
//  Displays recent institutional or insider trading activities with flow bar and activity list
//

import SwiftUI

struct RecentActivitiesSection: View {
    // MARK: - Properties

    let data: RecentActivitiesData

    // MARK: - State

    @State private var selectedTab: RecentActivitiesTab = .institutions
    @State private var selectedSort: RecentActivitiesSortOption = .byValue
    @State private var selectedFilter: InsiderActivityFilterOption = .all
    @State private var showInfoSheet: Bool = false

    // MARK: - Computed Properties

    private var sortedInstitutionalActivities: [InstitutionalActivity] {
        data.sortedInstitutionalActivities(by: selectedSort)
    }

    private var sortedInsiderActivities: [InsiderActivity] {
        // Always sort by date (most recent first) for both All and Informative tabs
        return data.insiderActivities.sortedActivities(by: .byDate, filter: selectedFilter)
    }

    // MARK: - Body

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header with title and info icon
            headerSection

            // Tab selector (Institutions / Insiders)
            RecentActivitiesTabSelector(
                selectedTab: $selectedTab,
                disabledTabs: []  // Both tabs enabled
            )

            // Content based on selected tab
            if selectedTab == .institutions {
                institutionsContent
            } else {
                insidersContent
            }
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

    // MARK: - Institutions Content

    private var institutionsContent: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Period label
            Text("Latest Filings (\(data.institutionalFlowSummary.periodDescription))")
                .font(AppTypography.footnote)
                .foregroundColor(AppColors.textMuted)
                .padding(.top, AppSpacing.xs)

            // Flow bar
            RecentActivitiesFlowBar(
                inFlowPercent: data.institutionalFlowSummary.inFlowPercent,
                formattedInFlow: data.institutionalFlowSummary.formattedInFlow,
                formattedOutFlow: data.institutionalFlowSummary.formattedOutFlow
            )

            // Legend
            RecentActivitiesFlowLegend()

            // Net flow badge
            RecentActivitiesNetFlowBadge(summary: data.institutionalFlowSummary)

            // Sort selector
            RecentActivitiesSortSelector(selectedSort: $selectedSort)

            // Activity list
            LazyVStack(spacing: AppSpacing.sm) {
                ForEach(sortedInstitutionalActivities) { activity in
                    InstitutionalActivityRow(activity: activity)
                }
            }
            .animation(.easeInOut(duration: 0.2), value: selectedSort)
        }
    }

    // MARK: - Insiders Content

    private var insidersContent: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Period label
            Text(data.insiderActivities.summary.periodDescription)
                .font(AppTypography.footnote)
                .foregroundColor(AppColors.textMuted)
                .padding(.top, AppSpacing.xs)

            // Informative Buys vs Sells summary card
            InsiderFlowSummaryCard(summary: data.insiderActivities.summary)

            // Net informative flow
            InsiderNetFlowBadge(summary: data.insiderActivities.summary)

            // Filter selector (All / Informative)
            InsiderFilterSelector(selectedFilter: $selectedFilter)

            // Activity list
            LazyVStack(spacing: AppSpacing.sm) {
                ForEach(sortedInsiderActivities) { activity in
                    InsiderActivityRow(activity: activity)
                }
            }
            .animation(.easeInOut(duration: 0.2), value: selectedFilter)
            .animation(.easeInOut(duration: 0.2), value: selectedSort)
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
