//
//  RecentActivitiesSection.swift
//  ios
//
//  Organism: Complete Recent Activities section card
//  Displays recent institutional, insider, or congressional trading activities
//

import SwiftUI

struct RecentActivitiesSection: View {
    // MARK: - Properties

    let data: RecentActivitiesData

    // MARK: - Constants

    private let initialDisplayCount = 10
    private let expandedListHeight: CGFloat = 500

    // MARK: - State

    @State private var selectedTab: RecentActivitiesTab = .insiders
    @State private var selectedSort: RecentActivitiesSortOption = .byValue
    @State private var selectedFilter: InsiderActivityFilterOption = .all
    @State private var congressSort: RecentActivitiesSortOption = .byValue
    @State private var showInfoSheet: Bool = false
    @State private var institutionsExpanded: Bool = false
    @State private var insidersExpanded: Bool = false
    @State private var congressExpanded: Bool = false

    // MARK: - Computed Properties

    private var sortedInstitutionalActivities: [InstitutionalActivity] {
        data.sortedInstitutionalActivities(by: selectedSort)
    }

    private var sortedInsiderActivities: [InsiderActivity] {
        return data.insiderActivities.sortedActivities(by: .byDate, filter: selectedFilter)
    }

    private var sortedCongressActivities: [CongressActivity] {
        data.congressActivities.sortedActivities(by: congressSort)
    }

    // MARK: - Body

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header with title and info icon
            headerSection

            // Tab selector (Insiders / Institutions / Congress)
            RecentActivitiesTabSelector(
                selectedTab: $selectedTab,
                disabledTabs: []
            )

            // Content based on selected tab
            // .id(selectedTab) prevents SwiftUI from animating row-by-row
            // removal when switching tabs (which freezes with 73+ rows)
            Group {
                switch selectedTab {
                case .insiders:
                    insidersContent
                case .institutions:
                    institutionsContent
                case .congress:
                    congressContent
                }
            }
            .id(selectedTab)
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
                    .font(AppTypography.heading)
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
        let allActivities = sortedInstitutionalActivities
        let displayedActivities = institutionsExpanded
            ? allActivities
            : Array(allActivities.prefix(initialDisplayCount))
        let hasMore = allActivities.count > initialDisplayCount

        return VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Period label
            Text("Latest Filings (\(data.institutionalFlowSummary.periodDescription))")
                .font(AppTypography.labelSmall)
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
            if institutionsExpanded {
                ScrollView {
                    LazyVStack(spacing: AppSpacing.sm) {
                        ForEach(displayedActivities) { activity in
                            InstitutionalActivityRow(activity: activity)
                        }
                    }
                }
                .scrollIndicators(.visible)
                .frame(maxHeight: expandedListHeight)
            } else {
                LazyVStack(spacing: AppSpacing.sm) {
                    ForEach(displayedActivities) { activity in
                        InstitutionalActivityRow(activity: activity)
                    }
                }
            }

            // Show more / Show less button
            if hasMore {
                showMoreButton(
                    isExpanded: institutionsExpanded,
                    totalCount: allActivities.count
                ) {
                    institutionsExpanded.toggle()
                }
            }
        }
    }

    // MARK: - Insiders Content

    private var insidersContent: some View {
        let allActivities = sortedInsiderActivities
        let displayedActivities = insidersExpanded
            ? allActivities
            : Array(allActivities.prefix(initialDisplayCount))
        let hasMore = allActivities.count > initialDisplayCount

        return VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Period label
            Text(data.insiderActivities.summary.periodDescription)
                .font(AppTypography.labelSmall)
                .foregroundColor(AppColors.textMuted)
                .padding(.top, AppSpacing.xs)

            // Informative Buys vs Sells summary card
            InsiderFlowSummaryCard(summary: data.insiderActivities.summary)

            // Net informative flow
            InsiderNetFlowBadge(summary: data.insiderActivities.summary)

            // Filter selector (All / Informative)
            InsiderFilterSelector(selectedFilter: $selectedFilter)

            // Activity list
            if insidersExpanded {
                ScrollView {
                    LazyVStack(spacing: AppSpacing.sm) {
                        ForEach(displayedActivities) { activity in
                            InsiderActivityRow(activity: activity)
                        }
                    }
                }
                .scrollIndicators(.visible)
                .frame(maxHeight: expandedListHeight)
            } else {
                LazyVStack(spacing: AppSpacing.sm) {
                    ForEach(displayedActivities) { activity in
                        InsiderActivityRow(activity: activity)
                    }
                }
            }

            // Show more / Show less button
            if hasMore {
                showMoreButton(
                    isExpanded: insidersExpanded,
                    totalCount: allActivities.count
                ) {
                    insidersExpanded.toggle()
                }
            }
        }
    }

    // MARK: - Congress Content

    private var congressContent: some View {
        let allActivities = sortedCongressActivities
        let displayedActivities = congressExpanded
            ? allActivities
            : Array(allActivities.prefix(initialDisplayCount))
        let hasMore = allActivities.count > initialDisplayCount

        return VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Period label
            Text(data.congressActivities.summary.periodDescription)
                .font(AppTypography.labelSmall)
                .foregroundColor(AppColors.textMuted)
                .padding(.top, AppSpacing.xs)

            // Total Buys vs Sells summary card
            CongressFlowSummaryCard(summary: data.congressActivities.summary)

            // Net flow
            CongressNetFlowBadge(summary: data.congressActivities.summary)

            // Sort selector (By Value / By Date)
            RecentActivitiesSortSelector(selectedSort: $congressSort)

            // Activity list
            if congressExpanded {
                ScrollView {
                    LazyVStack(spacing: AppSpacing.sm) {
                        ForEach(displayedActivities) { activity in
                            CongressActivityRow(activity: activity)
                        }
                    }
                }
                .scrollIndicators(.visible)
                .frame(maxHeight: expandedListHeight)
            } else {
                LazyVStack(spacing: AppSpacing.sm) {
                    ForEach(displayedActivities) { activity in
                        CongressActivityRow(activity: activity)
                    }
                }
            }

            // Show more / Show less button
            if hasMore {
                showMoreButton(
                    isExpanded: congressExpanded,
                    totalCount: allActivities.count
                ) {
                    congressExpanded.toggle()
                }
            }
        }
    }

    // MARK: - Show More Button

    private func showMoreButton(
        isExpanded: Bool,
        totalCount: Int,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: AppSpacing.xs) {
                Text(isExpanded ? "Show Less" : "Show All (\(totalCount))")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.primaryBlue)

                Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.primaryBlue)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, AppSpacing.sm)
        }
        .buttonStyle(.plain)
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
