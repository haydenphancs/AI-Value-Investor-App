//
//  ChatHistorySectionHeader.swift
//  ios
//
//  Molecule: Section header for chat history (TODAY, YESTERDAY, OLDER)
//

import SwiftUI

struct ChatHistorySectionHeader: View {
    let section: ChatHistorySection
    var showChevron: Bool = false
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            HStack {
                Text(section.rawValue)
                    .font(AppTypography.captionBold)
                    .foregroundColor(AppColors.textMuted)

                Spacer()

                if showChevron {
                    Image(systemName: "chevron.right")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundColor(AppColors.textMuted)
                }
            }
        }
        .buttonStyle(PlainButtonStyle())
        .disabled(onTap == nil)
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        ChatHistorySectionHeader(section: .today, showChevron: true)
        ChatHistorySectionHeader(section: .yesterday)
        ChatHistorySectionHeader(section: .older)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}

