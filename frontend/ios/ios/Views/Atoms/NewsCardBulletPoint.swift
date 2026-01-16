//
//  NewsCardBulletPoint.swift
//  ios
//
//  Atom: Bullet point item for news card expanded content
//

import SwiftUI

struct NewsCardBulletPoint: View {
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.sm) {
            // Bullet dot
            Circle()
                .fill(AppColors.textMuted)
                .frame(width: 5, height: 5)
                .padding(.top, 6)

            // Bullet text with bold title support
            formattedText
                .font(AppTypography.footnote)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var formattedText: Text {
        // Check if text has a bold prefix (text before colon)
        if let colonIndex = text.firstIndex(of: ":") {
            let boldPart = String(text[..<colonIndex])
            let normalPart = String(text[text.index(after: colonIndex)...])
            return Text("\(Text(boldPart + ":").fontWeight(.semibold).foregroundColor(AppColors.textPrimary))\(Text(normalPart))")
        } else {
            return Text(text)
        }
    }
}

#Preview {
    VStack(alignment: .leading, spacing: AppSpacing.md) {
        NewsCardBulletPoint(text: "High Pre-Orders Abroad: Apple is seeing unusually strong pre-order numbers in Europe and Asia.")
        NewsCardBulletPoint(text: "Supply Chain Scaling: Apple is ramping up production and logistics overseas.")
        NewsCardBulletPoint(text: "An example: This is an explain.")
    }
    .padding()
    .background(AppColors.cardBackground)
    .cornerRadius(AppCornerRadius.large)
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
