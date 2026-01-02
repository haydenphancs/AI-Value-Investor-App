//
//  LearnTabControl.swift
//  ios
//
//  Molecule: Custom tab control for Learn/Chat/Saved tabs
//

import SwiftUI

struct LearnTabControl: View {
    @Binding var selectedTab: LearnTab

    var body: some View {
        HStack(spacing: 0) {
            ForEach(LearnTab.allCases, id: \.rawValue) { tab in
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        selectedTab = tab
                    }
                } label: {
                    Text(tab.rawValue)
                        .font(AppTypography.bodyBold)
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
