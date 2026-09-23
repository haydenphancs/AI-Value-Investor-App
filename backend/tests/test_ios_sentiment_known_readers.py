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
    # F19-8: "Not tracked" is a both-windows claim. A measured-zero 24 h beside an unknown
    # 7 d used to reach it because only the SELECTED window was checked.
    assert "!sentimentData.socialBothWindowsKnown" in gate, (
        "the gate must also require BOTH windows known before stating 'Not tracked on Reddit'"
    )
    model = _strip((_IOS / "Models" / "TickerDetailModels.swift").read_text(encoding="utf-8"))
    helper = model[model.index("var socialBothWindowsKnown: Bool"):]
    helper = helper[:helper.index("\n")]
    assert "socialMentionsKnown && socialMentions7dKnown" in helper


def test_the_not_tracked_copy_still_exists_for_a_measured_zero():
    """Control: a real, measured "nobody is talking about this" must still say so — the
    fix must not route every ticker into the unavailable branch."""
    row = _IOS / "Views" / "Molecules" / "SentimentMetricsRow.swift"
    src = _strip(row.read_text(encoding="utf-8"))
    assert "Not tracked on Reddit" in src


# ── the Social tile names its source (developer, 2026-09-22) ─────────────────


def test_the_social_tile_names_reddit_as_its_source():
    """"Social Mentions 69" reads as all of social media; the count is Reddit only
    (ApeWisdom over r/wallstreetbets, r/stocks, r/investing …). Both branches — the
    measured one and the "N/A / Not tracked" one — carry the line."""
    row = _IOS / "Views" / "Molecules" / "SentimentMetricsRow.swift"
    src = _strip(row.read_text(encoding="utf-8"))
    assert 'private static let socialSource = "on Reddit"' in src
    assert src.count("source: Self.socialSource") == 2, (
        "both Social Mentions branches must name the source")


def test_the_news_tile_names_what_it_reads_but_never_the_provider():
    """Both tiles carry a source line — with one on a single tile its value sits a line
    lower than its neighbour's. The News line names the KIND of source; naming the
    market-data provider is forbidden by the licence (.claude/rules/marketing.md §1)."""
    row = _IOS / "Views" / "Molecules" / "SentimentMetricsRow.swift"
    src = _strip(row.read_text(encoding="utf-8"))
    assert 'private static let newsSource = "across news outlets"' in src
    assert "source: Self.newsSource" in src
    for banned in ("FMP", "Financial Modeling", "financialmodelingprep"):
        assert banned not in src, f"the News tile must not name the provider ({banned})"


def test_the_two_metric_tiles_stay_equal_height():
    """Only the Social tile carries a source line. Without the trio below, the shorter
    News tile floats to the middle of the taller one — the Key Statistics defect again."""
    row = _IOS / "Views" / "Molecules" / "SentimentMetricsRow.swift"
    src = _strip(row.read_text(encoding="utf-8"))
    assert "HStack(alignment: .top, spacing: AppSpacing.lg)" in src
    assert ".fixedSize(horizontal: false, vertical: true)" in src
    card = src[src.index("struct SentimentMetricCard"):]
    stretch = card.find(".frame(maxHeight: .infinity, alignment: .top)")
    surface = card.find(".cardSurface(")
    assert stretch != -1 and stretch < surface, (
        "the stretch frame must precede .cardSurface, which paints the frame it is on")



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


# ── the NEWS arm has the same flag (F18-5) ───────────────────────────────────────


def test_the_news_readers_consult_news_known_end_to_end():
    """A failed news feed used to arrive as `news_articles: 0, ▲0 =0 ▼0` — the same shape
    as a quiet week — and the row printed "N/A" as if it had measured that. The backend
    already served that reading UNCACHED; `news_known` is what lets the client say so."""
    from app.schemas.sentiment import SentimentAnalysisResponse
    import inspect
    from app.services import sentiment_service as ss

    assert SentimentAnalysisResponse.model_fields["news_known"].default is True
    src_py = inspect.getsource(ss.SentimentService.get_sentiment)
    assert "news_known=bool(news_known)" in src_py, "the builder must set the flag explicitly"

    src = _strip(_REPO.read_text(encoding="utf-8"))
    dto = _block(src, r"struct SentimentAnalysisDTO\s*:\s*Codable\s*")
    assert "let newsKnown: Bool?" in dto
    assert 'case newsKnown = "news_known"' in _block(dto, r"enum CodingKeys")
    assert "newsKnown: newsKnown ?? true" in _block(dto, r"func toDisplayModel\(\)")

    models = _strip(_MODELS.read_text(encoding="utf-8"))
    data = _block(models, r"struct SentimentAnalysisData\s*")
    assert "var newsKnown: Bool = true" in data
    for reader in (r"func formattedNewsArticles\(for", r"func formattedNewsChange\(for"):
        body = _block(data, reader)
        assert re.search(r"guard newsKnown else \{ return \"[^\"]+\" \}", body), reader
        # The guard comes BEFORE any count is read.
        assert body.index("guard newsKnown") < body.index("newsBullish"), reader
