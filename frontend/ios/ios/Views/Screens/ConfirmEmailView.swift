//
//  ConfirmEmailView.swift
//  ios
//
//  Screen: confirm a new account's email address with the code we emailed.
//
//  Why this exists (2026-11-26): every new email/password account was stuck. The "Confirm
//  signup" email carries a numeric code, but the app could only say "click the link", re-sent
//  the email on every sign-in attempt (rotating the code each time), and had no screen and no
//  route that could use a sign-up code. This screen is that missing step.
//
//  Shown by `SignInView` after a sign-up that needs confirmation AND when a sign-in is refused
//  with EMAIL_NOT_CONFIRMED. Confirming signs the user in with the password they just typed,
//  which SignInView keeps in memory only (never persisted); the server checks the code before
//  it looks at the password, so a pre-registered stranger's password never gets a session.
//
//  Once the server has accepted the code, the code is SPENT: a second try can only fail as
//  AUTH_CODE_INVALID, and "Send a new code" sends nothing to an address that is already
//  confirmed. So both "accepted" outcomes lock the code step — AUTH_CONFIRMED_SIGN_IN_FAILED
//  (confirmed, but the sign-in after it failed) turns "Sign in" into the primary action, and
//  `.confirmedPendingConnection` (signed in, profile read pending) waits for the reconnect
//  (IOS-CONF-1).
//
//  No ViewModel, like SignInView and ForgotPasswordView: the session transition belongs to
//  `AppState` (`confirmEmail`, `signIn`, `resendConfirmation`), and this view only drives it.
//

import SwiftUI
import Accessibility
import OSLog

struct ConfirmEmailView: View {
    @Environment(AppState.self) private var appState

    /// The address the code was sent to, captured when the screen opened.
    let email: String
    /// The password from the form that led here — used to sign in once the address is confirmed.
    let password: String
    /// True right after a sign-up, when an email has just gone out: the resend cooldown starts
    /// at once, since the provider ignores a second send within about a minute anyway.
    let codeJustSent: Bool
    /// The sign-up response's own sentence (the backend's `SIGN_UP_PENDING_MESSAGE`), present
    /// only when this screen follows a sign-up. NOT shown since 2026-11-26: it now only says
    /// "Check your email to confirm your address.", which the title already says, because an
    /// address that already has an account is refused on the sign-up form itself (409
    /// AUTH_EMAIL_ALREADY_REGISTERED) and never reaches this screen. Kept so a future server
    /// sentence can be shown without new plumbing. Never branched on.
    let signUpMessage: String?
    /// "Forgot password?" — the presenter opens its reset flow for `email`.
    let onForgotPassword: () -> Void
    /// "Use a different email" — back to the form that led here.
    let onUseDifferentEmail: () -> Void

    /// What the field accepts. The project sends 8 digits today; the backend takes 6-12, so a
    /// change of the provider's code length never strands a build that hard-coded one.
    static let codeLengths = 6...10
    static let resendCooldown: TimeInterval = 60
    /// The most a 429 can hold "Send a new code": the longest window any credential limiter uses.
    static let rateLimitWaitCapSeconds = 3600
    /// Deliberately hedged. The backend never says whether the address still needs confirming
    /// (that answer would tell anyone which addresses have accounts), and the provider silently
    /// skips a send it considers too soon — so "we sent you a new code" can be false.
    static let resendNote = "If your email still needs confirming, a new code is on its way."
    static let confirmedPendingNote = "Your email is confirmed. We'll finish signing you in as soon as the connection is back."
    static let stillUnconfirmedMessage = "Your email isn't confirmed yet. Enter the code from the email, or send a new one."
    /// After a confirm whose answer never arrived, AUTH_CODE_INVALID may mean "spent by the try
    /// you never heard back from" — the one path where no server copy says "confirmed".
    static let maybeConfirmedNote = "Your last try may have gone through. If it did, your email is already confirmed: use Sign in below."
    /// The neutral sign-in link. It serves a user who confirmed with an older app's link, or one
    /// whose address got confirmed some other way (Forgot password) while this screen was open.
    static let signInLinkTitle = "Already confirmed, or have an account? Sign in"
    /// An unstructured 404 on the confirm route: this build is talking to a server without it.
    static let routeMissingMessage = "We can't confirm with a code right now. Please try again later."
    /// Longest server sentence shown from the sign-up response; anything longer is cut, so a
    /// malformed body cannot push the code field off the screen.
    static let signUpMessageLimit = 280

