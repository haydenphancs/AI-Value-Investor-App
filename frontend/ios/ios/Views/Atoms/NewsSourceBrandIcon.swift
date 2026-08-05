//
//  NewsSourceBrandIcon.swift
//  ios
//
//  Atom: Branded news source icon with background color
//

import SwiftUI

struct NewsSourceBrandIcon: View {
    let source: NewsSource
    var size: CGFloat = 32
    var cornerRadius: CGFloat = 8

    private var brandColor: Color {
        Color(themedHex: source.brandColor, role: .graphic, fallback: AppColors.primaryFill)
    }

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: cornerRadius)
                .fill(brandColor)

            // Always the SF Symbol. There was a `source.iconName` branch that
            // did `Image(iconName)`, but NO such assets exist in the catalog —
            // it rendered an empty image and the fallback below never ran, so
            // any source with an iconName showed a blank chip. Restore the
            // branch only alongside the actual assets.
            Image(systemName: "newspaper.fill")
                .font(.system(size: size * 0.45, weight: .semibold))
                .foregroundColor(AppColors.textOnAccent)
        }
        .frame(width: size, height: size)
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        HStack(spacing: AppSpacing.md) {
            NewsSourceBrandIcon(source: NewsSource(name: "CNBC", iconName: nil))
            Text("CNBC")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
        }

        HStack(spacing: AppSpacing.md) {
            NewsSourceBrandIcon(source: NewsSource(name: "Reuters", iconName: nil))
            Text("Reuters")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
        }

        HStack(spacing: AppSpacing.md) {
            NewsSourceBrandIcon(source: NewsSource(name: "Bloomberg", iconName: nil))
            Text("Bloomberg")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
        }
    }
    .padding()
    .background(AppColors.background)
}
