//
//  PrivacyPolicyView.swift
//  ios
//
//  Screen: Privacy Policy. Authored for Caydex. NOTE: strong starting draft, not
//  certified legal advice — have counsel review before publishing, and consider
//  whether a named sub-processor list is required in your jurisdiction. Mirrored at
//  caydexinvest.com/privacy (documents/legal/privacy.html) for the App Store
//  Connect Privacy Policy URL (required for all apps).
//

import SwiftUI

struct PrivacyPolicyView: View {
    var body: some View {
        LegalDocumentView(
            title: "Privacy Policy",
            lastUpdated: "July 29, 2026",
            intro: "This Privacy Policy explains how Caydex (\u{201C}we,\u{201D} \u{201C}us,\u{201D} or \u{201C}our\u{201D}) collects, uses, and shares information when you use the Caydex mobile application and related services (the \u{201C}App\u{201D}). By using the App, you agree to this Policy.",
            sections: Self.sections
        )
    }

    static let sections: [LegalSection] = [
        LegalSection(
            heading: "Information We Collect",
            paragraphs: [
                "Account information: if you create an account, we collect your email address and any display name you provide.",
                "Usage information: content you create or request in the App, such as watchlists, portfolios, generated research reports, saved items, chat messages, and preferences.",
                "Holdings you enter: if you use Portfolio Insights, the share counts or position values you type in. These are self-reported by you \u{2014} we never connect to a brokerage and never see your actual account.",
                "Device information: app version, device type, and general technical/log data used for reliability and security. A push notification token if you enable notifications.",
                "An install identifier: a random identifier we generate on first launch and store in your device\u{2019}s keychain. We send it with requests to apply fair-usage limits and to keep your learning progress before you sign in. It is not derived from your device or Apple ID, is not shared with advertisers, and is not used to track you across other apps or websites. Because it lives in the keychain it can persist if you delete and reinstall the App; signing in supersedes it.",
                "We do not ask for or store your brokerage credentials, bank account numbers, or trading account access. Payment is handled by the Apple App Store; we do not receive your full payment card details."
            ]
        ),
        LegalSection(
            heading: "How We Use Information",
            paragraphs: [
                "To provide and operate the App\u{2019}s features, including generating AI research, syncing your preferences and content across devices, and maintaining your credit balance and subscription.",
                "To secure the App, prevent abuse, debug issues, and improve features and performance.",
                "To send you notifications you have enabled, and to communicate with you about your account or the service."
            ]
        ),
        LegalSection(
            heading: "AI Processing",
            paragraphs: [
                "Cay AI chat sends what you type to a third-party AI provider for processing. We ask for your explicit permission before this happens for the first time, and chat does not work until you give it. You can withdraw that permission at any time in Settings \u{2192} General \u{2022} AI Chat Data Permission; chat then stops working until you allow it again, and the rest of the App is unaffected.",
                "What is sent for chat: your message, the recent messages in that conversation, and market data for whatever you are viewing so the answer is relevant. What is NOT sent: your name, email, account identifier, watchlist, portfolio, or holdings.",
                "Generated research reports are produced from public market data. They contain no content you have typed and no information about you.",
                "Our AI provider processes this data on our behalf to return a response, and is instructed not to use it for their own purposes. Do not submit sensitive personal information you would not want processed this way. AI output is generated automatically and may be inaccurate."
            ]
        ),
        LegalSection(
            heading: "Service Providers",
            paragraphs: [
                "We rely on the following providers to run the App: Supabase (database, authentication, and file storage); Railway (application hosting); Google (the AI processing described above); Sentry (error and crash monitoring); and Apple (in-app purchases and push notification delivery).",
                "Market and financial data flows the other way \u{2014} we request it from data providers using a ticker symbol, and we do not send them anything about you. Those providers are listed in the App under Profile \u{2192} Data Sources.",
                "These providers may process your information only as needed to perform services for us and are bound by confidentiality and data-protection obligations. Our servers and these providers are located in the United States."
            ]
        ),
        LegalSection(
            heading: "How We Share Information",
            paragraphs: [
                "We do not sell your personal information. We share information only with the service providers described above, when required by law or legal process, to protect our rights or users\u{2019} safety, or in connection with a business transfer (such as a merger or acquisition), subject to this Policy."
            ]
        ),
        LegalSection(
            heading: "Data Retention",
            paragraphs: [
                "Content you create \u{2014} chat conversations, generated research reports, watchlists, holdings, learning progress, and bookmarks \u{2014} is kept until you delete it or delete your account. We do not expire it on a timer, so that your saved research stays available. You can delete individual conversations and reports at any time.",
                "Short-lived operational records are cleared automatically: fair-usage counters are deleted after 7 days, and cached market data expires on its own schedule.",
                "Deleting your account removes your account record and everything linked to it: chat conversations and messages, research reports and their generated PDF files, watchlists, portfolios and holdings, saved items, followed entities, settings, learning progress, notification tokens, credit balance, and credit history. This happens immediately, not on a delay. Error-monitoring records may persist for a short retention period at our monitoring provider; these contain diagnostic information and a pseudonymous account identifier, not your name or email.",
                "You can delete your account in the App under Profile \u{2192} Settings \u{2192} Delete Account."
            ]
        ),
        LegalSection(
            heading: "Security",
            paragraphs: [
                "We use administrative, technical, and organizational measures designed to protect your information, including encryption in transit and access controls. No method of transmission or storage is completely secure, so we cannot guarantee absolute security."
            ]
        ),
        LegalSection(
            heading: "Your Rights & Choices",
            paragraphs: [
                "Depending on your location, you may have rights to access, correct, delete, or export your personal information, or to object to or restrict certain processing. You can update your profile in the App, manage notifications in Settings, and delete your account from the App\u{2019}s settings, which removes your associated data as described above.",
                "To exercise other rights or ask a question, contact us at support@caydexinvest.com. We will respond consistent with applicable law."
            ]
        ),
        LegalSection(
            heading: "Push Notifications",
            paragraphs: [
                "If you enable notifications, we register a device token to deliver alerts you choose (such as research completion or watchlist activity). You can disable notifications at any time in the App or in your device settings."
            ]
        ),
        LegalSection(
            heading: "Children\u{2019}s Privacy",
            paragraphs: [
                "The App is not directed to children and is intended for users 18 and older. We do not knowingly collect personal information from children. If you believe a child has provided us information, contact us and we will delete it."
            ]
        ),
        LegalSection(
            heading: "International Users",
            paragraphs: [
                "We may process and store information in the United States and other countries where we or our service providers operate. By using the App, you understand your information may be transferred to jurisdictions with different data-protection laws than your own, subject to appropriate safeguards where required."
            ]
        ),
        LegalSection(
            heading: "Changes to This Policy",
            paragraphs: [
                "We may update this Policy from time to time. We will update the \u{201C}Last updated\u{201D} date and, where appropriate, provide additional notice. Your continued use of the App after changes take effect constitutes acceptance."
            ]
        ),
        LegalSection(
            heading: "Contact",
            paragraphs: [
                "Questions about this Privacy Policy? Contact us at support@caydexinvest.com."
            ]
        )
    ]
}

#Preview {
    NavigationStack {
        PrivacyPolicyView()
    }
    .preferredColorScheme(.dark)
}
