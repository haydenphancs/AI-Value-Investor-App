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
        ScrollView(.horizontal, showsIndicators: false) {
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
                }
            }
            .padding(.horizontal, AppSpacing.lg)
        }
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
                    .font(AppTypography.calloutBold)
                    .foregroundColor(isSelected ? AppColors.primaryBlue : AppColors.textMuted)
                    .padding(.horizontal, AppSpacing.md)

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
