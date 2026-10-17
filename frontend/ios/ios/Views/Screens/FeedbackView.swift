//
//  FeedbackView.swift
//  ios
//
//  Screen: Help Us Improve — report a problem or send an idea.
//
//  WHY THIS EXISTS
//  ---------------
//  The row used to fire a bare `mailto:` with a fixed subject and no body. Two things were
//  wrong with that. It did nothing at all on a device with no mail client (the Simulator
//  always) because the open's failure was discarded. And even where it worked, it dropped the
//  user into an empty compose window with no idea what to write, so the reports that arrived
//  were usually untriageable — no build number, no OS version, no steps.
//
//  DESIGN NOTE — the description is written ONCE
//  --------------------------------------------
//  The text field on this screen is the ONLY place the user types. On Send, that text becomes
//  the mail body and the composer opens ready to go. The composer deliberately does NOT open
//  with a "What happened: / What I expected:" template, because the user would then see empty
//  prompts under text they already wrote and reasonably conclude they had to type it twice.
//
//  The steps tell them WHAT to include; the field is WHERE they include it.
//
//  THE SCREENSHOT PICKER AND THE PRIVACY FILING SHIP TOGETHER
//  ----------------------------------------------------------
//  This screen reads one image from the photo library, so "Photos or Videos" is a DECLARED
//  collected data type. Four surfaces have to agree, and they were all updated together on
//  2026-08-07: `PrivacyInfo.xcprivacy`, `documents/legal/app-privacy-answers.md`, the privacy
//  policy (all three copies — the byte-identical `documents/` + `backend/templates/` pair and
//  the `PrivacyPolicyView.swift` mirror), and the App Store Connect questionnaire, which is
//  the one no test can reach and is noted in LAUNCH_CHECKLIST §7.
//
//  `test_ios_feedback_flow.py::test_photos_usage_and_the_privacy_filing_agree` fails the build
//  if the code and the machine-readable filing disagree **in either direction** — remove the
//  picker without undeclaring, or declare without shipping a picker, and it goes red. So if you
//  ever take the picker out, take the declaration out with it.
//
//  `PhotosPicker` runs out of process: it hands back the one image the user selected and
//  nothing else, which is why there is no permission prompt and no
//  `NSPhotoLibraryUsageDescription`. That is about ACCESS, not collection — the image still
//  reaches us by email, and that is what the filing is about.
//
//  THE REWARD, THE REPORT CODE AND THE ACCOUNT ID
//  ----------------------------------------------
//  A signed-in user can earn free credits for a report we CONFIRM. Nothing on this screen
//  grants anything: credits move only when the owner checks the report by hand and then runs
//  `backend/scripts/grant_feedback_reward.py --apply`. So the report carries two things the
//  owner needs for that: a random report code (the script's `--ref`; the same account cannot be
//  credited twice for one code) and, for an account, its Account ID — the sender address is
//  often a Hide My Email relay that matches no account. Guests can still report; they are told
//  the reward needs an account. Every reward string lives in `FeedbackRewardCopy`, and the path
//  from the "Rate Caydex" prompt opens this screen with `showsReward: false`, because Apple
//  forbids rewarding ratings.
//

import SwiftUI
import PhotosUI

/// Every user-facing string that mentions the reward, in one place.
///
/// Credits are NEVER automatic: the owner confirms a report by hand and only then grants them
/// with a script. So this copy promises a review, not a reward — no amount, no time frame, and
/// it says plainly that not every report qualifies. It is never tied to ratings or reviews
/// either (App Store Guideline 3.2.2), which is why `FeedbackView(showsReward: false)` shows
/// none of it on the path that starts at the rating prompt.
///
/// `test_ios_feedback_flow.py` pins the required phrases, the banned ones (numbers, "instantly",
/// "rating", …) and that no other literal in this file mentions credits.
enum FeedbackRewardCopy {
    static let rowSubtitle = "Report an issue · earn free credits"
    static let callout = "Earn free credits. We review every report. If we can confirm the issue, we'll add credits to your account and get back to you as soon as we've reviewed it. Not every report qualifies — for example, duplicates or issues we can't reproduce."
    static let guestNote = "Confirmed reports from signed-in accounts can earn free credits. We add them to an account, so sign in before you send if you'd like to be eligible."
    static let reviewStepTitle = "We review it"
    static let reviewStepDetail = "If we can confirm the issue, we'll add free credits to your account and get back to you as soon as we've reviewed it. Duplicates and issues we can't reproduce don't qualify."
    static let thankYou = "Your report is on its way. We review every report. If we can confirm the issue, we'll add credits to your account and get back to you as soon as we've reviewed it."
    static let composerCaption = "We'll include your app version, device model, account ID and a report code, so we know which build to look at and which account to credit. You can see and edit all of it before it sends."
}

