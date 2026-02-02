//
//  BulletPointRow.swift
//  ios
//
//  Molecule: Single bullet point row with indicator
//

import SwiftUI

struct BulletPointRow: View {
    let bulletPoint: ChatBulletPoint

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            BulletPointIndicator(type: bulletPoint.indicatorType)
                .frame(width: 20)

            Text(bulletPoint.text)
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

#Preview {
    VStack(alignment: .leading, spacing: AppSpacing.md) {
        BulletPointRow(bulletPoint: ChatBulletPoint(text: "Strong delivery numbers exceeded expectations in Q4", indicatorType: .success))
        BulletPointRow(bulletPoint: ChatBulletPoint(text: "Competition intensifying in EV market", indicatorType: .warning))
        BulletPointRow(bulletPoint: ChatBulletPoint(text: "Analyst price targets range from $180-$350", indicatorType: .info))
    }
    .padding()
    .background(AppColors.cardBackground)
    .cornerRadius(AppCornerRadius.large)
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
