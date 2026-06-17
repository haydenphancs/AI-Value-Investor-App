//
//  ReadAlongBlockView.swift
//  ios
//
//  Molecule: renders one narrated block (heading or paragraph) of a book core, highlighting the
//  sentence currently being read. Mirrors CoreHeadingView / CoreParagraphView styling so it is a
//  drop-in replacement for the narrated sections, adding only the live highlight.
//
//  `activeTime` is the book narration playhead (seconds) when THIS book's audio is the active
//  episode, else nil (no highlight). The active sentence is the one whose [start, end) contains it.
//

import SwiftUI

struct ReadAlongBlockView: View {
    let block: ReadAlongBlock
    let activeTime: Double?

    var body: some View {
        if block.isHeading {
            Text(ReadAlongText.attributed(spans: block.sentences, activeTime: activeTime, base: AppColors.textPrimary))
                .font(AppTypography.titleCompact)
                .padding(.top, AppSpacing.md)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            Text(ReadAlongText.attributed(spans: block.sentences, activeTime: activeTime, base: AppColors.textSecondary))
                .font(AppTypography.body)
                .lineSpacing(6)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
