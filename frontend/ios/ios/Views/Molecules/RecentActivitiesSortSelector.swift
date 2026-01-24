//
//  RecentActivitiesSortSelector.swift
//  ios
//
//  Molecule: Sort option selector for Recent Activities
//  Allows switching between By Value and By Date sorting
//

import SwiftUI

struct RecentActivitiesSortSelector: View {
    @Binding var selectedSort: RecentActivitiesSortOption

    var body: some View {
        HStack(spacing: 0) {
            ForEach(RecentActivitiesSortOption.allCases, id: \.rawValue) { option in
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        selectedSort = option
                    }
                } label: {
                    Text(option.rawValue)
                        .font(AppTypography.callout)
                        .foregroundColor(selectedSort == option ? AppColors.textPrimary : AppColors.textMuted)
                        .padding(.horizontal, AppSpacing.md)
                        .padding(.vertical, AppSpacing.xs)
                        .background(
                            selectedSort == option
                                ? AppColors.cardBackgroundLight
                                : Color.clear
                        )
                        .cornerRadius(AppCornerRadius.small)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(AppSpacing.xxs)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.medium)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xl) {
            RecentActivitiesSortSelector(
                selectedSort: .constant(.byValue)
            )

            RecentActivitiesSortSelector(
                selectedSort: .constant(.byDate)
            )
        }
        .padding()
    }
}
