//
//  TickerDetailTabBar.swift
//  ios
//
//  Molecule: Horizontal scrollable tab bar for Ticker Detail sections
//

import SwiftUI

struct TickerDetailTabBar: View {
    @Binding var selectedTab: TickerDetailTab

    var body: some View {
        HStack(spacing: 0) {
            ForEach(TickerDetailTab.allCases, id: \.rawValue) { tab in
                TickerDetailTabButton(
                    tab: tab,
                    isSelected: selectedTab == tab,
                    onTap: {
                        withAnimation(.easeInOut(duration: 0.2)) {
                            selectedTab = tab
                        }
                    }
                )
                .frame(maxWidth: tab == .news ? 50 : .infinity)
            }
        }
        .padding(.leading, AppSpacing.md)
        .padding(.trailing, AppSpacing.sm)
    }
}

struct TickerDetailTabButton: View {
    let tab: TickerDetailTab
    let isSelected: Bool
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            VStack(spacing: AppSpacing.sm) {
                Text(tab.rawValue)
                    .font(.system(size: 14, weight: .medium))
                    .foregroundColor(isSelected ? AppColors.primaryBlue : AppColors.textMuted)
                    .lineLimit(1)

                // Selection indicator
                Rectangle()
                    .fill(isSelected ? AppColors.primaryBlue : Color.clear)
                    .frame(height: 2)
            }
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    struct PreviewWrapper: View {
        @State private var selectedTab: TickerDetailTab = .overview

        var body: some View {
            VStack {
                TickerDetailTabBar(selectedTab: $selectedTab)
                Spacer()
            }
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
        .preferredColorScheme(.dark)
}
