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

import SwiftUI
import PhotosUI

struct FeedbackView: View {
    @Environment(\.appState) private var appState

    @State private var message = ""
    @State private var showingComposer = false
    @State private var didSend = false
    @FocusState private var isEditorFocused: Bool

    /// The picked screenshot. `PhotosPicker` runs OUT OF PROCESS, so choosing one grants access
    /// to that single image and nothing else — which is why this needs no
    /// `NSPhotoLibraryUsageDescription` and shows no permission prompt.
    @State private var pickedItem: PhotosPickerItem?
    @State private var screenshot: Data?
    @State private var screenshotPreview: UIImage?
    @State private var attachmentFailed = false

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
                subject: "Caydex feedback",
                body: reportBody,
                attachments: screenshot.map { [MailComposeView.Attachment.image($0)] } ?? []
            ) { sent in
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
            Text("Your report is on its way. We read every one, and we'll get to work on it.")
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

            ArticleCalloutBox(
                icon: "heart.fill",
                text: "We read every report, and we fix what we can as fast as we can. Thank you for helping us build this properly.",
                style: .success
            )
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Steps

    private struct Step: Identifiable {
        let id = UUID()
        let title: String
        let detail: String
    }

    private static let stepList: [Step] = [
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

    private var steps: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("How to report it")
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)

            VStack(alignment: .leading, spacing: AppSpacing.lg) {
                ForEach(Array(Self.stepList.enumerated()), id: \.element.id) { index, step in
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

            Text("We'll include your app version and device model so we know which build to look at. You can see and edit all of it before it sends.")
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
                    shareAttachments: screenshotPreview.map { [$0] } ?? []
                )
            }
        }
        .padding(.horizontal, AppSpacing.lg)
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
    /// Built in exactly one place and used by both the composer and the copy-to-pasteboard
    /// fallback, so the two can never say different things.
    private var reportBody: String {
        let written = message.trimmingCharacters(in: .whitespacesAndNewlines)
        return """
        \(written)


        \(AppInfo.diagnosticsBlock(
            tier: appState.user.tier.rawValue,
            isSignedIn: appState.auth.isAuthenticated
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
