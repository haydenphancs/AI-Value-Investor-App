//
//  MailUnavailableCard.swift
//  ios
//
//  The "this device can't send mail" contract, in ONE place.
//
//  WHY THIS EXISTS
//  ---------------
//  Every mail CTA in the app has to work on a device with no mail client — the iOS Simulator
//  ships no Mail app at all, and plenty of real devices have no account configured. Before
//  this, `Profile → Help & Support` and `Help Us Improve` were bare `mailto:` calls whose
//  failure was discarded, so both rows did nothing, silently, for the life of the file.
//
//  The fix produced a degradation pattern — always show the address as selectable text, and
//  offer a copy button when mail is unavailable — and that pattern was then hand-rolled in
//  SupportView, with a variant in ProfileView. FeedbackView would have been the third. These
//  two views are that pattern, so there is one implementation to keep correct.
//
//  `ProfileView` deliberately still uses an `.alert` instead: it has no card to degrade INTO,
//  and it is presented as a `.fullScreenCover`, so the global toast overlay on `RootView`
//  draws behind it and cannot be seen.
//

import SwiftUI

/// The support address, always selectable.
///
/// Selectable **unconditionally**, not only in the failure state: a user on a device that
/// cannot send mail at all still needs to be able to get the address out, and that is the
/// whole point of the screens this appears on.
struct SupportAddressText: View {
    var address: String = AppInfo.supportEmail

    var body: some View {
        Text(address)
            .font(AppTypography.bodyEmphasis)
            .foregroundColor(AppColors.primaryBlue)
            .textSelection(.enabled)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// Shown in place of a Send/Email button when the device has no mail client.
///
/// - Parameters:
///   - payload: the text the button hands off. The address alone on the Support screen; the
///     whole drafted report on the Feedback screen, so the user's typing is not lost just
///     because their device cannot mail it.
///   - message: why the Send button is gone.
///   - action: `.copy` or `.share`. These are genuinely different needs, not a style choice —
///     see the doc on `Action`.
struct MailUnavailableCard: View {

    enum Action {
        /// Put it on the pasteboard. Right for a SHORT string the user will obviously paste
        /// somewhere themselves — an email address.
        case copy(label: String)

        /// Open the system share sheet. Right for a whole REPORT.
        ///
        /// "Copied" on its own is a dead end: it tells the user something happened and nothing
        /// about what to do next, which is exactly how this read on a device with no Mail app.
        /// The share sheet gives them a real destination — Gmail, Messages, Notes, whatever
        /// they actually use — instead of a clipboard and a hope.
        case share(label: String)
    }

    let payload: String
    var message: String
    var action: Action

    /// Extra items for the SHARE path — the screenshot, when there is one.
    ///
    /// Ignored by `.copy` on purpose: the pasteboard cannot carry a string and an image
    /// together, and silently dropping the attachment while the UI says "attached" is exactly
    /// the kind of quiet lie this screen exists to stop.
    var shareAttachments: [Any] = []

    @State private var didCopy = false
    @State private var isSharing = false

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text(message)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)

            switch action {
            case .copy(let label):
                Button {
                    UIPasteboard.general.string = payload
                    didCopy = true
                    Haptics.success()
                } label: {
                    filled(title: didCopy ? "Copied" : label,
                           icon: didCopy ? "checkmark" : "doc.on.doc")
                }
                .buttonStyle(PlainButtonStyle())

            case .share(let label):
                Button {
                    isSharing = true
                } label: {
                    filled(title: label, icon: "square.and.arrow.up")
                }
                .buttonStyle(PlainButtonStyle())
                .sheet(isPresented: $isSharing) {
                    ShareSheet(items: [payload] + shareAttachments)
                }
            }
        }
    }

    /// The app's filled CTA recipe. Hand-rolled deliberately: there is no `ButtonStyle` in this
    /// codebase and the same shape is repeated at ~26 sites, so extracting an atom belongs in
    /// its own change across all of them, not smuggled in here.
    private func filled(title: String, icon: String) -> some View {
        Label(title, systemImage: icon)
            .font(AppTypography.bodyEmphasis)
            // `textOnAccent` on `primaryFill`, never `textPrimary` on `primaryBlue` —
            // see the fill-vs-text token rules in .claude/rules/ios-swiftui.md.
            .foregroundColor(AppColors.textOnAccent)
            .frame(maxWidth: .infinity)
            .padding(.vertical, AppSpacing.md)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .fill(AppColors.primaryFill)
            )
    }
}

#Preview {
    VStack(alignment: .leading, spacing: AppSpacing.lg) {
        SupportAddressText()
        MailUnavailableCard(
            payload: AppInfo.supportEmail,
            message: "No email app is set up on this device. Copy the address and write to us from wherever you read mail.",
            action: .copy(label: "Copy address")
        )
        MailUnavailableCard(
            payload: "Something went wrong…\n\n---\nApp: Caydex 1.0 (1)",
            message: "This device has no email app set up. Share your report and send it to us however you like — nothing you typed is lost.",
            action: .share(label: "Share report")
        )
    }
    .padding()
    .background(AppColors.background)
}
