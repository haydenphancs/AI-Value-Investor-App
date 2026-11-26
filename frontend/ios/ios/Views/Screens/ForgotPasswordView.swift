//
//  ForgotPasswordView.swift
//  ios
//
//  Screen: password recovery. Two steps in one view — request a code, then set a new
//  password with it.
//
//  Why this exists: there was no recovery path at all. A user who forgot their password
//  was permanently locked out of their account, their research, and their paid credits,
//  with no way back in.
//
//  Why a CODE rather than a tapped email link: the link flow needs either a hosted
//  redirect page or Universal Links with an Associated Domains entitlement. A code the user
//  types needs no hosting and no deep-link plumbing. Its LENGTH is a project-wide Supabase
//  setting (8 digits today; the backend accepts 6-12), so no copy here names a digit count —
//  "6-digit" copy told users to expect a shorter code than the one they received.
//
//  The new password is gated on the SAME rule the backend enforces (`PasswordRule`, pinned by
//  backend/tests/test_password_rule_parity.py). This screen used to gate on `count >= 8` and
//  say "At least 8 characters", so the button enabled for a password the server then refused
//  with a rule the user had never been shown.
//
//  After the server has VERIFIED the code it is spent, whatever happens next. So
//  AUTH_CODE_USED (the password could not be saved) and AUTH_PASSWORD_REJECTED (it was
//  refused) both clear the code field and leave the user on the code step with "Send a new
//  code" — never a "Please try again" that can only meet AUTH_CODE_INVALID.
//

import SwiftUI
import Accessibility
import OSLog

struct ForgotPasswordView: View {
    /// Pre-filled from the sign-in form so the user doesn't retype it.
    let initialEmail: String

    @Environment(\.dismiss) private var dismiss

    private enum Step { case requestCode, enterCode }
    private enum Field: Hashable { case email, code, newPassword }

    @State private var step: Step = .requestCode
    @State private var email: String
    @State private var code: String = ""
    @State private var newPassword: String = ""
    @State private var showNewPassword = false
    @State private var isSubmitting = false
    @State private var isResending = false
    @State private var errorMessage: String?
    /// A non-error status line (a send was asked for). Kept apart from `errorMessage` so a
    /// non-error is never painted in the error colour, and announced the same way.
    @State private var noticeMessage: String?
    /// When sending a code to an address unlocks again, keyed by `cooldownKey(_:)`. Every send
    /// REPLACES the previous code, so a second tap while the first email is still in flight
    /// kills the code the user is about to receive — and each send also spends one of the
    /// address's few recovery emails per hour. Keyed PER ADDRESS so both Send buttons honour it
    /// ("Use a different email" → Send Code used to skip it) while a corrected address is never
    /// held back by the wait of the one it replaced.
    @State private var sendCooldowns: [String: SendCooldown] = [:]
    /// Set after a successful reset; the view then shows a terminal success state.
    @State private var didReset = false
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
    /// the button for the life of the sheet.
    private static let rateLimitWaitCapSeconds = 3600

    init(initialEmail: String = "") {
        self.initialEmail = initialEmail
        _email = State(initialValue: initialEmail)
    }

    var body: some View {
        NavigationStack {
            ZStack {
                AppColors.background.ignoresSafeArea()

                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        header

                        if didReset {
                            successState
                        } else {
                            switch step {
                            case .requestCode: requestCodeForm
                            case .enterCode:   enterCodeForm
                            }
                        }

                        Spacer(minLength: 40)
                    }
                    .padding(.horizontal, 24)
                }
            }
            .navigationTitle("Reset Password")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(AppColors.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(didReset ? "Done" : "Cancel") { dismiss() }
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
        // VoiceOver users never see a red line appear: say it. Same for the status line and the
        // terminal success, which otherwise change the screen in silence.
        .onChange(of: errorMessage) { _, message in
            if let message { Self.announce(message) }
        }
        .onChange(of: noticeMessage) { _, message in
            if let message { Self.announce(message) }
        }
        .onChange(of: didReset) { _, reset in
            if reset { Self.announce("Password reset. You can now sign in with your new password.") }
        }
    }

    private static func announce(_ message: String) {
        AccessibilityNotification.Announcement(message).post()
    }

    // MARK: - Steps

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            Image(systemName: didReset ? "checkmark.circle.fill" : "lock.rotation")
                .font(AppTypography.iconXXL)
                .foregroundColor(didReset ? AppColors.bullish : AppColors.primaryBlue)
                .padding(.top, 24)
                .accessibilityHidden(true)

            Text(headerTitle)
                .font(AppTypography.titleLarge)
                .foregroundColor(AppColors.textPrimary)
                .accessibilityAddTraits(.isHeader)

