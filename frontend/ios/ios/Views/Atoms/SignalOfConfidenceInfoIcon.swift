//
//  SignalOfConfidenceInfoIcon.swift
//  ios
//
//  Atom: Info icon button for Signal of Confidence section
//

import SwiftUI

struct SignalOfConfidenceInfoIcon: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ZStack {
                Circle()
                    .fill(AppColors.cardBackgroundLight)
                    .frame(width: 24, height: 24)

                Image(systemName: "info")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(AppColors.textSecondary)
            }
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        SignalOfConfidenceInfoIcon {
            print("Info tapped")
        }
    }
}
