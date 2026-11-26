//
//  ChangeEmailView.swift
//  ios
//
//  Screen: change the signed-in account's email address (audit CC-09, 2026-11-26). Before this
//  there was no way to move an account to a new mailbox — someone who lost access to theirs (a
//  job ended) kept getting every reset code at a dead address.
//
//  Three steps, all in `ChangeEmailViewModel`:
//    1. Prove it is you. An account with a password types it (a stolen access token alone must
//       never be enough to move the account, and every reset code with it). An Apple/Google
//       account has no password, so it gets the recovery code Set a Password already uses,
//       mailed to the CURRENT address.
//    2. Supabase's Secure email change mails a code to the current address AND to the new one.
//    3. Enter both. The account's address changes only then.
//
//  The copy after step 2 is the same whatever happened: when another account already uses the
//  address, the server answers exactly like a real send (no code arrives anywhere). Nothing on
//  this screen may say which addresses have accounts.
//
//  Mirrors ChangePasswordView's layout and building blocks; its `.set` mode documents why the
//  recovery code's email says "Reset your Caydex password", which this screen names up front.
//

import SwiftUI
import Accessibility

struct ChangeEmailView: View {
    private enum Field: Hashable { case newEmail, currentPassword, proofCode, currentCode, newCode }

    @Environment(\.appState) private var appState
    @Environment(\.dismiss) private var dismiss
    @StateObject private var viewModel: ChangeEmailViewModel
    @FocusState private var focusedField: Field?

    init(usesPassword: Bool = true) {
        _viewModel = StateObject(wrappedValue: ChangeEmailViewModel(usesPassword: usesPassword))
    }

    /// Previews and tests only.
    init(viewModel: ChangeEmailViewModel) {
        _viewModel = StateObject(wrappedValue: viewModel)
    }

    var body: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()

            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    switch viewModel.step {
                    case .requestProofCode: requestProofCodeStep
                    case .form:             formStep
                    case .enterCodes:       enterCodesStep
                    case .done:             successState
                    }

