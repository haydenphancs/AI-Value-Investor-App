//
//  NewsDetailHeader.swift
//  ios
//
//  Molecule: Header bar for news detail screen with back button and source
//

import SwiftUI

struct NewsDetailHeader: View {
    let source: NewsSource
    var onBackTapped: (() -> Void)?
    var onMoreTapped: (() -> Void)?

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Back Button
            Button(action: {
                onBackTapped?()
            }) {
                Image(systemName: "chevron.left")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundColor(AppColors.textPrimary)
                    .frame(width: 32, height: 32)
                    .contentShape(Rectangle())
            }
            .buttonStyle(PlainButtonStyle())

            // Source Icon and Name
            HStack(spacing: AppSpacing.sm) {
                NewsSourceBrandIcon(source: source, size: 28, cornerRadius: 6)

                Text(source.name)
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)
            }

            Spacer()

            // More Options Button
            Button(action: {
                onMoreTapped?()
            }) {
                Image(systemName: "ellipsis")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundColor(AppColors.textPrimary)
                    .frame(width: 32, height: 32)
                    .contentShape(Rectangle())
            }
            .buttonStyle(PlainButtonStyle())
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
    }
}

#Preview {
    VStack {
        NewsDetailHeader(
            source: NewsSource(name: "CNBC", iconName: nil),
            onBackTapped: { print("Back tapped") },
            onMoreTapped: { print("More tapped") }
        )

        Divider()
            .background(AppColors.cardBackgroundLight)

        NewsDetailHeader(
            source: NewsSource(name: "Reuters", iconName: nil)
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
