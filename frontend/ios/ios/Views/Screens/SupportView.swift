//
//  SupportView.swift
//  ios
//
//  Screen: Help & Support. Native mirror of caydexinvest.com/support
//  (backend/app/templates/legal/support.html), which is also the App Store Connect Support URL.
//
//  WHY THIS EXISTS AS A SCREEN AT ALL
//  ----------------------------------
//  The Profile → "Help & Support" row used to call `UIApplication.shared.open` on a `mailto:`
//  and nothing else. The iOS Simulator ships no Mail app, so there was no handler, the open
//  failed, and — because no `completionHandler:` was passed — the failure was discarded. The row
//  did nothing at all, silently, for the entire life of the file. The same is true on any device
//  with no mail account configured.
//
//  It was also the ONLY row of the five in About & Legal that tried to leave the app;
//  Disclaimers, Data Sources, Terms of Use and Privacy Policy are all native screens. Email is
//  still offered here, but as one affordance on a screen that works without it — not as the
//  entire feature.
//
//  ⚠️ HAND-MAINTAINED MIRROR of `support.html`. If you change a disclaimer, a price, or the
//  13F lag here, change `support.html` too — `test_in_app_support_screen_mirrors_the_served_page`
//  asserts the load-bearing claims on BOTH sides and fails otherwise, and the PostToolUse hook
//  runs it the moment either file is edited.
//
//  ⛔️ Do NOT "fix" this by creating `documents/legal/support.html`. Its absence is DELIBERATE
//  (decided 2026-08-07, after measuring):
//
//    * That would only buy an HTML↔HTML byte-sync test. The drift that matters here is
//      HTML↔Swift — a different pair, which such a file does not touch.
//    * The `documents/legal/` copy of privacy/terms is not a review workflow, it is a deploy
//      workaround: Railway's service root is `backend/`, so `Dockerfile`'s `COPY . .` cannot
//      reach a repo-root file and the route would 404 in production. `support.html` was
//      authored directly in the deploy context — the shape with one fewer failure mode.
//    * The pair it would imitate has never drifted: byte-identical at every commit that has
//      ever touched them, and `test_served_copy_matches_the_authored_original` `pytest.skip`s
//      when the authored copy is absent, so it no-ops inside the container — the one place
//      drift would go unseen.
//
//  Measured at the time: this file and `support.html` share 94% of their distinct words. The
//  differences are page chrome that SHOULD differ (the marketing intro, the Legal cross-link
//  list, the copyright footer) — the app reaches Terms and Privacy through its own navigation.
//

import SwiftUI

struct SupportView: View {
    /// Set when a `mailto:` open fails, which is the normal case on the Simulator. Flips the
    /// contact card to a copyable address — strictly more useful than a toast, because the
    /// address IS what the user was reaching for.
    @State private var emailUnavailable = false
    @State private var didCopy = false

    /// Defined in `AppInfo`, not here — three screens reference it now, and the one that
    /// bounced (`feedback@`) did so because there were two places to get it wrong.
    static let supportEmail = AppInfo.supportEmail

    var body: some View {
        LegalDocumentView(
            title: "Help & Support",
            lastUpdated: "August 7, 2026",
            intro: "Investment research and education — not investment advice. Caydex turns public market data into structured research: ratings, scores, fair-value estimates and AI-written summaries you can read before you form your own view.",
            sections: Self.sections
        ) {
            contactCard
        }
    }

    // MARK: - Contact card (the actionable header)

