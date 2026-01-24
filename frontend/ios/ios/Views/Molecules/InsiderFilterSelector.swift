//
//  InsiderFilterSelector.swift
//  ios
//
//  Molecule: Filter selector for insider activities (All / Informative)
//  Allows filtering to show only informative transactions
//

import SwiftUI

struct InsiderFilterSelector: View {
    @Binding var selectedFilter: InsiderActivityFilterOption

    var body: some View {
        HStack(spacing: 0) {
            ForEach(InsiderActivityFilterOption.allCases, id: \.self) { filter in
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        selectedFilter = filter
                    }
                } label: {
                    Text(filter.rawValue)
                        .font(AppTypography.callout)
                        .foregroundColor(selectedFilter == filter ? AppColors.textPrimary : AppColors.textSecondary)
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.vertical, AppSpacing.sm)
                        .background(
                            Group {
                                if selectedFilter == filter {
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
        AppColors.cardBackground
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xl) {
            InsiderFilterSelector(selectedFilter: .constant(.all))

            InsiderFilterSelector(selectedFilter: .constant(.informative))
        }
        .padding()
    }
}
