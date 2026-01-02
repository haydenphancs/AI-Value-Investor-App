//
//  ChatHistoryHeader.swift
//  ios
//
//  Organism: Header with history button for chat tab
//

import SwiftUI

struct ChatHistoryHeader: View {
    var showingHistory: Bool = false
    var onHistoryTap: (() -> Void)?
    var onChevronTap: (() -> Void)?

    var body: some View {
        HStack {
            HistoryButton {
                onHistoryTap?()
            }

            Spacer()

            // Show chevron when viewing history
            if showingHistory {
                Button(action: {
                    onChevronTap?()
                }) {
                    Image(systemName: "chevron.right")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(AppColors.textSecondary)
                }
                .buttonStyle(PlainButtonStyle())
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.sm)
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        ChatHistoryHeader(showingHistory: false)
        ChatHistoryHeader(showingHistory: true)
        Spacer()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