    private var contactCard: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("Contact us")
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)

            Text("We aim to reply within two business days.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            // Always selectable, whether or not a mail client exists — so this screen is
            // useful on a device that cannot send mail at all.
            SupportAddressText()

            if emailUnavailable {
                // Degraded, in place. `openInSystem` has already raised the toast; this is the
                // part that actually helps. Shared with FeedbackView so there is one
                // implementation of the "no mail client" contract.
                MailUnavailableCard(
                    payload: Self.supportEmail,
                    message: "No email app is set up on this device. Copy the address and write to us from wherever you read mail.",
                    action: .copy(label: "Copy address")
                )
            } else {
                Button {
                    guard let url = URL(
                        string: "mailto:\(Self.supportEmail)?subject=Support%20Request"
                    ) else { return }
                    openInSystem(url, action: "open your email app") {
                        emailUnavailable = true
                    }
                } label: {
                    Label("Email Support", systemImage: "envelope.fill")
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
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .cardFill()
        )
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Content
    //
    // One `LegalSection` per `<h2>` in support.html, in the same order.

    static let sections: [LegalSection] = [
        LegalSection(
            heading: "Important disclaimer",
            paragraphs: [
                "Caydex is an information and education tool. It is not investment advice, and Caydex is not a broker-dealer, investment adviser, or financial planner.",
                "Nothing in the app is a recommendation to buy or sell any security. Ratings, scores, fair-value estimates and AI-generated commentary are automated interpretations of public data and may be wrong.",
                "Everything the app produces is general and impersonal. It is generated by the same methodology for every user and is not adapted to your portfolio, holdings, financial situation, risk tolerance, or objectives.",
                "Caydex is independent. We hold no positions in the securities we cover, we receive no compensation from any issuer, and no company can pay to be featured, rated, scored, or covered.",
                "We hold no money or securities and cannot place trades. Investing involves risk, including the possible loss of principal, and past performance does not guarantee future results. Always do your own research and consider consulting a licensed professional."
            ]
        ),
        LegalSection(
            heading: "How our ratings and estimates are produced",
            paragraphs: [
                "The technical rating (Strong Sell → Strong Buy). This is a deterministic, rules-based reading of standard technical indicators — moving averages, momentum and trend measures — computed from recent price and volume history. It is not AI-generated, it does not consider company fundamentals, news, or valuation, and it is not a forecast. The same inputs always produce the same rating, for every user.",
                "The fair-value estimate. A model output under stated assumptions, not a price target and not a prediction. Its assumptions can be wrong, and small changes to them can move the number substantially. Treat any gap between the estimate and the market price as a prompt to do your own research, not as a signal.",
                "Scores and reports. Report scores combine several computed sub-scores. A generated report is a point-in-time snapshot: it is produced once and stored, so an older report reflects the data as it stood when it was generated, not today's."
            ]
        ),
        LegalSection(
            heading: "AI-generated content",
            paragraphs: [
                "Research reports, summaries and the Cay AI assistant use large language models. AI output can be inaccurate, incomplete, or outdated, and can state wrong things confidently. Verify anything you intend to rely on against a primary source such as the company's SEC filings.",
                "Your chat messages are processed by a third-party AI provider so it can generate a response. Your name, email, account identifier, watchlist, portfolio and holdings are not sent with them. The app asks for your permission before any content is shared this way, and you can withdraw it in Settings. Details are in the Privacy Policy.",
                "The investing personas are educational simulations of well-known investing styles. They are not affiliated with, endorsed by, or representative of the views of any real person."
            ]
        ),
        LegalSection(
            heading: "Where our data comes from",
            paragraphs: [
                "Market, fundamental and filing data is supplied by third parties. We do not originate it and cannot guarantee it is accurate, complete, or current. Errors in upstream data flow through into our own ratings and estimates.",
                "Financial Modeling Prep — prices, fundamentals, statements, analyst data, insider and institutional filings. SEC EDGAR — underlying filings, including Forms 4 and 13F. CoinGecko — cryptocurrency market data. FRED (St. Louis Fed) — macroeconomic series. FINRA — short interest. ApeWisdom — social mention counts.",
                "Filing data is delayed by law, not by us. Institutional holdings come from quarterly Form 13F filings, which are due 45 days after quarter end — so the most recent institutional positions we can show are always at least six weeks old, and may have changed since. Congressional trade disclosures have their own statutory reporting windows. Quoted prices may be delayed."
            ]
        ),
        LegalSection(
            heading: "Subscriptions, credits and billing",
            paragraphs: [
                "Caydex is free to use for research. AI features (report generation and Cay AI chat) consume credits. Free accounts receive a monthly credit allowance; Caydex Pro ($14.99/month) and Caydex Max ($39.99/month) are optional auto-renewable subscriptions with larger monthly allowances.",
                "Unused credits do not roll over. Your allowance resets at the start of each calendar month (US Eastern Time). If you upgrade mid-month, your allowance is raised to the new tier immediately, and credits already spent that month remain spent."
            ]
        ),
        LegalSection(
            heading: "Managing or cancelling a subscription",
            paragraphs: [
                "Subscriptions are billed by Apple, not by us. Manage or cancel at any time in iOS Settings → your name → Subscriptions, or at apps.apple.com/account/subscriptions. Cancelling stops future renewals; access continues to the end of the paid period."
            ]
        ),
        LegalSection(
            heading: "Refunds",
            paragraphs: [
                "Because Apple processes all payments, refunds are handled by Apple, not by Caydex. Request one at reportaproblem.apple.com. If something went wrong on our side — a report you were charged for that failed to generate, for example — email us and we will restore the credits."
            ]
        ),
        LegalSection(
            heading: "Common questions",
            paragraphs: [
                "How do I delete my account and data? In the app: Profile → Settings → Delete Account. This permanently removes your account, watchlists, portfolios, saved reports, chat history and learning progress. It cannot be undone, and it does not cancel an Apple subscription — cancel that separately, as above. You can also email us and we will action it for you.",
                "I did not receive a confirmation or password-reset email. Check your spam folder first. If it still has not arrived after a few minutes, use Resend confirmation email on the confirmation screen, or contact us.",
                "Is my brokerage account connected? No. Caydex never connects to a brokerage, never holds funds, and cannot place trades. Any holdings shown in Portfolio Insights are figures you typed in yourself, and the diagnostics it returns describe only the data you entered.",
                "A number looks wrong. Tell us the ticker and the screen and we will look. Upstream data does contain errors. The company's own SEC filings at sec.gov/edgar are the authoritative source and should be preferred over anything shown in the app."
            ]
        )
    ]
}

#Preview {
    NavigationStack {
        SupportView()
    }
}
