//
//  PersonalizationConsentViewModel.swift
//  ios
//
//  Owns the "Personalized explanations" opt-in.
//
//  WHY THE CONSENT LIVES ON THE SERVER, not in UserDefaults like AIConsentStore:
//  this one gates a SERVER-side behaviour (whether the backend folds the reader's
//  preferences into the chat system instruction). A device-local flag would let a second
//  device silently personalize for a user who only consented on the first, and would be
//  lost on reinstall. The backend refuses to apply any profile whose `consented_at` is
//  null, so the stored timestamp IS the gate — not a mirror of it.
//
//  It is deliberately revocable. Consent that cannot be withdrawn is not consent; the
//  PUT sends `accepted_personalization_terms: false` to clear the timestamp, which stops
//  personalization on the very next turn.
//

import Combine
import Foundation

@MainActor
final class PersonalizationConsentViewModel: ObservableObject {

    /// Whether the reader has consented (mirrors the server's `consented_at`).
    @Published private(set) var isOn = false
    /// Whether Cay AI is ACTUALLY using it — consent AND an entitled tier AND a
    /// non-empty profile. Shown so a Free user is told the truth rather than being
    /// given a toggle that does nothing.
    @Published private(set) var isApplied = false
    /// The plan that would unlock it, straight from the server so the copy cannot drift
    /// from what was enforced.
    @Published private(set) var requiredTier: String?
    /// True when the reader has actually answered some preference questions.
    @Published private(set) var hasPreferences = false
    /// True when those answers would actually change an answer. Distinct from
    /// `hasPreferences`: a reader can answer every question and land on the house
    /// defaults, which renders nothing — they have stated something, but there is
    /// nothing to apply, and the copy must say so rather than accusing them of silence.
    @Published private(set) var wouldPersonalize = false

    @Published private(set) var isLoading = false
    @Published private(set) var isSaving = false
    @Published var errorMessage: String?
    /// True when the last load failed, so consent state is UNKNOWN. Callers that gate a
    /// flow on it (the paywall) must fall through rather than guess — offering the opt-in
    /// to someone who already consented is annoying; dead-ending a purchase is worse.
    @Published private(set) var hasLoadFailed = false

    private let apiClient: APIClient

    init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient
    }

    func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            let dto = try await apiClient.request(
                endpoint: .getMyInvestorProfile,
                responseType: InvestorProfileDTO.self
            )
            apply(dto)
            hasLoadFailed = false
        } catch {
            // Read-only failure: leave the toggle in its last known state rather than
            // flipping it to a value the server never confirmed. Reported, not swallowed.
            hasLoadFailed = true
            errorMessage = AppError.from(error).message
            Analytics.shared.track(.backgroundSyncFailed, [
                "op": .string("personalization_consent_load"),
                "code": .string(AppError.from(error).analyticsCode),
            ])
        }
    }

    /// Grant or revoke. Not optimistic on purpose: this is a consent record, so the UI
    /// must show what the SERVER stored, never what we hoped it stored. A failed grant
    /// that looked successful would be the worst possible bug in this particular switch.
    func setConsent(_ granted: Bool) async {
        guard !isSaving else { return }
        isSaving = true
        defer { isSaving = false }
        do {
            let dto = try await apiClient.request(
                endpoint: .updateMyInvestorProfile(
                    body: UpdateInvestorProfileBody(acceptedPersonalizationTerms: granted)
                ),
                responseType: InvestorProfileDTO.self
            )
            apply(dto)
            // Two literal call sites, not a ternary or a `let`: the analytics guard
            // greps for `Analytics.shared.track(.someCase`, so an event name reached
            // through a variable is invisible to it and the metric silently reads
            // "nobody does this" instead of "not instrumented".
            if granted {
                Analytics.shared.track(.personalizationConsentGranted)
            } else {
                Analytics.shared.track(.personalizationConsentWithdrawn)
            }
        } catch {
            errorMessage = AppError.from(error).message
            Analytics.shared.track(.backgroundSyncFailed, [
                "op": .string("personalization_consent_save"),
                "code": .string(AppError.from(error).analyticsCode),
            ])
            // A user-initiated mutation that fails must SAY SO. `errorMessage` was set here
            // and rendered nowhere, so a failed grant looked exactly like a UI glitch: the
            // switch animated on, snapped back, and nothing was shown. auth.md §6 bans that
            // pattern precisely because ~20 revert-and-say-nothing sites survived on it —
            // and this one is a consent record, where "did that save?" is the whole question.
            // Worse in reverse: a failed REVOKE leaves the user believing they withdrew
            // consent while the server keeps personalizing.
            AppActions.shared.reportMutationFailure(
                error,
                action: granted ? "turn on personalized explanations"
                                : "turn off personalized explanations"
            )
        }
    }

    private func apply(_ dto: InvestorProfileDTO) {
        isOn = dto.consentedAt != nil
        isApplied = dto.applied
        requiredTier = dto.requiredTier
        hasPreferences = !dto.isEmpty
        wouldPersonalize = dto.wouldPersonalize
        hasLoadFailed = false
    }

    /// Copy for the row's subtitle — honest about which of the four conditions is the
    /// one currently stopping it.
    ///
    /// The last two branches used to be one, reading `requiredTier ?? "Pro"`. That was
    /// wrong for the case this build actually ships in: the server's `applied` now also
    /// reflects the `CHAT_PERSONALIZATION_ENABLED` feature flag, so a consented Pro
    /// subscriber can be `!isApplied` with NO tier problem at all — and the old copy told
    /// them "available on Pro", i.e. upgrade to the plan they are already on. `requiredTier`
    /// is nil exactly when the tier is not the blocker, so it separates the two cleanly.
    var statusText: String {
        // UNKNOWN is not OFF. On a failed load `isOn` keeps its initial `false`, so the row
        // used to state "Off — answers are the same for everyone" with total confidence while
        // the server's `consented_at` was set and Cay AI was personalizing on the very next
        // turn. The type doc above says callers gating on `hasLoadFailed` must not guess;
        // this is the screen the user acts on, so it must not guess either.
        if hasLoadFailed { return "Couldn't check — tap to retry" }
        if !isOn { return "Off — answers are the same for everyone" }
        // "Stated nothing" and "stated things that change nothing" are DIFFERENT, and the
        // old copy answered both with the first. A reader who chose "Still learning" +
        // "A bit of both" — the middle option on both onboarding questions — matches the
        // house defaults, so nothing renders into the prompt. Telling them to go and add
        // preferences was simply false: they had answered. See migration 134.
        if !hasPreferences {
            return "On — add your preferences in Learning Preferences above"
        }
        if !wouldPersonalize {
            return "On — your answers match our defaults, so replies look the same. Add a subject to see a difference"
        }
        if !isApplied {
            if let plan = requiredTier {
                return "On — available on \(plan.capitalized)"
            }
            return "On — not switched on yet, we'll start using it soon"
        }
        return "On — Cay AI tailors how it explains things"
    }
}
