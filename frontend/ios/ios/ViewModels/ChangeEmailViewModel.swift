//
//  ChangeEmailViewModel.swift
//  ios
//
//  State and network calls for `ChangeEmailView` (audit CC-09, 2026-11-26).
//
//  The flow, server side: `POST /auth/change-email` takes ONE proof — the current password, or
//  (an Apple/Google account has none) the recovery code `POST /auth/forgot-password` mails to the
//  current address — and Supabase's Secure email change then mails a code to the current AND the
//  new address. `POST /auth/change-email/confirm` takes both codes.
//
//  Three things this type must never do:
//    * retry a call. Every one of them is charged against a per-user limiter before any work,
//      and two of them spend single-use codes (`retryCount: 0` on all three);
//    * say anything that tells the user an address belongs to another account. The server
//      answers that case exactly like a real send, so the copy after a send is always the same
//      ("we sent a code to each address"), and a code that never arrives is the only sign;
//    * keep a secret longer than it is needed. The password and the proof code are cleared once
//      the server has the request; the two email codes once the change is done.
//

import Combine
import Foundation
import OSLog

@MainActor
final class ChangeEmailViewModel: ObservableObject {
    enum Step: Equatable {
        /// Apple/Google account: send the proof code to the current address first.
        case requestProofCode
        /// New address + proof (password, or the emailed code).
        case form
        /// The two codes Supabase sent, one per address.
        case enterCodes
        /// The account's address is now `changedEmail`.
        case done
    }

    /// Account with a password → it proves itself with it. Without one → an emailed code.
    /// Starts from the profile's `hasPassword` (unknown counts as "has one"), and switches to the
    /// code route if the server answers AUTH_PASSWORD_NOT_SET — otherwise a password-less account
    /// whose probe was unknown would be told to use a code on a screen that offered none.
    @Published private(set) var usesPassword: Bool

    @Published var step: Step
    @Published var newEmail = ""
    @Published var currentPassword = ""
    @Published var proofCode = ""
    @Published var currentCode = ""
    @Published var newCode = ""
    @Published private(set) var isSubmitting = false
    @Published var errorMessage: String?
    @Published var noticeMessage: String?
    /// The address the codes were sent to (what the user typed, as the server echoed it).
    @Published private(set) var pendingEmail: String?
    @Published private(set) var changedEmail: String?
    /// Which send a wait belongs to. Separate on purpose: the proof code arriving must not hold
    /// "Send Codes" for a minute, and each send replaces only its OWN earlier code.
    enum SendKind: Hashable {
        /// `forgotPassword` → the recovery code proving an Apple/Google account.
        case proofCode
        /// `changeEmail` → the two email-change codes.
        case changeCodes
    }

    struct Cooldown: Equatable {
        let until: Date
        /// True when the wait came from a 429's `Retry-After`: the denying limiter's whole
        /// window, an upper bound rather than a countdown.
        let isUpperBound: Bool
    }

    /// When each send unlocks again. Every send replaces the previous code of its kind, so a
    /// second tap while the first email is in flight would kill the code the user is about to get.
    @Published private(set) var cooldowns: [SendKind: Cooldown] = [:]

    static let sendCooldownSeconds = 60
    /// The longest window any limiter on these routes uses (an hour), so a malformed
    /// `Retry-After` cannot lock the button for the life of the screen.
    static let rateLimitWaitCapSeconds = 3600

    private let apiClient: APIClient
    private static let log = Logger(subsystem: "com.phan.caydex", category: "auth")

    init(usesPassword: Bool, apiClient: APIClient = .shared) {
        self.usesPassword = usesPassword
        self.apiClient = apiClient
        self.step = usesPassword ? .form : .requestProofCode
    }

    // MARK: - Derived

