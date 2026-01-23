//
//  ShareholderBreakdownSection.swift
//  ios
//
//  Organism: Complete Shareholder Breakdown section card
//  Displays ownership distribution with bar chart and legend
//

import SwiftUI

struct ShareholderBreakdownSection: View {
    // MARK: - Properties

    let breakdownData: ShareholderBreakdown
    var onTopHoldersTapped: (() -> Void)?

    // MARK: - State

    @State private var showInfoSheet: Bool = false

    // MARK: - Body

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header with title, info icon, and Top 10 link
            headerSection

            // Horizontal stacked bar chart
            ShareholderBreakdownBar(
                insidersPercent: breakdownData.insidersPercent,
                institutionsPercent: breakdownData.institutionsPercent,
                publicOtherPercent: breakdownData.publicOtherPercent
            )
            .padding(.vertical, AppSpacing.sm)

            // Legend with percentages
            legendSection
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
        .sheet(isPresented: $showInfoSheet) {
            ShareholderBreakdownInfoSheet()
        }
    }

    // MARK: - Header Section

    private var headerSection: some View {
        HStack {
            HStack(spacing: AppSpacing.sm) {
                Text("Shareholder Breakdown")
                    .font(AppTypography.title3)
                    .foregroundColor(AppColors.textPrimary)

                ShareholderBreakdownInfoIcon {
                    showInfoSheet = true
                }
            }

            Spacer()

            if onTopHoldersTapped != nil {
                Button(action: { onTopHoldersTapped?() }) {
                    Text("Top 10")
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.primaryBlue)
                }
                .buttonStyle(.plain)
            }
        }
    }

    // MARK: - Legend Section

    private var legendSection: some View {
        VStack(spacing: AppSpacing.md) {
            HolderBreakdownLegendItem(
                color: HoldersColors.insiders,
                label: "Insiders",
                percentage: breakdownData.formattedInsiders
            )

            HolderBreakdownLegendItem(
                color: HoldersColors.institutions,
                label: "Institutions",
                percentage: breakdownData.formattedInstitutions
            )

            HolderBreakdownLegendItem(
                color: HoldersColors.publicOther,
                label: "Public/Other",
                percentage: breakdownData.formattedPublicOther
            )
        }
    }
}

// MARK: - Preview

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            ShareholderBreakdownSection(
                breakdownData: ShareholderBreakdown.sampleData,
                onTopHoldersTapped: {
                    print("Top 10 tapped")
                }
            )
            .padding()
        }
    }
}
