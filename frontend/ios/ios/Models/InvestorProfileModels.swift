//
//  InvestorProfileModels.swift
//  ios
//
//  DTOs for GET/PUT /users/me/investor-profile — how a reader prefers to LEARN.
//
//  ⚠️ SCOPE IS A COMPLIANCE BOUNDARY, NOT A PRODUCT ONE. These are *content*
//  preferences: experience level, explanation style, answer depth, topics of interest.
//  The app deliberately collects NO finances, risk tolerance, time horizon, tax
//  situation or goals — the five things the backend's ADVICE_BOUNDARY and the live
//  Terms §2 both state it does not know about the reader. Collecting any of them would
//  turn a relevance layer into a suitability profile, which is the line between an
//  educational publication and personalized investment advice. Do not add a
//  risk-tolerance question to this screen.
//
//  Every value is a closed enum on BOTH sides. The server maps the chosen key to
//  server-authored English before it reaches the model prompt, so no user-typed text
//  can ever steer the AI. A free-text field here would break that guarantee.
//
//  APIClient deliberately does NOT set `.convertFromSnakeCase`, so every snake_case key
//  is spelled out in CodingKeys — without it this decodes as a throw.
//

import Foundation

// MARK: - Vocabulary

/// How much investing experience the reader says they have.
enum InvestorExperienceLevel: String, Codable, CaseIterable, Sendable {
    case new
    case learning
    case experienced

    var title: String {
        switch self {
        case .new: return "Brand new"
        case .learning: return "Still learning"
        case .experienced: return "Experienced"
        }
    }

    var subtitle: String {
        switch self {
        case .new: return "Explain everything from scratch"
        case .learning: return "Define terms as they come up"
        case .experienced: return "Skip the basics"
        }
    }
}

/// How technical the language should be.
enum InvestorExplanationStyle: String, Codable, CaseIterable, Sendable {
    case plainLanguage = "plain_language"
    case balanced
    case technical

    var title: String {
        switch self {
        case .plainLanguage: return "Plain language"
        case .balanced: return "A bit of both"
        case .technical: return "Technical"
        }
    }
}

/// How long an answer should be.
enum InvestorAnswerDepth: String, Codable, CaseIterable, Sendable {
    case brief
    case standard
    case deep

    var title: String {
        switch self {
        case .brief: return "Short and direct"
        case .standard: return "A little more detail"
        case .deep: return "Go deep"
        }
    }
}

/// Subjects the reader likes to read about. Drives WHAT Cay AI covers first — never a
/// claim that anything in these areas suits them.
enum InvestorTopic: String, Codable, CaseIterable, Sendable {
    case value, growth, dividends, technology, energy
    case healthcare, crypto
    case etfIndex = "etf_index"
    case macro
    case smallCap = "small_cap"

    var title: String {
        switch self {
        case .value: return "Value"
        case .growth: return "Growth"
        case .dividends: return "Dividends"
        case .technology: return "Technology"
        case .energy: return "Energy"
        case .healthcare: return "Healthcare"
        case .crypto: return "Crypto"
        case .etfIndex: return "ETFs & Index"
        case .macro: return "The economy"
        case .smallCap: return "Small caps"
        }
    }
}

/// What the reader is trying to learn to do.
enum InvestorLearningGoal: String, Codable, CaseIterable, Sendable {
    case readFinancials = "read_financials"
    case valueABusiness = "value_a_business"
    case understandCharts = "understand_charts"
    case understandMacro = "understand_macro"
    case followSmartMoney = "follow_smart_money"

    var title: String {
        switch self {
        case .readFinancials: return "Read financial statements"
        case .valueABusiness: return "Value a business"
        case .understandCharts: return "Understand charts"
        case .understandMacro: return "Understand the economy"
        case .followSmartMoney: return "Follow smart money"
        }
    }
}

/// Which app-exclusive signal feeds the reader follows.
enum InvestorFollowSignal: String, Codable, CaseIterable, Sendable {
    case whales, congress, insiders, earnings

    var title: String {
        switch self {
        case .whales: return "Institutions"
        case .congress: return "Congress"
        case .insiders: return "Insiders"
        case .earnings: return "Earnings"
        }
    }
}

// MARK: - Response

/// `GET`/`PUT /users/me/investor-profile`.
///
/// Unknown/absent enum values decode to their default rather than throwing: the backend
/// vocabulary can grow independently of a shipped app, and a hard decode here would
/// break the profile screen for anyone on an older build.
struct InvestorProfileDTO: Codable, Sendable {
    let experienceLevel: InvestorExperienceLevel
    let explanationStyle: InvestorExplanationStyle
    let answerDepth: InvestorAnswerDepth
    let topics: [InvestorTopic]
    let learningGoals: [InvestorLearningGoal]
    let followSignals: [InvestorFollowSignal]

