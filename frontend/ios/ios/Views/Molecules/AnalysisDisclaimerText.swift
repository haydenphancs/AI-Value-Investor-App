//
//  AnalysisDisclaimerText.swift
//  ios
//
//  Disclaimer line for analysis sections — now a TAPPABLE route to the full disclaimers.
//
//  Why it changed: this used to render a plain `Text`. It said the right words and went
//  nowhere. That made it a dead end at 9 call sites — including the two that host the
//  Strong Buy/Sell meter — while `InlineDisclaimerNotice`, the tappable equivalent that
//  presents `DisclaimersView`, was wired to only 2 places in the whole app. A user could
//  reach every rating, score and fair-value estimate without ever seeing the language that
//  actually matters ("not registered investment advisors", "general and impersonal, not
//  tailored to you", "we hold no positions").
//
//  Wrapping rather than deleting is deliberate: it makes all 9 existing call sites tappable
//  without touching 9 files, and keeps one place to change the wording.
//
//  ⚠️ The old default text said "AI-generated content may be inaccurate" at EVERY call site,
//  including the technical meter — which is deterministic, rules-based, and contains no AI at
//  all. Mislabelling a published formula as a black box is inaccurate in the direction that
//  hurts, so the default no longer claims it. Surfaces that ARE AI-generated should say so
//  explicitly by passing `text:`.
//

import SwiftUI

struct AnalysisDisclaimerText: View {
    let text: String

    /// Default wording is deliberately true of EVERY surface this appears on.
    /// Pass `text:` for anything more specific — see `.aiGenerated` and `.rating` below.
    init(text: String = "For educational purposes only · not financial advice · not tailored to you") {
        self.text = text
    }

    var body: some View {
        // Reuses the existing atom, which owns the info icon, the underlined link, the
        // accessibility label, and the NavigationStack-wrapped sheet that solves
        // DisclaimersView being push-only. Do not reimplement any of that here.
        InlineDisclaimerNotice(text: text, linkLabel: "Details")
    }
}

extension AnalysisDisclaimerText {
    /// For surfaces whose content really is model-generated (reports, chat, AI summaries).
    static var aiGenerated: AnalysisDisclaimerText {
        AnalysisDisclaimerText(
            text: "AI-generated · may be inaccurate · not financial advice"
        )
    }

    /// For the technical meter. Says what the rating IS, which no surface previously did:
    /// a rules-based read of price indicators, not a recommendation and not a forecast.
    static var rating: AnalysisDisclaimerText {
        AnalysisDisclaimerText(
            text: "Rules-based reading of price indicators · not a recommendation or forecast"
        )
    }

    /// For a published fair-value number sitting next to a live market price.
    static var fairValue: AnalysisDisclaimerText {
        AnalysisDisclaimerText(
            text: "Model estimate under assumptions · not a price target"
        )
    }

    /// For the Book Guides.
    ///
    /// Short on purpose: the screen already shows the real title, author and author bio directly
    /// above this, so the line only has to say the guide is OUR writing rather than the book or an
    /// authorised edition. It replaced five sentences that also repeated the title and author.
    ///
    /// The fuller claims — non-affiliation, whose rights the book is, and that the narration is a
    /// synthetic voice rather than the author's — live in the "Book Guides" card of
    /// `DisclaimersView`, which the "Details" link opens. Keep the two in step: this line is the
    /// summary, that card is the disclosure.
    static var book: AnalysisDisclaimerText {
        AnalysisDisclaimerText(
            text: "Caydex's own study guide · not the book itself"
        )
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            AnalysisDisclaimerText()
            AnalysisDisclaimerText.rating
            AnalysisDisclaimerText.fairValue
            AnalysisDisclaimerText.aiGenerated
            AnalysisDisclaimerText.book
        }
        .padding()
    }
}