    private static let log = Logger(subsystem: "com.phan.caydex", category: "auth")
    /// Cap on what the field keeps, so a pasted paragraph cannot grow it without bound.
    private static let fieldLimit = 16

    private enum Activity { case confirming, resending, signingIn }

    @State private var code = ""
    /// One request at a time — the in-flight guard for every button on this screen.
    @State private var activity: Activity?
    @State private var errorMessage: String?
    @State private var note: String?
    @State private var resendAvailableAt: Date?
    /// The code was accepted but the profile read failed: the session is kept and the app keeps
    /// retrying (`.restoring`). The code is spent, so the form must not invite a second try.
    @State private var confirmedAwaitingConnection = false
    /// The code was accepted — the address is confirmed and the code spent — but the sign-in
    /// after it failed (AUTH_CONFIRMED_SIGN_IN_FAILED). The code step locks and "Sign in", with
    /// the password in hand, becomes the primary action.
    @State private var confirmedNeedsSignIn = false
    /// What the server said when it accepted the code. Kept apart from `errorMessage` so a failed
    /// sign-in attempt afterwards does not erase "your email is confirmed".
    @State private var confirmedNotice: String?
    /// The last confirm ended without an answer (timeout, dropped connection, unreadable reply):
    /// the server may have spent the code. See `maybeConfirmedNote`.
    @State private var lastConfirmUnanswered = false
    @FocusState private var codeFieldFocused: Bool

    init(
        email: String,
        password: String,
        codeJustSent: Bool,
        signUpMessage: String? = nil,
        onForgotPassword: @escaping () -> Void,
        onUseDifferentEmail: @escaping () -> Void
    ) {
        self.email = email
        self.password = password
        self.codeJustSent = codeJustSent
        self.signUpMessage = signUpMessage
        self.onForgotPassword = onForgotPassword
        self.onUseDifferentEmail = onUseDifferentEmail
        _resendAvailableAt = State(
            initialValue: codeJustSent ? Date().addingTimeInterval(Self.resendCooldown) : nil
        )
    }

