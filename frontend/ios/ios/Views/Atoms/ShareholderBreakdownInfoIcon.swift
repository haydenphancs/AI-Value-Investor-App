//
//  ShareholderBreakdownInfoIcon.swift
//  ios
//
//  Atom: Info icon button for Shareholder Breakdown section
//  Tappable icon that triggers educational sheet
//

import SwiftUI

struct ShareholderBreakdownInfoIcon: View {
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

        ShareholderBreakdownInfoIcon {
            print("Info tapped")
        }
    }
}
