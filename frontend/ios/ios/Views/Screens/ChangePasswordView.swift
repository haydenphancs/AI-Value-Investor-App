//
//  ChangePasswordView.swift
//  ios
//
//  Screen: change the password of a signed-in user — or CREATE one, for an account that has
//  none.
//
//  `.change` requires the CURRENT password even though the caller is already authenticated.
//  Without that, a stolen access token would be enough to take permanent ownership of the
//  account by changing its password — the backend enforces the same rule.
//
//  `.set` exists because an Apple/Google account has no password at all: Supabase provisions it
//  through `sign_in_with_id_token` and one is never written, so `auth.users.encrypted_password`
//  is NULL. Those users were shown the `.change` form and told **"Your current password is
//  incorrect"** about a password that has never existed — and burned one of five attempts per
//  15 minutes each time they tried. A TestFlight tester found it by asking the obvious question.
//
//  `.set` cannot simply drop the Current-password field: something has to replace the proof it
//  was providing, or a stolen token becomes account ownership by a shorter route. That proof is
//  the code emailed to the account address — the same recovery OTP the signed-out
//  ForgotPassword flow uses, via the unchanged `POST /auth/forgot-password`. Its length is a
//  project-wide Supabase setting (8 digits today; the backend accepts 6-12), so the copy names
//  no digit count: "a 6-digit code" told users to expect a shorter code than they received.
//
//  Both modes gate the new password on `PasswordRule` — the rule the server enforces on
//  `ChangePasswordRequest` and `SetPasswordRequest` — and show it as a live checklist. `.change`
//  used to gate on `count >= 8` alone, so its button enabled for a password the server refused.
//  Pinned by backend/tests/test_password_rule_parity.py.
//
//  On `.set`, once the server has VERIFIED the code it is spent, whatever happens next. So
//  AUTH_CODE_USED (the password could not be saved) and AUTH_PASSWORD_REJECTED (it was
//  refused) both clear the code field and leave the user on the code step with "Send a new
//  code" — never a "Please try again" that can only meet AUTH_CODE_INVALID.
//
//  ⚠️ It does NOT reuse `ForgotPasswordView`, which posts to `/auth/reset-password` and gets
//  back a bare message. That stamps `password_changed_at` and evicts every token minted before
//  it — including this session's — so a SIGNED-IN user completing it would be silently signed
//  out. `/auth/set-password` re-mints this caller's tokens after the stamp; `AuthService`
//  adopts them.
//

import SwiftUI
import Accessibility
import OSLog

struct ChangePasswordView: View {
    /// Which job this screen is doing. Defaults to `.change`, so every existing call site and
    /// the preview keep their exact previous behaviour.
    enum Mode {
        /// The account has a password. Prove it, then replace it.
        case change
        /// The account has none (Apple/Google). Prove the mailbox with an emailed code instead.
        case set
    }

    /// Two-step flow, `.set` only. `.change` never leaves `.form`.
    private enum Step { case form, requestCode, enterCode }
    private enum Field: Hashable { case currentPassword, code, newPassword, confirmPassword }

    let mode: Mode

    @Environment(\.appState) private var appState
    @Environment(\.dismiss) private var dismiss

    @State private var step: Step
    @State private var currentPassword = ""
    @State private var code = ""
    @State private var newPassword = ""
    @State private var confirmPassword = ""
    @State private var isSubmitting = false
    @State private var isResending = false
    @State private var errorMessage: String?
    /// A non-error status line (a send was asked for), announced like the error.
    @State private var noticeMessage: String?
    /// When sending a code unlocks again (`.set`), keyed by `cooldownKey(_:)`. Every send
    /// replaces the previous code, so a tap while the first email is in flight kills the code
    /// the user is about to receive. Both Send buttons honour it, and a 429 sets it too. Keyed
    /// by address, like `ForgotPasswordView`, so the wait always belongs to the mailbox it was
    /// earned on.
    @State private var sendCooldowns: [String: SendCooldown] = [:]
    @State private var didChange = false
    @FocusState private var focusedField: Field?

    private struct SendCooldown {
        let until: Date
        /// True when the wait came from a 429's `Retry-After`. The server sends the denying
        /// limiter's WHOLE window there, not the time left in it, so the wait is shown as an
        /// upper bound ("up to 60 min") — never as an exact countdown.
        let isUpperBound: Bool
    }

