//
//  SmartMoneyInfoIcon.swift
//  ios
//
//  Atom: Info icon button for Smart Money section
//  Tappable icon that triggers educational sheet
//

import SwiftUI

struct SmartMoneyInfoIcon: View {
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            Image(systemName: "info.circle")
                .font(.system(size: 14, weight: .medium))
                .foregroundColor(AppColors.textMuted)
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        SmartMoneyInfoIcon {
            print("Info tapped")
        }
    }
}
