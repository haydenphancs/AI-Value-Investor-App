"""Request/response models for the investor profile (how a reader prefers to LEARN).

`Literal` rather than `str` on every field on purpose: it makes an out-of-vocabulary
value a structural 422 at the edge, BEFORE it reaches the service or Postgres. That is
the outermost of the three layers keeping user-authored text out of the rendered
preference block (edge `Literal` → `sanitize_profile` → migration 131 CHECK), which is
what lets that block be injected into the chat system instruction unfenced.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

ExperienceLevel = Literal["new", "learning", "experienced"]
ExplanationStyle = Literal["plain_language", "balanced", "technical"]
AnswerDepth = Literal["brief", "standard", "deep"]
Topic = Literal[
    "value", "growth", "dividends", "technology", "energy",
    "healthcare", "crypto", "etf_index", "macro", "small_cap",
]
LearningGoal = Literal[
    "read_financials", "value_a_business", "understand_charts",
    "understand_macro", "follow_smart_money",
]
FollowSignal = Literal["whales", "congress", "insiders", "earnings"]

# Matches user_investor_profile_service.MAX_ARRAY_LENGTH. Not a product limit — the UI
# offers ten chips at most; this only stops a client posting the vocabulary 10k times.
_MAX_ITEMS = 32


class UpdateInvestorProfileRequest(BaseModel):
    """Every field optional so a partially-completed onboarding can still save.

    An omitted field keeps the server's default rather than erroring: the onboarding
    steps are individually skippable by design, and a half-finished profile is more
    useful than none.
    """

    experience_level: Optional[ExperienceLevel] = None
    explanation_style: Optional[ExplanationStyle] = None
    answer_depth: Optional[AnswerDepth] = None
    topics: Optional[List[Topic]] = Field(default=None, max_length=_MAX_ITEMS)
    learning_goals: Optional[List[LearningGoal]] = Field(default=None, max_length=_MAX_ITEMS)
    follow_signals: Optional[List[FollowSignal]] = Field(default=None, max_length=_MAX_ITEMS)
    # Set when the user accepts the amended Terms covering preference-based
    # personalization. The feature must not apply a profile captured before that
    # disclosure existed, so this is recorded rather than inferred.
    accepted_personalization_terms: Optional[bool] = None


class InvestorProfileResponse(BaseModel):
    """The stored profile plus whether it is actually being APPLIED.

    `applied` is deliberately separate from the profile itself: every tier can SAVE a
    profile, only Pro/Max have it applied. Free users see their answers acknowledged
    and an honest reason they are not in use, rather than a feature that silently
    does nothing. `required_tier` names the same plan the server enforced so the
    paywall cannot drift from it (mirrors the signals surface).
    """

    experience_level: ExperienceLevel
    explanation_style: ExplanationStyle
    answer_depth: AnswerDepth
    topics: List[str] = Field(default_factory=list)
    learning_goals: List[str] = Field(default_factory=list)
    follow_signals: List[str] = Field(default_factory=list)

    has_profile: bool = False
    is_empty: bool = True
    profile_version: int = 1
    consented_at: Optional[str] = None

    applied: bool = False
    required_tier: Optional[str] = None
