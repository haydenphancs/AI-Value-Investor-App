"""Every iOS reader of the social-mention counts has a NEUTRAL state for `known == false`.

The lesson pinned by the last pass (`project_fmp_rebuild_deepcheck_2026_09_11`): a `*_known`
companion flag that gates ONE reader and not the others turns "+0.00%" into a red arrow
beside a dash. `SentimentAnalysisData` has three readers of the social counts — the value,
the change caption and the caption colour — and all three must consult `socialKnown(for:)`.
Brace-bound to the declarations, comment-stripped, mutation-tested by hand.
"""

from __future__ import annotations

import re
from pathlib import Path

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_MODELS = _IOS / "Models" / "TickerDetailModels.swift"
_REPO = _IOS / "Core" / "Repositories" / "StockRepository.swift"


def _strip(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", l) for l in src.splitlines())


def _block(src: str, header_re: str) -> str:
    """The brace-bound body that starts at the first match of `header_re`."""
    m = re.search(header_re, src)
    assert m, f"declaration not found: {header_re}"
    i = src.index("{", m.end())
    depth, j = 0, i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
        j += 1
    raise AssertionError("unbalanced braces")


def test_every_social_reader_consults_the_known_flag():
    src = _strip(_MODELS.read_text(encoding="utf-8"))
    struct = _block(src, r"struct SentimentAnalysisData\s*")
    assert "let socialMentionsKnown: Bool" in struct and "let socialMentions7dKnown: Bool" in struct
    readers = ["formattedSocialMentions", "formattedSocialChange", "socialChangeColor"]
    for name in readers:
        body = _block(struct, rf"func {name}\(for timeframe: SentimentTimeframe\)")
        assert "socialKnown(for: timeframe)" in body, f"{name} renders a value the backend marked unknown"
    accessor = _block(struct, r"func socialKnown\(for timeframe: SentimentTimeframe\)")
    assert "socialMentionsKnown" in accessor and "socialMentions7dKnown" in accessor
    # Anti-vacuity: the readers really are inside the struct, and the value reader still
    # formats a real number when known.
    assert len(readers) == 3 and '"—"' in _block(struct, r"func formattedSocialMentions\(")
    assert "%.1fK" in _block(struct, r"func formattedSocialMentions\(")


def test_the_dto_decodes_the_flags_as_optional_and_defaults_to_measured():
    src = _strip(_REPO.read_text(encoding="utf-8"))
    dto = _block(src, r"struct SentimentAnalysisDTO\s*:\s*Codable\s*")
    # Wire rule: a shipped non-Optional field never becomes Optional; the NEW flags are
    # Optional because older backends do not send them.
    assert "let socialMentions: Double\n" in dto and "let socialMentions7d: Double\n" in dto
    assert "let socialMentionsKnown: Bool?" in dto and "let socialMentions7dKnown: Bool?" in dto
    keys = _block(dto, r"enum CodingKeys")
    assert 'case socialMentionsKnown = "social_mentions_known"' in keys
    assert 'case socialMentions7dKnown = "social_mentions_7d_known"' in keys
    mapper = _block(dto, r"func toDisplayModel\(\)")
    assert "socialMentionsKnown: socialMentionsKnown ?? true" in mapper
    assert "socialMentions7dKnown: socialMentions7dKnown ?? true" in mapper


# ── the VIEW decides whether those three readers are ever called ─────────────────────


def test_the_row_does_not_let_social_data_available_preempt_the_known_flag():
    """The three neutral readers above sat behind `if sentimentData.socialDataAvailable`,
    and that flag is computed BACKEND-side as `count_24h > 0 or count_7d > 0` — where a
    failed lookup also reports 0. So when both windows were unknown the view took the else
    branch and hardcoded "Not tracked on Reddit": a stated reason for an absence that was
    never measured, which is the exact incident the flag was added for (the 42501 that
    answered every ticker "0 mentions this week" for months).

    A `*_known` flag threaded through the schema, the service, the DTO and the readers is
    still dead if the view that calls them never runs.
    """
    row = _IOS / "Views" / "Molecules" / "SentimentMetricsRow.swift"
    src = _strip(row.read_text(encoding="utf-8"))
    body = _block(src, r"struct SentimentMetricsRow\s*:\s*View\s*")
    assert "socialDataAvailable" in body, "the availability gate vanished entirely"
    assert "socialKnown(for: selectedTimeframe)" in body, (
        "a failed lookup still renders \"Not tracked on Reddit\" as a measured fact"
    )
    # The known check must be part of the GATE, not only of a font or a dimming flag.
    gate = body[body.index("socialDataAvailable"):]
    gate = gate[:gate.index("SentimentMetricCard")]
    assert "socialKnown" in gate, (
        "the known flag is read somewhere in the row but does not affect which branch runs"
    )
    assert "!sentimentData.socialKnown(for: selectedTimeframe)" in gate


def test_the_not_tracked_copy_still_exists_for_a_measured_zero():
    """Control: a real, measured "nobody is talking about this" must still say so — the
    fix must not route every ticker into the unavailable branch."""
    row = _IOS / "Views" / "Molecules" / "SentimentMetricsRow.swift"
    src = _strip(row.read_text(encoding="utf-8"))
    assert "Not tracked on Reddit" in src



# ── News Sentiment tile: a tie has no lean (TestFlight E6, build 1.0 (8)) ────
#
# A 2/2/2 split used to fall through `bullishRatio >= bearishRatio` and print
# "33% Positive". The tie is now an INTEGER comparison checked after the zero guard
# (0/0/0 stays "N/A") and before the Mixed band; dominance is strict.

def _news_label_body() -> str:
    src = _strip(_MODELS.read_text(encoding="utf-8"))
    struct = _block(src, r"struct SentimentAnalysisData\s*")
    body = _block(struct, r"func formattedNewsArticles\(for timeframe: SentimentTimeframe\)")
    assert len(body) < len(struct) / 4, "scan is not bounded to the label function"
    return body


def test_news_label_breaks_a_tie_on_integer_counts_before_dominance():
    body = _news_label_body()
    assert "bullish == bearish" in body
    assert '"Balanced"' in body
    assert body.index("bullish == bearish") < body.index("% Positive")
    assert "bullishRatio >= bearishRatio" not in body, "a tie must not resolve to Positive"
    assert "bullishRatio > bearishRatio" in body


def test_news_label_keeps_na_for_zero_articles_ahead_of_the_tie():
    body = _news_label_body()
    assert 'if total == 0 { return "N/A" }' in body
    assert body.index('"N/A"') < body.index("bullish == bearish"), "0/0/0 would read Balanced"


def test_the_tie_word_is_explained_in_the_info_sheet_and_crypto_shares_the_reader():
    sheet = _strip((_IOS / "Views" / "Molecules" / "SentimentInfoSheet.swift").read_text(encoding="utf-8"))
    assert "Balanced" in sheet and "equal bullish and bearish" in sheet
    crypto_vm = _strip((_IOS / "ViewModels" / "CryptoDetailViewModel.swift").read_text(encoding="utf-8"))
    assert "sentimentAnalysisData = dto.toDisplayModel()" in crypto_vm, "crypto no longer maps into the shared model"
    row = _strip((_IOS / "Views" / "Molecules" / "SentimentMetricsRow.swift").read_text(encoding="utf-8"))
    assert "formattedNewsArticles(for:" in row, "the tile no longer reads the shared label"
