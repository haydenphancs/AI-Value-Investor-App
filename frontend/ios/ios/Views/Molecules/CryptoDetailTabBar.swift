//
//  CryptoDetailTabBar.swift
//  ios
//
//  Molecule: Horizontal scrollable tab bar for Crypto Detail sections
//

import SwiftUI

struct CryptoDetailTabBar: View {
    @Binding var selectedTab: CryptoDetailTab

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 0) {
                ForEach(CryptoDetailTab.allCases, id: \.rawValue) { tab in
                    CryptoDetailTabButton(
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

struct CryptoDetailTabButton: View {
    let tab: CryptoDetailTab
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
        @State private var selectedTab: CryptoDetailTab = .overview

        var body: some View {
            VStack {
                CryptoDetailTabBar(selectedTab: $selectedTab)
                Spacer()
            }
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
        .preferredColorScheme(.dark)
}
