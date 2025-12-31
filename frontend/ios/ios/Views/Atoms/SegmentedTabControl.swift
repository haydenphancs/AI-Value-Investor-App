//
//  SegmentedTabControl.swift
//  ios
//
//  Atom: Custom segmented control for tab switching
//

import SwiftUI

struct SegmentedTabControl<T: Hashable & RawRepresentable>: View where T.RawValue == String {
    let tabs: [T]
    @Binding var selectedTab: T

    var body: some View {
        HStack(spacing: 0) {
            ForEach(tabs, id: \.rawValue) { tab in
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
        @State private var selected = TrackingTab.assets

        var body: some View {
            SegmentedTabControl(
                tabs: TrackingTab.allCases,
                selectedTab: $selected
            )
            .padding()
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
}
