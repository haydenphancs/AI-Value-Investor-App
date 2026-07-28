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
            lastUpdated: "July 27, 2026",
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
                "Device information: a push notification token (if you enable notifications), app version, device type, and general technical/log data used for reliability and security.",
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
                "When you request AI analysis or use Cay AI chat, your query and relevant context are processed by our AI systems and by third-party artificial-intelligence and machine-learning service providers acting on our behalf to generate a response. We instruct these providers to process the data only to provide the service.",
                "Do not submit sensitive personal information you do not want processed this way. AI output is generated automatically and may be inaccurate."
            ]
        ),
        LegalSection(
            heading: "Service Providers",
            paragraphs: [
                "We rely on trusted third parties to run the App, including: cloud hosting and database infrastructure providers; market and financial data providers; third-party AI/LLM providers; analytics and error-monitoring providers; and Apple, for in-app purchases and push notification delivery.",
                "These providers may process your information only as needed to perform services for us and are bound by confidentiality and data-protection obligations."
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
                "We retain your information for as long as your account is active or as needed to provide the App, comply with legal obligations, resolve disputes, and enforce our agreements. When you delete your account, we delete or de-identify your personal information within a reasonable period, except where retention is required by law."
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
