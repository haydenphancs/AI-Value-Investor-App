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
        Color(hex: source.brandColor)
    }

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: cornerRadius)
                .fill(brandColor)

            // Use custom icon if available, otherwise system icon
            if let iconName = source.iconName {
                Image(iconName)
                    .resizable()
                    .scaledToFit()
                    .frame(width: size * 0.5, height: size * 0.5)
                    .foregroundColor(.white)
            } else {
                Image(systemName: "newspaper.fill")
                    .font(.system(size: size * 0.45, weight: .semibold))
                    .foregroundColor(.white)
            }
        }
        .frame(width: size, height: size)
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        HStack(spacing: AppSpacing.md) {
            NewsSourceBrandIcon(source: NewsSource(name: "CNBC", iconName: nil))
            Text("CNBC")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)
        }

        HStack(spacing: AppSpacing.md) {
            NewsSourceBrandIcon(source: NewsSource(name: "Reuters", iconName: nil))
            Text("Reuters")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)
        }

        HStack(spacing: AppSpacing.md) {
            NewsSourceBrandIcon(source: NewsSource(name: "Bloomberg", iconName: nil))
            Text("Bloomberg")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)
        }
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
