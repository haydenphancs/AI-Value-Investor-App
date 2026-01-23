//
//  SmartMoneyTabSelector.swift
//  ios
//
//  Molecule: Tab pills selector for Smart Money section
//  Allows switching between Insider, Hedge Funds, and Congress data
//

import SwiftUI

struct SmartMoneyTabSelector: View {
    @Binding var selectedTab: SmartMoneyTab

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            ForEach(SmartMoneyTab.allCases, id: \.rawValue) { tab in
                tabPill(for: tab)
            }

            Spacer()
        }
    }

    private func tabPill(for tab: SmartMoneyTab) -> some View {
        Button {
            withAnimation(.easeInOut(duration: 0.2)) {
                selectedTab = tab
            }
        } label: {
            Text(tab.rawValue)
                .font(AppTypography.calloutBold)
                .foregroundColor(selectedTab == tab ? AppColors.textPrimary : AppColors.textMuted)
                .padding(.horizontal, AppSpacing.md)
                .padding(.vertical, AppSpacing.sm)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.pill)
                        .fill(selectedTab == tab ? AppColors.cardBackgroundLight : Color.clear)
                        .overlay(
                            RoundedRectangle(cornerRadius: AppCornerRadius.pill)
                                .stroke(
                                    selectedTab == tab ? Color.clear : AppColors.cardBackgroundLight,
                                    lineWidth: 1
                                )
                        )
                )
        }
        .buttonStyle(.plain)
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
