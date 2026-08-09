//
//  TermsOfUseView.swift
//  ios
//
//  Screen: Terms of Use (EULA). Authored for Caydex — a fintech + AI research app.
//  NOTE: this is a strong starting draft, not certified legal advice; have counsel
//  review before publishing. The same text is mirrored at caydexinvest.com/terms
//  (documents/legal/terms.html) for the App Store Connect metadata link.
//

import SwiftUI

struct TermsOfUseView: View {
    var body: some View {
        LegalDocumentView(
            title: "Terms of Use",
            lastUpdated: "August 8, 2026",
            intro: "These Terms of Use (\u{201C}Terms\u{201D}) govern your access to and use of the Caydex mobile application and related services (the \u{201C}App\u{201D}), operated by Caydex (\u{201C}we,\u{201D} \u{201C}us,\u{201D} or \u{201C}our\u{201D}). By downloading, accessing, or using the App, you agree to be bound by these Terms. If you do not agree, do not use the App.",
            sections: Self.sections
        )
    }

    static let sections: [LegalSection] = [
        LegalSection(
            heading: "Eligibility & Acceptance",
            paragraphs: [
                "You must be at least 18 years old (or the age of majority in your jurisdiction) and able to form a binding contract to use the App. By using the App you represent that you meet these requirements.",
                "These Terms incorporate our Privacy Policy and in-app Disclaimers by reference. Continued use of the App after changes to these Terms constitutes acceptance of the revised Terms."
            ]
        ),
        LegalSection(
            heading: "Not Financial Advice",
            paragraphs: [
                "The App is an educational and informational research tool. Nothing in the App constitutes financial, investment, legal, tax, or other professional advice, a recommendation, or an offer or solicitation to buy or sell any security or financial instrument.",
                "Caydex is not a registered investment adviser, broker-dealer, or financial planner. You are solely responsible for your own investment decisions and their outcomes. Past performance does not guarantee future results, and all investing involves risk, including the possible loss of principal. Consult a qualified professional before making investment decisions.",
                "Everything the App produces is general and impersonal. Ratings, scores, fair-value estimates, and research reports are produced from public data by the same methodology for every user, and are not adapted to your portfolio, holdings, financial situation, risk tolerance, or objectives. Where a feature uses figures you enter yourself — for example Portfolio Insights — it returns neutral descriptive diagnostics about the data you provided, and does not recommend that you buy, sell, or hold anything.",
                "Caydex is independent. We hold no positions in the securities covered by the App, we receive no compensation from any issuer, and no company can pay to be featured, rated, scored, or covered. We do not sell advertising or paid placement to issuers, and no third party directs or reviews our ratings before publication.",
                "We do not provide brokerage, custody, or execution services. We never hold your money or securities, we cannot place trades, and no fiduciary or advisory relationship is created between you and Caydex by your use of the App."
            ]
        ),
        LegalSection(
            heading: "AI-Generated Content",
            paragraphs: [
                "The App uses artificial intelligence to generate analysis, reports, summaries, and chat responses. AI output may be inaccurate, incomplete, outdated, or misleading, and may not reflect real-world events. Treat all AI output as informational only and independently verify anything you rely on.",
                "Investor \u{201C}personas\u{201D} and similar features are educational simulations. They do not represent the actual views, statements, or endorsement of any real person."
            ]
        ),
        LegalSection(
            heading: "Accounts & Security",
            paragraphs: [
                "Some features require an account. You are responsible for maintaining the confidentiality of your credentials and for all activity under your account. Notify us promptly of any unauthorized use.",
                "You agree to provide accurate information and to keep it up to date. We may suspend or terminate accounts that violate these Terms."
            ]
        ),
        LegalSection(
            heading: "Credits, Subscriptions & Billing",
            paragraphs: [
                "The App uses a credit system for AI features (for example, AI reports and Cay AI chat). Paid subscription tiers provide a monthly allocation of credits. Unused monthly credits do not roll over: your monthly allowance resets at the start of each calendar month, US Eastern Time. If you upgrade mid-month your allowance is raised to the new tier immediately, and credits already spent that month remain spent.",
                "Credit packs are separate. Credits you buy as a one-time credit pack are held in a separate balance, do not expire, and are not reset by the monthly renewal. Your monthly allowance is spent first, so a pack is only drawn on once that month's allowance is used up.",
                "Subscriptions are billed through your Apple App Store account. Payment is charged at confirmation of purchase, and subscriptions renew automatically for the same period and price unless canceled at least 24 hours before the end of the current period. You can manage or cancel subscriptions in your App Store account settings; deleting the App does not cancel a subscription.",
                "Except where required by applicable law or the App Store\u{2019}s policies, purchases and credits are non-refundable. Prices and allocations may change prospectively; changes will not affect a billing period already paid for."
            ]
        ),
        LegalSection(
            heading: "Acceptable Use",
            paragraphs: [
                "You agree not to: (a) reverse engineer, decompile, or attempt to extract source code except as permitted by law; (b) scrape, harvest, or bulk-export data from the App; (c) resell, redistribute, or commercially exploit the App or its content without authorization; (d) interfere with or disrupt the App\u{2019}s operation or security; (e) attempt to circumvent usage limits, credit accounting, or access controls; or (f) use the App for any unlawful purpose.",
                "You are responsible for complying with all laws and regulations applicable to you, including securities laws in your jurisdiction."
            ]
        ),
        LegalSection(
            heading: "Market Data & Third-Party Content",
            paragraphs: [
                "Market data, financial statements, prices, and related information are provided by third-party sources and may be delayed, inaccurate, or incomplete. Quotes may be delayed by 15\u{2013}20 minutes or more depending on the exchange and provider.",
                "We do not guarantee the accuracy, completeness, or timeliness of any data. Where the App\u{2019}s data conflicts with an official exchange or filing, the official source controls. Third-party content is the responsibility of its providers."
            ]
        ),
        LegalSection(
            heading: "Intellectual Property",
            paragraphs: [
                "The App, including its software, design, text, and branding, is owned by Caydex or its licensors and is protected by intellectual property laws. Subject to these Terms, we grant you a limited, personal, non-exclusive, non-transferable, revocable license to use the App for your own non-commercial use.",
                // Carve-out added deliberately: without it, the sentence above claimed
                // Caydex owns ALL text in the app, which is not true of market data,
                // news, filings, or the ideas discussed in our book study guides.
                "This does not claim ownership of third-party material presented in the App. Market data, news content, corporate filings, and other third-party information belong to their respective owners and are used under licence or as public records \u{2014} see Data Sources in the App. Our educational summaries of published books are our own original writing about those books; the underlying books, their titles, and their authors\u{2019} rights belong to their respective owners, and Caydex is not affiliated with, authorised, or endorsed by any author or publisher.",
                "Third-party names, marks, and logos are the property of their owners. We use them to identify companies, securities, data providers, and works we discuss. Their use does not imply any affiliation with, sponsorship by, or endorsement of Caydex.",
                "You retain ownership of content you submit, and grant us a license to process it as needed to operate and improve the App."
            ]
        ),
        LegalSection(
            heading: "Copyright Complaints",
            paragraphs: [
                "We respect intellectual property rights and respond to clear notices of alleged copyright infringement concerning material in the App.",
                "If you believe material in the App infringes your copyright, send a written notice to copyright@caydexinvest.com including: (1) your physical or electronic signature; (2) identification of the copyrighted work you claim is infringed; (3) identification of the material you claim is infringing and enough detail for us to locate it in the App; (4) your name, address, telephone number, and email; (5) a statement that you have a good-faith belief the use is not authorised by the copyright owner, its agent, or the law; and (6) a statement that the information in your notice is accurate and, under penalty of perjury, that you are the copyright owner or authorised to act on the owner\u{2019}s behalf.",
                "We will review complete notices promptly and may remove or disable access to material we determine is likely infringing. If you believe your material was removed in error, you may send a counter-notice to the same address with equivalent detail.",
                "Please note that under U.S. law you may be liable for damages, including costs and attorneys\u{2019} fees, if you knowingly misrepresent that material is infringing."
            ]
        ),
        LegalSection(
            heading: "Disclaimer of Warranties",
            paragraphs: [
                "The App is provided \u{201C}as is\u{201D} and \u{201C}as available,\u{201D} without warranties of any kind, express or implied, including merchantability, fitness for a particular purpose, non-infringement, and any warranty regarding accuracy, reliability, or uninterrupted operation, to the maximum extent permitted by law."
            ]
        ),
        LegalSection(
            heading: "Limitation of Liability",
            paragraphs: [
                "To the maximum extent permitted by law, Caydex and its operators will not be liable for any indirect, incidental, special, consequential, or punitive damages, or for any investment losses or lost profits, arising from or related to your use of the App.",
                "Our total aggregate liability for any claim relating to the App will not exceed the greater of the amount you paid us in the 12 months before the claim or USD $50."
            ]
        ),
        LegalSection(
            heading: "Indemnification",
            paragraphs: [
                "You agree to indemnify and hold harmless Caydex and its operators from any claims, damages, liabilities, and expenses (including reasonable legal fees) arising from your use of the App, your content, or your violation of these Terms or applicable law."
            ]
        ),
        LegalSection(
            heading: "Termination",
            paragraphs: [
                "We may suspend or terminate your access to the App at any time for conduct that violates these Terms or that we reasonably believe is harmful to other users, us, or third parties. You may stop using the App at any time; certain provisions (including intellectual property, disclaimers, limitation of liability, and dispute resolution) survive termination."
            ]
        ),
        LegalSection(
            heading: "Governing Law & Dispute Resolution",
            paragraphs: [
                "These Terms are governed by the laws of the State of Delaware, United States, without regard to its conflict-of-laws rules, except where local consumer-protection law provides otherwise.",
                "Except where prohibited by law, any dispute arising from these Terms or the App will be resolved by binding individual arbitration rather than in court, and you and Caydex waive the right to a jury trial and to participate in a class action. You may opt out of arbitration by contacting us within 30 days of first accepting these Terms. Nothing here limits your statutory rights that cannot be waived."
            ]
        ),
        LegalSection(
            heading: "Changes to These Terms",
            paragraphs: [
                "We may update these Terms from time to time. We will update the \u{201C}Last updated\u{201D} date and, where appropriate, provide additional notice. Your continued use of the App after changes take effect constitutes acceptance."
            ]
        ),
        LegalSection(
            heading: "Contact",
            paragraphs: [
                "Questions about these Terms? Contact us at support@caydexinvest.com."
            ]
        )
    ]
}

#Preview {
    NavigationStack {
        TermsOfUseView()
    }
}
