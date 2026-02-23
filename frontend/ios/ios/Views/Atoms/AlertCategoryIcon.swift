//
//  AlertCategoryIcon.swift
//  ios
//
//  Atom: Icon for alert/event categories
//

import SwiftUI

struct AlertCategoryIcon: View {
    let alert: AppAlert
    var size: CGFloat = 40

    private var iconSize: CGFloat {
        size * 0.45
    }

    var body: some View {
        ZStack {
            Circle()
                .fill(alert.iconColor.opacity(0.15))
                .frame(width: size, height: size)

            Image(systemName: alert.iconName)
                .font(.system(size: iconSize, weight: .semibold))
                .foregroundColor(alert.iconColor)
        }
    }
}

#Preview {
    HStack(spacing: 20) {
        ForEach(AppAlert.sampleData) { alert in
            AlertCategoryIcon(alert: alert)
        }
    }
    .padding()
    .background(AppColors.background)
}
