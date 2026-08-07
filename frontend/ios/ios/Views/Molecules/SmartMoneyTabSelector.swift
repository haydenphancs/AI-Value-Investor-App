//
//  SmartMoneyTabSelector.swift
//  ios
//
//  Molecule: Tab pills selector for Smart Money section
//  Allows switching between Insider, Institutions, and Congress data
//  ("Institutions" is SmartMoneyTab.hedgeFunds — code "hedge fund" = UI "Institutions")
//

import SwiftUI

struct SmartMoneyTabSelector: View {
    @Binding var selectedTab: SmartMoneyTab

    // Segmented control — matches RecentActivitiesTabSelector exactly: equal-width
    // tabs in a rounded `cardBackgroundNested` container, the selected tab filled
    // with `toggleSelectedBackground`.
    //
    // It was `cardBackgroundLight`, which has the SAME dark arm as the
    // `cardBackgroundNested` track (#252B3B) — so the selected tab was 1.00:1 and
    // invisible in dark, distinguished only by its ink. "Matches
    // RecentActivitiesTabSelector exactly" was true then and has to stay true: both
    // carried the identical defect and both were fixed together.
    var body: some View {
        HStack(spacing: 0) {
            ForEach(SmartMoneyTab.allCases, id: \.rawValue) { tab in
                Button {
                    selectedTab = tab
                } label: {
                    Text(tab.rawValue)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(selectedTab == tab ? AppColors.textPrimary : AppColors.textMuted)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, AppSpacing.sm)
                        .background(
                            selectedTab == tab ? AppColors.toggleSelectedBackground : Color.clear
                        )
                        .cornerRadius(AppCornerRadius.medium)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(AppSpacing.xs)
        .cardSurface(AppColors.cardBackgroundNested, cornerRadius: AppCornerRadius.large)
    }
}

#Preview {
    struct PreviewWrapper: View {
        @State private var selectedTab = SmartMoneyTab.insider

        var body: some View {
            ZStack {
                AppColors.background
                    .ignoresSafeArea()

                VStack(spacing: AppSpacing.xl) {
                    SmartMoneyTabSelector(selectedTab: $selectedTab)

                    Text("Selected: \(selectedTab.rawValue)")
                        .foregroundColor(AppColors.textPrimary)
                }
                .padding()
            }
        }
    }

    return PreviewWrapper()
}