struct FeedbackView: View {
    @Environment(\.appState) private var appState

    /// Whether this screen may mention the reward at all. False on the path from the "Rate
    /// Caydex" prompt: a reward offered one tap after a rating prompt reads as paying for a
    /// rating, which Apple forbids.
    private let showsReward: Bool

    @State private var message = ""
    @State private var showingComposer = false
    @State private var didSend = false
    @FocusState private var isEditorFocused: Bool

    /// Names this report in the subject and the body, so a confirmed report can be credited
    /// exactly once. Replaced every time a report leaves the screen — the composer closing for
    /// any reason (a saved draft is a report that may still be sent), or the share sheet
    /// closing — so two reports never share a code.
    @State private var reportCode = AppInfo.newReportCode()

    /// Whether the report that just went out was offered the reward, frozen when the composer
    /// opened. The thank-you alert reads this rather than `canEarnReward`, which could flip if
    /// the profile arrived while the composer was up — and promise credits for a report that
    /// left without an Account ID.
    @State private var sentWithReward = false

    /// The picked screenshot. `PhotosPicker` runs OUT OF PROCESS, so choosing one grants access
    /// to that single image and nothing else — which is why this needs no
    /// `NSPhotoLibraryUsageDescription` and shows no permission prompt.
    @State private var pickedItem: PhotosPickerItem?
    @State private var screenshot: Data?
    @State private var screenshotPreview: UIImage?
    @State private var attachmentFailed = false

    /// Explicit because `showsReward` is private with no default, which makes the memberwise
    /// init private too, and the call sites need it. Defaults to true: Profile's row is the
    /// ordinary way in.
    init(showsReward: Bool = true) {
        self.showsReward = showsReward
    }

    /// The account a confirmed report would be credited to, or nil for a guest. The same source as
    /// `AppActions.currentAccountId` and Profile's row gate: the profile is kept through a
    /// mid-session `.restoring` window, so a reconnecting user is not mistaken for a guest. A cold
    /// launch that is still reconnecting has no profile yet — no Account ID to send, so no reward
    /// copy, and no "sign in" note either (see `isReconnecting`).
    private var accountId: String? { appState.user.profile?.id }

    /// The reward copy needs both: a path where it may appear, and an account to add credits to.
    private var canEarnReward: Bool { showsReward && accountId != nil }

    /// Signed in, but the session is still healing. Not a guest: telling them to sign in would be
    /// wrong (`.claude/rules/auth.md` §5), so with no profile yet the screen is simply neutral.
    private var isReconnecting: Bool { appState.auth.status == .restoring }

    /// Enough to be worth sending. Guards against an accidental empty tap, not against short
    /// reports — "chart is blank on AAPL" is a perfectly good bug report.
    private var canSend: Bool {
        message.trimmingCharacters(in: .whitespacesAndNewlines).count >= 3
    }

