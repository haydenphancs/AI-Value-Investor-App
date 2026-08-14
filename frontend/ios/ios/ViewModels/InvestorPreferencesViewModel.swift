//
//  InvestorPreferencesViewModel.swift
//  ios
//
//  Owns the Settings editor for the reader's learning preferences.
//
//  WHY THIS EXISTS. The preferences were captured during first-run onboarding and then
//  UNEDITABLE FOREVER: onboarding is gated behind `@AppStorage("has_completed_onboarding")`
//  and never runs again, and nothing else in the app referenced the vocabulary. Meanwhile
//  the Settings row told readers to "add some interests in Settings to give it something
//  to use" — an instruction that could not be followed for the life of the install, on a
//  feature they had just paid for and consented to.
//
//  It also makes an empty profile RECOVERABLE. A reader who skipped onboarding, or whose
//  one-shot best-effort PUT failed on a flaky first-run connection, previously had no way
//  back: their answers were gone and the feature was permanently inert.
//
//  Edits are PARTIAL by design. `UpdateInvestorProfileBody` omits nil fields and the
//  backend's `sanitize_updates` writes only the columns present, so saving this screen
//  cannot clear a field it does not show.
//

import Combine
import Foundation

@MainActor
final class InvestorPreferencesViewModel: ObservableObject {

    @Published var experienceLevel: InvestorExperienceLevel?
    @Published var explanationStyle: InvestorExplanationStyle?
    @Published var answerDepth: InvestorAnswerDepth?
    @Published var topics: Set<InvestorTopic> = []
    @Published var learningGoals: Set<InvestorLearningGoal> = []

    @Published private(set) var isLoading = false
    @Published private(set) var isSaving = false
    /// True once a load has landed. Until then the editor must not offer Save — writing
    /// the empty in-memory state over a stored profile would clear it.
    @Published private(set) var isLoaded = false
    @Published private(set) var hasLoadFailed = false
    @Published var errorMessage: String?
    /// Set after a successful save so the view can confirm. Cleared on the next edit.
    @Published var didSave = false

    private let apiClient: APIClient
    /// Guards against a late `load()` overwriting a newer confirmed save — the same
    /// generation-token pattern `ChatViewModel.seedGeneration` uses.
    private var stateGeneration: UInt64 = 0

    init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient
    }

    var canSave: Bool { isLoaded && !isSaving }

    func load() async {
        isLoading = true
        let generation = stateGeneration
        defer { isLoading = false }
        do {
            let dto = try await apiClient.request(
                endpoint: .getMyInvestorProfile,
                responseType: InvestorProfileDTO.self
            )
            // A save that landed while this read was in flight is NEWER. Applying the read
            // would silently roll the reader's edit back to the pre-save server state.
            guard generation == stateGeneration else { return }
            apply(dto)
            hasLoadFailed = false
            isLoaded = true
        } catch {
            hasLoadFailed = true
            errorMessage = AppError.from(error).message
            Analytics.shared.track(.backgroundSyncFailed, [
                "op": .string("investor_preferences_load"),
                "code": .string(AppError.from(error).analyticsCode),
            ])
        }
    }

    func save() async {
        guard canSave else { return }
        isSaving = true
        stateGeneration &+= 1
        defer { isSaving = false }
        do {
            let dto = try await apiClient.request(
                endpoint: .updateMyInvestorProfile(body: UpdateInvestorProfileBody(
                    experienceLevel: experienceLevel,
                    explanationStyle: explanationStyle,
                    answerDepth: answerDepth,
                    topics: Array(topics),
                    learningGoals: Array(learningGoals)
                )),
                responseType: InvestorProfileDTO.self
            )
            apply(dto)
            didSave = true
        } catch {
            errorMessage = AppError.from(error).message
            // A user-initiated mutation that fails must SAY SO (auth.md §6). These are the
            // reader's own answers; losing them silently is the failure this whole screen
            // exists to prevent.
            AppActions.shared.reportMutationFailure(error, action: "save your learning preferences")
        }
    }

    private func apply(_ dto: InvestorProfileDTO) {
        // Only pre-select what the reader actually ANSWERED. A stored value equal to the
        // column default is indistinguishable from "never asked" in the value columns —
        // which is exactly the confusion `answered_fields` was added to resolve — so
        // showing it as chosen would put words in their mouth.
        let answered = Set(dto.answeredFields)
        experienceLevel = answered.contains("experience_level") ? dto.experienceLevel : nil
        explanationStyle = answered.contains("explanation_style") ? dto.explanationStyle : nil
        answerDepth = answered.contains("answer_depth") ? dto.answerDepth : nil
        topics = Set(dto.topics)
        learningGoals = Set(dto.learningGoals)
    }
}