    /// Whether a row exists server-side at all (vs. all-defaults).
    let hasProfile: Bool
    /// True when the reader expressed no preference — nothing to personalize with.
    let isEmpty: Bool
    let profileVersion: Int
    let consentedAt: String?

    /// Whether Cay AI is ACTUALLY using this profile. Free tier saves but does not apply,
    /// so the UI can be honest instead of showing a control that does nothing.
    let applied: Bool
    /// The plan that would unlock it — named by the server so the paywall cannot drift
    /// from what was enforced.
    let requiredTier: String?
    /// Which fields the reader explicitly submitted. Lets the editor show a value the
    /// reader CHOSE differently from an untouched default — they are identical in the
    /// value columns, which is the whole reason this field exists (migration 134).
    let answeredFields: [String]
    /// Whether these answers would actually change an answer. Distinct from `isEmpty`
    /// ("stated nothing") and from `applied` ("changing answers right now"): a reader can
    /// have answered every question and still land on the house defaults, in which case
    /// there is nothing to apply but they must NOT be told they said nothing.
    let wouldPersonalize: Bool

    enum CodingKeys: String, CodingKey {
        case experienceLevel = "experience_level"
        case explanationStyle = "explanation_style"
        case answerDepth = "answer_depth"
        case topics
        case learningGoals = "learning_goals"
        case followSignals = "follow_signals"
        case hasProfile = "has_profile"
        case isEmpty = "is_empty"
        case profileVersion = "profile_version"
        case consentedAt = "consented_at"
        case applied
        case requiredTier = "required_tier"
        case answeredFields = "answered_fields"
        case wouldPersonalize = "would_personalize"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // Tolerant on purpose — see the type doc. A value the backend added after this
        // build shipped must degrade to the default, never throw on the profile screen.
        experienceLevel = (try? c.decode(InvestorExperienceLevel.self, forKey: .experienceLevel)) ?? .learning
        explanationStyle = (try? c.decode(InvestorExplanationStyle.self, forKey: .explanationStyle)) ?? .balanced
        answerDepth = (try? c.decode(InvestorAnswerDepth.self, forKey: .answerDepth)) ?? .brief
        // Element-wise: one unknown member must not discard the whole array (Swift's
        // array decode is all-or-nothing, which is how a single new value would blank
        // every selection the reader made).
        topics = (try? c.decode([String].self, forKey: .topics))?.compactMap(InvestorTopic.init(rawValue:)) ?? []
        learningGoals = (try? c.decode([String].self, forKey: .learningGoals))?
            .compactMap(InvestorLearningGoal.init(rawValue:)) ?? []
        followSignals = (try? c.decode([String].self, forKey: .followSignals))?
            .compactMap(InvestorFollowSignal.init(rawValue:)) ?? []
        hasProfile = (try? c.decode(Bool.self, forKey: .hasProfile)) ?? false
        isEmpty = (try? c.decode(Bool.self, forKey: .isEmpty)) ?? true
        profileVersion = (try? c.decode(Int.self, forKey: .profileVersion)) ?? 1
        consentedAt = try? c.decodeIfPresent(String.self, forKey: .consentedAt)
        applied = (try? c.decode(Bool.self, forKey: .applied)) ?? false
        requiredTier = try? c.decodeIfPresent(String.self, forKey: .requiredTier)
        // Both tolerant like every field above. They are also ABSENT on a backend that
        // predates them, and the defaults are the safe reading: nothing known to be
        // answered, and nothing claimed to be personalizing.
        answeredFields = (try? c.decode([String].self, forKey: .answeredFields)) ?? []
        wouldPersonalize = (try? c.decode(Bool.self, forKey: .wouldPersonalize)) ?? false
    }
}

// MARK: - Request

/// `PUT` body. Every field optional so a skipped onboarding step leaves the stored
/// value alone rather than clearing it — `nil` is omitted from the JSON entirely.
/// `nonisolated`: returned from `APIEndpoint.body`, which is nonisolated and hands it to
/// the encoder off the main actor — see `AnalyticsBatchRequest`.
nonisolated struct UpdateInvestorProfileBody: Encodable, Sendable {
    var experienceLevel: InvestorExperienceLevel?
    var explanationStyle: InvestorExplanationStyle?
    var answerDepth: InvestorAnswerDepth?
    var topics: [InvestorTopic]?
    var learningGoals: [InvestorLearningGoal]?
    var followSignals: [InvestorFollowSignal]?
    /// Set when the reader accepts the amended Terms covering preference-based
    /// personalization. Recorded rather than inferred — the feature must not apply a
    /// profile captured before that disclosure existed.
    var acceptedPersonalizationTerms: Bool?

    enum CodingKeys: String, CodingKey {
        case experienceLevel = "experience_level"
        case explanationStyle = "explanation_style"
        case answerDepth = "answer_depth"
        case topics
        case learningGoals = "learning_goals"
        case followSignals = "follow_signals"
        case acceptedPersonalizationTerms = "accepted_personalization_terms"
    }
}
