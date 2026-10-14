"""What the credit surfaces SAY — the copy half of the 2026-10-12 credits pass.

Companion to `test_ios_credits_single_source.py` (which pins where the balance comes from and when
it is re-read). These pin what the screens tell the user about it:

1. An empty monthly pool still says when it resets — "No credits remaining · monthly credits
   reset Nov 1, 2026" — and a placeholder date (no `resets_at`) is never stated as the account's.
2. The Account card keeps its visual "Monthly Credits 4/50" EXACTLY (owner, 2026-10-12: "4/50 is
   correct way" — USED of the monthly total) and gains a VoiceOver reading that says "4 of 50
   used" instead of the ambiguous "4 slash 50"; accessibility modifiers only.
3. Counts agree in number ("1 credit", never "1 credits").
4. The comments the audit found false stay gone, and the one CODE fact behind the worst of them
   (`.getUserCredits` is `.signInRequired`) is asserted directly.
5. A failed report's refund chip never claims credits came back before the server confirmed it:
   `is_refunded` stays the CLAIM flag (Retry's gate keys on it) and the separate optional
   `refund_confirmed` drives "Refund pending".
6. The ledger's new `expire` kind (unused monthly credits retired at a reset — migration 194) is
   drawn as routine, not as a warning.

Same house style as its companion: comments stripped, scans brace-bound with presence asserted
first, and an in-memory mutation table that must fail with each guard's own message.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios" / "ios"
_MODELS = _IOS / "Models" / "ResearchModels.swift"
_PROFILE = _IOS / "Views" / "Screens" / "ProfileView.swift"
_PROFILE_VM = _IOS / "ViewModels" / "ProfileViewModel.swift"
_BADGE = _IOS / "Views" / "Atoms" / "CreditsBadge.swift"
_CARD = _IOS / "Views" / "Molecules" / "CreditsBalanceCard.swift"
_BUY = _IOS / "Views" / "Screens" / "BuyCreditsView.swift"
_ENDPOINT = _IOS / "Core" / "Services" / "APIEndpoint.swift"
_APP_STATE = _IOS / "Core" / "State" / "AppState.swift"
_CONTENT = _IOS / "ContentView.swift"
_LEARN_VM = _IOS / "ViewModels" / "LearnViewModel.swift"
_POLL = _IOS / "Core" / "Services" / "TaskPollingManager.swift"
_VM = _IOS / "ViewModels" / "ResearchViewModel.swift"
_REPORT_CARD = _IOS / "Views" / "Molecules" / "ReportCard.swift"
_HISTORY = _IOS / "Models" / "CreditHistoryModels.swift"
_HISTORY_SERVICE = _REPO / "backend" / "app" / "services" / "credit_history_service.py"


# ── helpers ──────────────────────────────────────────────────────────────────

def _strip(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for raw in src.splitlines():
        if raw.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", raw))
    return "\n".join(out)


def _code(path: pathlib.Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return _strip(path.read_text(encoding="utf-8"))


def _prose(path: pathlib.Path) -> str:
    """The RAW source with comment markers dropped and whitespace collapsed, so a sentence that
    wraps across comment lines reads as one sentence."""
    assert path.exists(), f"guard is stale — {path.name} moved"
    text = re.sub(r"(?m)^\s*///?\s?", " ", path.read_text(encoding="utf-8"))
    return re.sub(r"\s+", " ", text)


def _balanced(src: str, start: int) -> str:
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start: i + 1]
    raise AssertionError(f"unbalanced braces from offset {start}")


def _block(src: str, header: str) -> str:
    assert src.count(header) == 1, f"expected exactly one `{header}`, found {src.count(header)}"
    return _balanced(src, src.index("{", src.index(header) + len(header)))


def _struct(src: str, name: str) -> str:
    m = re.search(rf"^[ \t]*struct[ \t]+{name}\b[^{{\n]*\{{", src, re.M)
    assert m, f"struct {name} not found — re-derive this scan"
    return _balanced(src, m.end() - 1)


def _modifiers_after(src: str, block_start: int) -> str:
    """The modifier chain that follows the balanced block opened at `block_start`, up to the
    next blank line (comments are stripped, so a commented sibling leaves one)."""
    end = block_start + len(_balanced(src, block_start))
    tail = src[end:]
    stop = re.search(r"\n[ \t]*\n", tail)
    return tail[: stop.start() if stop else len(tail)]


# ── 1. an empty monthly pool still states its reset date ─────────────────────


def test_an_empty_monthly_pool_still_states_its_reset_date():
    models = _code(_MODELS)
    balance = _struct(models, "CreditBalance")
    summary = _block(balance, "var compositionSummary: String")
    for arm in (r"case\s*\(\.none,\s*\.none\)", r"case\s*\(\.none,\s*\.some\(let kept\)\)"):
        m = re.search(arm, summary)
        assert m, f"compositionSummary lost its `{arm}` arm — re-derive"
        nxt = re.search(r"\bcase\b", summary[m.end():])
        body = summary[m.end(): m.end() + (nxt.start() if nxt else len(summary))]
        assert "monthlyResetSummary" in body, (
            "an empty monthly pool no longer says when the monthly credits come back")
    assert "renews.lowercased()" not in summary, "the composed line lowercases the MONTH again"
    reset = _block(balance, "var monthlyResetSummary: String?")
    guard = re.search(r"guard\s+renewalDateIsKnown\s+else\s*\{\s*return\s+nil\s*\}", reset)
    assert guard and "renewalDateText" in reset[guard.end():], (
        "monthlyResetSummary states a PLACEHOLDER reset date (no resets_at) as the account's")
    assert "ResearchFormatters.creditResetFormatter" in _block(balance, "private var renewalDateText: String"), (
        "the reset date is no longer formatted with the ET-pinned creditResetFormatter")
    for header in ("static func from(_ response: BackendCreditsResponse) -> CreditBalance",
                   "static func from(_ info: CreditInfo) -> CreditBalance"):
        body = _block(models, header)
        assert re.search(r"renewalDateIsKnown:\s*Self\.parseResetDateIfPresent\(\w+\.resetsAt\)\s*!=\s*nil", body), (
            f"`{header}` no longer derives renewalDateIsKnown from the server's resets_at")
    present = _block(models, "static func parseResetDateIfPresent(_ resetsAt: String?) -> Date?")
    assert re.search(r"guard\s+let\s+resetsAt\s+else\s*\{\s*return\s+nil\s*\}", present), (
        "parseResetDateIfPresent invents a date for an absent resets_at")


# ── 2. the Account card: used/total visual unchanged, VoiceOver says "used" ──


def test_the_account_card_reads_used_semantics_without_changing_the_visual():
    view = _code(_PROFILE)
    section = _block(view, "private var creditManagementSection: some View")
    visual = 'Text("\\(viewModel.creditsUsed)/\\(viewModel.creditsTotal)")'
    assert section.count(visual) == 1, (
        "the Account card's visual 'Monthly Credits used/total' changed — the owner kept it as is")
    at = section.index(visual)
    style = section[at: at + 200]
    assert ".font(AppTypography.dataHeading)" in style and ".foregroundColor(AppColors.primaryBlue)" in style, (
        "the Account card's visual 'Monthly Credits used/total' changed — the owner kept it as is")
    hstack = section.rindex("HStack {", 0, at)
    chain = _modifiers_after(section, section.index("{", hstack))
    for modifier in (".accessibilityElement(children: .ignore)", '.accessibilityLabel("Monthly credits")',
                     ".accessibilityValue(viewModel.creditUsageAccessibilityValue)"):
        assert modifier in chain, (
            f"the Account card's usage row lost `{modifier}` — VoiceOver reads '4 slash 50' again")
    bar = section.index("GeometryReader")
    assert ".accessibilityHidden(true)" in _modifiers_after(section, section.index("{", bar)), (
        "the usage bar is spoken separately again — its content is already in the row's value")
    reset = section.index("Text(viewModel.creditResetLabel)")
    assert ".accessibilityHidden(true)" in section[reset: section.index("Spacer()", reset)], (
        "the reset label is spoken separately again — its content is already in the row's value")
    value = _block(_code(_PROFILE_VM), "var creditUsageAccessibilityValue: String")
    for token in ("creditsUsed", "creditsTotal", " of ", " used", "never expire"):
        assert token in value, (
            f"the VoiceOver value no longer reads used-of-total semantics (`{token}` missing)")
    assert "creditsRemaining" not in value, (
        "the VoiceOver value no longer reads used-of-total semantics (`creditsRemaining` used)")


# ── 3. counts agree in number ────────────────────────────────────────────────


def test_credit_counts_agree_in_number():
    badge = _code(_BADGE)
    assert 'credits == 1 ? "credit" : "credits"' in badge and "\\(unit) remaining" in badge, (
        'CreditsBadge says "1 credits" again')
    assert "\\(credits) credits remaining" not in badge and 'Text("credits")' not in badge, (
        'CreditsBadge says "1 credits" again')
    card = _code(_CARD)
    assert 'balance.credits == 1 ? "credit" : "credits"' in card and 'Text("credits")' not in card, (
        'CreditsBalanceCard says "1 credits" again')
    buy = _code(_BUY)
    assert 'credits.remaining == 1 ? "credit available" : "credits available"' in buy and (
        'Text("credits available")' not in buy), 'Buy Credits says "1 credits available" again'
    chip = _block(_code(_MODELS), "var refundChipText: String?")
    assert 'amount == 1 ? "credit" : "credits"' in chip, 'the refund chip says "Refunded 1 credits" again'


# ── 4. the credits comments tell the truth ───────────────────────────────────

_FALSE_CLAIMS = (
    (_PROFILE_VM, "`.getUserCredits` is `.guestAllowed`"),
    (_ENDPOINT, "even though its sibling `.getUserCredits` above is"),
    (_APP_STATE, "fan-out, `ResearchViewModel.loadCredits()`, and `refreshEntitlement()`"),
    (_CONTENT, "the Wiser tab — which reads `appState.user.credits`"),
    (_LEARN_VM, "The Learn screen now reads `appState.user.credits`"),
    (_BUY, "refreshed only by an entitlement change"),
    (_POLL, "feeds `CreditBalance` on the Research and Learn cards"),
    (_VM, "it refreshes on init / completion only"),
    (_VM, "keeps its OWN `creditBalance` (it has no `AppState` reference)"),
)


def test_the_credits_comments_tell_the_truth():
    policy = _block(_code(_ENDPOINT), "nonisolated var authPolicy: AuthPolicy")
    at = policy.index(".getUserCredits")
    ret = re.search(r"return\s+\.(\w+)", policy[at:])
    assert ret and ret.group(1) == "signInRequired", (
        ".getUserCredits is no longer `.signInRequired` — the comments below describe a route "
        "that changed")
    stale = [f"{path.name}: {claim}" for path, claim in _FALSE_CLAIMS if claim in _prose(path)]
    assert not stale, f"a false credits comment is back: {stale}"


# ── 5. the refund chip never claims an unconfirmed refund ────────────────────


def test_a_refund_chip_never_claims_an_unconfirmed_refund():
    poll = _code(_POLL)
    dto = _struct(poll, "BackendReportListItem")
    assert "let isRefunded: Bool?" in dto and 'case isRefunded = "is_refunded"' in dto, (
        "the list DTO no longer decodes is_refunded — Retry's gate keys on that CLAIM flag")
    assert "let refundConfirmed: Bool?" in dto and 'case refundConfirmed = "refund_confirmed"' in dto, (
        "the list DTO no longer decodes refund_confirmed")
    decoder = _block(poll, "extension BackendReportListItem: Decodable")
    assert re.search(r"self\.refundConfirmed\s*=\s*try\s+container\.decodeIfPresent\(Bool\.self,\s*forKey:\s*\.refundConfirmed\)", decoder), (
        "the list DTO no longer decodes refund_confirmed")
    models = _code(_MODELS)
    report = _struct(models, "AnalysisReport")
    assert "var refundConfirmed: Bool? = nil" in report, "AnalysisReport lost refundConfirmed"
    assert "refundConfirmed: item.refundConfirmed" in _block(models, "static func from(_ item: BackendReportListItem) -> AnalysisReport"), (
        "AnalysisReport.from(_:) drops refund_confirmed — every card reads 'Refunded' again")
    assert "refundConfirmed: refundConfirmed" in _block(report, "func withClientTimeout() -> AnalysisReport"), (
        "withClientTimeout() drops refundConfirmed")
    chip = _block(report, "var refundChipText: String?")
    guard = re.search(r"guard\s+status\s*==\s*\.failed\s*,\s*isRefunded\s+else\s*\{\s*return\s+nil\s*\}", chip)
    pending = chip.find('if refundConfirmed == false { return "Refund pending" }')
    refunded = chip.find('return "Refunded')
    assert guard and guard.end() <= pending < refunded, (
        "the refund chip claims 'Refunded' before checking that the refund was confirmed")
    assert re.search(r"var\s+refundIsPending:\s*Bool\s*\{\s*status\s*==\s*\.failed\s*&&\s*isRefunded\s*&&\s*"
                     r"refundConfirmed\s*==\s*false\s*\}", report), "refundIsPending no longer means claimed-but-unconfirmed"
    # Degraded input: an older backend, or a refund from before migration 195, sends no
    # refund_confirmed (nil). That is UNKNOWN, not pending — the legacy chip stands.
    nil_as_pending = re.search(r"refundConfirmed\s*(?:!=\s*true|\?\?)", chip + _block(report, "var refundIsPending: Bool"))
    assert not nil_as_pending, (
        "a legacy row (refund_confirmed nil) reads 'Refund pending' — only an explicit false may")
    card = _code(_REPORT_CARD)
    assert "report.refundChipText" in card and '"Refunded ' not in card, (
        "ReportCard builds its own 'Refunded' text again instead of the confirmation-aware chip")
    assert "report.refundIsPending ? AppColors.caution : AppColors.textSecondary" in card, (
        "a pending refund is no longer set apart in the caution TEXT token")
    retry = _block(_code(_VM), "func retryReport(_ report: AnalysisReport)")
    assert "if backendId != nil, report.isRefunded" in retry, (
        "Retry's pre-delete gate no longer keys on the CLAIM flag (is_refunded)")


# ── 6. an expired-credits ledger row is drawn as routine ─────────────────────


def _kind_return(block: str, kind: str) -> str:
    m = re.search(rf'case\s+[^:\n]*"{kind}"[^:\n]*:\s*return\s+([^\n]+)', block)
    assert m, f'the `"{kind}"` kind has no arm — re-derive'
    return m.group(1).strip()


def test_an_expired_credits_row_is_neutral():
    history = _code(_HISTORY)
    icon = _block(history, "var iconName: String")
    assert _kind_return(icon, "expire") == '"hourglass"', (
        'the `expire` ledger kind no longer has its neutral hourglass icon')
    color = _kind_return(_block(history, "var iconColor: Color"), "expire")
    assert color == "AppColors.textSecondary", (
        f"the `expire` ledger kind is tinted `{color}` — an expiry is routine, never a warning")
    service = _HISTORY_SERVICE.read_text(encoding="utf-8")
    backend = re.search(r'^KIND_EXPIRE\s*=\s*"([^"]+)"', service, re.M)
    # The backend constant lands with migration 194's mapping; once it exists it must agree.
    if backend:
        assert backend.group(1) == "expire", (
            f"the backend sends kind {backend.group(1)!r} but iOS draws 'expire'")


# ── mutation table ───────────────────────────────────────────────────────────

_C1 = test_an_empty_monthly_pool_still_states_its_reset_date
_C2 = test_the_account_card_reads_used_semantics_without_changing_the_visual
_C3 = test_credit_counts_agree_in_number
_C4 = test_the_credits_comments_tell_the_truth
_C5 = test_a_refund_chip_never_claims_an_unconfirmed_refund
_C6 = test_an_expired_credits_row_is_neutral

_EMPTY_ARM = (
    "        case (.none, .none):\n"
    "            guard let reset = monthlyResetSummary else { return \"No credits remaining\" }\n"
    "            return \"No credits remaining · \\(reset.lowercasedFirstLetter)\"\n")
_KNOWN_GUARD = "        guard renewalDateIsKnown else { return nil }\n"
_A11Y_VALUE = "                    .accessibilityValue(viewModel.creditUsageAccessibilityValue)\n"

_MUTATIONS = [
    ("E1-empty-pool-bare", _MODELS,
     ((_EMPTY_ARM, "        case (.none, .none):\n            return \"No credits remaining\"\n"),),
     _C1, "an empty monthly pool no longer says when the monthly credits come back"),
    ("E2-placeholder-date-stated", _MODELS, ((_KNOWN_GUARD, ""),),
     _C1, "monthlyResetSummary states a PLACEHOLDER reset date"),
    ("E3-info-mapper-always-known", _MODELS,
     (("            purchasedCredits: info.purchasedCredits,\n"
       "            renewalDateIsKnown: Self.parseResetDateIfPresent(info.resetsAt) != nil\n",
       "            purchasedCredits: info.purchasedCredits,\n            renewalDateIsKnown: true\n"),),
     _C1, "no longer derives renewalDateIsKnown from the server's resets_at"),
    ("V1-visual-shows-remaining", _PROFILE,
     (('Text("\\(viewModel.creditsUsed)/\\(viewModel.creditsTotal)")',
       'Text("\\(viewModel.creditsRemaining)/\\(viewModel.creditsTotal)")'),),
     _C2, "the Account card's visual 'Monthly Credits used/total' changed"),
    ("V2-value-from-remaining", _PROFILE_VM,
     (('        var parts = ["\\(creditsUsed) of \\(creditsTotal) used", creditResetLabel]\n',
       '        var parts = ["\\(creditsRemaining) of \\(creditsTotal) left", creditResetLabel]\n'),),
     _C2, "the VoiceOver value no longer reads used-of-total semantics"),
    ("V3-value-dropped", _PROFILE, ((_A11Y_VALUE, ""),),
     _C2, "lost `.accessibilityValue(viewModel.creditUsageAccessibilityValue)`"),
    ("V4-bar-spoken", _PROFILE,
     (("                    .frame(height: 8)\n                    .accessibilityHidden(true)\n",
       "                    .frame(height: 8)\n"),),
     _C2, "the usage bar is spoken separately again"),
    ("N1-badge-plural-only", _BADGE,
     (('"You have \\(credits) \\(unit) remaining"', '"You have \\(credits) credits remaining"'),),
     _C3, 'CreditsBadge says "1 credits" again'),
    ("N2-card-plural-only", _CARD,
     (('Text(balance.credits == 1 ? "credit" : "credits")', 'Text("credits")'),),
     _C3, 'CreditsBalanceCard says "1 credits" again'),
    ("F1-learn-claim-back", _LEARN_VM,
     (("    // that contradicted the real one on Research and Profile. The Learn screen renders\n",
       "    // that contradicted the real one on Research and Profile. The Learn screen now\n"
       "    // reads `appState.user.credits`, the documented single source of truth.\n"
       "    // The Learn screen renders\n"),),
     _C4, "a false credits comment is back"),
    ("F2-wiser-claim-back", _CONTENT,
     (("    /// The balance to display: `AppState.user.credits`, the single source",
       "    /// while the Wiser tab — which reads\n    /// `appState.user.credits`, the documented single source of truth — shows it.\n"
       "    /// The balance to display: `AppState.user.credits`, the single source"),),
     _C4, "a false credits comment is back"),
    ("R1-chip-ignores-confirmation", _MODELS,
     (('        if refundConfirmed == false { return "Refund pending" }\n', ""),),
     _C5, "the refund chip claims 'Refunded' before checking that the refund was confirmed"),
    ("R2-mapper-drops-confirmation", _MODELS,
     (("            processingStartedAt: processingStartedAt,\n            refundConfirmed: item.refundConfirmed\n",
       "            processingStartedAt: processingStartedAt\n"),),
     _C5, "AnalysisReport.from(_:) drops refund_confirmed"),
    ("R3-card-hardcodes-refunded", _REPORT_CARD,
     (("                            Text(refundLine)\n",
       "                            Text(\"Refunded \\(report.creditsCharged ?? 20) credits\")\n"),),
     _C5, "ReportCard builds its own 'Refunded' text again"),
    ("R5-nil-reads-as-pending", _MODELS,
     (('        if refundConfirmed == false { return "Refund pending" }\n',
       '        if refundConfirmed != true { return "Refund pending" }\n'),),
     _C5, "the refund chip claims 'Refunded' before checking"),
    ("R6-pending-flag-counts-nil", _MODELS,
     (("    var refundIsPending: Bool { status == .failed && isRefunded && refundConfirmed == false }\n",
       "    var refundIsPending: Bool { status == .failed && isRefunded && refundConfirmed != true }\n"),),
     _C5, "refundIsPending no longer means claimed-but-unconfirmed"),
    ("R4-dto-drops-key", _POLL,
     (("        self.refundConfirmed = try container.decodeIfPresent(Bool.self, forKey: .refundConfirmed)\n",
       "        self.refundConfirmed = nil\n"),),
     _C5, "the list DTO no longer decodes refund_confirmed"),
    ("X1-expire-as-caution", _HISTORY,
     (('        case "expire":                      return AppColors.textSecondary\n',
       '        case "expire":                      return AppColors.caution\n'),),
     _C6, "the `expire` ledger kind is tinted `AppColors.caution`"),
    ("X2-expire-icon-gone", _HISTORY,
     (('        case "expire":   return "hourglass"\n', ""),),
     _C6, 'the `"expire"` kind has no arm'),
]


@pytest.mark.parametrize(
    "path,edits,test,message",
    [m[1:] for m in _MUTATIONS],
    ids=[m[0] for m in _MUTATIONS],
)
def test_each_mutation_is_killed(monkeypatch, path, edits, test, message):
    """Each guard above must go red on the regression it names, WITH the message that names
    it. Patched in memory only — the Swift files are never rewritten."""
    real_read_text = pathlib.Path.read_text
    original = real_read_text(path, encoding="utf-8")
    mutated = original
    for old, new in edits:
        assert mutated.count(old) == 1, (
            f"mutation anchor `{old[:60]!r}` occurs {mutated.count(old)}x in {path.name} — "
            "re-derive this mutation against the new source rather than deleting it")
        mutated = mutated.replace(old, new, 1)
    assert mutated != original

    def fake_read_text(self, *args, **kwargs):
        if pathlib.Path(self) == path:
            return mutated
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", fake_read_text)
    with pytest.raises(AssertionError, match=re.escape(message)):
        test()


def test_the_mutation_table_covers_every_guard():
    covered = {m[3] for m in _MUTATIONS}
    for guard in (_C1, _C2, _C3, _C4, _C5, _C6):
        assert guard in covered, f"{guard.__name__} has no mutation in _MUTATIONS"
    names = [m[0] for m in _MUTATIONS]
    assert len(names) == len(set(names)), "duplicate mutation ids"


def test_the_prose_reader_joins_wrapped_comment_lines():
    """`_prose` must read a sentence that wraps across `//` lines as one sentence, or the
    false-claim scan above passes on a claim that simply wraps differently."""
    raw = _LEARN_VM.read_text(encoding="utf-8")
    assert "The Learn screen renders\n    // no balance at all" in raw
    assert "The Learn screen renders no balance at all" in _prose(_LEARN_VM)
