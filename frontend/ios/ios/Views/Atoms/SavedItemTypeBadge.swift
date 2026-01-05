//
//  SavedItemTypeBadge.swift
//  ios
//
//  Atom: Type badge for saved items (BOOK, CONCEPT, CHAT, REPORT)
//

import SwiftUI

struct SavedItemTypeBadge: View {
    let type: SavedItemType

    var body: some View {
        Text(type.displayName)
            .font(AppTypography.captionBold)
            .foregroundColor(type.textColor)
    }
}

#Preview {
    VStack(alignment: .leading, spacing: AppSpacing.md) {
        ForEach(SavedItemType.allCases, id: \.rawValue) { type in
            SavedItemTypeBadge(type: type)
        }
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
