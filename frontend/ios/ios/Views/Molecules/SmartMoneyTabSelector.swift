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

    // Segmented control — matches RecentActivitiesTabSelector exactly: equal-
    // width tabs in a rounded `cardBackground` container, the selected tab
    // filled with `cardBackgroundLight`.
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
                            selectedTab == tab ? AppColors.cardBackgroundLight : Color.clear
                        )
                        .cornerRadius(AppCornerRadius.medium)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(AppSpacing.xs)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
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
