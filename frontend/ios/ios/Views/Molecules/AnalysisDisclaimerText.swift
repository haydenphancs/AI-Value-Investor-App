//
//  AnalysisDisclaimerText.swift
//  ios
//
//  Disclaimer text for analysis sections
//

import SwiftUI

struct AnalysisDisclaimerText: View {
    let text: String

    init(text: String = "Data Disclaimer: For educational purposes only. Not financial advice. AI-generated content may be inaccurate.") {
        self.text = text
    }

    var body: some View {
        Text(text)
            .font(AppTypography.caption)
            .foregroundColor(AppColors.textMuted)
            .multilineTextAlignment(.leading)
            .fixedSize(horizontal: false, vertical: true)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        AnalysisDisclaimerText()
            .padding()
    }
}
