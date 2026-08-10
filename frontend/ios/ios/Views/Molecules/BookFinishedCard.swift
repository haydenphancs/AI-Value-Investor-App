//
//  BookFinishedCard.swift
//  ios
//
//  Molecule: the whole-book finale — the card shown over a scrim inside the reader the moment the
//  LAST remaining core of a book is completed (see BookProgressStore.didFinishBook).
//
//  Deliberately a SIBLING of LessonCompletionCard rather than an extension of it. That one is a
//  full-bleed PAGE in the Journey story deck: it owns its Spacers and fills the screen, always
//  renders a "Lesson N of M · X min" badge that no prop can suppress, always renders a secondary
//  "Close", and uses the deck's 26pt pill CTA with no shadow. Reshaping it into a popup would mean
//  three new optional props, optionality changes across five construction sites, and two
//  behavioural branches — in a layout that is wrong for the job.
//
//  What IS reused verbatim, so the app's two celebrations read as one family: the checkmark ring's
//  tokens and the entrance spring timings.
//

import SwiftUI

struct BookFinishedCard: View {
    /// Rendered under a small "You finished" lead-in, so a long book title wraps on its own line.
    let bookTitle: String
    /// One line of praise under the title.
    let message: String
    /// The single filled CTA. There is deliberately no second exit — the card IS the end of the book.
    let ctaTitle: String
    let onCTATapped: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @State private var cardOpacity: Double = 0
    @State private var cardOffset: CGFloat = 30
    @State private var ringProgress: CGFloat = 0
    @State private var checkScale: CGFloat = 0.5
    @State private var checkOpacity: Double = 0

    var body: some View {
        VStack(spacing: AppSpacing.xl) {
            checkmarkRing

            VStack(spacing: AppSpacing.sm) {
                Text("You finished")
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textSecondary)

                Text(bookTitle)
                    .font(AppTypography.titleLarge)
                    .foregroundColor(AppColors.textPrimary)
                    .multilineTextAlignment(.center)
                    // Long titles must wrap, not truncate — the book's name is the whole point of
                    // the card, and book 10's title is 24 characters longer than book 9's.
                    .fixedSize(horizontal: false, vertical: true)
            }

            Text(message)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)

            // The app's canonical filled CTA (see MailUnavailableCard's `filled`): `textOnAccent`
            // on `primaryFill`, never `textPrimary` on `primaryBlue`. There is no PrimaryButton
            // atom and no ButtonStyle in this codebase, so the recipe is hand-rolled as everywhere.
            Button(action: onCTATapped) {
                Text(ctaTitle)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textOnAccent)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, AppSpacing.md)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(AppColors.primaryFill)
                    )
            }
            .buttonStyle(PlainButtonStyle())
            // "Thank you" alone is opaque as a VoiceOver action — say what it does.
            .accessibilityHint("Closes the book and returns to where you opened it.")
            .padding(.top, AppSpacing.sm)
        }
        .padding(AppSpacing.xxl)
        // On a scrim, NOT nested inside another card — so the default card background is correct
        // here (cardBackgroundNested would be the bug), and `.overlay` is the documented elevation
        // for exactly this case: it drops the hairline border and uses AppShadows.overlay.
        .cardSurface(cornerRadius: AppCornerRadius.extraLarge, elevation: .overlay)
        .frame(maxWidth: 420)
        .padding(.horizontal, AppSpacing.xl)
        .opacity(cardOpacity)
        .offset(y: cardOffset)
        .onAppear(perform: animateIn)
    }

    /// Same tokens as LessonCompletionCard's checkmark, with the outer ring SWEPT rather than
    /// popped. `bullish` is a 4.5:1 text token used as a stroke, which the role rules permit (a
    /// text value in a graphic is merely less vivid); the glyph is `textOnFill` on `gainFill`.
    private var checkmarkRing: some View {
        ZStack {
            Circle()
                .stroke(AppColors.bullish.opacity(0.20), lineWidth: 4)
                .frame(width: 80, height: 80)

            Circle()
                .trim(from: 0, to: ringProgress)
                .stroke(AppColors.bullish, style: StrokeStyle(lineWidth: 4, lineCap: .round))
                .rotationEffect(.degrees(-90))
                .frame(width: 80, height: 80)

            Circle()
                .fill(AppColors.gainFill)
                .frame(width: 70, height: 70)
                .opacity(checkOpacity)

            Image(systemName: "checkmark")
                .font(AppTypography.iconJumbo).fontWeight(.bold)
                .foregroundColor(AppColors.textOnFill)
                .scaleEffect(checkScale)
                .opacity(checkOpacity)
        }
        // Decorative: the headline already says the book is finished.
        .accessibilityHidden(true)
    }

    private func animateIn() {
        // Three stacked motions (offset + ring sweep + scale) is exactly what Reduce Motion is for.
        guard !reduceMotion else {
            cardOpacity = 1; cardOffset = 0
            ringProgress = 1; checkScale = 1; checkOpacity = 1
            return
        }
        // The first curve is LessonCompletionCard's verbatim, so both celebrations enter alike.
        withAnimation(.spring(response: 0.5, dampingFraction: 0.7).delay(0.1)) {
            cardOpacity = 1
            cardOffset = 0
        }
        withAnimation(.easeOut(duration: 0.55).delay(0.25)) {
            ringProgress = 1
        }
        withAnimation(.spring(response: 0.6, dampingFraction: 0.6).delay(0.5)) {
            checkScale = 1
            checkOpacity = 1
        }
    }
}

#Preview("Long title") {
    ZStack {
        AppColors.background.ignoresSafeArea()
        AppColors.scrim.ignoresSafeArea()
        BookFinishedCard(
            bookTitle: "The Little Book that Still Beats the Market",
            message: "All 5 cores, start to finish. It's marked mastered in your library.",
            ctaTitle: "Thank you",
            onCTATapped: {}
        )
    }
}

#Preview("Short title") {
    ZStack {
        AppColors.background.ignoresSafeArea()
        AppColors.scrim.ignoresSafeArea()
        BookFinishedCard(
            bookTitle: "Rich Dad Poor Dad",
            message: "All 7 cores, start to finish. It's marked mastered in your library.",
            ctaTitle: "Thank you",
            onCTATapped: {}
        )
    }
}