    private static let log = Logger(subsystem: "com.phan.caydex", category: "auth")
    /// The wait after every send the server accepted.
    private static let sendCooldownSeconds = 60
    /// The most a 429 can hold a Send button: the longest window any credential limiter uses
    /// (a recovery email is 3 per address per hour), so a malformed `Retry-After` cannot lock
    /// the button for the life of the screen.
    private static let rateLimitWaitCapSeconds = 3600

    init(mode: Mode = .change) {
        self.mode = mode
        _step = State(initialValue: mode == .set ? .requestCode : .form)
    }

    var body: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()

            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    if didChange {
                        successState
                    } else {
                        switch step {
                        case .form:        form
                        case .requestCode: requestCodeStep
                        case .enterCode:   enterCodeStep
                        }
                    }

                    Spacer(minLength: 40)
                }
                .padding(.horizontal, 24)
                .padding(.top, 16)
            }
        }
        .navigationTitle(mode == .set ? "Set a Password" : "Change Password")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(AppColors.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        // VoiceOver users never see a red line appear: say it. Same for the status line and the
        // terminal success, which otherwise change the screen in silence.
        .onChange(of: errorMessage) { _, message in
            if let message { Self.announce(message) }
        }
        .onChange(of: noticeMessage) { _, message in
            if let message { Self.announce(message) }
        }
        .onChange(of: didChange) { _, changed in
            if changed { Self.announce(mode == .set ? "Password set." : "Password changed.") }
        }
    }

    private static func announce(_ message: String) {
        AccessibilityNotification.Announcement(message).post()
    }

    // MARK: - Steps

    /// `.change` — current password, then the new one.
    private var form: some View {
        VStack(alignment: .leading, spacing: 20) {
            accountUsernameField

            labeled("Current password") {
                SecureField("", text: $currentPassword)
                    .textContentType(.password)
                    .focused($focusedField, equals: .currentPassword)
                    .submitLabel(.next)
                    .onSubmit { focusedField = .newPassword }
                    .accessibilityLabel("Current password")
            }

            newPasswordFields

            // The full rule as a live checklist — the one the server enforces on
            // `ChangePasswordRequest` — instead of "At least 8 characters", a subset of it.
            PasswordRequirementsView(password: newPassword)

            Text("Other devices will need to sign in again.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)

            mismatchNotice

            primaryButton(
                title: "Change Password", busyLabel: "Changing password",
                enabled: canSubmitChange, isBusy: isSubmitting, action: submitChange
            )

            statusLines
        }
    }

    /// `.set` step 1 — explain why there is no Current-password field, and send the code.
    private var requestCodeStep: some View {
        VStack(alignment: .leading, spacing: 20) {
            Image(systemName: "lock.badge.clock")
                .font(AppTypography.iconXXL)
                .foregroundColor(AppColors.primaryBlue)
                .padding(.top, 8)
                .accessibilityHidden(true)

            Text(providerExplanation)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            // Naming the exact address matters for Hide My Email: an Apple private-relay user
            // sees an @privaterelay.appleid.com address here and would otherwise assume the
            // code had gone somewhere they cannot read.
            Text("We'll email a code to \(accountEmail) to confirm it's you.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)

            sendCodeButton

            statusLines
        }
    }

    /// Step 1's Send Code, under the same wait as "Send a new code". A 429 used to leave it
    /// live, and every further tap the server refused still spent one of the caller's per-IP
    /// recovery slots — shared by everyone behind the same carrier NAT or Wi-Fi.
    private var sendCodeButton: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let wait: Int = secondsUntilSend(to: accountEmail, at: context.date)
            let upperBound = waitIsUpperBound(for: accountEmail)
            primaryButton(
                title: wait > 0
                    ? "Send Code (\(Self.waitLabel(seconds: wait, isUpperBound: upperBound)))"
                    : "Send Code",
                busyLabel: "Sending code",
                enabled: !accountEmail.isEmpty && wait == 0, isBusy: isSubmitting
            ) {
                requestCode(isResend: false)
            }
            .accessibilityHint(wait > 0 ? Self.waitHint(seconds: wait, isUpperBound: upperBound) : "")
        }
    }

    /// `.set` step 2 — the code plus the new password.
    private var enterCodeStep: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Enter the code from the email we sent to \(accountEmail), then choose your password.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            accountUsernameField

            labeled("Code from email") {
                TextField("", text: $code)
                    .textContentType(.oneTimeCode)
                    .keyboardType(.numberPad)
                    .autocorrectionDisabled()
                    .focused($focusedField, equals: .code)
                    .submitLabel(.next)
                    .onSubmit { focusedField = .newPassword }
                    .accessibilityLabel("Code from email")
            }

            newPasswordFields

            // The full rule as a live checklist, the one `_validate_password_strength`
            // enforces server-side, so the button's state is never a surprise.
            PasswordRequirementsView(password: newPassword)

            Text("You'll still be able to sign in with \(providerName) as well.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)

            mismatchNotice

            primaryButton(
                title: "Set Password", busyLabel: "Setting password",
                enabled: canSubmitSet, isBusy: isSubmitting, action: submitSet
            )

            statusLines

            resendButton
        }
    }

    private var successState: some View {
        VStack(alignment: .leading, spacing: 12) {
            Image(systemName: "checkmark.circle.fill")
                .font(AppTypography.iconXXL)
                .foregroundColor(AppColors.bullish)
                .accessibilityHidden(true)

            Text(mode == .set ? "Password set" : "Password changed")
                .font(AppTypography.titleLarge)
                .foregroundColor(AppColors.textPrimary)
                .accessibilityAddTraits(.isHeader)

            Text(mode == .set
                 ? "You can now sign in with your email and password, as well as with \(providerName). Any other devices signed in to this account will need to sign in again."
                 : "Your password has been updated. Any other devices signed in to this account will need to sign in again.")
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

    private var newPasswordFields: some View {
        Group {
            labeled("New password") {
                SecureField("", text: $newPassword)
                    .textContentType(.newPassword)
                    .focused($focusedField, equals: .newPassword)
                    .submitLabel(.next)
                    .onSubmit { focusedField = .confirmPassword }
                    .accessibilityLabel("New password")
            }

            labeled("Confirm new password") {
                SecureField("", text: $confirmPassword)
                    .textContentType(.newPassword)
                    .focused($focusedField, equals: .confirmPassword)
                    .submitLabel(.go)
                    .onSubmit(submitFromKeyboard)
                    .accessibilityLabel("Confirm new password")
            }
        }
    }

    /// The account's email in a `.username` field, on both forms.
    ///
    /// Password AutoFill can only save or UPDATE a credential when the form holding the new
    /// password also holds the account name. Without it the old password stayed saved in
    /// Passwords and AutoFill kept filling it at sign-in, which then failed. Read-only (it is
    /// this account's address) and visually minimal; omitted while the profile is not loaded,
    /// because an empty username field would bind the credential to nothing.
    ///
    /// The FIELD is hidden from accessibility: VoiceOver announced it as an editable "text
    /// field" whose edits went nowhere. AutoFill reads `textContentType`, not the
    /// accessibility tree. The row speaks instead, as static text: "Account email", then the
    /// same address the field shows on screen — on `.change` it is the only place the screen
    /// names the account.
    @ViewBuilder
    private var accountUsernameField: some View {
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
            .accessibilityLabel("Account email")
            .accessibilityValue(accountEmail)
            .accessibilityAddTraits(.isStaticText)
        }
    }

    /// "Send a new code" on the `.set` code step, with a cooldown after every send and after a
    /// 429. It is the way forward from an invalid or expired code (AUTH_CODE_INVALID) and from
    /// a code the server already used up (AUTH_CODE_USED, AUTH_PASSWORD_REJECTED).
    private var resendButton: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let wait: Int = secondsUntilSend(to: accountEmail, at: context.date)
            let upperBound = waitIsUpperBound(for: accountEmail)
            Button {
                requestCode(isResend: true)
            } label: {
                HStack(spacing: AppSpacing.sm) {
                    Spacer()
                    if isResending {
                        ProgressView()
                            .tint(AppColors.primaryBlue)
                            .accessibilityLabel("Sending a new code")
                    } else {
                        Text(Self.resendTitle(wait: wait, isUpperBound: upperBound))
                            .font(AppTypography.bodyEmphasis)
                            .foregroundColor(AppColors.primaryBlue)
                    }
                    Spacer()
                }
                .padding(.vertical, 12)
                .overlay(
                    RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                        .stroke(AppColors.primaryBlue, lineWidth: 1)
                )
                // A stroke draws only the outline: without this the whole interior (Spacers and
                // padding) is dead and only the words themselves take the tap.
                .contentShape(Rectangle())
            }
            .disabled(wait > 0 || isResending || isSubmitting || accountEmail.isEmpty)
            .accessibilityHint(Self.resendHint(wait: wait, isUpperBound: upperBound))
        }
    }

    private static func resendTitle(wait: Int, isUpperBound: Bool) -> String {
        wait > 0
            ? "Send a new code (\(waitLabel(seconds: wait, isUpperBound: isUpperBound)))"
            : "Send a new code"
    }

    /// Hedged: the server answers a send it refused with the same 200 as one it made, so the
    /// hint never promises that the code in hand is about to die.
    private static func resendHint(wait: Int, isUpperBound: Bool) -> String {
        wait > 0
            ? waitHint(seconds: wait, isUpperBound: isUpperBound)
            : "Asks for a new code. Earlier codes stop working once a new one is sent."
    }

    /// The wait on a Send button: seconds under two minutes, whole minutes above, so a 429's
    /// hour reads "up to 60 min", not "3600s". "Up to" whenever the wait is an upper bound.
    private static func waitLabel(seconds: Int, isUpperBound: Bool) -> String {
        let amount = seconds < 120 ? "\(seconds)s" : "\((seconds + 59) / 60) min"
        return isUpperBound ? "up to \(amount)" : amount
    }

    private static func waitHint(seconds: Int, isUpperBound: Bool) -> String {
        let amount = seconds == 1 ? "1 second"
            : seconds < 120 ? "\(seconds) seconds"
            : "\((seconds + 59) / 60) minutes"
        return isUpperBound ? "Available in at most \(amount)" : "Available in \(amount)"
    }

    /// Mismatch is worth calling out before submitting rather than after a round-trip — it is
    /// the most common mistake on this form.
    @ViewBuilder
    private var mismatchNotice: some View {
        if !confirmPassword.isEmpty && confirmPassword != newPassword {
            Text("The new passwords don't match.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.bearish)
        }
    }

    /// The status line (a new code went out) and the error, directly under the button that
    /// produced them — not at the foot of a long form where the keyboard would hide them.
    @ViewBuilder
    private var statusLines: some View {
        if let noticeMessage {
            Text(noticeMessage)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        if let errorMessage {
            Text(errorMessage)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.bearish)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - Derived copy

    private var accountEmail: String {
        (appState.user.profile?.email ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// "Apple" / "Google", or a neutral fallback when the backend sent no provider list (an
    /// older build, or a probe that failed). Never prints a raw identifier at the user.
    private var providerName: String {
        appState.user.profile?.primaryProviderLabel ?? "your current sign-in method"
    }

    private var providerExplanation: String {
        if let label = appState.user.profile?.primaryProviderLabel {
            return "You sign in with \(label), so this account doesn't have a password yet. You can add one and use either."
        }
        return "This account doesn't have a password yet. You can add one and still use your current sign-in method."
    }

    // MARK: - Validation

    /// The code as ASCII digits. `wholeNumberValue` folds other scripts' digits (an Arabic-Indic
    /// number pad types ٣, not 3) into the ASCII the server expects, and drops anything else.
    private var normalizedCode: String {
        String(code.compactMap { ch -> Character? in
            guard let value = ch.wholeNumberValue, (0...9).contains(value) else { return nil }
            return Character(String(value))
        })
    }

    private var canSubmitChange: Bool {
        !currentPassword.isEmpty
            // The rule `ChangePasswordRequest` enforces server-side, not just its length: a
            // length-only gate enabled the button for a password the server then refused.
            && PasswordRule.isSatisfied(newPassword)
            && newPassword == confirmPassword
            && newPassword != currentPassword
    }

    private var canSubmitSet: Bool {
        // 6-12 digits is the server's own window (`SetPasswordRequest.code`).
        (6...12).contains(normalizedCode.count)
            // `PasswordRule` is the same rule the sign-up form shows as a live checklist and the
            // same one `_validate_password_strength` enforces server-side. Gating on it here
            // makes the 422 unreachable from this screen.
            && PasswordRule.isSatisfied(newPassword)
            && newPassword == confirmPassword
    }

    // MARK: - Send cooldown

    /// The address as the backend keys its per-email limiter (`strip().lower()`).
    private static func cooldownKey(_ address: String) -> String {
        address.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    private func secondsUntilSend(to address: String, at date: Date) -> Int {
        guard let cooldown = sendCooldowns[Self.cooldownKey(address)] else { return 0 }
        return max(0, Int(cooldown.until.timeIntervalSince(date).rounded(.up)))
    }

    private func waitIsUpperBound(for address: String) -> Bool {
        sendCooldowns[Self.cooldownKey(address)]?.isUpperBound ?? false
    }

    /// Start (or extend) the wait for `address`. Never SHORTENS one already running: the
    /// later of the two ends wins.
    private func startSendCooldown(for address: String, seconds: Int, isUpperBound: Bool) {
        let key = Self.cooldownKey(address)
        let until = Date().addingTimeInterval(TimeInterval(seconds))
        if let running = sendCooldowns[key], running.until >= until { return }
        sendCooldowns[key] = SendCooldown(until: until, isUpperBound: isUpperBound)
    }

    /// Lift the wait after a send the server ACCEPTED, once the code it protected was verified
    /// (and so used up): that code was the newest, nothing is in flight any more, and the user
    /// needs a new one now. A 429's wait stays — that is the server's own refusal.
    private func releaseSendCooldownAfterSpentCode(for address: String) {
        let key = Self.cooldownKey(address)
        if sendCooldowns[key]?.isUpperBound == false { sendCooldowns[key] = nil }
    }

    /// The wait a 429 imposes, in seconds. Its `Retry-After` is the denying limiter's whole
    /// window — an honest upper bound on the wait, not the time left. Floored at the normal
    /// cooldown so a 0 or negative header cannot re-open the button at once, and capped at
    /// `rateLimitWaitCapSeconds` so an absurd one cannot lock it for the life of the screen.
    private static func rateLimitWait(retryAfter: Int) -> Int {
        min(max(retryAfter, sendCooldownSeconds), rateLimitWaitCapSeconds)
    }

    // MARK: - Actions

    /// Return on the last field submits whichever form is showing, when it is complete.
    private func submitFromKeyboard() {
        switch mode {
        case .change: if canSubmitChange { submitChange() }
        case .set:    if canSubmitSet { submitSet() }
        }
    }

    private func submitChange() {
        guard !isSubmitting, canSubmitChange else { return }
        errorMessage = nil
        noticeMessage = nil
        isSubmitting = true
        Task {
            defer { isSubmitting = false }
            do {
                // Through AuthService, NOT APIClient directly: the server invalidates every
                // token issued before this change — including the one authenticating this very
                // request — and returns replacements. AuthService adopts them, which is what
                // keeps THIS device signed in while other devices are correctly evicted.
                // Decoding it here as a bare MessageResponse silently dropped those tokens and
                // signed the user out on their next request.
                try await appState.authService.changePassword(
                    currentPassword: currentPassword,
                    newPassword: newPassword
                )
                clearSecrets()
                didChange = true
            } catch {
                report(error, step: "change")
            }
        }
    }

    /// Send the code — the first one (step 1) or a replacement (the code step).
    private func requestCode(isResend: Bool) {
        guard !isSubmitting, !isResending, !accountEmail.isEmpty,
              secondsUntilSend(to: accountEmail, at: Date()) == 0 else { return }
        errorMessage = nil
        noticeMessage = nil
        if isResend { isResending = true } else { isSubmitting = true }
        let address = accountEmail
        Task {
            defer { if isResend { isResending = false } else { isSubmitting = false } }
            do {
                // The unchanged public recovery route — no new delivery path to get wrong. It
                // answers identically whether or not the address is registered, but here we
                // know it is: it came from this signed-in account's own profile.
                _ = try await APIClient.shared.request(
                    endpoint: .forgotPassword(email: address),
                    responseType: MessageResponse.self
                )
                startSendCooldown(for: address, seconds: Self.sendCooldownSeconds, isUpperBound: false)
                if isResend {
                    // In the usual case a new code went out and the one in hand just died, so
                    // the field is cleared rather than inviting a wasted attempt. The words stay
                    // hedged: this route answers the SAME 200 when the identity provider refused
                    // the send, and then the earlier code still works.
                    code = ""
                    noticeMessage = "If a new code was sent to \(address), use the one in the newest email. Earlier codes stop working once a new one is sent."
                }
                step = .enterCode
                focusedField = .code
            } catch {
                // A 429 holds BOTH Send buttons for its `Retry-After`, shown as an upper bound.
                // Every tap the server refuses still spends one of the caller's per-IP recovery
                // slots. The typed code is kept: nothing new was sent, so it still works.
                if case .rateLimited(let retryAfter)? = error as? APIError {
                    startSendCooldown(
                        for: address, seconds: Self.rateLimitWait(retryAfter: retryAfter),
                        isUpperBound: true
                    )
                }
                report(error, step: isResend ? "resend-code" : "request-code")
            }
        }
    }

    private func submitSet() {
        guard !isSubmitting, !isResending, canSubmitSet else { return }
        errorMessage = nil
        noticeMessage = nil
        isSubmitting = true
        let address = accountEmail
        Task {
            defer { isSubmitting = false }
            do {
                // Same token-adoption reason as submitChange: setting a password stamps
                // `password_changed_at`, which kills the token that made this request.
                try await appState.authService.setPassword(
                    code: normalizedCode,
                    newPassword: newPassword
                )
                // The settings row is driven by `hasPassword`, which just flipped. Re-read the
                // profile so it stops offering "Set a Password" the moment this screen closes.
                await appState.refreshProfile()
                clearSecrets()
                didChange = true
            } catch {
                if let backendCode = Self.backendCode(of: error) {
                    switch backendCode {
                    case "AUTH_CODE_USED":
                        // The server verified — and so USED UP — the code, then could not save
                        // the password (an outage, or a refusal that is not about the password).
                        // The code is dead, so it goes; the password was not the problem, so it
                        // stays for the next code. "Send a new code" below is the way on, at once.
                        code = ""
                        releaseSendCooldownAfterSpentCode(for: address)
                    case "AUTH_PASSWORD_REJECTED":
                        // The server verified — and so USED UP — the code before the identity
                        // provider refused the password, and that password is refused forever.
                        // Clear them; "Send a new code" below is the way on, at once.
                        code = ""
                        newPassword = ""
                        confirmPassword = ""
                        releaseSendCooldownAfterSpentCode(for: address)
                    default:
                        // AUTH_CODE_INVALID keeps the typed code: it may be a typo. The
                        // server's sentence names both remedies (fix it, or send a new one).
                        break
                    }
                }
                report(error, step: "set")
            }
        }
    }

    /// Log the failure (codes only — never the address, a code or a password) and show the
    /// user-facing words. Cancellation is not a failure and shows nothing.
    private func report(_ error: Error, step: String) {
        let appError = AppError.from(error)
        guard !appError.isCancellation else { return }
        let backendCode = Self.backendCode(of: error) ?? "none"
        let flow = mode == .set ? "set" : "change"
        Self.log.warning("password-\(flow, privacy: .public): \(step, privacy: .public) failed: \(appError.analyticsCode, privacy: .public) backend=\(backendCode, privacy: .public)")
        // Drop the keyboard so the message under the button is on screen.
        focusedField = nil
        errorMessage = appError.message
    }

    /// The backend `error_code`, when the failure carried one. The FLOW keys off the code; the
    /// WORDS always come from `AppError.from(error).message`.
    private static func backendCode(of error: Error) -> String? {
        guard let api = error as? APIError else { return nil }
        switch api {
        case .businessError(let code, _), .authError(let code, _):
            return code
        default:
            return nil
        }
    }

    /// Clear the secrets from memory as soon as they're no longer needed.
    private func clearSecrets() {
        currentPassword = ""
        newPassword = ""
        confirmPassword = ""
        code = ""
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
        .disabled(!enabled || isSubmitting || isResending)
    }

    /// The visible caption is hidden from VoiceOver because each field carries the same words as
    /// its own `accessibilityLabel` — a field with an empty title was read as just "secure text
    /// field", and Voice Control's "tap Current password" found nothing.
    @ViewBuilder
    private func labeled(
        _ label: String, @ViewBuilder content: () -> some View
    ) -> some View {
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

#Preview("Change") {
    NavigationStack { ChangePasswordView() }
}

#Preview("Set") {
    NavigationStack { ChangePasswordView(mode: .set) }
}
