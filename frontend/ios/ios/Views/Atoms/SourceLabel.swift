//
//  SourceLabel.swift
//  ios
//
//  Atom: Displays news source with icon
//

import SwiftUI

struct SourceLabel: View {
    let source: NewsSource

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            Image(systemName: source.systemIconName)
                .font(.system(size: 10, weight: .medium))
                .foregroundColor(AppColors.textMuted)

            Text(source.name)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
    }
}

#Preview {
    VStack(spacing: 10) {
        SourceLabel(source: NewsSource(name: "Reuters", iconName: nil))
        SourceLabel(source: NewsSource(name: "CNBC", iconName: nil))
        SourceLabel(source: NewsSource(name: "Bloomberg", iconName: nil))
    }
    .padding()
    .background(AppColors.background)
}
