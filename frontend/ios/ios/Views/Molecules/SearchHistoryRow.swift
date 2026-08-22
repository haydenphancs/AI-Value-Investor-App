//
//  SearchHistoryRow.swift
//  ios
//
//  Molecule: one row of the user's search history — a ticker they opened, or a question they
//  asked Cay AI. Visually matches `SearchResultRow` (icon · title · subtitle · trailing action)
//  so the list does not change shape between "Results" and "Recent Searches".
//
//  Not `SearchResultRow`: that one is built around a `SearchResultItem` and always renders a
//  symbol-shaped row, and a question has no ticker, no exchange and no follow affordance. Not
//  `ChatHistoryItemRow` either — that is bound to `ChatHistoryItem` and carries a type badge,
//  a pin state and a 3-dot menu with popup anchoring, none of which belong here.
//

import SwiftUI

struct SearchHistoryRow: View {
    let entry: SearchHistoryEntry
    var onTap: (() -> Void)?
    var onRemove: (() -> Void)?

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            Button(action: { onTap?() }) {
                HStack(spacing: AppSpacing.md) {
                    Image(systemName: entry.iconName)
                        .font(AppTypography.iconSmall)
                        // A TEXT-role token: this glyph carries the row's meaning (symbol
                        // lookup vs AI question), so it has to clear 4.5:1 in both appearances.
                        .foregroundColor(entry.kind == .question
                                         ? AppColors.primaryBlue
                                         : AppColors.textSecondary)
                        .frame(width: 36, height: 36)
                        .background(AppColors.cardBackgroundLight)
                        .clipShape(Circle())

                    VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                        Text(entry.text)
                            .font(AppTypography.bodyEmphasis)
                            .foregroundColor(AppColors.textPrimary)
                            // A question can be a full sentence; a symbol never wraps. Two
                            // lines keeps a long question readable without letting one row
                            // push the rest of the list off screen.
                            .lineLimit(entry.kind == .question ? 2 : 1)
                            .multilineTextAlignment(.leading)

                        if let subtitle = entry.subtitle, !subtitle.isEmpty {
                            Text(subtitle)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)
                                .lineLimit(1)
                        }
                    }

                    Spacer(minLength: AppSpacing.sm)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(PlainButtonStyle())

            // A SEPARATE button, outside the row button — nesting it would make the whole row
            // and the delete share one hit target and the row would swallow the tap.
            Button(action: { onRemove?() }) {
                Image(systemName: "xmark")
                    .font(AppTypography.caption).fontWeight(.semibold)
                    .foregroundColor(AppColors.textMuted)
                    .frame(width: 32, height: 32)
                    .contentShape(Rectangle())
            }
            .buttonStyle(PlainButtonStyle())
            .accessibilityLabel("Remove \(entry.text) from recent searches")
        }
        .padding(.vertical, AppSpacing.sm)
    }
}

#Preview {
    VStack(spacing: 0) {
        SearchHistoryRow(entry: SearchHistoryEntry(
            kind: .ticker, text: "AAPL", subtitle: "Apple Inc.", rawType: "stock"))
        SearchHistoryRow(entry: SearchHistoryEntry(
            kind: .question, text: "Why did the S&P 500 fall this week, and is it a buying opportunity?"))
        SearchHistoryRow(entry: SearchHistoryEntry(
            kind: .ticker, text: "BTC", subtitle: "Crypto", rawType: "crypto"))
    }
    .padding()
    .background(AppColors.background)
}
