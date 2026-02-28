//
//  LiveNewsHeader.swift
//  ios
//
//  Molecule: Static header for Live News section (non-scrolling)
//

import SwiftUI

struct LiveNewsHeader: View {
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

                    Text("All")
                        .font(AppTypography.bodySmall)
                }
                .foregroundColor(AppColors.textSecondary)
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
