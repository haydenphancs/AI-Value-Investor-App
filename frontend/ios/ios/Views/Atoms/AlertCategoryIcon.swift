//
//  AlertCategoryIcon.swift
//  ios
//
//  Atom: Icon for alert/event categories
//

import SwiftUI

struct AlertCategoryIcon: View {
    let type: AlertEventType
    var size: CGFloat = 40

    private var iconSize: CGFloat {
        size * 0.45
    }

    var body: some View {
        ZStack {
            Circle()
                .fill(type.iconBackgroundColor.opacity(0.15))
                .frame(width: size, height: size)

            Image(systemName: type.systemIconName)
                .font(.system(size: iconSize, weight: .semibold))
                .foregroundColor(type.iconBackgroundColor)
        }
    }
}

#Preview {
    HStack(spacing: 20) {
        AlertCategoryIcon(type: .earnings)
        AlertCategoryIcon(type: .market)
        AlertCategoryIcon(type: .smartMoney)
    }
    .padding()
    .background(AppColors.background)
}
