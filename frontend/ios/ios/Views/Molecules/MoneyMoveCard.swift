//
//  MoneyMoveCard.swift
//  ios
//
//  Molecule: Card showing a money move with completion + audio indicators
//

import SwiftUI

struct MoneyMoveCard: View {
    let moneyMove: MoneyMove
    var onTap: (() -> Void)?
    @ObservedObject private var progress = MoneyMovesProgressStore.shared

    var body: some View {
        Button {
            onTap?()
        } label: {
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                // ALWAYS a cover plate — never a conditional, never a category badge.
                //
                // MoneyMoveCoverImage already degrades to the category gradient + grain when
                // the url is nil, so "no artwork yet", "still loading" and "artwork arrived"
                // all render the same 16:9 block. That is the whole point: the BUNDLED
                // money_moves.json carries no image urls at all, so until the backend prefetch
                // lands every card on the See-All screen used to start straight at its title
                // — no picture, no badge, because that screen also passed `showIcon: false` —
                // and then REFLOW when the art arrived. A card that changes height under the
                // reader's thumb is worse than a plain plate.
                //
                // The `showIcon` parameter died here: with the badge branch gone it had no
                // reader, and a parameter that is accepted and ignored lies to its callers.
                MoneyMoveCoverImage(
                    url: moneyMove.imageUrl,
                    gradientColors: moneyMove.category.gradientColors,
                    cornerRadius: AppCornerRadius.medium,
                    aspectRatio: 16 / 9
                )

                // Title
                Text(moneyMove.title)
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)

                // Subtitle
                Text(moneyMove.subtitle)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)

                Spacer(minLength: 0)

                // One meta row: publication date, read time, and the completion mark pushed to
                // the trailing edge.
                //
                // The completion mark used to sit in its own header row opposite a headphones
                // badge, which cost a whole 28-40pt band for two glyphs. The headphones went
                // entirely — narration is on every article, so a badge that is always present
                // distinguishes nothing, and the article's own Listen button is the real
                // affordance. (`MoneyMove.hasAudio` went with it — nothing read it after.)
                //
                // A `LearnerCountBadge` branch also lived here, gated on `learnerCount`. It was
                // DEAD — the backend never writes that field, `viewCount` is blank on every
                // article, and two test files forbid non-blank engagement numbers outright. It
                // was not harmless dead code: measured, if it had ever rendered it needed
                // ~184pt in a 176pt row, so it was the one thing guaranteed to break this
                // layout the moment it fired.
                metaRow
            }
            .padding(AppSpacing.md)
            .frame(width: 200)
            .cardSurface(cornerRadius: AppCornerRadius.extraLarge)
            .contentShape(RoundedRectangle(cornerRadius: AppCornerRadius.extraLarge))
        }
        .buttonStyle(.plain)
    }

    // MARK: - Meta row

    /// ⚠️ `ViewThatFits`, because this row overflows at xLarge — a NON-accessibility size
    /// reachable from the ordinary Text Size slider — not just at AX sizes.
    ///
    /// `AppSpacing` is unscaled, so adding a 4th child adds a fixed 12pt gap while the text
    /// scales to `readingCap` 1.4x. In the 176pt content width (200pt frame − 24pt padding):
    /// "Wednesday" + read time + checkmark + gaps measures ~157pt at Large but ~178pt at
    /// xLarge and ~201pt at the cap. The failure is SILENT — inside a horizontal ScrollView
    /// the row wraps and every card grows a line together, rather than visibly clipping.
    ///
    /// So: try the full form, then fall back to the abbreviated weekday with the clock glyph
    /// dropped (`ReadTimeLabel` already supports `showIcon: false`), which lands ~155pt at the
    /// cap. `.fixedSize` on the date keeps `ViewThatFits` measuring the ideal width instead of
    /// a pre-compressed one.
    private var metaRow: some View {
        ViewThatFits(in: .horizontal) {
            metaRow(dateStyle: .full, showClock: true)
            metaRow(dateStyle: .short, showClock: false)
        }
    }

    private func metaRow(dateStyle: MoneyMoveDateFormatting.Style, showClock: Bool) -> some View {
        HStack(spacing: AppSpacing.sm) {
            // Omitted entirely when there is no date worth showing. That is the live case, not
            // a hypothetical: seven "coming soon" placeholder cards ship with
            // `createdAt == .distantPast`, which formats naively as "Jan 1, 1".
            if let date = MoneyMoveDateFormatting.label(for: moneyMove.createdAt, style: dateStyle) {
                TimeAgoLabel(text: date, color: AppColors.textSecondary)
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
            }

            ReadTimeLabel(minutes: moneyMove.estimatedMinutes, showIcon: showClock)

            Spacer(minLength: 0)

            // Completion status (read-only): filled green check once done, else a muted
            // hollow circle. The Complete action lives at the end of the article.
            Image(systemName: progress.isCompleted(slug: moneyMove.slug)
                  ? "checkmark.circle.fill" : "circle")
                .font(AppTypography.iconSmall).fontWeight(.semibold)
                .foregroundColor(progress.isCompleted(slug: moneyMove.slug)
                                 ? AppColors.bullish : AppColors.textMuted)
        }
    }
}

#Preview {
    ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: AppSpacing.lg) {
            ForEach(MoneyMove.sampleData) { moneyMove in
                MoneyMoveCard(moneyMove: moneyMove)
            }
        }
        .padding()
    }
    .background(AppColors.background)
}
