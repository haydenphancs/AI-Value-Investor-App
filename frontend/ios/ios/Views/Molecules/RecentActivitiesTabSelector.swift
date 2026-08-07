//
//  RecentActivitiesTabSelector.swift
//  ios
//
//  Molecule: Tab selector for Recent Activities section
//  Switches between Institutions and Insiders tabs
//

import SwiftUI

struct RecentActivitiesTabSelector: View {
    @Binding var selectedTab: RecentActivitiesTab
    let disabledTabs: Set<RecentActivitiesTab>

    init(selectedTab: Binding<RecentActivitiesTab>, disabledTabs: Set<RecentActivitiesTab> = []) {
        self._selectedTab = selectedTab
        self.disabledTabs = disabledTabs
    }

    var body: some View {
        HStack(spacing: 0) {
            ForEach(RecentActivitiesTab.allCases, id: \.rawValue) { tab in
                let isDisabled = disabledTabs.contains(tab)

                Button {
                    if !isDisabled {
                        selectedTab = tab
                    }
                } label: {
                    Text(tab.rawValue)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(tabForegroundColor(for: tab, isDisabled: isDisabled))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, AppSpacing.sm)
                        // `toggleSelectedBackground`, NOT `cardBackgroundLight`: this
                        // track is `cardBackgroundNested`, and those two tokens have
                        // byte-identical dark arms (#252B3B), so the selected tab was
                        // 1.00:1 — invisible in dark, with `.cornerRadius` supplying no
                        // edge to rescue it. Only the ink distinguished it. The toggle
                        // token separates 1.37 dark / 1.25 light and already carries
                        // `textPrimary` in the manifest.
                        .background(
                            selectedTab == tab && !isDisabled
                                ? AppColors.toggleSelectedBackground
                                : Color.clear
                        )
                        .cornerRadius(AppCornerRadius.medium)
                }
                .buttonStyle(.plain)
                .disabled(isDisabled)
            }
        }
        .padding(AppSpacing.xs)
        .cardSurface(AppColors.cardBackgroundNested, cornerRadius: AppCornerRadius.large)
    }

    private func tabForegroundColor(for tab: RecentActivitiesTab, isDisabled: Bool) -> Color {
        if isDisabled {
            return AppColors.textMuted.opacity(0.5)
        }
        return selectedTab == tab ? AppColors.textPrimary : AppColors.textMuted
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xl) {
            RecentActivitiesTabSelector(
                selectedTab: .constant(.institutions)
            )

            RecentActivitiesTabSelector(
                selectedTab: .constant(.institutions),
                disabledTabs: [.insiders]
            )
        }
        .padding()
    }
}
