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
                        .foregroundColor(selectedSort == option ? AppColors.textPrimary : AppColors.textSecondary)
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.vertical, AppSpacing.sm)
                        .background(
                            Group {
                                if selectedSort == option {
                                    RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                                        .fill(AppColors.cardBackground)
                                }
                            }
                        )
                }
                .buttonStyle(.plain)
            }
        }
        .padding(AppSpacing.xxs)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.background)
        )
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
