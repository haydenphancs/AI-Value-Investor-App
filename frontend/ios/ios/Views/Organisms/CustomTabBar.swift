//
//  CustomTabBar.swift
//  ios
//
//  Organism: Custom bottom tab bar
//

import SwiftUI

struct CustomTabBar: View {
    @Binding var selectedTab: HomeTab

    var body: some View {
        HStack(spacing: 0) {
            ForEach(HomeTab.allCases, id: \.self) { tab in
                TabBarItem(tab: tab, isSelected: selectedTab == tab) {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        selectedTab = tab
                    }
                }
            }
        }
        .padding(.top, AppSpacing.md)
        .padding(.bottom, AppSpacing.xl)
        .background(
            AppColors.tabBarBackground
                .shadow(color: Color.black.opacity(0.3), radius: 10, x: 0, y: -5)
        )
    }
}

#Preview {
    VStack {
        Spacer()
        CustomTabBar(selectedTab: .constant(.home))
    }
    .background(AppColors.background)
}
