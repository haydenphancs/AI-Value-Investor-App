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
                .font(AppTypography.iconTiny).fontWeight(.medium)
                .foregroundColor(AppColors.textMuted)

            Text(source.displayName)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                // A publisher name is a single-line label. Left unbounded, a
                // long one ("Business Insider India") wraps and drags the row
                // taller than the thumbnail beside it.
                .lineLimit(1)
                .truncationMode(.tail)
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
