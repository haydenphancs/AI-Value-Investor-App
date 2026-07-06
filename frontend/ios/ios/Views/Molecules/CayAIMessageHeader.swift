//
//  CayAIMessageHeader.swift
//  ios
//
//  Molecule: the "✦ Cay AI" attribution row shown above every assistant message.
//  Composes the CayAIAvatar atom + a name label.
//

import SwiftUI

struct CayAIMessageHeader: View {
    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            CayAIAvatar(size: 20)
            Text("Cay AI")
                .font(AppTypography.labelSmallEmphasis)
                .foregroundColor(AppColors.textSecondary)
        }
    }
}

#Preview {
    CayAIMessageHeader()
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
