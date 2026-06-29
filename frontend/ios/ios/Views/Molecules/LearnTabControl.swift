//
//  LearnTabControl.swift
//  ios
//
//  Molecule: Custom tab control for Learn/Chat tabs
//

import SwiftUI

struct LearnTabControl: View {
    @Binding var selectedTab: LearnTab
    /// When set, tapping the "Chat" tab calls this instead of switching `selectedTab` — the Wiser
    /// screen uses it to present the full-screen AIChatScreen cover. "Learn" still switches inline.
    var onChatTapped: (() -> Void)?

    var body: some View {
        HStack(spacing: 0) {
            ForEach(LearnTab.allCases, id: \.rawValue) { tab in
                Button {
                    if tab == .chat {
                        onChatTapped?()
                    } else {
                        withAnimation(.easeInOut(duration: 0.2)) {
                            selectedTab = tab
                        }
                    }
                } label: {
                    Text(tab.rawValue)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(selectedTab == tab ? AppColors.textPrimary : AppColors.textMuted)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, AppSpacing.md)
                        .background(
                            selectedTab == tab
                                ? AppColors.cardBackgroundLight
                                : Color.clear
                        )
                        .cornerRadius(AppCornerRadius.medium)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(AppSpacing.xs)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

#Preview {
    struct PreviewWrapper: View {
        @State private var selected = LearnTab.learn

        var body: some View {
            LearnTabControl(selectedTab: $selected)
                .padding()
                .background(AppColors.background)
        }
    }

    return PreviewWrapper()
        .preferredColorScheme(.dark)
}
