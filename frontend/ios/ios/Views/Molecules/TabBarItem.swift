//
//  TabBarItem.swift
//  ios
//
//  Molecule: Individual tab bar item
//

import SwiftUI

struct TabBarItem: View {
    let tab: HomeTab
    let isSelected: Bool
    var onTap: (() -> Void)?

    private var iconColor: Color {
        isSelected ? AppColors.tabBarSelected : AppColors.tabBarUnselected
    }

    private var textColor: Color {
        isSelected ? AppColors.tabBarSelected : AppColors.tabBarUnselected
    }

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            VStack(spacing: AppSpacing.xs) {
                Image(systemName: tab.systemIconName)
                    .font(.system(size: 20, weight: isSelected ? .semibold : .regular))
                    .foregroundColor(iconColor)

                Text(tab.rawValue)
                    .font(AppTypography.caption)
                    .foregroundColor(textColor)
            }
            .frame(maxWidth: .infinity)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    HStack {
        ForEach(HomeTab.allCases, id: \.self) { tab in
            TabBarItem(tab: tab, isSelected: tab == .home)
        }
    }
    .padding()
    .background(AppColors.tabBarBackground)
}