    var body: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()

            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    header
                    codeField
                    statusLines
                    primaryAction
                    resendButton
                    otherWays
                    Spacer(minLength: 40)
                }
                .padding(.horizontal, 24)
            }
            .scrollDismissesKeyboard(.interactively)
        }
        // Its own error, cleared on the way in and out, so nothing stale greets the user on
        // either side.
        .onAppear {
            errorMessage = nil
            note = nil
            codeFieldFocused = true
        }
        .onDisappear {
            errorMessage = nil
            note = nil
        }
        .onChange(of: errorMessage) { _, message in announce(message) }
        .onChange(of: note) { _, message in announce(message) }
        .onChange(of: confirmedNotice) { _, message in announce(message) }
    }

    // MARK: - Sections

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            Image(systemName: "envelope.badge.fill")
                .font(AppTypography.iconXXL)
                .foregroundColor(AppColors.primaryBlue)
                .padding(.top, 40)
                .accessibilityHidden(true)

            Text("Check your email")
                .font(AppTypography.titleLarge)
                .foregroundColor(AppColors.textPrimary)
                .accessibilityAddTraits(.isHeader)

            // No digit count: the provider's code length is a dashboard setting, and the app
            // must not promise a number it does not control.
            Text("Enter the code we sent to")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
            Text(email)
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)
                .lineLimit(2)
                .truncationMode(.middle)

            Text("It can take a minute to arrive. Check your spam folder too.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.bottom, 8)
    }

    private var codeField: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Code from email")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .accessibilityHidden(true)
            TextField("", text: $code)
                .textContentType(.oneTimeCode)
                .keyboardType(.numberPad)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .font(AppTypography.bodyEmphasis)
                .focused($codeFieldFocused)
                .submitLabel(.go)
                .onSubmit(confirm)
                .disabled(codeSpent)
                .accessibilityLabel("Code from email")
                .padding(.vertical, 12)
                .padding(.horizontal, 14)
                .cardSurface(cornerRadius: AppCornerRadius.medium)
                .foregroundColor(AppColors.textPrimary)
                // Spaces and dashes from a pasted "1234 5678" are dropped as typed, so what
                // the field shows is exactly what will be checked.
                .onChange(of: code) { oldValue, newValue in
                    let cleaned = Self.digits(in: newValue, limit: Self.fieldLimit)
                    if cleaned != newValue { code = cleaned }
                    // A new code is being typed: the verdict on the previous one no longer
                    // applies. Seen in the Simulator (2026-11-26): "That code is invalid…" stayed
                    // under a freshly typed, correct code until Confirm was tapped.
                    if Self.digits(in: oldValue, limit: Self.fieldLimit) != cleaned {
                        errorMessage = nil
                    }
                }

            if codeDigits.count > Self.codeLengths.upperBound {
                Text("That's longer than our codes. Check that only the code was pasted.")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
        }
    }

    @ViewBuilder
    private var statusLines: some View {
        if let confirmedNotice {
            Text(confirmedNotice)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
        }
        if let errorMessage {
            Text(errorMessage)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.loss)
                .fixedSize(horizontal: false, vertical: true)
        }
        if let note {
            Text(note)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// Confirm, until the server has accepted the code; then, when the sign-in after it failed,
    /// Sign in — the only action left that can succeed.
    @ViewBuilder
    private var primaryAction: some View {
        if confirmedNeedsSignIn {
            primaryButton(
                title: "Sign in", busyLabel: "Signing in",
                isBusy: activity == .signingIn, enabled: true, action: signInWithPassword
            )
        } else {
            primaryButton(
                title: "Confirm", busyLabel: "Confirming",
                isBusy: activity == .confirming, enabled: canConfirm, action: confirm
            )
        }
    }

    /// Ticks once a second only to redraw the countdown; the gate itself is `resendAvailableAt`.
    private var resendButton: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let wait = secondsUntilResend(at: context.date)
            Button(action: resend) {
                Text(resendLabel(wait: wait))
                    .font(AppTypography.bodySmall)
                    .foregroundColor(
                        wait > 0 || activity != nil || codeSpent
                            ? AppColors.textMuted : AppColors.primaryBlue
                    )
            }
            .disabled(wait > 0 || activity != nil || codeSpent)
            .accessibilityLabel(resendAccessibilityLabel(wait: wait))
            .frame(maxWidth: .infinity, alignment: .center)
        }
    }

    private var otherWays: some View {
        VStack(spacing: 14) {
            // Promoted to the primary button once the code is accepted; a second copy here
            // would be two buttons doing one thing.
            if !confirmedNeedsSignIn {
                linkButton(Self.signInLinkTitle, busy: activity == .signingIn, action: signInWithPassword)
            }
            linkButton("Forgot password?", busy: false, action: onForgotPassword)
            linkButton("Use a different email", busy: false, action: onUseDifferentEmail)
        }
        .frame(maxWidth: .infinity, alignment: .center)
        .padding(.top, 8)
    }

    // MARK: - State

    private var codeDigits: String { Self.digits(in: code, limit: Self.fieldLimit) }

    /// The server accepted the code, so it can never be used again: the code field, Confirm and
    /// "Send a new code" all lock.
    private var codeSpent: Bool { confirmedAwaitingConnection || confirmedNeedsSignIn }

    private var canConfirm: Bool {
        Self.codeLengths.contains(codeDigits.count) && !codeSpent
    }

    private func secondsUntilResend(at now: Date) -> Int {
        guard let resendAvailableAt else { return 0 }
        return max(0, Int(resendAvailableAt.timeIntervalSince(now).rounded(.up)))
    }

    private func resendLabel(wait: Int) -> String {
        if activity == .resending { return "Sending…" }
        // Above 90 s the hold is a 429's whole window, and the limiter slides, so the real wait
        // can be shorter: an upper bound, said as one.
        if wait > 90 { return "Send a new code (up to \(Int((Double(wait) / 60).rounded(.up))) min)" }
        if wait > 0 { return "Send a new code in \(wait)s" }
        return "Send a new code"
    }

    private func resendAccessibilityLabel(wait: Int) -> String {
        if wait > 90 {
            return "Send a new code, available in at most \(Int((Double(wait) / 60).rounded(.up))) minutes"
        }
        return wait > 0 ? "Send a new code, available in \(wait) seconds" : "Send a new code"
    }

    /// The code's digits as ASCII, everything else dropped — the server's own rule
    /// (`schemas/auth.py`): any Unicode DECIMAL digit is that digit, so an Arabic-Indic or
    /// full-width number pad types a code the server accepts, while "½", superscripts, letters,
    /// spaces and dashes are not digits at all. (`Character.isNumber` would keep "½"; ASCII-only
    /// would silently swallow every key on a number pad that types another script's digits.)
    static func digits(in text: String, limit: Int) -> String {
        var out = ""
        for scalar in text.unicodeScalars where out.count < limit {
            // Matched against 0...9 rather than converted with `Int(_:)`: a Double-to-Int
            // conversion traps on a value it cannot represent, and this one never needs to.
            guard scalar.properties.numericType == .decimal,
                  let value = scalar.properties.numericValue,
                  let digit = (0...9).first(where: { Double($0) == value }) else { continue }
            out += String(digit)
        }
        return out
    }

    /// A server sentence fit to show: trimmed, nil when empty, cut at `signUpMessageLimit`.
    /// Unused while the header shows no server sentence (see `signUpMessage`); kept with it.
    static func displayable(_ message: String?) -> String? {
        guard let trimmed = message?.trimmingCharacters(in: .whitespacesAndNewlines),
              !trimmed.isEmpty else { return nil }
        return String(trimmed.prefix(signUpMessageLimit))
    }

    // MARK: - Actions

    private func confirm() {
        let digits = codeDigits
        guard Self.codeLengths.contains(digits.count), activity == nil, !codeSpent else {
            return
        }
        errorMessage = nil
        note = nil
        activity = .confirming
        Task {
            defer { activity = nil }
            do {
                switch try await appState.confirmEmail(email: email, code: digits, password: password) {
                case .signedIn:
                    // Nothing to do here: SignInView closes itself when the session arrives.
                    break
                case .confirmedPendingConnection:
                    confirmedAwaitingConnection = true
                    confirmedNotice = Self.confirmedPendingNote
                }
            } catch {
                guard !Task.isCancelled else { return }
                report(error, during: "confirm")
                let appError = AppError.from(error)
                let unanswered = lastConfirmUnanswered
                lastConfirmUnanswered = false
                switch Self.backendCode(of: error) {
                case "AUTH_CONFIRMED_SIGN_IN_FAILED", "AUTH_PASSWORD_REJECTED":
                    // The code WORKED: the address is confirmed and the code is spent. (On this
                    // route a refused password can only come after the code was checked — the
                    // request schema never validates it.) Lock the code step and make Sign in
                    // the way forward, with the server's sentence saying why.
                    lockAfterConfirmation(notice: Self.message(for: appError))
                case "AUTH_CODE_INVALID":
                    errorMessage = Self.message(for: appError)
                    if unanswered { note = Self.maybeConfirmedNote }
                default:
                    lastConfirmUnanswered = Self.mayHaveReachedServer(appError)
                    errorMessage = Self.message(for: appError)
                }
            }
        }
    }

    /// The code is spent; only signing in (or a password reset) can finish the job.
    private func lockAfterConfirmation(notice: String) {
        code = ""
        codeFieldFocused = false
        errorMessage = nil
        note = nil
        confirmedNeedsSignIn = true
        confirmedNotice = notice
    }

    private func resend() {
        guard activity == nil, !codeSpent, secondsUntilResend(at: Date()) == 0 else {
            return
        }
        errorMessage = nil
        note = nil
        activity = .resending
        Task {
            defer { activity = nil }
            do {
                try await appState.resendConfirmation(email: email)
                // A new code replaces the old one, so the old one is cleared from the field.
                code = ""
                lastConfirmUnanswered = false
                resendAvailableAt = Date().addingTimeInterval(Self.resendCooldown)
                note = Self.resendNote
            } catch {
                guard !Task.isCancelled else { return }
                report(error, during: "resend")
                let appError = AppError.from(error)
                // A 429 holds the button for the window the server named (3 sends per address
                // per 15 minutes since 2026-11-26), so it cannot be tapped into the same
                // refusal again. Capped, so a malformed Retry-After cannot lock it for good.
                if case .rateLimited(let seconds) = appError {
                    let wait = min(max(seconds, Int(Self.resendCooldown)), Self.rateLimitWaitCapSeconds)
                    resendAvailableAt = Date().addingTimeInterval(TimeInterval(wait))
                }
                errorMessage = Self.message(for: appError)
            }
        }
    }

    /// An ordinary sign-in with the password in hand. The link below the form, for a user who
    /// confirmed with an older app's link (or already has an account); the PRIMARY button once
    /// this screen's code was accepted but the sign-in after it failed.
    private func signInWithPassword() {
        guard activity == nil else { return }
        errorMessage = nil
        note = nil
        activity = .signingIn
        Task {
            defer { activity = nil }
            do {
                try await appState.signIn(email: email, password: password)
            } catch {
                guard !Task.isCancelled else { return }
                report(error, during: "sign-in")
                let appError = AppError.from(error)
                if case .emailNotConfirmed = appError {
                    // The server's latest word: the address is NOT confirmed after all, so the
                    // code step is the way forward again.
                    if confirmedNeedsSignIn {
                        confirmedNeedsSignIn = false
                        confirmedNotice = nil
                    }
                    errorMessage = Self.stillUnconfirmedMessage
                } else {
                    errorMessage = Self.message(for: appError)
                }
            }
        }
    }

    // MARK: - Helpers

    /// `AppError`'s copy, minus the shapes that would mislead here: a legacy 401's "your session
    /// has expired" (there is no session yet), an `.unknown`'s text, which is never written for
    /// users, and an unstructured 404's "The requested item could not be found." (COMPAT-2).
    static func message(for appError: AppError) -> String {
        switch appError {
        case .unauthorized, .tokenExpired:
            return "We couldn't sign you in. Please try again."
        case .unknown, .cancelled:
            return "Something went wrong. Please try again."
        case .notFound:
            return routeMissingMessage
        default:
            let message = appError.message
            return message.isEmpty ? "Something went wrong. Please try again." : message
        }
    }

    /// A failure after which the server may still have acted on the request: it went out, and no
    /// readable answer came back.
    static func mayHaveReachedServer(_ appError: AppError) -> Bool {
        switch appError {
        case .timeout, .noConnection, .serverError, .unknown:
            return true
        default:
            return false
        }
    }

    /// The backend `error_code`, when the failure carried one. The FLOW keys off the code (lock a
    /// spent code); the WORDS always come from `message(for:)`.
    static func backendCode(of error: Error) -> String? {
        guard let api = error as? APIError else { return nil }
        switch api {
        case .businessError(let code, _), .authError(let code, _):
            return code
        default:
            return nil
        }
    }

    /// The failure's codes only — never the address, the code or the password.
    private func report(_ error: Error, during step: String) {
        let appError = AppError.from(error)
        let backend = Self.backendCode(of: error) ?? "none"
        if case .notFound = appError {
            // A structured 404 decodes to `.businessError`, so an unstructured one means the
            // route itself is missing: an app newer than the server it reached (a rolled-back
            // backend, or a build pointed at an old one). Contract drift, not a user error.
            Self.log.warning("confirm-email screen: \(step, privacy: .public) got an unstructured 404 — route missing on this server (contract drift)")
            return
        }
        Self.log.notice("confirm-email screen: \(step, privacy: .public) failed: \(appError.analyticsCode, privacy: .public) backend=\(backend, privacy: .public)")
    }

    private func announce(_ message: String?) {
        guard let message, !message.isEmpty else { return }
        AccessibilityNotification.Announcement(message).post()
    }

    private func primaryButton(
        title: String, busyLabel: String, isBusy: Bool, enabled: Bool,
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
        .disabled(!enabled || activity != nil)
    }

    @ViewBuilder
    private func linkButton(_ title: String, busy: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            if busy {
                ProgressView()
                    .tint(AppColors.primaryBlue)
                    .accessibilityLabel("Signing in")
            } else {
                Text(title)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.primaryBlue)
                    .multilineTextAlignment(.center)
            }
        }
        .disabled(activity != nil)
    }
}

#Preview("After sign-up") {
    ConfirmEmailView(
        email: "someone@example.com",
        password: "Sample-passw0rd",
        codeJustSent: true,
        signUpMessage: "Check your email to confirm your address.",
        onForgotPassword: {},
        onUseDifferentEmail: {}
    )
    .environment(AppState())
}

#Preview("From sign-in") {
    ConfirmEmailView(
        email: "a.very.long.address.for.layout@example.com",
        password: "Sample-passw0rd",
        codeJustSent: false,
        onForgotPassword: {},
        onUseDifferentEmail: {}
    )
    .environment(AppState())
}