    var trimmedNewEmail: String {
        newEmail.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// A shape check only — the server's validator is the authority. It keeps the button off for
    /// an obviously unfinished address, never on for one the server would accept.
    var newEmailLooksValid: Bool {
        let email = trimmedNewEmail
        guard email.count <= 254, !email.contains(where: { $0.isWhitespace }) else { return false }
        let parts = email.split(separator: "@", omittingEmptySubsequences: false)
        guard parts.count == 2, !parts[0].isEmpty else { return false }
        let domain = parts[1]
        return domain.contains(".") && !domain.hasPrefix(".") && !domain.hasSuffix(".")
    }

    func isSameAsCurrent(_ currentEmail: String) -> Bool {
        trimmedNewEmail.lowercased() == currentEmail.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    func canStart(currentEmail: String) -> Bool {
        guard !isSubmitting, newEmailLooksValid, !isSameAsCurrent(currentEmail) else { return false }
        return usesPassword ? !currentPassword.isEmpty : Self.isCodeShaped(proofCode)
    }

    var canConfirm: Bool {
        !isSubmitting && Self.isCodeShaped(currentCode) && Self.isCodeShaped(newCode)
    }

    func secondsUntilSend(_ kind: SendKind, at date: Date) -> Int {
        guard let cooldown = cooldowns[kind] else { return 0 }
        return max(0, Int(cooldown.until.timeIntervalSince(date).rounded(.up)))
    }

    func waitIsUpperBound(_ kind: SendKind) -> Bool {
        cooldowns[kind]?.isUpperBound ?? false
    }

    /// 6-12 digits is the server's own window (`_normalize_otp_code`).
    static func isCodeShaped(_ code: String) -> Bool {
        (6...12).contains(normalizedCode(code).count)
    }

    /// ASCII digits only. `wholeNumberValue` folds other scripts' digits (an Arabic-Indic number
    /// pad types ٣, not 3) into the ASCII the server expects, and drops spaces and dashes.
    static func normalizedCode(_ code: String) -> String {
        String(code.compactMap { ch -> Character? in
            guard let value = ch.wholeNumberValue, (0...9).contains(value) else { return nil }
            return Character(String(value))
        })
    }

    // MARK: - Actions

    /// Apple/Google accounts: mail the recovery code to the current address — the same unchanged
    /// public route Set a Password uses, so there is no new delivery path to get wrong.
    func sendProofCode(to currentEmail: String) async {
        guard !isSubmitting, !currentEmail.isEmpty, secondsUntilSend(.proofCode, at: Date()) == 0 else { return }
        begin()
        defer { isSubmitting = false }
        do {
            _ = try await apiClient.request(
                endpoint: .forgotPassword(email: currentEmail),
                responseType: MessageResponse.self,
                retryCount: 0
            )
            startCooldown(.proofCode, seconds: Self.sendCooldownSeconds, isUpperBound: false)
            proofCode = ""
            step = .form
        } catch {
            fail(error, step: "proof-code", kind: .proofCode)
        }
    }

    func start(currentEmail: String) async {
        guard canStart(currentEmail: currentEmail), secondsUntilSend(.changeCodes, at: Date()) == 0 else { return }
        begin()
        defer { isSubmitting = false }
        let address = trimmedNewEmail
        do {
            let response = try await apiClient.request(
                endpoint: .changeEmail(
                    newEmail: address,
                    currentPassword: usesPassword ? currentPassword : nil,
                    code: usesPassword ? nil : Self.normalizedCode(proofCode)
                ),
                responseType: EmailChangeResponse.self,
                retryCount: 0
            )
            // The server has the proof now; do not keep it.
            currentPassword = ""
            proofCode = ""
            if response.isChanged {
                changedEmail = response.email
                step = .done
                return
            }
            pendingEmail = response.email.isEmpty ? address : response.email
            currentCode = ""
            newCode = ""
            startCooldown(.changeCodes, seconds: Self.sendCooldownSeconds, isUpperBound: false)
            step = .enterCodes
        } catch {
            if let code = Self.backendCode(of: error), code == "AUTH_CODE_USED" {
                // The proof code was verified — and so used up — before the change failed. The
                // code is dead; the way on is a new one, at once.
                proofCode = ""
                releaseAcceptedCooldown(.proofCode)
                if !usesPassword { step = .requestProofCode }
            }
            if Self.backendCode(of: error) == "AUTH_PASSWORD_NOT_SET" {
                // The account has no password after all (an Apple/Google account whose probe was
                // unknown). Switch to the emailed-code route the server's sentence points at.
                currentPassword = ""
                usesPassword = false
                step = .requestProofCode
            }
            fail(error, step: "start", kind: .changeCodes)
        }
    }

    /// Returns true once the account's address has changed.
    func confirm() async -> Bool {
        guard canConfirm else { return false }
        begin()
        defer { isSubmitting = false }
        do {
            let response = try await apiClient.request(
                endpoint: .confirmEmailChange(
                    currentCode: Self.normalizedCode(currentCode),
                    newCode: Self.normalizedCode(newCode)
                ),
                responseType: EmailChangeResponse.self,
                retryCount: 0
            )
            guard response.isChanged else {
                // A server that answers "codes_sent" here is not the one this build was written
                // against. Say what is true: nothing is confirmed yet.
                Self.log.error("email-change: confirm answered status=\(response.status, privacy: .public)")
                errorMessage = "We couldn't confirm the change. Please try again."
                return false
            }
            changedEmail = response.email
            currentCode = ""
            newCode = ""
            step = .done
            return true
        } catch {
            // AUTH_CODE_INVALID keeps both codes: either may be a typo, and the server's sentence
            // names the address whose code failed when it knows. "Start again" is the way out.
            fail(error, step: "confirm", kind: nil)
            return false
        }
    }

    /// Back to the beginning, keeping the typed new address. Earlier codes stop working once
    /// new ones are sent.
    func startOver() {
        currentCode = ""
        newCode = ""
        pendingEmail = nil
        errorMessage = nil
        noticeMessage = nil
        step = usesPassword ? .form : .requestProofCode
    }

    // MARK: - Helpers

    private func begin() {
        errorMessage = nil
        noticeMessage = nil
        isSubmitting = true
    }

    /// Start (or extend) a wait. Never SHORTENS one already running: the later end wins.
    private func startCooldown(_ kind: SendKind, seconds: Int, isUpperBound: Bool) {
        let until = Date().addingTimeInterval(TimeInterval(seconds))
        if let running = cooldowns[kind], running.until >= until { return }
        cooldowns[kind] = Cooldown(until: until, isUpperBound: isUpperBound)
    }

    /// Lift the wait a send the server ACCEPTED imposed, once the code it protected is spent.
    /// A 429's wait stays — that is the server's own refusal.
    private func releaseAcceptedCooldown(_ kind: SendKind) {
        if cooldowns[kind]?.isUpperBound == false { cooldowns[kind] = nil }
    }

    /// Log the failure (codes only — never an address, a code or a password) and show the
    /// user-facing words. Cancellation is not a failure and shows nothing.
    private func fail(_ error: Error, step: String, kind: SendKind?) {
        guard !Task.isCancelled else { return }
        let appError = AppError.from(error)
        guard !appError.isCancellation else { return }
        // A 429 holds the send it refused for its `Retry-After`, floored at the normal wait (a
        // 0 or negative header cannot re-open the button at once) and capped (a huge one cannot
        // lock it for the life of the screen).
        if let kind, case .rateLimited(let retryAfter)? = error as? APIError {
            startCooldown(
                kind,
                seconds: min(max(retryAfter, Self.sendCooldownSeconds), Self.rateLimitWaitCapSeconds),
                isUpperBound: true
            )
        }
        let backendCode = Self.backendCode(of: error) ?? "none"
        Self.log.warning("email-change: \(step, privacy: .public) failed: \(appError.analyticsCode, privacy: .public) backend=\(backendCode, privacy: .public)")
        errorMessage = appError.message
    }

    /// The backend `error_code`, when the failure carried one. The FLOW keys off the code; the
    /// WORDS always come from `AppError.from(error).message`.
    static func backendCode(of error: Error) -> String? {
        guard let api = error as? APIError else { return nil }
        switch api {
        case .businessError(let code, _), .authError(let code, _):
            return code
        default:
            return nil
        }
    }

    // MARK: - Previews

    static var mockCodesSent: ChangeEmailViewModel {
        let vm = ChangeEmailViewModel(usesPassword: true)
        vm.newEmail = "new-home@example.com"
        vm.pendingEmail = "new-home@example.com"
        vm.step = .enterCodes
        return vm
    }
}
