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
            Image(systemName: "info.circle")
                .font(AppTypography.iconSmall).fontWeight(.medium)
                .foregroundColor(AppColors.textMuted)
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
