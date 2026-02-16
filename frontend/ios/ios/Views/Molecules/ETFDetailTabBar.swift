//
//  ETFDetailTabBar.swift
//  ios
//
//  Molecule: Horizontal scrollable tab bar for ETF Detail sections
//

import SwiftUI

struct ETFDetailTabBar: View {
    @Binding var selectedTab: ETFDetailTab

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 0) {
                ForEach(ETFDetailTab.allCases, id: \.rawValue) { tab in
                    ETFDetailTabButton(
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

struct ETFDetailTabButton: View {
    let tab: ETFDetailTab
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
        @State private var selectedTab: ETFDetailTab = .overview

        var body: some View {
            VStack {
                ETFDetailTabBar(selectedTab: $selectedTab)
                Spacer()
            }
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
        .preferredColorScheme(.dark)
}
