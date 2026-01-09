//
//  TickerNewsCardFooter.swift
//  ios
//
//  Molecule: Footer with external link and expand/collapse for news card
//

import SwiftUI

struct TickerNewsCardFooter: View {
    let hasExpandableContent: Bool
    let isExpanded: Bool
    var onExternalLinkTap: (() -> Void)?
    var onExpandToggle: (() -> Void)?

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // External link button
            Button(action: {
                onExternalLinkTap?()
            }) {
                NewsExternalLinkIcon()
            }
            .buttonStyle(PlainButtonStyle())

            // Expand/collapse button (only show if has content)
            if hasExpandableContent {
                Button(action: {
                    onExpandToggle?()
                }) {
                    NewsCardExpandIcon(isExpanded: isExpanded)
                }
                .buttonStyle(PlainButtonStyle())
            }

            Spacer()
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.xl) {
        // With expandable content - collapsed
        TickerNewsCardFooter(
            hasExpandableContent: true,
            isExpanded: false
        )

        // With expandable content - expanded
        TickerNewsCardFooter(
            hasExpandableContent: true,
            isExpanded: true
        )

        // Without expandable content
        TickerNewsCardFooter(
            hasExpandableContent: false,
            isExpanded: false
        )
    }
    .padding()
    .background(AppColors.cardBackground)
    .cornerRadius(AppCornerRadius.large)
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
