//
//  CommodityDetailTabBar.swift
//  ios
//
//  Molecule: Horizontal scrollable tab bar for Commodity Detail sections
//

import SwiftUI

struct CommodityDetailTabBar: View {
    @Binding var selectedTab: CommodityDetailTab

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 0) {
                ForEach(CommodityDetailTab.allCases, id: \.rawValue) { tab in
                    CommodityDetailTabButton(
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

struct CommodityDetailTabButton: View {
    let tab: CommodityDetailTab
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
        @State private var selectedTab: CommodityDetailTab = .overview

        var body: some View {
            VStack {
                CommodityDetailTabBar(selectedTab: $selectedTab)
                Spacer()
            }
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
        .preferredColorScheme(.dark)
}
