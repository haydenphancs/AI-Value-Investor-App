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
    /// Congress is Pro/Max: when true the server withheld the Congress rows and the tab
    /// shows the locked stub. Defaulted so `sampleData` previews and older call sites
    /// keep compiling; the live host passes `holdersData.isCongressLocked`.
    var isCongressLocked: Bool = false

    // MARK: - Constants

    private static let institutionsSortKey = "caydex_holders_sort"
    private static let insiderFilterKey = "caydex_holders_insider_filter"
    private static let congressSortKey = "caydex_holders_congress_sort"

    private let initialDisplayCount = 10
    private let expandedListHeight: CGFloat = 500

    // MARK: - State

    /// Which sub-tab to open on. `@State` with an `init`-provided seed rather than a
    /// `Binding`: the user's taps stay local (nobody outside cares), but a NOTIFICATION
    /// DEEP LINK can preselect the list it is about — "Insider activity in ACHR" opens
    /// on Insiders, "Josh Gottheimer bought GOOGL" on Congress.
    @State private var selectedTab: RecentActivitiesTab
    /// Institutions sort, Insiders filter and Congress sort — remembered on this device
    /// (TestFlight 1.0 (9): set once, keep). They were `@State` with hard defaults, so every
    /// ticker opened on By Value / All again. Stored as ids, never the pill LABELS ("By Value
    /// ($)" is a rawValue): see the `storageID` mappings at the bottom of this file. The
    /// sub-tab above stays `@State` on purpose — a notification deep link seeds it.
    ///
    /// Device-only, and deliberately NOT cleared by `AppState.discardDataForEndedSession()`:
    /// display choices of this phone, not account data.
    @AppStorage(RecentActivitiesSection.institutionsSortKey) private var institutionsSortID: String = RecentActivitiesSortOption.byValue.storageID
    @AppStorage(RecentActivitiesSection.insiderFilterKey) private var insiderFilterID: String = InsiderActivityFilterOption.all.storageID
    @AppStorage(RecentActivitiesSection.congressSortKey) private var congressSortID: String = RecentActivitiesSortOption.byValue.storageID
    @State private var showInfoSheet: Bool = false
    @State private var showPaywall: Bool = false
    @State private var institutionsExpanded: Bool = false
    @State private var insidersExpanded: Bool = false
    @State private var congressExpanded: Bool = false
    @Environment(\.appState) private var appState

    /// `initialTab` defaults to Insiders — the previous hardcoded value — so the only
    /// behaviour that changes is a deep link that names a section. A congress-trade push
    /// preselects `.congress`; on Free that lands on the locked stub, not an empty list.
    init(data: RecentActivitiesData, initialTab: RecentActivitiesTab? = nil, isCongressLocked: Bool = false) {
        self.data = data
        self.isCongressLocked = isCongressLocked
        self._selectedTab = State(initialValue: initialTab ?? .insiders)
    }


    // MARK: - Computed Properties

    /// The stored choices, resolved for display only — an unknown id reads as the default
    /// and is never written back. Only a selector tap (the bindings below) writes.
    private var selectedSort: RecentActivitiesSortOption { .stored(institutionsSortID) }
    private var selectedFilter: InsiderActivityFilterOption { .stored(insiderFilterID) }
    private var congressSort: RecentActivitiesSortOption { .stored(congressSortID) }

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
                disabledTabs: [],
                lockedTabs: isCongressLocked ? [.congress] : []
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
                    if isCongressLocked {
                        LockedSectionCard(title: "Congress", message: SmartMoneySection.congressLockedMessage, nested: true) {
                            showPaywall = true
                        }
                    } else {
                        congressContent
                    }
                }
            }
            .id(selectedTab)
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .cardFill()
        )
        .sheet(isPresented: $showInfoSheet) {
            RecentActivitiesInfoSheet()
        }
        // Same plan gate as the Smart Money card above; `.environment(\.appState, appState)`
        // is REQUIRED for the sheet to highlight the caller's real plan.
        .sheet(isPresented: $showPaywall) {
            PaywallView(context: .congressHolders)
                .environment(\.appState, appState)
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
            Text("Latest 13F filings · \(data.institutionalFlowSummary.quarterYearDescription)")
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
            RecentActivitiesSortSelector(selectedSort: RecentActivitiesSortOption.binding($institutionsSortID))

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
            if data.insiderActivities.isUnavailable {
                // The insider fetch FAILED: no 0/0 card, no "+ 0" badge, no "no transactions".
                Text("Insider trades couldn't be loaded. Try again in a few minutes.")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, AppSpacing.sm)
            } else {
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
                InsiderFilterSelector(selectedFilter: InsiderActivityFilterOption.binding($insiderFilterID))

                // Activity list
                if displayedActivities.isEmpty {
                    // Say so instead of a blank list under the filter pills. Filter-aware:
                    // under "Informative" award/option/tax rows may still exist.
                    Text(selectedFilter == .informative
                         ? "No open-market insider buys or sells in the last 12 months."
                         : "No insider transactions in the last 12 months.")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, AppSpacing.xs)
                } else if insidersExpanded {
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
            RecentActivitiesSortSelector(selectedSort: RecentActivitiesSortOption.binding($congressSortID))

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
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Remembered choices

/// Storage ids for the Recent Activities pills — NOT `rawValue`, which is the pill LABEL
/// ("By Value ($)", "Informative"); relabelling a pill must not reset anyone's choice. A
/// storage contract: never change an id. File-private: only this section stores them.
private extension RecentActivitiesSortOption {
    var storageID: String {
        switch self {
        case .byValue: return "by_value"
        case .byDate:  return "by_date"
        }
    }

    /// By Value (the old default) for a missing or unknown id. Display-only.
    static func stored(_ id: String) -> RecentActivitiesSortOption {
        allCases.first { $0.storageID == id } ?? .byValue
    }

    /// `@AppStorage`'s string as the selector's binding; only a tap writes.
    static func binding(_ id: Binding<String>) -> Binding<RecentActivitiesSortOption> {
        Binding(get: { Self.stored(id.wrappedValue) }, set: { id.wrappedValue = $0.storageID })
    }
}

private extension InsiderActivityFilterOption {
    var storageID: String {
        switch self {
        case .all:         return "all"
        case .informative: return "informative"
        }
    }

    /// All (the old default) for a missing or unknown id. Display-only.
    static func stored(_ id: String) -> InsiderActivityFilterOption {
        allCases.first { $0.storageID == id } ?? .all
    }

    /// `@AppStorage`'s string as the selector's binding; only a tap writes.
    static func binding(_ id: Binding<String>) -> Binding<InsiderActivityFilterOption> {
        Binding(get: { Self.stored(id.wrappedValue) }, set: { id.wrappedValue = $0.storageID })
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
