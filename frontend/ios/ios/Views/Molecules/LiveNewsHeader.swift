//
//  LiveNewsHeader.swift
//  ios
//
//  Molecule: Static header for Live News section (non-scrolling)
//

import SwiftUI

struct LiveNewsHeader: View {
    /// Reflects the active filters ("All", "2 filters"). Defaulted so existing
    /// call sites and previews keep compiling.
    var filterLabel: String = "All"
    var hasActiveFilters: Bool = false
    var onFilterTapped: (() -> Void)?

    var body: some View {
        HStack(alignment: .center) {
            HStack(spacing: AppSpacing.sm) {
                LiveIndicator()
                    .alignmentGuide(VerticalAlignment.center) { d in d[VerticalAlignment.center] }

                Text("Live News")
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)
            }

            Spacer()

            Button(action: { onFilterTapped?() }) {
                HStack(spacing: AppSpacing.xs) {
                    Image(systemName: "line.3.horizontal.decrease")
                        .font(AppTypography.iconXS).fontWeight(.medium)

                    Text(filterLabel)
                        .font(AppTypography.bodySmall)
                }
                .foregroundColor(hasActiveFilters ? AppColors.primaryBlue : AppColors.textSecondary)
            }
            .buttonStyle(PlainButtonStyle())
        }
        .frame(height: 44) // Fixed height to prevent layout shifts
        .padding(.horizontal, AppSpacing.lg)
        .background(AppColors.background)
    }
}

#Preview {
    VStack {
        LiveNewsHeader()
        Spacer()
    }
    .background(AppColors.background)
}
