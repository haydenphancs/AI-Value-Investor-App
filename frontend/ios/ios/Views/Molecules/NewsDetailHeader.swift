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
    /// Share the article. Replaces the old "…" overflow button, which opened a
    /// confirmation dialog whose only surviving item was "Share Article" — a
    /// second tap for a menu of one. ("Save Article" and "Report Issue" were
    /// removed earlier because both were `print()` stubs.)
    var onShareTapped: (() -> Void)?
    /// Hidden when there is no URL to share, rather than shown-but-inert.
    var canShare: Bool = true

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Back Button
            Button(action: {
                onBackTapped?()
            }) {
                Image(systemName: "chevron.left")
                    .font(AppTypography.iconMedium).fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)
                    .frame(width: 32, height: 32)
                    .contentShape(Rectangle())
            }
            .buttonStyle(PlainButtonStyle())

            // Source Icon and Name
            HStack(spacing: AppSpacing.sm) {
                NewsSourceBrandIcon(source: source, size: 28, cornerRadius: 6)

                Text(source.displayName)
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
            }

            Spacer()

            // Share Button
            if canShare {
                Button(action: {
                    onShareTapped?()
                }) {
                    Image(systemName: "square.and.arrow.up")
                        .font(AppTypography.iconMedium).fontWeight(.semibold)
                        .foregroundColor(AppColors.textPrimary)
                        .frame(width: 32, height: 32)
                        .contentShape(Rectangle())
                }
                .buttonStyle(PlainButtonStyle())
                .accessibilityLabel("Share article")
            }
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
            onShareTapped: { print("Share tapped") }
        )

        Divider()
            .background(AppColors.cardBackgroundLight)

        // No shareable URL — the share button is absent, not inert.
        NewsDetailHeader(
            source: NewsSource(name: "Reuters", iconName: nil),
            canShare: false
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
