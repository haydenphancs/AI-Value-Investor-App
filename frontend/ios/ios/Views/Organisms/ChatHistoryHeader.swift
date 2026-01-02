//
//  ChatHistoryHeader.swift
//  ios
//
//  Organism: Header with history button for chat tab
//

import SwiftUI

struct ChatHistoryHeader: View {
    var onHistoryTap: (() -> Void)?

    var body: some View {
        HStack {
            HistoryButton {
                onHistoryTap?()
            }

            Spacer()
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.sm)
    }
}

#Preview {
    VStack {
        ChatHistoryHeader()
        Spacer()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
