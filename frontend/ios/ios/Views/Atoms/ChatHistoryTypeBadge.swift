//
//  ChatHistoryTypeBadge.swift
//  ios
//
//  Atom: Type badge for chat history items (BOOK, CONCEPT, STOCK, etc.)
//

import SwiftUI

struct ChatHistoryTypeBadge: View {
    let type: ChatHistoryItemType

    var body: some View {
        Text(type.displayName)
            .font(AppTypography.captionBold)
            .foregroundColor(type.textColor)
    }
}

#Preview {
    VStack(alignment: .leading, spacing: AppSpacing.md) {
        ForEach(ChatHistoryItemType.allCases, id: \.rawValue) { type in
            ChatHistoryTypeBadge(type: type)
        }
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