                    Spacer(minLength: 40)
                }
                .padding(.horizontal, 24)
                .padding(.top, 16)
            }
        }
        .navigationTitle("Change Email")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(AppColors.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .onChange(of: viewModel.errorMessage) { _, message in
            if let message { Self.announce(message) }
        }
        .onChange(of: viewModel.noticeMessage) { _, message in
            if let message { Self.announce(message) }
        }
        .onChange(of: viewModel.step) { _, step in
            if step == .done { Self.announce("Email changed.") }
        }
    }

    private static func announce(_ message: String) {
        AccessibilityNotification.Announcement(message).post()
    }

    // MARK: - Steps

    /// Apple/Google account, step 1: explain, then mail the proof code to the current address.
    private var requestProofCodeStep: some View {
        VStack(alignment: .leading, spacing: 20) {
            Image(systemName: "envelope.badge.shield.half.filled")
                .font(AppTypography.iconXXL)
                .foregroundColor(AppColors.primaryBlue)
                .padding(.top, 8)
                .accessibilityHidden(true)

            Text(providerExplanation)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            // The recovery email's subject is "Reset your Caydex password". Saying so up front
            // keeps the user from ignoring it; nothing about the password changes.
            Text("We'll email a code to \(accountEmail) to confirm it's you. The email is titled “Reset your Caydex password”, but your password won't change.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)

            sendButton(.proofCode, title: "Send Code", busyLabel: "Sending code") {
                Task { await viewModel.sendProofCode(to: accountEmail) }
            }

            statusLines
        }
    }

    /// Step 1 with the proof: the new address, plus the password or the emailed code.
    private var formStep: some View {
        VStack(alignment: .leading, spacing: 20) {
            currentEmailRow

            labeled("New email") {
                TextField("", text: $viewModel.newEmail)
                    .textContentType(.emailAddress)
                    .keyboardType(.emailAddress)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .focused($focusedField, equals: .newEmail)
                    .submitLabel(.next)
                    .onSubmit { focusedField = viewModel.usesPassword ? .currentPassword : .proofCode }
                    .accessibilityLabel("New email")
            }

            if viewModel.isSameAsCurrent(accountEmail) && !viewModel.newEmail.isEmpty {
                Text("That's already the email on your account.")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.bearish)
            }

            if viewModel.usesPassword {
                labeled("Current password") {
                    SecureField("", text: $viewModel.currentPassword)
                        .textContentType(.password)
                        .focused($focusedField, equals: .currentPassword)
                        .submitLabel(.go)
                        .onSubmit(submitStart)
                        .accessibilityLabel("Current password")
                }
            } else {
                Text("Enter the code from the email we sent to \(accountEmail).")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)

                labeled("Code from email") {
                    codeField(text: $viewModel.proofCode, field: .proofCode, label: "Code from email")
                        .submitLabel(.go)
                        .onSubmit(submitStart)
                }
            }

            Text("We'll send a code to your current email and another to the new one. You'll need both.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)

            sendButton(
                .changeCodes, title: "Send Codes", busyLabel: "Sending codes",
                enabled: viewModel.canStart(currentEmail: accountEmail),
                action: submitStart
            )

            statusLines

            if !viewModel.usesPassword {
                secondaryButton(title: "Send a new code") {
                    viewModel.startOver()
                }
            }
        }
    }

    /// Step 2: the two codes.
    private var enterCodesStep: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("We sent a code to each address. Enter both to finish. If a code doesn't arrive, check spam, or start again.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            labeled("Code sent to \(accountEmail)") {
                codeField(text: $viewModel.currentCode, field: .currentCode,
                          label: "Code sent to your current email")
                    .submitLabel(.next)
                    .onSubmit { focusedField = .newCode }
            }

            labeled("Code sent to \(viewModel.pendingEmail ?? viewModel.trimmedNewEmail)") {
                codeField(text: $viewModel.newCode, field: .newCode,
                          label: "Code sent to your new email")
                    .submitLabel(.go)
                    .onSubmit(submitConfirm)
            }

            primaryButton(
                title: "Change Email", busyLabel: "Changing email",
                enabled: viewModel.canConfirm, isBusy: viewModel.isSubmitting,
                action: submitConfirm
            )

            statusLines

            secondaryButton(title: "Start again") {
                viewModel.startOver()
                focusedField = .newEmail
            }
        }
    }

    private var successState: some View {
        VStack(alignment: .leading, spacing: 12) {
            Image(systemName: "checkmark.circle.fill")
                .font(AppTypography.iconXXL)
                .foregroundColor(AppColors.bullish)
                .accessibilityHidden(true)

            Text("Email changed")
                .font(AppTypography.titleLarge)
                .foregroundColor(AppColors.textPrimary)
                .accessibilityAddTraits(.isHeader)

            Text("Your account's email is now \(viewModel.changedEmail ?? viewModel.trimmedNewEmail). Use it to sign in from now on. If you saved your password in Passwords, update the email there too.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            Button { dismiss() } label: {
                Text("Done")
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textOnAccent)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .background(AppColors.primaryFill)
                    .cornerRadius(AppCornerRadius.medium)
            }
            .padding(.top, 8)
        }
    }

    // MARK: - Shared pieces

    /// The account's current address as a read-only `.username` field: Password AutoFill fills
    /// the current password only when the form names the account. Hidden from VoiceOver as a
    /// field (edits go nowhere); the row speaks as static text instead.
    @ViewBuilder
    private var currentEmailRow: some View {
        if !accountEmail.isEmpty {
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: "person.crop.circle")
                    .font(AppTypography.iconXS)
                    .foregroundColor(AppColors.textMuted)
                    .accessibilityHidden(true)
                TextField("", text: .constant(accountEmail))
                    .textContentType(.username)
                    .keyboardType(.emailAddress)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                    .allowsHitTesting(false)
                    .accessibilityHidden(true)
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Current email")
            .accessibilityValue(accountEmail)
            .accessibilityAddTraits(.isStaticText)
        }
    }

    private func codeField(text: Binding<String>, field: Field, label: String) -> some View {
        TextField("", text: text)
            .textContentType(.oneTimeCode)
            .keyboardType(.numberPad)
            .autocorrectionDisabled()
            .focused($focusedField, equals: field)
            .accessibilityLabel(label)
    }

    /// A send button under the shared wait: every send replaces the previous code, and a 429's
    /// wait is shown as an upper bound ("up to 60 min").
    private func sendButton(
        _ kind: ChangeEmailViewModel.SendKind, title: String, busyLabel: String,
        enabled: Bool = true, action: @escaping () -> Void
    ) -> some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let wait: Int = viewModel.secondsUntilSend(kind, at: context.date)
            let upperBound: Bool = viewModel.waitIsUpperBound(kind)
            primaryButton(
                title: wait > 0 ? "\(title) (\(Self.waitLabel(seconds: wait, isUpperBound: upperBound)))" : title,
                busyLabel: busyLabel,
                enabled: enabled && wait == 0 && !accountEmail.isEmpty,
                isBusy: viewModel.isSubmitting,
                action: action
            )
        }
    }

    private static func waitLabel(seconds: Int, isUpperBound: Bool) -> String {
        let amount = seconds < 120 ? "\(seconds)s" : "\((seconds + 59) / 60) min"
        return isUpperBound ? "up to \(amount)" : amount
    }

    @ViewBuilder
    private var statusLines: some View {
        if let notice = viewModel.noticeMessage {
            Text(notice)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        if let error = viewModel.errorMessage {
            Text(error)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.bearish)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - Derived copy

    private var accountEmail: String {
        (appState.user.profile?.email ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var providerExplanation: String {
        if let label = appState.user.profile?.primaryProviderLabel {
            return "You sign in with \(label), so this account has no password to confirm it's you. We'll use a code instead."
        }
        return "This account has no password to confirm it's you. We'll use a code instead."
    }

    // MARK: - Actions

    private func submitStart() {
        guard viewModel.canStart(currentEmail: accountEmail) else { return }
        focusedField = nil
        Task { await viewModel.start(currentEmail: accountEmail) }
    }

    private func submitConfirm() {
        guard viewModel.canConfirm else { return }
        focusedField = nil
        Task {
            if await viewModel.confirm() {
                // Profile, the Settings rows and every screen that shows the address read it
                // from the profile — re-read it so none of them keeps the old one.
                await appState.refreshProfile()
            }
        }
    }

    // MARK: - Building blocks

    @ViewBuilder
    private func primaryButton(
        title: String, busyLabel: String, enabled: Bool, isBusy: Bool,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack {
                Spacer()
                if isBusy {
                    ProgressView()
                        .tint(AppColors.textOnAccent)
                        .accessibilityLabel(busyLabel)
                } else {
                    Text(title)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.textOnAccent)
                }
                Spacer()
            }
            .padding(.vertical, 14)
            .background(enabled ? AppColors.primaryFill : AppColors.primaryFill.opacity(0.4))
            .cornerRadius(AppCornerRadius.medium)
        }
        .disabled(!enabled || viewModel.isSubmitting)
    }

    private func secondaryButton(title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack {
                Spacer()
                Text(title)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.primaryBlue)
                Spacer()
            }
            .padding(.vertical, 12)
            .overlay(
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .stroke(AppColors.primaryBlue, lineWidth: 1)
            )
            // A stroke draws only the outline: without this only the words take the tap.
            .contentShape(Rectangle())
        }
        .disabled(viewModel.isSubmitting)
    }

    /// The visible caption is hidden from VoiceOver because each field carries the same words as
    /// its own `accessibilityLabel`.
    @ViewBuilder
    private func labeled(_ label: String, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .accessibilityHidden(true)
            content()
                .padding(.vertical, 12)
                .padding(.horizontal, 14)
                .cardSurface(cornerRadius: AppCornerRadius.medium)
                .foregroundColor(AppColors.textPrimary)
        }
    }
}

#Preview("Password account") {
    NavigationStack { ChangeEmailView() }
        .environment(\.appState, AppState())
}

#Preview("Apple / Google account") {
    NavigationStack { ChangeEmailView(usesPassword: false) }
        .environment(\.appState, AppState())
}

#Preview("Codes sent") {
    NavigationStack { ChangeEmailView(viewModel: .mockCodesSent) }
        .environment(\.appState, AppState())
}