            Text(headerSubtitle)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.bottom, 8)
    }

    private var headerTitle: String {
        if didReset { return "Password reset" }
        return step == .requestCode ? "Forgot your password?" : "Enter your code"
    }

    private var headerSubtitle: String {
        if didReset {
            return "You can now sign in with your new password. Any other devices will need to sign in again."
        }
        switch step {
        case .requestCode:
            return "Enter your email and we'll send you a code to reset it."
        case .enterCode:
            // Deliberately hedged: the backend never confirms whether the address is
            // registered, so promising "we sent you an email" would be a lie for an
            // unknown address.
            return "If an account exists for \(trimmedEmail), we've sent a code. Enter the code from your email below with your new password. Check spam if it hasn't arrived."
        }
    }

    private var requestCodeForm: some View {
        VStack(alignment: .leading, spacing: 20) {
            labeled("Email") {
                TextField("", text: $email)
                    .textContentType(.emailAddress)
                    .keyboardType(.emailAddress)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .focused($focusedField, equals: .email)
                    .submitLabel(.send)
                    .onSubmit { if emailLooksValid { sendCode(isResend: false) } }
                    .accessibilityLabel("Email")
            }

            sendCodeButton

            statusLines
        }
    }

    /// Step 1's Send Code. It honours the same per-address wait as "Send a new code": without
    /// it, "Use a different email" → Send Code (same address) skipped the cooldown, and after a
    /// 429 every further tap still spent one of the caller's per-IP recovery slots — shared by
    /// everyone behind the same carrier NAT or Wi-Fi. A different address is never held back.
    private var sendCodeButton: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let wait: Int = secondsUntilSend(to: trimmedEmail, at: context.date)
            let upperBound = waitIsUpperBound(for: trimmedEmail)
            primaryButton(
                title: wait > 0
                    ? "Send Code (\(Self.waitLabel(seconds: wait, isUpperBound: upperBound)))"
                    : "Send Code",
                busyLabel: "Sending code",
                enabled: emailLooksValid && wait == 0, isBusy: isSubmitting
            ) {
                sendCode(isResend: false)
            }
            .accessibilityHint(wait > 0 ? Self.waitHint(seconds: wait, isUpperBound: upperBound) : "")
        }
    }

    private var enterCodeForm: some View {
        VStack(alignment: .leading, spacing: 20) {
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

            newPasswordInput

            // The full rule as a live checklist — the same one the sign-up form shows and the
            // server enforces — instead of "At least 8 characters", which was a subset of it.
            PasswordRequirementsView(password: newPassword)

            primaryButton(
                title: "Reset Password", busyLabel: "Resetting password",
                enabled: canReset, isBusy: isSubmitting
            ) {
                submitReset()
            }

            // Directly under the button that caused it, not at the foot of a long form where
            // a small phone (or the keyboard) would hide it.
            statusLines

            resendButton

            Button {
                step = .requestCode
                code = ""
                errorMessage = nil
                noticeMessage = nil
                focusedField = .email
            } label: {
                Text("Use a different email")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.primaryBlue)
            }
            .disabled(isSubmitting || isResending)
            .frame(maxWidth: .infinity, alignment: .center)
        }
    }

    /// The new password with a show/hide control. There is no confirm field on this screen, and
    /// a mistyped hidden password spends the single-use code on a password the user never
    /// meant — forcing another recovery email (a handful per hour per address).
    ///
    /// Built by hand rather than through `labeled(_:content:)`: that pads its content 12 pt top
    /// and bottom, so a 44-pt eye inside it made this box ~20 pt taller than its neighbours.
    /// Here only the field is padded and the eye fills the box's height on its own.
    private var newPasswordInput: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("New password")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .accessibilityHidden(true)

            HStack(spacing: 0) {
                Group {
                    if showNewPassword {
                        TextField("", text: $newPassword)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                    } else {
                        SecureField("", text: $newPassword)
                    }
                }
                .textContentType(.newPassword)
                .focused($focusedField, equals: .newPassword)
                .submitLabel(.go)
                .onSubmit { if canReset { submitReset() } }
                .accessibilityLabel("New password")
                .padding(.vertical, 12)
                .padding(.leading, 14)

                Button {
                    toggleNewPasswordVisibility()
                } label: {
                    Image(systemName: showNewPassword ? "eye.slash" : "eye")
                        .font(AppTypography.iconSmall)
                        .foregroundColor(AppColors.textSecondary)
                        // The glyph alone was a ~14-pt target, far under the 44 × 44 minimum. A
                        // Button hit-tests what its label DRAWS, so the frame needs the shape.
                        .frame(minWidth: 44, minHeight: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(showNewPassword ? "Hide password" : "Show password")
            }
            .cardSurface(cornerRadius: AppCornerRadius.medium)
            .foregroundColor(AppColors.textPrimary)
        }
    }

    /// Show or hide the new password, keeping the keyboard up when the field had it.
    ///
    /// Swapping SecureField ↔ TextField REPLACES the field. Writing `.newPassword` again in the
    /// same update changes nothing (it already held that value), so SwiftUI had no reason to
    /// focus the replacement and could drop the keyboard on every tap. Clear it in this update
    /// and set it on the next run loop, once the replacement exists: a real change, applied to
    /// the field that is now on screen. (Reasoned from SwiftUI's focus model; not yet watched
    /// on a device.)
    private func toggleNewPasswordVisibility() {
        let wasFocused = focusedField == .newPassword
        if wasFocused { focusedField = nil }
        showNewPassword.toggle()
        guard wasFocused else { return }
        DispatchQueue.main.async {
            focusedField = .newPassword
        }
    }

    /// The account's email, in a `.username` field, on the step that sets the new password.
    ///
    /// Password AutoFill can only save or UPDATE a credential when the form holding the new
    /// password also holds the account name. Without it, a reset left the old password saved
    /// in Passwords, AutoFill kept filling it at sign-in, and the user concluded the reset had
    /// failed — while each retry spent a login attempt. Read-only (it is the address the code
    /// went to; editing it would only break the reset) and visually minimal.
    ///
    /// The FIELD is hidden from accessibility: VoiceOver announced it as an editable "text
    /// field" whose edits went nowhere. AutoFill reads `textContentType`, not the
    /// accessibility tree. The row speaks instead, as static text: "Account email", then the
    /// same address the field shows on screen.
    private var accountUsernameField: some View {
        HStack(spacing: AppSpacing.xs) {
            Image(systemName: "person.crop.circle")
                .font(AppTypography.iconXS)
                .foregroundColor(AppColors.textMuted)
                .accessibilityHidden(true)
            TextField("", text: .constant(trimmedEmail))
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
        .accessibilityValue(trimmedEmail)
        .accessibilityAddTraits(.isStaticText)
    }

    /// "Send a new code", always on the code step, with a cooldown after every send and after
    /// a 429. This is the way forward from an invalid or expired code and from a code the
    /// server already used up (AUTH_CODE_USED / AUTH_PASSWORD_REJECTED after verification), so
    /// it must never hide.
    private var resendButton: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let wait: Int = secondsUntilSend(to: trimmedEmail, at: context.date)
            let upperBound = waitIsUpperBound(for: trimmedEmail)
            Button {
                sendCode(isResend: true)
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
            .disabled(wait > 0 || isResending || isSubmitting || !emailLooksValid)
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

    /// The status line (a new code went out) and the error, in that order.
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

    private var successState: some View {
        primaryButton(
            title: "Back to Sign In", busyLabel: "Back to Sign In",
            enabled: true, isBusy: false
        ) { dismiss() }
    }

    // MARK: - Validation

    private var trimmedEmail: String {
        email.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var emailLooksValid: Bool {
        trimmedEmail.contains("@") && trimmedEmail.count >= 5
    }

    /// The code as ASCII digits. `wholeNumberValue` folds other scripts' digits (an Arabic-Indic
    /// number pad types ٣, not 3) into the ASCII the server expects, and drops anything else —
    /// spaces, dashes, a pasted "Code:" label.
    private var normalizedCode: String {
        String(code.compactMap { ch -> Character? in
            guard let value = ch.wholeNumberValue, (0...9).contains(value) else { return nil }
            return Character(String(value))
        })
    }

    private var canReset: Bool {
        // 6-12 digits is the server's own window (`ResetPasswordRequest.code`); the project
        // sends 8 today, so the floor is the schema's, not a promise about the email.
        (6...12).contains(normalizedCode.count)
            && PasswordRule.isSatisfied(newPassword)
            && emailLooksValid
    }

    // MARK: - Send cooldown

    /// The address as the backend keys its per-email limiter (`strip().lower()`), so a change
    /// of case or a stray space is the same mailbox — never a way around the wait.
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
    /// `rateLimitWaitCapSeconds` so an absurd one cannot lock it for the life of the sheet.
    private static func rateLimitWait(retryAfter: Int) -> Int {
        min(max(retryAfter, sendCooldownSeconds), rateLimitWaitCapSeconds)
    }

    // MARK: - Actions

    /// Request a code — the first one (step 1) or a replacement (the code step).
    private func sendCode(isResend: Bool) {
        guard !isSubmitting, !isResending, emailLooksValid,
              secondsUntilSend(to: trimmedEmail, at: Date()) == 0 else { return }
        errorMessage = nil
        noticeMessage = nil
        if isResend { isResending = true } else { isSubmitting = true }
        let address = trimmedEmail
        Task {
            defer { if isResend { isResending = false } else { isSubmitting = false } }
            do {
                _ = try await APIClient.shared.request(
                    endpoint: .forgotPassword(email: address),
                    responseType: MessageResponse.self
                )
                // Advance regardless of whether the address is registered — the backend
                // deliberately doesn't say, and neither should the UI.
                startSendCooldown(for: address, seconds: Self.sendCooldownSeconds, isUpperBound: false)
                if isResend {
                    // In the usual case a new code went out and the one in hand just died, so
                    // the field is cleared rather than inviting a wasted attempt. The words stay
                    // hedged: this route answers the SAME 200 when nothing was sent (an unknown
                    // address, or the identity provider refusing the send), and then the
                    // earlier code still works.
                    code = ""
                    noticeMessage = "If a new code was sent to \(address), use the one in the newest email. Earlier codes stop working once a new one is sent."
                }
                step = .enterCode
                focusedField = .code
            } catch {
                let appError = AppError.from(error)
                guard !appError.isCancellation else { return }
                let backendCode = Self.backendCode(of: error) ?? "none"
                // A 429 holds BOTH Send buttons for this address for its `Retry-After`, shown
                // as an upper bound. Every tap the server refuses still spends one of the
                // caller's per-IP recovery slots, which other people behind the same network
                // share. The typed code is kept: nothing new was sent, so it still works.
                let waitSeconds: Int
                if case .rateLimited(let retryAfter)? = error as? APIError {
                    waitSeconds = Self.rateLimitWait(retryAfter: retryAfter)
                    startSendCooldown(for: address, seconds: waitSeconds, isUpperBound: true)
                } else {
                    waitSeconds = 0
                }
                Self.log.warning("forgot-password: code request failed (resend=\(isResend, privacy: .public)): \(appError.analyticsCode, privacy: .public) backend=\(backendCode, privacy: .public) wait=\(waitSeconds, privacy: .public)s")
                // Drop the keyboard so the message under the button is on screen.
                focusedField = nil
                errorMessage = appError.message
            }
        }
    }

    private func submitReset() {
        guard !isSubmitting, !isResending, canReset else { return }
        errorMessage = nil
        noticeMessage = nil
        isSubmitting = true
        let address = trimmedEmail
        Task {
            defer { isSubmitting = false }
            do {
                _ = try await APIClient.shared.request(
                    endpoint: .resetPassword(
                        email: address,
                        code: normalizedCode,
                        newPassword: newPassword
                    ),
                    responseType: MessageResponse.self
                )
                // `code` is spent; the new password is left for Password AutoFill to read as
                // the fields leave the screen (it offers to update the saved credential then).
                // The view's state dies with the sheet.
                code = ""
                didReset = true
            } catch {
                let appError = AppError.from(error)
                guard !appError.isCancellation else { return }
                let backendCode = Self.backendCode(of: error)
                Self.log.warning("forgot-password: reset failed: \(appError.analyticsCode, privacy: .public) backend=\(backendCode ?? "none", privacy: .public)")
                // Drop the keyboard so the message under the button is on screen.
                focusedField = nil
                switch backendCode {
                case "AUTH_CODE_INVALID":
                    // Stay on this step with the typed code kept: it may be a typo (fix it) or
                    // expired (send a new one — the button below). The server's sentence says
                    // both, so nothing is added to it here.
                    break
                case "AUTH_CODE_USED":
                    // The server verified — and so USED UP — the code, then could not save the
                    // password (an outage, or a refusal that is not about the password). The
                    // code is dead, so it goes; the password was not the problem, so it stays
                    // for the next code. "Send a new code" below is the way on, at once.
                    code = ""
                    releaseSendCooldownAfterSpentCode(for: address)
                case "AUTH_PASSWORD_REJECTED":
                    // The server verified — and so USED UP — the code before the identity
                    // provider refused the password, and the same password is refused forever.
                    // Clear both; the server's sentence says a new code is needed.
                    code = ""
                    newPassword = ""
                    releaseSendCooldownAfterSpentCode(for: address)
                default:
                    break
                }
                errorMessage = appError.message
            }
        }
    }

    /// The backend `error_code`, when the failure carried one. The FLOW keys off the code (stay
    /// on the code step, clear a spent code); the WORDS always come from
    /// `AppError.from(error).message`. Logged as a code only — never the email, code or password.
    private static func backendCode(of error: Error) -> String? {
        guard let api = error as? APIError else { return nil }
        switch api {
        case .businessError(let code, _), .authError(let code, _):
            return code
        default:
            return nil
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
        .disabled(!enabled || isSubmitting || isResending)
    }

    /// The visible caption is hidden from VoiceOver because each field carries the same words as
    /// its own `accessibilityLabel` — a field with an empty title used to be read as just "text
    /// field", and Voice Control's "tap Email" found nothing.
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

#Preview {
    ForgotPasswordView(initialEmail: "someone@example.com")
}