    var body: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()

            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: AppSpacing.xl) {
                    intro
                    steps
                    composer
                    Spacer().frame(height: AppSpacing.xxxl)
                }
                .padding(.top, AppSpacing.lg)
            }
            // Tapping the background dismisses the keyboard — without this the Send button can
            // sit under it on a small device with the field expanded.
            .scrollDismissesKeyboard(.interactively)
        }
        .navigationTitle("Help Us Improve")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(AppColors.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .sheet(isPresented: $showingComposer) {
            MailComposeView(
                recipient: AppInfo.supportEmail,
                subject: "Caydex feedback · \(reportCode)",
                body: reportBody,
                attachments: screenshot.map { [MailComposeView.Attachment.image($0)] } ?? []
            ) { sent in
                // Sent, saved as a draft, cancelled or failed: a fresh code either way, so a draft
                // finished later in Mail and the next report from here never share one.
                reportCode = AppInfo.newReportCode()
                if sent {
                    didSend = true
                    message = ""
                    clearScreenshot()
                }
            }
            .ignoresSafeArea()
        }
        .alert("Thank you", isPresented: $didSend) {
            Button("You're welcome", role: .cancel) {}
        } message: {
            Text(sentWithReward
                 ? FeedbackRewardCopy.thankYou
                 : "Your report is on its way. We read every one, and we'll get to work on it.")
        }
        .alert("Couldn't attach that", isPresented: $attachmentFailed) {
            Button("OK", role: .cancel) {}
        } message: {
            Text("That image couldn't be read. Try picking it again, or send the report without it — the description is the part we need most.")
        }
        // Loading the picked image is async and CAN fail (an iCloud photo that isn't downloaded,
        // a format the decoder rejects). Silence there would look exactly like the bug this whole
        // screen exists to fix, so it reports.
        .onChange(of: pickedItem) { _, item in
            guard let item else { return }
            Task {
                if let data = try? await item.loadTransferable(type: Data.self),
                   let preview = UIImage(data: data) {
                    screenshot = data
                    screenshotPreview = preview
                } else {
                    clearScreenshot()
                    attachmentFailed = true
                }
            }
        }
    }

    private func clearScreenshot() {
        pickedItem = nil
        screenshot = nil
        screenshotPreview = nil
    }

    // MARK: - Intro

    private var intro: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("Found something wrong? Tell us.")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
                .fixedSize(horizontal: false, vertical: true)

            Text("Caydex gets better because people take the time to report what's broken or missing. It genuinely helps, and we don't take it for granted.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            if canEarnReward {
                ArticleCalloutBox(
                    icon: "gift.fill",
                    text: FeedbackRewardCopy.callout,
                    style: .success
                )
            } else {
                ArticleCalloutBox(
                    icon: "heart.fill",
                    text: "We read every report, and we fix what we can as fast as we can. Thank you for helping us build this properly.",
                    style: .success
                )
                // A guest on the ordinary path is told why there is no reward for them, rather
                // than finding out after sending. Never on the rating path (`showsReward`), and
                // never to a signed-in user whose profile has not loaded yet (`isReconnecting`).
                if showsReward && !isReconnecting {
                    Text(FeedbackRewardCopy.guestNote)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Steps

    private struct Step: Identifiable {
        let id = UUID()
        let title: String
        let detail: String
    }

    private static let baseSteps: [Step] = [
        Step(
            title: "Take a screenshot",
            detail: "Press the side button and volume up together while the problem is on screen. You'll attach it below — it shows us far more than words can."
        ),
        Step(
            title: "Describe what happened",
            detail: "What you were doing, what you expected, and what you saw instead. If it involves a specific ticker or screen, tell us which."
        ),
        Step(
            title: "Attach it and send",
            detail: "Attach the screenshot below, then tap Send Report. Your mail opens with everything already in it — the description, the screenshot, and which build you're on."
        )
    ]

    /// The last step says what happens AFTER sending: the reward version only where
    /// `canEarnReward`, a neutral one everywhere else (guests, and the path from the rating
    /// prompt). Two fixed lists rather than one built in `body`, because `Step.id` is a fresh
    /// UUID — a list rebuilt per render would hand `ForEach` new identities every time.
    private static let rewardStepList: [Step] = baseSteps + [
        Step(
            title: FeedbackRewardCopy.reviewStepTitle,
            detail: FeedbackRewardCopy.reviewStepDetail
        )
    ]

    private static let neutralStepList: [Step] = baseSteps + [
        Step(
            title: "We review it",
            detail: "We read every report, fix what we can, and get back to you as soon as we've reviewed it."
        )
    ]

    private var stepList: [Step] {
        canEarnReward ? Self.rewardStepList : Self.neutralStepList
    }

    private var steps: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("How to report it")
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)

            VStack(alignment: .leading, spacing: AppSpacing.lg) {
                ForEach(Array(stepList.enumerated()), id: \.element.id) { index, step in
                    HStack(alignment: .top, spacing: AppSpacing.md) {
                        // Explicit tokens: NumberedBadge's DEFAULTS are `textPrimary` on
                        // `primaryBlue`, which is a text token on a fill — white on #60A5FA is
                        // ~2.2:1 in dark mode. See the fill-vs-text rules in ios-swiftui.md.
                        NumberedBadge(
                            number: index + 1,
                            size: 26,
                            backgroundColor: AppColors.primaryFill,
                            textColor: AppColors.textOnAccent
                        )
                        .padding(.top, 2)

                        VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                            Text(step.title)
                                .font(AppTypography.bodyEmphasis)
                                .foregroundColor(AppColors.textPrimary)
                            Text(step.detail)
                                .font(AppTypography.bodySmall)
                                .foregroundColor(AppColors.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
            .padding(AppSpacing.lg)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .cardFill()
            )
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Composer

    private var composer: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("What happened?")
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)

            // A growing TextField, matching ChatInputBar — the app's only multi-line entry.
            // NOT a TextEditor: it has no placeholder, needs its background fought, and would
            // be the first in the codebase with no styling precedent to follow.
            TextField(
                "The chart on AAPL was blank after I switched to 1Y…",
                text: $message,
                axis: .vertical
            )
            .font(AppTypography.body)
            .foregroundColor(AppColors.textPrimary)
            .autocapitalization(.sentences)
            .disableAutocorrection(false)
            .focused($isEditorFocused)
            .lineLimit(4...12)
            .padding(.horizontal, AppSpacing.lg)
            .padding(.vertical, AppSpacing.md)
            .cardSurface(cornerRadius: AppCornerRadius.large, elevation: .raised)
            .contentShape(Rectangle())
            .onTapGesture { isEditorFocused = true }

            screenshotPicker

            Text(composerCaption)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)

            if MailComposeView.canSendMail {
                sendButton
            } else {
                // Checked UP FRONT, not after a dead tap — the advantage `canSendMail()` has
                // over `mailto:`. The payload is the whole report, so their typing survives.
                SupportAddressText()
                MailUnavailableCard(
                    payload: reportBody,
                    message: "This device has no email app set up. Send your report to us however you like — pick Mail, Messages, or anything else. Nothing you typed is lost.",
                    action: .share(label: "Share report"),
                    shareAttachments: screenshotPreview.map { [$0] } ?? [],
                    // The share sheet cannot say whether anything was sent, so retire the code
                    // whenever it closes: a second report shared from here gets its own.
                    onShareDismissed: { reportCode = AppInfo.newReportCode() }
                )
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    /// Says what the diagnostics block will contain, so it must track `reportBody`: the account ID
    /// is named exactly when there is one to send. Only the reward path says why ("which account
    /// to credit"); a signed-in user who came from the rating prompt sends an Account ID too, but
    /// must not be told about the reward.
    private var composerCaption: String {
        if canEarnReward {
            return FeedbackRewardCopy.composerCaption
        }
        if accountId != nil {
            return "We'll include your app version, device model, account ID and a report code so we know which build to look at. You can see and edit all of it before it sends."
        }
        return "We'll include your app version, device model and a report code so we know which build to look at. You can see and edit all of it before it sends."
    }

    // MARK: - Screenshot

    /// Optional, and framed that way. `PhotosPicker` runs out of process: choosing an image
    /// hands this app that one image and nothing else, which is why there is no permission
    /// prompt and no `NSPhotoLibraryUsageDescription`. The image is still declared in the App
    /// Privacy filing, because it does reach us by email.
    @ViewBuilder
    private var screenshotPicker: some View {
        if let preview = screenshotPreview {
            HStack(spacing: AppSpacing.md) {
                Image(uiImage: preview)
                    .resizable()
                    .scaledToFill()
                    .frame(width: 44, height: 44)
                    .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.small))

                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text("Screenshot attached")
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                    Text("It'll be sent with your report.")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }

                Spacer()

                Button("Remove") { clearScreenshot() }
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.loss)
            }
            .padding(AppSpacing.md)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .cardFill()
            )
        } else {
            PhotosPicker(selection: $pickedItem, matching: .images, photoLibrary: .shared()) {
                Label("Attach a screenshot (optional)", systemImage: "photo.on.rectangle.angled")
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.primaryBlue)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, AppSpacing.md)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .strokeBorder(AppColors.primaryBlue.opacity(0.35), lineWidth: 1)
                    )
            }
        }
    }

    private var sendButton: some View {
        Button {
            isEditorFocused = false
            sentWithReward = canEarnReward
            showingComposer = true
        } label: {
            Label("Send Report", systemImage: "paperplane.fill")
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(canSend ? AppColors.textOnAccent : AppColors.textDisabled)
                .frame(maxWidth: .infinity)
                .padding(.vertical, AppSpacing.md)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                        .fill(canSend ? AppColors.primaryFill : AppColors.cardBackgroundLight)
                )
        }
        .buttonStyle(PlainButtonStyle())
        .disabled(!canSend)
    }

    // MARK: - The report

    /// The mail body: the user's own words, then the diagnostics.
    ///
    /// Built in exactly one place and used by both the composer and the share fallback, so the
    /// two can never say different things — including the report code and Account ID a
    /// confirmed report is credited by.
    private var reportBody: String {
        let written = message.trimmingCharacters(in: .whitespacesAndNewlines)
        return """
        \(written)


        \(AppInfo.diagnosticsBlock(
            tier: appState.user.tier.rawValue,
            isSignedIn: appState.auth.isAuthenticated,
            accountId: accountId,
            reportCode: reportCode
        ))
        """
    }
}

#Preview {
    NavigationStack {
        FeedbackView()
    }
    .environment(\.appState, AppState())
}
