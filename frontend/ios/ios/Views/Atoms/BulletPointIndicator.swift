//
//  BulletPointIndicator.swift
//  ios
//
//  Atom: Colored indicator icon for bullet points
//

import SwiftUI

struct BulletPointIndicator: View {
    let type: ChatBulletPoint.IndicatorType

    var body: some View {
        Image(systemName: type.iconName)
            .font(.system(size: 14, weight: .medium))
            .foregroundColor(type.color)
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        BulletPointIndicator(type: .success)
        BulletPointIndicator(type: .warning)
        BulletPointIndicator(type: .info)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
