//
//  AlertIconView.swift
//  ios
//
//  Atom: Icon container for alert types
//

import SwiftUI

struct AlertIconView: View {
    let type: AlertType

    private var backgroundColor: Color {
        switch type {
        case .whalesAlert:
            return AppColors.alertOrange
        case .earningsAlert:
            return AppColors.alertBlue
        case .whalesFollowing:
            return AppColors.alertBlue
        case .wiserTrending:
            return AppColors.alertPurple
        }
    }

    var body: some View {
        ZStack {
            Circle()
                .fill(backgroundColor.opacity(0.2))
                .frame(width: 40, height: 40)

            Image(systemName: type.systemIconName)
                .font(.system(size: 16, weight: .semibold))
                .foregroundColor(backgroundColor)
        }
    }
}

#Preview {
    HStack(spacing: 20) {
        AlertIconView(type: .whalesAlert)
        AlertIconView(type: .earningsAlert)
        AlertIconView(type: .whalesFollowing)
        AlertIconView(type: .wiserTrending)
    }
    .padding()
    .background(AppColors.background)
}
