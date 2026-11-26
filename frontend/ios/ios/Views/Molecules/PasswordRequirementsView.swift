//
//  PasswordRequirementsView.swift
//  ios
//
//  The password rule and its live checklist, shared by every form that sets a NEW password:
//  sign-up (`SignInView`), password reset (`ForgotPasswordView`) and change / set password
//  (`ChangePasswordView`).
//
//  Moved out of `SignInView.swift`, where the checklist was `private`: the reset and change
//  forms could not reuse it and advertised only "At least 8 characters" while the server
//  demands four character classes, so a user typing `abcdefgh` learned the real rule only from
//  a 422 after submitting (IOS-AUTH-05).
//

import SwiftUI

// MARK: - Password rules

/// The new-password rule, mirrored from the backend and from the identity provider.
///
/// ⚠️ THIS MUST MATCH `_validate_password_strength` in `backend/app/schemas/auth.py`, which in
/// turn mirrors the Supabase setting `Auth → Providers → Email → Password requirements`
/// (lowercase, uppercase, digits and symbols; minimum 8). Pinned, character set by character
/// set, by `backend/tests/test_password_rule_parity.py`.
///
/// EXACT, not a superset (reversed on purpose, 2026-11-26, PW-1). The provider counts a class
/// only from ASCII: A-Z, a-z, 0-9 and the 32 symbols in `symbols`. This rule used to count any
/// `isUppercase` / `isNumber` / non-alphanumeric character, so "Blue Sky 2025" (a space as its
/// only "symbol") or "élan2025€É" turned every row green, enabled the button — and on reset /
/// set-password the server spent the single-use code before the provider refused the password,
/// three times an hour until recovery locked. A space, an accented letter or an emoji is still
/// ALLOWED in a password; it just counts toward no class.
///
/// The provider also refuses anything over 72 UTF-8 BYTES ("Password cannot be longer than 72
/// characters", checked live) — an emoji is 4 bytes, an accented letter 2 — and the backend
/// refuses a leading or trailing space. Both are gated here too, each shown as a warning row
/// only when broken.
///
/// Sign-IN must never gate on this: an existing account's password predates the rule.
enum PasswordRule {
    static let minLength = 8
    /// The provider's ceiling, in UTF-8 bytes (bcrypt reads no further).
    static let maxBytes = 72
    /// EXACTLY the provider's symbol list — 32 characters, the backslash included. Nothing
    /// else counts as a symbol: not a space, not `€`, not an em dash.
    static let symbols = "!@#$%^&*()_+-=[]{};':\"|<>?,./`~\\"

    private static let symbolSet: Set<Unicode.Scalar> = Set(symbols.unicodeScalars)
    private static let asciiUppercase: ClosedRange<Unicode.Scalar> = "A"..."Z"
    private static let asciiLowercase: ClosedRange<Unicode.Scalar> = "a"..."z"
    private static let asciiDigits: ClosedRange<Unicode.Scalar> = "0"..."9"

    /// Counted in Unicode scalars, which is what the backend's `len()` counts — never stricter
    /// than the server for a password holding an emoji.
    static func hasMinLength(_ p: String) -> Bool { p.unicodeScalars.count >= minLength }
    static func fitsMaxBytes(_ p: String) -> Bool { p.utf8.count <= maxBytes }
    static func hasNoEdgeWhitespace(_ p: String) -> Bool {
        guard let first = p.unicodeScalars.first, let last = p.unicodeScalars.last else { return true }
        return !first.properties.isWhitespace && !last.properties.isWhitespace
    }
    static func hasUppercase(_ p: String) -> Bool { p.unicodeScalars.contains { asciiUppercase.contains($0) } }
    static func hasLowercase(_ p: String) -> Bool { p.unicodeScalars.contains { asciiLowercase.contains($0) } }
    static func hasDigit(_ p: String) -> Bool { p.unicodeScalars.contains { asciiDigits.contains($0) } }
    static func hasSymbol(_ p: String) -> Bool { p.unicodeScalars.contains { symbolSet.contains($0) } }

    static func isSatisfied(_ p: String) -> Bool {
        hasMinLength(p) && fitsMaxBytes(p) && hasNoEdgeWhitespace(p)
            && hasUppercase(p) && hasLowercase(p) && hasDigit(p) && hasSymbol(p)
    }
}

// MARK: - Checklist

/// The rule, shown as a live checklist rather than as an error after submitting.
struct PasswordRequirementsView: View {
    let password: String

    static let tooLongWarning =
        "Too long: \(PasswordRule.maxBytes) bytes at most (emoji and accented letters use more than one)"
    static let edgeSpaceWarning = "No space at the start or end"

    init(password: String) {
        self.password = password
    }

    private var items: [(label: String, met: Bool)] {
        [
            ("\(PasswordRule.minLength)+ characters", PasswordRule.hasMinLength(password)),
            ("An uppercase letter (A–Z)", PasswordRule.hasUppercase(password)),
            ("A lowercase letter (a–z)", PasswordRule.hasLowercase(password)),
            ("A number (0–9)", PasswordRule.hasDigit(password)),
            ("A symbol such as ! @ # $ — spaces don't count", PasswordRule.hasSymbol(password)),
        ]
    }

    /// Rules almost no password comes near, so they are shown only when broken — a permanent
    /// row would be noise. Without them the button would sit disabled with every row green.
    private var warnings: [String] {
        var out: [String] = []
        if !PasswordRule.fitsMaxBytes(password) { out.append(Self.tooLongWarning) }
        if !PasswordRule.hasNoEdgeWhitespace(password) { out.append(Self.edgeSpaceWarning) }
        return out
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxs) {
            ForEach(items, id: \.label) { item in
                HStack(spacing: AppSpacing.xs) {
                    // `gain` is a TEXT-role token (4.5:1 in both appearances) — correct for a
                    // glyph read as meaning. `textMuted` for unmet, NOT `loss`: nothing is
                    // wrong yet, the user is still typing, and painting the whole list red on
                    // an empty field reads as five errors before a single keystroke.
                    Image(systemName: item.met ? "checkmark.circle.fill" : "circle")
                        .font(AppTypography.iconXS)
                        .foregroundColor(item.met ? AppColors.gain : AppColors.textMuted)
                    Text(item.label)
                        .font(AppTypography.caption)
                        .foregroundColor(item.met ? AppColors.textSecondary : AppColors.textMuted)
                }
            }
            // A warning IS something wrong with what was typed, so it is `loss` (text role).
            ForEach(warnings, id: \.self) { warning in
                HStack(alignment: .firstTextBaseline, spacing: AppSpacing.xs) {
                    Image(systemName: "exclamationmark.circle.fill")
                        .font(AppTypography.iconXS)
                        .foregroundColor(AppColors.loss)
                    Text(warning)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.loss)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(accessibilitySummary)
    }

    private var accessibilitySummary: String {
        let rows = items.map { "\($0.label): \($0.met ? "met" : "not met")" }.joined(separator: ", ")
        let alerts = warnings.map { ". Warning: \($0)" }.joined()
        return "Password requirements. " + rows + alerts
    }
}

#Preview {
    VStack(alignment: .leading, spacing: 24) {
        PasswordRequirementsView(password: "")
        PasswordRequirementsView(password: "abcdefgh")
        // A space is allowed but counts as no symbol: the symbol row stays open.
        PasswordRequirementsView(password: "Blue Sky 2025")
        PasswordRequirementsView(password: "Abcdefg1!")
        PasswordRequirementsView(password: "Abcdefg1! " + String(repeating: "🙂", count: 16))
    }
    .padding()
    .background(AppColors.background)
}
