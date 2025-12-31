//
//  SectionHeader.swift
//  ios
//
//  Atom: Section header with optional "See All" button
//

import SwiftUI

struct SectionHeader: View {
    let title: String
    var showSeeAll: Bool = false
    var onSeeAllTapped: (() -> Void)?

    var body: some View {
        HStack {
            Text(title)
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)

            Spacer()

            if showSeeAll {
                Button(action: {
                    onSeeAllTapped?()
                }) {
                    Text("See All")
                        .font(AppTypography.callout)
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
    }
}

#Preview {
    VStack(spacing: 20) {
        SectionHeader(title: "Daily Briefing")
        SectionHeader(title: "Recent Research", showSeeAll: true)
    }
    .padding()
    .background(AppColors.background)
}
