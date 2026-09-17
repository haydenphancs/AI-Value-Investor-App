//
//  SmartMoneySection.swift
//  ios
//
//  Organism: Complete Smart Money section card
//  Displays insider / institutions / congress trading activity with flow chart.
//  NOTE: the "institutions" tab is SmartMoneyTab.hedgeFunds (FMP 13F data) — the
//  code says "hedge fund", the UI label is "Institutions".
//

import SwiftUI

struct SmartMoneySection: View {
    // MARK: - Properties

    let holdersData: HoldersData

    // MARK: - State

    @Environment(\.appState) private var appState
    @State private var selectedTab: SmartMoneyTab = .insider
    @State private var showInfoSheet: Bool = false
    @State private var showPaywall: Bool = false

    /// Free-tier copy for the withheld Congress segment.
    static let congressLockedMessage =
        "Congressional trades in this stock are part of a plan. Insider and institutional flow stay free."

    // MARK: - Computed Properties

    private var currentData: SmartMoneyData {
        holdersData.smartMoneyData(for: selectedTab)
    }

    // MARK: - Body

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header with title and info icon
            headerSection

            // Tab selector (Insider / Institutions / Congress) — "Institutions" = SmartMoneyTab.hedgeFunds
            SmartMoneyTabSelector(
                selectedTab: $selectedTab,
                lockedTabs: holdersData.isCongressLocked ? [.congress] : []
            )

            // Congress is Pro/Max: the server withheld the data and raised the flag. This
            // branch MUST come before the empty state below — a redacted series would
            // otherwise print "No congress activity data available", which is a lie.
            if selectedTab == .congress && holdersData.isCongressLocked {
                LockedSectionCard(title: "Congress", message: Self.congressLockedMessage, nested: true) {
                    showPaywall = true
                }
                .padding(.top, AppSpacing.xs)
            } else {
                // Period label
                Text("\(currentData.summary.periodDescription) Flow")
                    .font(AppTypography.labelSmall)
                    .foregroundColor(AppColors.textMuted)
                    .padding(.top, AppSpacing.xs)

                // Flow chart (price on top, buy/sell volume below)
                if currentData.flowData.allSatisfy({ !$0.hasActivity }) {
                    // Empty state when no data for this tab
                    VStack(spacing: AppSpacing.sm) {
                        Image(systemName: "chart.bar.xaxis")
                            .font(.system(size: 28))
                            .foregroundColor(AppColors.textMuted)
                        Text("No \(selectedTab.rawValue.lowercased()) activity data available")
                            .font(AppTypography.bodySmall)
                            .foregroundColor(AppColors.textMuted)
                            .multilineTextAlignment(.center)
                    }
                    .frame(maxWidth: .infinity)
                    .frame(height: 200)
                } else {
                    SmartMoneyFlowChart(
                        priceData: currentData.priceData,
                        dailyPrices: currentData.dailyPrices,
                        flowData: currentData.flowData,
                        // Small fixed gap below the price axis, plus a deterministic
                        // volume axis (uniformVolumeAxis) that parks the top label at
                        // a constant 80% of the domain — so Insider / Institutions /
                        // Congress all show the SAME clean two-axis layout regardless
                        // of magnitude. (Report's insider chart omits both → unchanged.)
                        priceVolumeGap: AppSpacing.sm,
                        uniformVolumeAxis: true,
                        // Congress bars are dollars (STOCK Act ranges); Insider &
                        // Institutions are 13F/Form-4 SHARE counts. Marks the axis "$".
                        isDollarDenominated: selectedTab == .congress
                    )
                    .id(selectedTab.rawValue)
                    .animation(.easeInOut(duration: 0.3), value: selectedTab)

                    // Legend — hedge-fund & insider flow are in shares, congress in $
                    SmartMoneyFlowLegend(
                        buyLabel: selectedTab == .congress ? "Buy Volume" : "Shares Bought",
                        sellLabel: selectedTab == .congress ? "Sell Volume" : "Shares Sold"
                    )
                    .padding(.top, AppSpacing.sm)

                    // Net flow badge
                    SmartMoneyNetFlowBadge(summary: currentData.summary)
                        .padding(.top, AppSpacing.sm)
                }
            }
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .cardFill()
        )
        .sheet(isPresented: $showInfoSheet) {
            SmartMoneyInfoSheet()
        }
        // A PLAN gate, so the plan sheet — not the BuyCredits route a 402 takes.
        // `.environment(\.appState, appState)` is REQUIRED: PaywallView reads the custom
        // `\.appState` key and a sheet inherits neither environment automatically.
        .sheet(isPresented: $showPaywall) {
            PaywallView(context: .congressHolders)
                .environment(\.appState, appState)
        }
    }

    // MARK: - Header Section

    private var headerSection: some View {
        HStack {
            HStack(spacing: AppSpacing.sm) {
                Text("Smart Money")
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)

                SmartMoneyInfoIcon {
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
            SmartMoneySection(
                holdersData: HoldersData.sampleData
            )
            .padding()
        }
    }
}
