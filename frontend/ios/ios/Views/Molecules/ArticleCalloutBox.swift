//
//  ArticleCalloutBox.swift
//  ios
//
//  Molecule: Styled callout box for important information
//

import SwiftUI

struct ArticleCalloutBox: View {
    let icon: String
    let text: String
    let style: CalloutStyle

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            // Icon
            Image(systemName: icon)
                .font(.system(size: 16, weight: .semibold))
                .foregroundColor(style.borderColor)
                .frame(width: 24)

            // Text
            Text(text)
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textPrimary)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(style.backgroundColor)
                .overlay(
                    RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                        .strokeBorder(style.borderColor.opacity(0.5), lineWidth: 1)
                )
        )
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        ArticleCalloutBox(
            icon: "lightbulb.fill",
            text: "DeFi protocols have processed over $180B in total value locked, representing a 340% increase from last year.",
            style: .highlight
        )

        ArticleCalloutBox(
            icon: "exclamationmark.triangle.fill",
            text: "Investors should remain vigilant. While opportunities abound, the regulatory landscape is still evolving.",
            style: .warning
        )

        ArticleCalloutBox(
            icon: "checkmark.circle.fill",
            text: "This approach has proven successful across multiple market cycles.",
            style: .success
        )

        ArticleCalloutBox(
            icon: "info.circle.fill",
            text: "Traditional banks are investing heavily in blockchain technology to remain competitive.",
            style: .info
        )
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
