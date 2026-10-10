"""
Company Weekly (Drop 2) — the code-owned NEWS TEMPLATES (contract D7, owner decisions 2026-10-09).

A news day's post is never written by a model. `compose(record, …)` turns ONE validated record of
`company_news_rules` (an `InsiderBuysWeek`, a `ThirteenFFiling`, a `MoneyMap` — drop 2a; a
`CongressCount`, a `CompanyStake`, an `EarningsReport`, a `ThemeExplainer` — drop 2b, composed here
but run only once `selection.SHIPPED_SERIES` and the per-series switch MARKETING_NEWS_SERIES list
them, and drawn only once `template_onscreen.SHIPPED_LAYOUTS` holds `pair` / `grid`) into the
accepted-output shape the rest of the pipeline already handles: the hook and four narration lines,
four video cards (one per line), the video's opening card, the post image's closed `image_spec`
(`template_onscreen`), its name-free alt text (`image_post`), the image footer, every platform's
caption composed through `post_copy` with authorship "template", and the provenance needed to do
it all again. Every sentence comes from `LEXICON` (pinned verbatim in
`tests/data/marketing_news_lexicon_v1.json`; a change bumps `TEMPLATE_VERSION`), every number from
the formatters below, every name from the record. Nothing is sliced and nothing is guessed: a rule
that cannot be met raises `NewsTemplateRefused(code)` and the script flow moves on to the next
candidate or series.

The rules every output obeys (`validate_package`, run at the end of `compose` and again by
`revalidate` at create_posts):

* **Verb table** — a Form 4 purchase is "disclosed buying" / "purchase(s)" — never "open-market":
  SEC code P is "open market OR private purchase" and the feed cannot tell the two apart; a 13F
  move is "newly reported", "reported more / fewer shares of", "no longer reported"; a Money Map
  says "reported revenue", "gross / operating profit or loss", "net income / net loss". Class C
  outputs are scanned for every off-table verb (bought, sold, purchased, added, trimmed, exited, …).
  An amended filing is only ever said to be INCLUDED in the figures ("The figures include an
  amended Form 4"): the record cannot prove a full restatement, so no line says it "replaces" one.
* **Placement (A7)** — a person (a Form 4 reporting name the strict renderer accepted) may appear
  only in WRITTEN text: the caption paragraphs after the headline (the YouTube description's
  included). Never anywhere in the VIDEO (owner decision 2026-10-09 "Role-only video": every
  narrated word is burned as a caption on a frame, and YouTube Shorts picks its own cover frame) —
  not the hook, not a narration line, not a card (title OR body), not the opening card — and never
  in any image string, the alt text, the whole of an X / Bluesky / Threads caption, the first line
  of any other caption, a title, a hashtag or the CTA. The video says the role, possessed by the
  company ("GameStop's chief executive", "the company's chief financial officer"), and its
  narration is the same whether or not the record carries a name. A person whose name collides
  with restricted text is rendered role-only in the captions too.
* **Members of Congress** are never named: a row whose rendered name is a member is dropped, and
  every output string is scanned against the block-list. A Congress Count (2b) is a count of ≥ 2
  distinct members who "disclosed purchases" of one company's stock, dated by the disclosure month:
  never "bought" / "sold" / "purchased", no dollar figure, and no word that narrows the count to a
  member (chamber, party, state, district, committee, title — `CONGRESS_NARROWING_RE`, the company
  name masked).
* **2b series name companies only** (`persons` is always empty): a stake says its own verb and
  basis ("invested", "committed up to", a carrying or fair value) with its as-of date, never
  "bet" / "worth"; earnings state the reported EPS beside the analyst estimate on the analysts'
  basis, its sign a word or a "-" — never "earned" / "lost" / "earnings of" (the calendar's figure is
  not the official accounting result) and never "expected" / "beat" / "miss" / "surprise"
  (`EARNINGS_BANNED_RE`) — and the revenue pair at ONE shared precision (`revenue_pair`) or not at
  all; a theme lists every member in the long captions and narrates at most THEME_NARRATED_MAX
  segment facts, says "{n} of its {m} companies" wherever it gives the count when the record's
  `theme_size` is larger than its members (a completeness claim only when the two are equal), and
  its title — Caydex's own wording — passes the news ban and `THEME_TITLE_BANNED_RE`, unmasked.
* **Counts and articles** — a Form 4 roundup's row count is a lower bound ("At least 3 CEOs …"),
  never the week's total (the record holds at most five rows, after the adapter's gates), and a
  count of distinct PEOPLE: a record whose rows name one person twice (one person, two issuers) is
  refused `record_invalid`; no "a" /
  "an" stands before a company name or a figure (its sound would decide it), only before a role
  word whose sound is fixed ("A CEO", "A director of Oracle").
* **Banned words (A8)** — `copy_rules.BANNED_COPY` + `FORECAST_COPY` + `NEWS_BANNED_RE` on every
  output string (company, filer, segment and person names masked: they are names, not our words;
  the slots pass their own, smaller list), plus the structural rules: no emoji, no cashtag, no "#"
  in a body, no "!", every "%" followed by " of", `clean(s) == s`.
* **Formats (A9)** — X / Bluesky / Threads carry the headline only; Facebook / LinkedIn the
  headline + paragraphs; TikTok the video headline + its first paragraph; Instagram and the
  YouTube description the video headline + up to four paragraphs — counted after the paragraphs the
  picked headline already states are left out (no paragraph repeats its headline's figure pair).
  Variants are tried long → short, then trailing paragraphs are dropped, then the outlet is
  (`over_budget`). Fewer than
  `NEWS_MIN_OUTLETS` survivors → `too_few_outlets`.

Pure: stdlib + the pure marketing modules (`company_news_rules`, `post_copy`, `template_onscreen`,
`compliance`) + `trillion_club.copy_rules` + `schemas.marketing`. FMP-free (`PURE_MODULES`).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

from app.schemas.marketing import AUDIO_WORD_MAX_CHARS, image_post_problem
from app.services._earnings_common import eps_digit_shift_suspect
from app.services.marketing import company_news_rules as rules
from app.services.marketing import post_copy
from app.services.marketing import template_onscreen as onscreen
from app.services.marketing.compliance import _is_emoji, clean, fold, given_names, scan_text
from app.services.trillion_club import copy_rules

logger = logging.getLogger(__name__)

# ── version, shape ────────────────────────────────────────────────────────────

#: Stored as `output.template_version` and `marketing_scripts.prompt_version`. Bump it with ANY
#: change to LEXICON, a formatter or a selection rule: `revalidate` refuses an output composed by
#: another version (a deploy between the build and create_posts fails the day loudly, never posts
#: text the current code would not write). Pinned with the lexicon fixture.
#: news-v2 (review round 1, 2026-10-09): no "open-market"; neutral amended-filing lines; card 1 is
#: role-only; the 13F cover opens on its headline's kind; "more than 99%"; the all-indirect note.
#: news-v3 (review round 3, same day): `money_cents`' sub-cent figure reads "under $0.01" (round 2's
#: "less than $0.01" pushed the Money Map hook past HOOK_WORDS for 4+-word company names), and a
#: sub-cent year offers the tight `mm.hook.*.tiny` hook variants.
#: news-v4 (review round 4, same day): role-only video (owner decision 2026-10-09) — narration L1
#: names the role, never the person (`ins.l1.role.*` replace `ins.l1.name.*` / `ins.l1.noname.*`;
#: a director is "a director of the company"); the `mm.hook.*.tiny` variant is offered for ANY
#: figure whose longer hooks all miss HOOK_WORDS (a 6-word name's ordinary loss year).
#: news-v5 (live-data finding L1(b), 2026-10-09): a 13F post leads with what matters — the lead kind
#: is the one holding the largest move by value (`_f13_move_value`), never a counted kind with no
#: move in the record; the image draws every kind with moves (new → more → fewer → no longer
#: reported, ≤ F13_ROWS_PER_KIND rows each by value); narration covers the lead kind and the next;
#: caption counts and the image subtitle start with the lead kind (`f13.cnt.*.first` /
#: `f13.cnt.new.rest`, `f13.img.sub.more` / `.fewer`, `f13.l.more.next` / `.fewer.next`; the
#: "none this quarter" lines are gone).
#: news-v6 (review round 8, 2026-10-10): an exit ranks by `ThirteenFMove.prev_value_usd` (its value
#: on the previous quarter's book, the shared record contract) — before, every exit the adapter
#: built ranked last; the 13F image always fits the worker's `rows` layout: ≤ F13_IMAGE_ROWS rows,
#: only the lead and the next kind under a title longer than F13_FULL_TITLE_CHARS, and a name word
#: longer than F13_MAX_WORD_CHARS refused (`slot_rejected`). The lexicon is unchanged.
#: news-v7 (drop 2b, 2026-10-10): the four 2b series — congress_count (`cg.*`), company_stakes
#: (`st.*`), earnings (`er.*`) and theme_explainer (`th.*`) — join the lexicon and SERIES_SPECS; the
#: 2a entries and outputs are unchanged.
#: news-v8 (review round 8, lens 2, 2026-10-10): a stake's `listed_since` says what the record states —
#: "{ee} has been listed on an exchange since {month}" / card "Listed since" — never "first listed" (a
#: relisted company, e.g. Arm, was listed before); a theme reads `ThemeExplainer.theme_size` (the
#: theme's own count before the adapter's gates, the shared record contract): larger than its members →
#: "{n} of its {m}" in the hook, the cover, the image subtitle and alt text, the captions and the
#: YouTube title (`th.*.part`), never a completeness claim; "The {n} companies in …" and "{theme}: {n}
#: companies." only when the size equals the members; no size → neither; "each one's largest reported
#: segment" only when every listed member has a fact, and the alt text's "each one's" only when every
#: tile carries its segment (else "for {j} of them"). The 2a entries and outputs are unchanged.
#: news-v9 (review round 9, 2026-10-10): a Form 4 roundup's count is a lower bound — "At least {n}
#: {roles} disclosed buying …" (image title, caption headlines) and "At least {m} more …" (narration
#: L4, card 4, the video paragraph) — never the week's total (≤ 5 rows, after the adapter's gates);
#: a director is "A director of {co}" (never "A Oracle director"); the share noun agrees with the
#: count ("1 share", `ins.share.*`) and a count that would read "about 0" is refused; earnings say the
#: figure with its sign on the analysts' basis — `er.hook.long` / `.short`, "negative $0.05" spoken —
#: never "earned" / "lost" / "earnings of", the headlines carry the basis note where it fits
#: (`er.ch.*.basis`), no "a" stands before a figure ("vs an analyst estimate of {est_s}"), no caption
#: paragraph restates the hook, the YouTube title says "{co} results vs estimates" (never the named
#: company's "earnings"), and the revenue pair is written at one shared precision
#: (`revenue_pair`) or left out; a theme title with a news-banned or verdict word is refused.
#: news-v10 (review round 10, 2026-10-10): a caption's paragraphs are assembled after its headline is
#: picked — an earnings ".long" headline states the revenue pair, so the revenue paragraph is left out
#: under it (`_Parts.stated_by_headline`) and the next paragraph moves up; the theme-title verdict scan
#: covers the performance and ranking families (`THEME_TITLE_BANNED_ROWS`: perform…, top, gainers,
#: movers, returns, rising, breakout, high flyers, beaters, rally, surge, soar…); a Form 4 roundup whose
#: rows name the same person twice is refused `record_invalid` (its "At least {n}" counts people, a row
#: is one issuer). The lexicon is unchanged.
#: news-v11 (review round 11, 2026-10-10): no caption paragraph restates what its headline states, in any
#: series (`_Parts.stated_by_headline`): an earnings ".basis" headline carries the basis note, so the
#: basis paragraph (`er.l3.basis`, now its own paragraph, the estimate definition `er.estimate` the next)
#: is left out under it; a stake's ".long" headline cites the source, so `st.p.source` is left out under
#: it; a Congress ".long" headline counts each member once, so `cg.l2` is left out under it; a Form 4
#: single-row headline names the role and company, so the role-only "The filing is by …" paragraph is
#: left out under the two headlines that carry them (it stays under ".min", which names no company).
#: The lexicon is unchanged.
TEMPLATE_VERSION = "news-v11"
AUTHORSHIP = post_copy.AUTHORSHIP_TEMPLATE
NEWS_SCRIPT_LINES = 4
HOOK_WORDS: Tuple[int, int] = (5, 14)
LINE_WORDS: Tuple[int, int] = (8, 20)
#: hook + the four lines: ~21-34 s of narration at Kokoro's pace.
NARRATION_WORDS: Tuple[int, int] = (45, 75)
NEWS_MIN_OUTLETS = 3
CARD_TITLE_MAX_WORDS = 8
CARD_BODY_MAX_WORDS = 28
VIDEO_LAYOUT = "per_line"
#: The opening card's and the spotlight's headline are drawn large: the first variant at most
#: this long wins, else the shortest.
OPEN_HEADLINE_MAX_CHARS = 64
#: Ratios in a `bars` image are rounded to this many places (JSONB re-serialises numbers).
RATIO_PLACES = 4

#: A record older than this (days from its newest date to the run date) is a stale source.
INSIDER_MAX_AGE_DAYS = 7
THIRTEEN_F_MAX_AGE_DAYS = 120
#: ~18 months: the adapter's `money_map_stale` gate, kept here as the template's own floor.
MONEY_MAP_MAX_AGE_DAYS = 550
#: Congress Count (2b): the count is read at least this long after its disclosure month ends (the
#: adapter's `congress_not_due` gate) and no more than CONGRESS_MAX_AGE_DAYS before the run; the
#: month is always the calendar month before the run (`selection.congress_disclosure_month`).
CONGRESS_SETTLE_DAYS = 7
CONGRESS_MAX_AGE_DAYS = 7
#: Company Stakes (2b): the adapter's `stake_stale` (verified ≤ 120 days ago) and `stake_too_old`
#: (as of ≤ 3 years ago) gates, kept here as the template's own floor (pinned equal to
#: `company_news_rules.STAKE_STALE_DAYS` / `STAKE_MAX_AGE_DAYS` by a test).
STAKE_MAX_VERIFIED_AGE_DAYS = 120
STAKE_MAX_AS_OF_AGE_DAYS = 3 * 365
#: The investee's name and the stake's source title are drawn on the `pair` image (the names under
#: their plates, the source as a line and in the footer): longer ones are refused `slot_rejected`
#: (the fit tests in tests/test_marketing_news_layouts.py prove these bounds against the worker).
STAKE_NAME_MAX_CHARS = 40
STAKE_SOURCE_MAX_CHARS = 80
STAKE_BACKGROUND_MAX_CHARS = 300
#: Name words the worker must draw whole, in characters (every bound proved in the widest glyph, "W",
#: at the floor size against marketing/news_layouts.py by tests/test_marketing_news_layouts.py):
#: a header's company name, a theme title and the cover's headline (NAME_WORD_MAX_CHARS); a stake's
#: investor and investee names under their plates, each in HALF the column (PAIR_NAME_WORD_MAX_CHARS);
#: a grid tile's company name and its segment line beside the plate (GRID_NAME_WORD_MAX_CHARS,
#: GRID_LINE_WORD_MAX_CHARS). A longer word is refused `slot_rejected` — or, for a theme member, the
#: member is left off the grid and counted in its "+k more" (it is still listed in every long caption).
NAME_WORD_MAX_CHARS = 20
PAIR_NAME_WORD_MAX_CHARS = 15
GRID_NAME_WORD_MAX_CHARS = 14
GRID_LINE_WORD_MAX_CHARS = 18
#: A video text card's TITLE draws at most this many "W"s a line at the floor (its body: more than
#: NAME_WORD_MAX_CHARS): a theme card titled with member names falls back to its generic title, the
#: names moving to the body, when a name word is longer.
CARD_TITLE_WORD_MAX_CHARS = 17
#: Twelve grid tiles fit the image only while each tile's name and segment line wrap to at most
#: this many lines (`_char_lines` at the word caps above: an upper bound on the worker's wrap), and
#: the theme's title (the image title, THEME_TITLE_LINE_CHARS "W"s a line at the floor) to at most
#: THEME_TITLE_MAX_LINES.
GRID_MAX_LINES = 2
THEME_TITLE_LINE_CHARS = 24
THEME_TITLE_MAX_LINES = 3
#: Earnings (2b): the report day is in the week before the run (the adapter's window).
EARNINGS_MAX_AGE_DAYS = 7
#: A quarter's results are reported within this many days of its end, else the period is implausible.
EARNINGS_MAX_REPORT_LAG_DAYS = 120
#: |actual − estimate| above this many times |estimate| is a feed error (`eps_gap_implausible`).
EARNINGS_MAX_GAP_RATIO = 10.0
#: Theme Explainer (2b): the member list is at most this old (the adapter's `theme_stale`); a member's
#: segment fact is drawn and narrated only when its fiscal year is recent and its name is short
#: enough for a grid tile line; narration covers at most THEME_NARRATED_MAX facts, the image at most
#: `template_onscreen.MAX_TILES` members ("+k more" for the rest), the long captions every member.
THEME_MAX_AGE_DAYS = 70
THEME_FACT_MAX_YEARS = 2
THEME_SEGMENT_MAX_CHARS = 36
THEME_NARRATED_MAX = 6
THEME_MIN_FACTS = 3


@dataclass(frozen=True)
class SeriesSpec:
    """One shipped series: its class, its `post_copy` category (hashtag key) and its image."""

    series: str
    content_class: str        # "C" | "F"
    category: str             # "news:<series>"
    kicker: str               # the static kicker (Form 4 posts swap in their filing window)
    layout: str               # template_onscreen layout; Form 4 posts with ONE row use "spotlight"


#: Every series the templates compose: keys == `company_news_rules.NEWS_SERIES` ⊇
#: `selection.SHIPPED_SERIES` (pinned). The 2b series (congress_count, company_stakes, earnings,
#: theme_explainer) are composed here in code, but a run reaches them only once their id joins
#: SHIPPED_SERIES and the per-series switch (MARKETING_NEWS_SERIES, default the 2a four); `pair` and
#: `grid` validate only once `template_onscreen.SHIPPED_LAYOUTS` lists them (until then a stake or a
#: theme is refused `image_spec_invalid` — fail closed).
SERIES_SPECS: Dict[str, SeriesSpec] = {
    "ceo_buys": SeriesSpec("ceo_buys", "C", "news:ceo_buys", "FILED LAST WEEK · FORM 4", "rows"),
    "insider_buys": SeriesSpec("insider_buys", "C", "news:insider_buys", "FILED LAST WEEK · FORM 4", "rows"),
    "thirteen_f": SeriesSpec("thirteen_f", "C", "news:thirteen_f", "13F SEASON", "rows"),
    "congress_count": SeriesSpec("congress_count", "C", "news:congress_count", "CONGRESS · DISCLOSED",
                                 "spotlight"),
    "company_stakes": SeriesSpec("company_stakes", "F", "news:company_stakes", "COMPANY STAKES", "pair"),
    "earnings": SeriesSpec("earnings", "F", "news:earnings", "EARNINGS VS ESTIMATES", "rows"),
    "money_map": SeriesSpec("money_map", "F", "news:money_map", "MONEY MAP", "bars"),
    "theme_explainer": SeriesSpec("theme_explainer", "F", "news:theme_explainer", "INSIDE A THEME", "grid"),
}

#: Every reason `compose` refuses a record (the script flow logs it and tries the next candidate).
REFUSAL_CODES: Tuple[str, ...] = (
    "record_invalid", "stale_source", "too_few_rows", "implausible_figures", "slot_rejected",
    "congress_name", "placement", "script_shape", "too_few_outlets", "image_spec_invalid",
)


class NewsTemplateRefused(ValueError):
    """A record the templates cannot render. `code` is one of REFUSAL_CODES (`detail` names the
    rule, never a person). Raised by `compose` only; it never crosses the API (its
    `classify_exception` branch exists for the classifier walk)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else str(code))
        self.code = code
        self.detail = detail


# ── the lexicon (pinned verbatim in tests/data/marketing_news_lexicon_v1.json) ─

LEXICON: Dict[str, str] = {
    # shared
    "list.1": "{a}",
    "list.2": "{a} and {b}",
    "list.3": "{a}, {b} and {c}",
    "list.more.1": "{a} and {k} more",
    "list.more.2": "{a}, {b} and {k} more",
    "list.more.3": "{a}, {b}, {c} and {k} more",
    "alt.source": "Source: {source}. {as_of}.",
    # roles (image label / sentence word / plural / narration / narration plural / noun phrase)
    "role.ceo.label": "CEO",
    "role.ceo.word": "CEO",
    "role.ceo.plural": "CEOs",
    "role.ceo.sp": "chief executive",
    "role.ceo.sp.plural": "chief executives",
    "role.ceo.np": "the CEO",
    "role.ceo.np.sp": "the company's chief executive",
    "role.cfo.label": "CFO",
    "role.cfo.word": "CFO",
    "role.cfo.plural": "CFOs",
    "role.cfo.sp": "chief financial officer",
    "role.cfo.sp.plural": "chief financial officers",
    "role.cfo.np": "the CFO",
    "role.cfo.np.sp": "the company's chief financial officer",
    "role.director.label": "Director",
    "role.director.word": "director",
    "role.director.plural": "directors",
    "role.director.sp": "director",
    "role.director.sp.plural": "directors",
    "role.director.np": "a director",
    "role.director.np.sp": "a director of the company",
    "role.mixed.word": "company insider",
    "role.mixed.plural": "company insiders",
    "role.mixed.sp": "company insider",
    "role.mixed.sp.plural": "company insiders",
    # Form 4: CEO Buys / Insider Buys
    "ins.kicker.last_week": "FILED LAST WEEK · FORM 4",
    "ins.kicker.window": "FILED {window_upper} · FORM 4",
    "ins.asof": "Filed {window_img}",
    "ins.subj.poss": "{co_s} {role}",
    "ins.subj.of": "The {role} of {co}",
    "ins.subj.dir": "A director of {co}",
    "ins.subj_sp.poss": "{co_sp_s} {role_sp}",
    "ins.subj_sp.of": "The {role_sp} of {co_sp}",
    "ins.subj_sp.dir": "A director of {co_sp}",
    "ins.hook": "{subj_sp} disclosed buying {amt} of the company's stock.",
    "ins.hook.short": "{subj_sp} disclosed buying {amt} of company stock.",
    "ins.hook.min": "A {role_sp} disclosed buying {amt} of {co_sp} stock.",
    # L1 names the role, never a person: every narrated word is burned on a video frame.
    "ins.l1.role.one": "The filing is by {role_np_sp} and was filed on {filed_sp}.",
    "ins.l1.role.many": "The filings are by {role_np_sp} and were filed from {first_sp} to {last_sp}.",
    "ins.l1.role.short.one": "The filing is by {role_np_sp}.",
    "ins.l1.role.short.many": "The filings are by {role_np_sp}.",
    # the share noun agrees with the figure as written ("1 share", "about 1 share", "850 shares")
    "ins.share.one": "share",
    "ins.share.many": "shares",
    "ins.l2.one.one": "It reports one purchase of {shares_w} {share_noun}, for {amt}.",
    "ins.l2.one.many": "It reports {k} purchases, {shares_w} {share_noun} in total, for {amt}.",
    "ins.l2.many.one": "They report one purchase of {shares_w} {share_noun}, for {amt}.",
    "ins.l2.many.many": "They report {k} purchases, {shares_w} {share_noun} in total, for {amt}.",
    "ins.l3.indirect.all": "The shares are reported as held indirectly, not in the {role_sp}'s own name.",
    "ins.l3.indirect.some": "Part of the shares are reported as held indirectly.",
    "ins.l3.amended": "The figures shown here include an amended Form 4.",
    "ins.l3.explainer": "Officers and directors must file a Form 4 within two business days of a trade.",
    # A count of rows is a LOWER BOUND, never the week's total (review round 9): the record holds at most
    # INSIDER_MAX_ROWS rows, after the adapter's dollar, listing, cap, role and amendment gates.
    "ins.l4.more.one": "At least 1 more {role_sp} disclosed buying their own company's stock in the same week.",
    "ins.l4.more.many": "At least {m} more {roles_sp} disclosed buying their own company's stock in the same week.",
    "ins.l4.amounts": "Amounts are the filing's share count times the cost it reports for each share.",
    "ins.c1.title": "Who filed",
    "ins.c1.noname.officer": "The {role} of {co}",
    "ins.c1.noname.dir": "A director of {co}",
    "ins.c2.title.one": "The purchase",
    "ins.c2.title.many": "The purchases",
    "ins.c2.body": "{k_txt} · {shares_s} {share_noun} · {amt_s}",
    "ins.k.one": "1 purchase",
    "ins.k.many": "{k} purchases",
    "ins.c3.indirect.all.title": "Held indirectly",
    "ins.c3.indirect.all.body": "Not in the {role}'s own name, per the filing",
    "ins.c3.indirect.some.title": "Partly held indirectly",
    "ins.c3.indirect.some.body": "Part of the shares, per the filing",
    "ins.c3.amended.title": "Amended filing",
    "ins.c3.amended.body": "The figures include an amended Form 4 (Form 4/A)",
    "ins.c3.explainer.title": "What a Form 4 is",
    "ins.c3.explainer.body": "Filed within two business days of an insider's trade",
    "ins.c4.more.title": "Same week",
    "ins.c4.more.one": "At least 1 more {role} disclosed buying their own company's stock",
    "ins.c4.more.many": "At least {m} more {roles} disclosed buying their own company's stock",
    "ins.c4.amounts.title": "How the amount is counted",
    "ins.c4.amounts.body": "Shares times the cost of each share, per the filing",
    "ins.open.head": "{subj} disclosed buying {co} stock",
    "ins.open.head.min": "{subj} disclosed buying stock",
    "ins.img.title.many": "At least {n} {roles} disclosed buying their own company's stock",
    "ins.img.note.indirect.all": "The purchases are reported as held indirectly.",
    "ins.img.note.indirect.some": "Some purchases are reported as held indirectly.",
    "ins.img.note.amended": "The figures include an amended Form 4 (Form 4/A).",
    "ins.img.spot.count": "{k_txt} · {shares_s} {share_noun}",
    "ins.img.spot.filed": "Filed {filed_window_img}",
    "ins.img.flag.indirect.all": "Held indirectly",
    "ins.img.flag.indirect.some": "Partly held indirectly",
    "ins.img.flag.amended": "Amended filing (Form 4/A)",
    "ins.ch.many.long": "At least {n} {roles} disclosed buying their own company's stock in Form 4s filed "
                        "{window_cap}. The largest here: {co}, {amt}.",
    "ins.ch.many.short": "At least {n} {roles} disclosed buying their own company's stock in Form 4s filed "
                         "{window_cap}.",
    "ins.ch.many.min": "At least {n} {roles} disclosed buying their own company's stock.",
    "ins.ch.one.long.one": "{subj} disclosed buying {amt} of {co} stock in a Form 4 filed {filed_cap}.",
    "ins.ch.one.long.many": "{subj} disclosed buying {amt} of {co} stock in Form 4s filed {filed_window_cap}.",
    "ins.ch.one.short": "{subj} disclosed buying {amt} of the company's stock.",
    "ins.ch.one.min": "A {role} disclosed buying {amt} of their company's stock.",
    "ins.p.rows": "The purchases shown: {rows}.",
    "ins.p.rows2": "Also shown: {rows}.",
    "ins.row.name": "{co}, {role} {person}, {amt}",
    "ins.row.noname": "{co}, {role}, {amt}",
    "ins.p.one.name.one": "The filing names {person}, {role_np} of {co}.",
    "ins.p.one.name.many": "The filings name {person}, {role_np} of {co}.",
    "ins.p.one.noname.one": "The filing is by {role_np} of {co}.",
    "ins.p.one.noname.many": "The filings are by {role_np} of {co}.",
    "ins.p.flag.indirect.all": "The shares are reported as held indirectly rather than in the buyer's own name.",
    "ins.p.flag.indirect.some": "Some of the shares are reported as held indirectly rather than in the buyer's own "
                                "name.",
    "ins.p.flag.amended": "The figures include an amended Form 4 (Form 4/A).",
    "ins.p.form4": "A Form 4 is the report a company's officers and directors file within two business days of a "
                   "trade. Amounts are each filing's share count times the cost it reports for each share.",
    "ins.vp.more.one": "At least 1 more {role} disclosed buying their own company's stock in Form 4s filed the "
                       "same week.",
    "ins.vp.more.many": "At least {m} more {roles} disclosed buying their own company's stock in Form 4s filed the "
                        "same week.",
    "ins.yt.long": "{subj} disclosed buying {amt_s} of {co} stock",
    "ins.yt.short": "{subj} disclosed buying {amt_s} of stock",
    "ins.yt.min": "A {role} disclosed buying {amt_s} of stock",
    "ins.alt.rows": "The image lists each purchase by company, role and amount: {rows}.",
    "ins.alt.row": "{co}, {role}, {amt_s}",
    "ins.alt.spot": "It shows {amt_s} in {k_txt} of {shares_s} {share_noun}.",
    # 13F Season
    "f13.kicker": "13F SEASON",
    "f13.asof": "Quarter ended {period_img} · filed {filed_img}",
    "f13.asof.amended": "Quarter ended {period_img} · filed {filed_img} · amended {amended_img}",
    "f13.subj.poss": "{filer_s} latest 13F",
    "f13.subj.of": "The latest 13F of {filer}",
    "f13.subj_sp.poss": "{filer_sp_s} latest 13F",
    "f13.subj_sp.poss.short": "{filer_sp_s} 13F",
    "f13.subj_sp.of": "The latest 13F of {filer_sp}",
    "f13.subj_sp.of.short": "The 13F of {filer_sp}",
    "f13.holding.one": "holding",
    "f13.holding.many": "holdings",
    "f13.hook.new": "{subj_sp} lists {n} newly reported {holdings}.",
    "f13.hook.gone": "{subj_sp} no longer reports {n} {holdings} from the quarter before.",
    "f13.hook.more": "{subj_sp} reports more shares of {n} {holdings} than the quarter before.",
    "f13.hook.fewer": "{subj_sp} reports fewer shares of {n} {holdings} than the quarter before.",
    "f13.hook.gone.short": "{subj_sp} no longer reports {n} {holdings}.",
    "f13.hook.more.short": "{subj_sp} reports more shares of {n} {holdings}.",
    "f13.hook.fewer.short": "{subj_sp} reports fewer shares of {n} {holdings}.",
    "f13.l1": "It covers the quarter that ended {period_sp} and was filed on {filed_sp}.",
    "f13.l1.amended": "It covers the quarter that ended {period_sp}; an amended version was filed on {amended_sp}.",
    "f13.l1.short": "This filing covers the quarter that ended {period_sp}.",
    "f13.l1.amended.short": "An amended version covers the quarter that ended {period_sp}.",
    # The kind lines: line 2 narrates the lead kind, line 3 the next (the ids keep their v4 names;
    # "l2" / "l3" no longer fix the position). ".next": a more / fewer line after another kind's.
    "f13.l2.list.one": "The holding newly reported in this filing is {list}.",
    "f13.l2.list.many": "The holdings newly reported in this filing are {list}.",
    "f13.l2.count.one": "1 holding was newly reported in this filing.",
    "f13.l2.count.many": "{n} holdings were newly reported in this filing.",
    "f13.l3.gone.list.one": "The holding no longer reported in this filing is {list}.",
    "f13.l3.gone.list.many": "The holdings no longer reported in this filing are {list}.",
    "f13.l3.gone.count.one": "1 holding was no longer reported in this filing.",
    "f13.l3.gone.count.many": "{n} holdings were no longer reported in this filing.",
    "f13.l3.more": "Compared with the quarter before, it reported more shares of {list}.",
    "f13.l3.fewer": "Compared with the quarter before, it reported fewer shares of {list}.",
    "f13.l.more.next": "Compared with the quarter before, it also reported more shares of {list}.",
    "f13.l.fewer.next": "Compared with the quarter before, it also reported fewer shares of {list}.",
    "f13.l3.more.count": "It reported more shares of {n} {holdings} than the quarter before.",
    "f13.l3.fewer.count": "It reported fewer shares of {n} {holdings} than the quarter before.",
    "f13.l4.listed": "{co_sp} first appears in this filing; it was listed in {month_sp}.",
    "f13.l4.explainer": "A 13F lists U.S. stock holdings at a quarter's end and is filed up to 45 days later.",
    "f13.l4.explainer.short": "A 13F is filed up to 45 days after a quarter's end.",
    "f13.l.explainer2": "A 13F shows holdings at the quarter's end, not when shares changed hands.",
    "f13.c1.title": "Quarter ended",
    "f13.c1.body": "{period_img} · filed {filed_img}",
    "f13.c1.body.amended": "{period_img} · amended {amended_img}",
    "f13.head.new": "Newly reported",
    "f13.head.gone": "No longer reported",
    "f13.head.more": "Reported more shares",
    "f13.head.fewer": "Reported fewer shares",
    "f13.c.count.one": "1 holding",
    "f13.c.count.many": "{n} holdings",
    "f13.c4.listed.title": "First appears",
    "f13.c4.listed.body": "{co}, listed {month_img}",
    "f13.c4.explainer.title": "About 13F filings",
    "f13.c4.explainer.body": "U.S. stock holdings, filed up to 45 days after the quarter",
    "f13.c.explainer2.title": "What a 13F shows",
    "f13.c.explainer2.body": "Holdings at the quarter's end, not trade dates",
    "f13.img.sub.new": "{n} newly reported",
    "f13.img.sub.gone": "{n} no longer reported",
    "f13.img.sub.more": "{n} with more shares",
    "f13.img.sub.fewer": "{n} with fewer shares",
    "f13.img.more": "+{k} more",
    "f13.img.cell.listed": "listed {month_img}",
    "f13.open.new.poss": "newly reported {holdings} in {filer_s} 13F",
    "f13.open.new.of": "newly reported {holdings} in the 13F of {filer}",
    "f13.open.gone.poss": "{holdings} no longer reported in {filer_s} 13F",
    "f13.open.gone.of": "{holdings} no longer reported in the 13F of {filer}",
    "f13.open.more.poss": "{holdings} with more shares in {filer_s} 13F",
    "f13.open.more.of": "{holdings} with more shares in the 13F of {filer}",
    "f13.open.fewer.poss": "{holdings} with fewer shares in {filer_s} 13F",
    "f13.open.fewer.of": "{holdings} with fewer shares in the 13F of {filer}",
    # Caption counts: the lead kind first, carrying the noun (".first"; "f13.cnt.new" is the new
    # kind's), then the rest without it.
    "f13.cnt.new": "{n} newly reported {holdings}",
    "f13.cnt.more.first": "{n} {holdings} with more shares",
    "f13.cnt.fewer.first": "{n} {holdings} with fewer shares",
    "f13.cnt.gone.first": "{n} {holdings} no longer reported",
    "f13.cnt.new.rest": "{n} newly reported",
    "f13.cnt.gone": "{n} no longer reported",
    "f13.cnt.more": "{n} with more shares",
    "f13.cnt.fewer": "{n} with fewer shares",
    "f13.lead.gone": "{n} {holdings} no longer reported",
    "f13.lead.more": "more shares of {n} {holdings}",
    "f13.lead.fewer": "fewer shares of {n} {holdings}",
    "f13.ch.long.poss": "{filer_s} 13F for the quarter ended {period_cap}: {counts}.",
    "f13.ch.long.of": "The 13F of {filer} for the quarter ended {period_cap}: {counts}.",
    "f13.ch.short": "{subj}: {lead}.",
    "f13.p.new": "Newly reported, with the value the filing gives at the quarter's end: {items}.",
    "f13.p.gone": "No longer reported: {items}.",
    "f13.p.more": "Reported more shares of: {items}.",
    "f13.p.fewer": "Reported fewer shares of: {items}.",
    "f13.p.listed": "{co} first appears in this filing; it was listed in {month_cap}.",
    "f13.p.explainer": "A 13F lists U.S. stock holdings at a quarter's end and is filed up to 45 days later; "
                       "it does not show when shares changed hands.",
    "f13.item.value": "{co}, {value}",
    "f13.yt.poss": "{filer_s} 13F: {lead}",
    "f13.yt.of": "13F of {filer}: {lead}",
    "f13.yt.min": "Latest 13F: {lead}",
    "f13.alt.section": "{heading}: {items}.",
    # Money Map
    "mm.kicker": "MONEY MAP",
    "mm.asof": "Fiscal {fy}, ended {period_img}",
    "mm.hook.profit.long": "For every $100 of revenue in fiscal {fy}, {co_sp} kept {per100} as net income.",
    "mm.hook.profit.short": "{co_sp} kept {per100} of every $100 of revenue as net income.",
    "mm.hook.loss.long": "In fiscal {fy}, {co_sp} had a net loss of {per100} for every $100 of revenue.",
    "mm.hook.loss.short": "{co_sp} had a net loss of {per100} for every $100 of revenue.",
    "mm.hook.profit.min": "{co_sp}: {per100} of net income per $100 of revenue.",
    "mm.hook.loss.min": "{co_sp}: a net loss of {per100} per $100 of revenue.",
    # The tightest frames, offered LAST and only when no longer hook fits HOOK_WORDS (a 6-word name).
    "mm.hook.profit.tiny": "{co_sp}: {per100} net income per $100 of revenue.",
    "mm.hook.loss.tiny": "{co_sp}: net loss {per100} per $100 of revenue.",
    "mm.l1": "{co_sp} reported revenue of {rev} in fiscal {fy}.",
    "mm.l2.two": "Its {s1} segment made up {sh1} of {basis}, and {s2} made up {sh2} of {basis}.",
    "mm.l2.one": "Its {s1} segment made up {sh1} of {basis} that year.",
    "mm.basis.revenue": "revenue",
    "mm.basis.segments": "segment sales",
    "mm.word.profit": "profit",
    "mm.word.loss": "loss",
    "mm.l3.both": "After the cost of sales, gross {gp_word} was {gp}; after operating costs, operating {op_word} "
                  "was {op}.",
    "mm.l3.both.short": "Gross {gp_word} was {gp}, and operating {op_word} was {op}.",
    "mm.l3.gross": "After the cost of sales, gross {gp_word} was {gp} that year.",
    "mm.l3.operating": "After all operating costs, operating {op_word} was {op} that year.",
    "mm.l3.none": "Revenue is the total the company reported from sales over the year.",
    "mm.l4.profit": "After interest, taxes and everything else, net income was {ni}.",
    "mm.l4.loss": "After interest, taxes and everything else, the net loss was {ni}.",
    "mm.c1.title": "Revenue",
    "mm.c1.body": "{rev_s} in fiscal {fy}",
    "mm.c2.title": "Segments",
    "mm.c2.two": "{s1}: {sh1} of {basis} · {s2}: {sh2} of {basis}",
    "mm.c2.one": "{s1}: {sh1} of {basis}",
    "mm.c3.title": "After costs",
    "mm.c3.both": "Gross {gp_word} {gp_s} · operating {op_word} {op_s}",
    "mm.c3.gross": "Gross {gp_word} {gp_s}",
    "mm.c3.operating": "Operating {op_word} {op_s}",
    "mm.c3.none.title": "What revenue is",
    "mm.c3.none.body": "The total the company reported from sales",
    "mm.c4.profit.title": "Net income",
    "mm.c4.loss.title": "Net loss",
    "mm.c4.body": "{ni_s} in fiscal {fy}",
    "mm.img.title": "How {co} makes money",
    "mm.img.subtitle": "Fiscal {fy} · as reported",
    "mm.bar.other": "Other",
    "mm.bar.elim": "Intersegment eliminations",
    "mm.flow.revenue": "Revenue",
    "mm.flow.gross.profit": "Gross profit",
    "mm.flow.gross.loss": "Gross loss",
    "mm.flow.op.profit": "Operating profit",
    "mm.flow.op.loss": "Operating loss",
    "mm.flow.net.profit": "Net income",
    "mm.flow.net.loss": "Net loss",
    "mm.img.callout.profit": "For every $100 of revenue, {per100} was net income",
    "mm.img.callout.loss": "For every $100 of revenue, the net loss was {per100}",
    "mm.open.profit": "of every $100 of {co} revenue was net income",
    "mm.open.loss": "net loss for every $100 of {co} revenue",
    "mm.ch.long.profit": "How {co} makes money: {rev} of revenue in fiscal {fy}, and {per100} of every $100 kept "
                         "as net income.",
    "mm.ch.long.loss": "How {co} makes money: {rev} of revenue in fiscal {fy}, and a net loss of {per100} for "
                       "every $100.",
    "mm.ch.short": "How {co} makes money, from its fiscal {fy} results.",
    "mm.p.segments": "Revenue by segment in fiscal {fy}: {items}.",
    "mm.p.elim": "Sales between its own segments, {elim}, are taken out of that total.",
    "mm.item": "{name}, {value}",
    "mm.item.other": "other, {value}",
    "mm.p.costs.both": "After the cost of sales, gross {gp_word} was {gp}. After operating costs, operating "
                       "{op_word} was {op}.",
    "mm.p.costs.gross": "After the cost of sales, gross {gp_word} was {gp}.",
    "mm.p.costs.operating": "After operating costs, operating {op_word} was {op}.",
    "mm.p.net.profit": "Net income was {ni}: {per100} of every $100 of revenue.",
    "mm.p.net.loss": "The net loss was {ni}: {per100} for every $100 of revenue.",
    "mm.p.source": "Figures are as reported in the company's annual financial statements for fiscal {fy}.",
    "mm.yt": "How {co} makes money: fiscal {fy} results",
    "mm.yt.short": "How {co} makes money",
    "mm.alt.segments": "Bars show revenue by segment: {items}.",
    "mm.alt.flow": "A second set of bars shows {items}.",
    "mm.alt.item": "{label} {value}",
    "mm.alt.callout": "{callout}.",
    # Congress Count (2b-1). A count of ≥ 2 distinct members who DISCLOSED purchases (never "bought"),
    # dated by the disclosure month; no member is named or narrowed (no chamber, party, state,
    # district, committee or title), no dollar figure.
    "cg.kicker": "CONGRESS · DISCLOSED IN {month_upper}",
    "cg.asof": "{month_long} · as of {as_of_img}",
    "cg.hook.year": "{n} members of Congress disclosed purchases of {co_sp} stock in {month_long}.",
    "cg.hook": "{n} members of Congress disclosed purchases of {co_sp} stock in {month_name}.",
    "cg.hook.min": "{n} members of Congress disclosed {co_sp} stock purchases in {month_name}.",
    "cg.l1": "The count covers periodic transaction reports disclosed during {month_long}, as of {as_of_sp}.",
    "cg.l2": "Each member is counted once, however many reports they filed that month.",
    "cg.l3": "These reports can include a spouse's or dependent child's trades, and give amounts only as ranges.",
    "cg.l4": "A trade can be disclosed up to 45 days after it happens, so the trades may be older.",
    "cg.c1.title": "Disclosed in",
    "cg.c1.body": "{month_long} · as of {as_of_img}",
    "cg.c2.title": "Counted once",
    "cg.c2.body": "Each member, however many reports",
    "cg.c3.title": "What the reports cover",
    "cg.c3.body": "Spouse and dependent-child trades; amounts as ranges",
    "cg.c4.title": "Reporting delay",
    "cg.c4.body": "Up to 45 days after the trade",
    "cg.headline": "members of Congress disclosed purchases of {co} stock",
    "cg.img.once": "Each member counted once",
    "cg.ch.long": "{n} members of Congress disclosed purchases of {co} stock in {month_long}, counting each member "
                  "once.",
    "cg.ch.short": "{n} members of Congress disclosed purchases of {co} stock in {month_long}.",
    "cg.ch.min": "{n} members of Congress disclosed purchases of {co} stock in {month_short}.",
    "cg.p.count": "The count covers periodic transaction reports disclosed during {month_long}, as of {as_of_cap}.",
    "cg.yt": "{n} members of Congress disclosed purchases of {co} stock in {month_long}",
    "cg.yt.short": "{n} members of Congress disclosed purchases of {co} stock in {month_short}",
    "cg.alt.title": "Members of Congress disclosed purchases of {co} stock",
    "cg.alt.title.min": "Disclosed purchases by members of Congress",
    "cg.alt.body": "The image shows {n}, the number of members of Congress who disclosed purchases of {co} stock "
                   "in {month_long}, each counted once.",
    # Company Stakes (2b-2). The stake's own verb and basis with its as-of date; a figure is always a
    # disclosed dollar amount (an ownership share alone is never posted: its basis is not recorded).
    "st.kicker": "COMPANY STAKES",
    "st.asof": "As of {as_of_img}",
    "st.h.invested": "{inv} invested {amt} in {ee}",
    "st.h.committed_up_to": "{inv} committed up to {amt} to {ee}",
    "st.h.carrying_value": "{inv} reported its {ee} stake at a carrying value of {amt}",
    "st.h.carrying_value.short": "{inv} carried its {ee} stake at {amt}",
    "st.h.fair_value": "{inv} reported its {ee} stake at a fair value of {amt}",
    "st.h.fair_value.short.poss": "{inv_s} {ee} stake had a fair value of {amt}",
    "st.h.fair_value.short.of": "The {ee} stake of {inv} had a fair value of {amt}",
    "st.hook": "{h}.",
    "st.l1": "The figure is as of {as_of_sp}, from the source named in this post.",
    "st.l2.invested": "That is the amount the source reports as invested, not a figure for today.",
    "st.l2.committed_up_to": "That is the most the agreement allows; the source says up to that amount.",
    "st.l2.carrying_value": "Carrying value is the amount the stake is recorded at in the investor's accounts.",
    "st.l2.fair_value": "Fair value is the amount the source says the stake was measured at on that date.",
    "st.l3.private": "As of that date, {ee} was not listed on a public exchange.",
    "st.l3.non_us": "As of that date, shares of {ee} traded on an exchange outside the U.S.",
    "st.l3.us": "Shares of {ee} are listed on a U.S. exchange.",
    "st.l3.listed": "{ee} has been listed on an exchange since {month_sp}.",
    "st.l3.commitment": "A commitment is money agreed to be invested, which may be paid over time.",
    "st.l3.source": "The figure comes from a company filing or an official company release.",
    "st.l4": "Companies disclose stakes like this in filings and official releases.",
    "st.c1.title": "As of",
    "st.c1.body": "{as_of_img}",
    "st.c2.invested.title": "Invested",
    "st.c2.invested.body": "The amount put in, not a figure for today",
    "st.c2.committed_up_to.title": "Committed up to",
    "st.c2.committed_up_to.body": "The most the agreement allows",
    "st.c2.carrying_value.title": "Carrying value",
    "st.c2.carrying_value.body": "What the stake is recorded at in the accounts",
    "st.c2.fair_value.title": "Fair value",
    "st.c2.fair_value.body": "The source's measure of the stake on that date",
    "st.c3.private.title": "Not listed",
    "st.c3.private.body": "Not traded on a public exchange as of that date",
    "st.c3.non_us.title": "Listed outside the U.S.",
    "st.c3.non_us.body": "On an exchange outside the U.S.",
    "st.c3.us.title": "Listed in the U.S.",
    "st.c3.us.body": "Shares trade on a U.S. exchange",
    "st.c3.listed.title": "Listed since",
    "st.c3.listed.body": "{month_img}",
    "st.c3.commitment.title": "A commitment",
    "st.c3.commitment.body": "Money agreed to be invested, which may be paid over time",
    "st.c3.source.title": "Where it comes from",
    "st.c3.source.body": "A company filing or an official release",
    "st.c4.title": "Source",
    "st.c4.body": "{source}",
    "st.label.invested": "invested",
    "st.label.committed_up_to": "committed up to",
    "st.label.carrying_value": "carrying value",
    "st.label.fair_value": "fair value",
    "st.img.source": "Source: {source}",
    "st.open.invested": "{inv} invested in {ee}",
    "st.open.committed_up_to": "{inv} committed funds to {ee}",
    "st.open.value.poss": "{inv_s} stake in {ee}",
    "st.open.value.of": "The stake of {inv} in {ee}",
    "st.ch.long": "{h}, as of {as_of_cap}. Source: {source}.",
    "st.ch.short": "{h}, as of {as_of_cap}.",
    "st.p.source": "Source: {source}, as of {as_of_cap}.",
    "st.alt.body": "The image shows {inv} and {ee}, with {amt_s} labelled {label}.",
    # Earnings vs Estimates (2b-3). The reported EPS beside the analyst estimate; never "expected",
    # "beat", "miss" or "surprise"; no "adjusted" claim (contract A1): the basis note says the figure
    # can be on a different basis. Review round 9: the calendar's EPS is the figure on the basis
    # analysts estimate (often not the official accounting result), so no line says the company
    # "earned", "lost" or had "earnings of" / "a loss of" — the figure is said with its sign
    # ("negative $0.05" spoken, "-$0.05" written) and "EPS" where it is written. No "a"/"an" stands
    # before a figure: the article would depend on how the figure is read ("an $8.1B").
    "er.kicker": "EARNINGS VS ESTIMATES",
    "er.asof": "Reported {report_img}",
    "er.neg": "negative {x}",
    "er.hook.long": "{co_sp} reported {eps} a share on the analysts' basis; the estimate was {est}.",
    "er.hook.short": "{co_sp} reported {eps} a share; analysts estimated {est}.",
    "er.l1.period": "These figures cover the quarter that ended {period_sp}, reported on {report_sp}.",
    "er.l1.report": "The company reported these figures on {report_sp}, after analysts published their estimate.",
    "er.l2.revenue": "Revenue was {ra}, against an analyst estimate of {re}.",
    "er.estimate": "An analyst estimate is the average figure analysts published before the report.",
    "er.l3.basis": "The estimate is the analyst consensus; the reported figure can be on a different basis from "
                   "the official accounting result.",
    "er.l4.quarterly": "Companies report results each quarter, and analysts publish their estimates before each "
                       "report.",
    "er.c1.period.title": "Quarter ended",
    "er.c1.period.body": "{period_img} · reported {report_img}",
    "er.c1.report.title": "Reported",
    "er.c1.report.body": "{report_img}",
    "er.c2.revenue.title": "Revenue",
    "er.c2.revenue.body": "{ra_s} vs an estimate of {re_s}",
    "er.c.estimate.title": "The estimate",
    "er.c.estimate.body": "The average analyst figure published before the report",
    "er.c3.title": "About the figures",
    "er.c3.body": "The consensus basis can differ from the official accounts",
    "er.c4.quarterly.title": "Each quarter",
    "er.c4.quarterly.body": "Results are reported; analysts publish estimates first",
    "er.open.head": "EPS vs an analyst estimate of {est_s}",
    "er.img.title": "Reported vs analyst estimates",
    "er.img.sub.period": "Quarter ended {period_img} · reported {report_img}",
    "er.img.sub.report": "Reported {report_img}",
    "er.img.eps": "EPS",
    "er.img.revenue": "Revenue",
    "er.img.vs": "vs {x} estimate",
    "er.img.note": "The estimate is the analyst consensus; its basis can differ from the official accounts.",
    # The headline carries the basis note wherever a variant with it fits (`.basis`, tried first); the
    # plain variants are the fallback for the tightest budgets (the note is then in the paragraphs).
    "er.ch.long.basis": "{co} reported EPS of {eps_s} vs an analyst estimate of {est_s}, and revenue of {ra} vs "
                        "{re}. The EPS basis can differ from the official accounts.",
    "er.ch.short.basis": "{co} reported EPS of {eps_s} vs an analyst estimate of {est_s}. The EPS basis can differ "
                         "from the official accounts.",
    "er.ch.min.basis": "{co}: EPS of {eps_s} vs an analyst estimate of {est_s}; its basis can differ from official "
                       "accounts.",
    "er.ch.long": "{co} reported EPS of {eps_s} vs an analyst estimate of {est_s}, and revenue of {ra} vs {re}.",
    "er.ch.short": "{co} reported EPS of {eps_s} vs an analyst estimate of {est_s}.",
    "er.ch.min": "{co}: EPS of {eps_s} vs an analyst estimate of {est_s}.",
    "er.p.period": "These figures cover the quarter that ended {period_cap}, reported on {report_cap}.",
    "er.p.report": "The company reported these figures on {report_cap}.",
    "er.yt": "{co} results vs estimates: EPS {eps_s} vs {est_s}",
    "er.yt.short": "{co}: EPS {eps_s} vs an estimate of {est_s}",
    "er.alt.title": "{co}: reported vs analyst estimates",
    "er.alt.eps": "The image shows EPS of {eps_s} against an analyst estimate of {est_s}.",
    "er.alt.revenue": "It shows revenue of {ra_s} against an estimate of {re_s}.",
    # Theme Explainer (2b-4). What the members of a Caydex grouping report as their largest revenue
    # segment — no performance, momentum, ETF or AI-written text. ("sell" is a NEWS_BANNED_RE word:
    # the hook asks where the revenue comes from.)
    "th.kicker": "INSIDE A THEME",
    "th.asof": "Members as of {tickers_img}",
    "th.hook": "Where do {n} companies in {theme} get their revenue?",
    "th.hook.short": "{theme}: where {n} companies get their revenue.",
    # `.part` (review round 8): the record's `theme_size` (the theme's own count, before any gate) is
    # larger than its members — the post says "{n} of its {m}" wherever it gives the count and never
    # claims the list is complete.
    "th.hook.part": "Where do {n} of the {m} companies in {theme} get their revenue?",
    "th.hook.part.short": "{theme}: where {n} of its {m} companies get their revenue.",
    "th.hook.part.min": "{theme}: revenue at {n} of its {m} companies.",
    "th.pair.poss.poss": "{m1_s} largest revenue segment was {g1}, and {m2_s} was {g2}.",
    "th.pair.poss.of": "{m1_s} largest revenue segment was {g1}, and that of {m2} was {g2}.",
    "th.pair.of.poss": "The largest revenue segment of {m1} was {g1}, and {m2_s} was {g2}.",
    "th.pair.of.of": "The largest revenue segment of {m1} was {g1}, and that of {m2} was {g2}.",
    "th.one.poss": "{m1_s} largest revenue segment in its latest reported year was {g1}.",
    "th.one.of": "The largest revenue segment of {m1} in its latest reported year was {g1}.",
    "th.explainer": "A segment is a part of the business a company reports revenue for on its own.",
    "th.grouping": "This grouping is Caydex's own, based on each company's business.",
    "th.c.pair.title": "{m1} and {m2}",
    "th.c.pair.body": "{g1} · {g2}",
    "th.c.pair.title.min": "Largest segments",
    "th.c.pair.body.min": "{m1}: {g1} · {m2}: {g2}",
    "th.c.one.title": "{m1}",
    "th.c.one.body": "Largest segment: {g1}",
    "th.c.one.title.min": "Largest segment",
    "th.c.one.body.min": "{m1}: {g1}",
    "th.c.explainer.title": "What a segment is",
    "th.c.explainer.body": "A part of the business reported on its own",
    "th.c.grouping.title": "A Caydex grouping",
    "th.c.grouping.body": "Based on each company's business",
    "th.img.sub": "{n} companies, by largest revenue segment",
    "th.img.sub.part": "{n} of its {m} companies, by largest revenue segment",
    "th.img.line.share": "{seg} · {share}",
    "th.img.line": "{seg}",
    "th.img.more": "+{k} more",
    "th.open.head": "companies in {theme}, by largest revenue segment",
    "th.open.head.part": "of the {m} companies in {theme}, by largest revenue segment",
    # "each one's" only when every listed member has a segment fact in the post
    "th.ch.long": "{theme}: where {n} companies get their revenue, by largest reported segment.",
    "th.ch.long.each": "{theme}: where {n} companies get their revenue, by each one's largest reported segment.",
    "th.ch.long.part": "{theme}: where {n} of its {m} companies get their revenue, by largest reported segment.",
    "th.ch.long.part.each": "{theme}: where {n} of its {m} companies get their revenue, by each one's largest "
                            "reported segment.",
    "th.ch.short": "{theme}: where {n} companies get their revenue.",
    "th.ch.short.part": "{theme}: where {n} of its {m} companies get their revenue.",
    # "{theme}: {n} companies." reads as the theme's size: offered only when the record says it is
    "th.ch.min": "{theme}: {n} companies.",
    "th.ch.min.part": "{theme}: {n} of its {m} companies.",
    # the member list: "The {n} companies" only when the record says they are all of them
    "th.p.members": "The {n} companies in {theme}: {names}.",
    "th.p.members.part": "{n} of the {m} companies in {theme}: {names}.",
    "th.p.members.some": "{n} companies in {theme}: {names}.",
    "th.p.facts": "Largest revenue segments, as each company reports them: {items}.",
    "th.item.share": "{co}, {seg}, {share} of revenue",
    "th.item": "{co}, {seg}",
    "th.yt": "{theme}: where {n} companies get their revenue",
    "th.yt.short": "{theme}: {n} companies",
    "th.yt.part": "{theme}: where {n} of its {m} companies get their revenue",
    "th.yt.part.short": "{theme}: {n} of its {m} companies",
    "th.alt.title.min": "Companies in a theme",
    # the alt text: which companies (the whole-theme claim only when the record makes it), then how many
    # of the shown tiles carry their largest segment ("each one's" only when every tile does)
    "th.alt.body": "The image shows {k} of the {n} companies in {theme}.",
    "th.alt.body.all": "The image shows the {n} companies in {theme}.",
    "th.alt.body.some": "The image shows {k} companies in {theme}.",
    "th.alt.segs": "It gives the largest revenue segment for {j} of them.",
    "th.alt.segs.each": "It gives each one's largest revenue segment.",
    "th.alt.names": "Shown: {names}.",
}


def _t(entry_id: str, **fills: Any) -> str:
    """LEXICON[entry_id] with its slots filled. A missing slot is a bug (KeyError), never a gap."""
    return LEXICON[entry_id].format_map(fills)


# ── numbers and dates (Kokoro reads the caption/narration forms well) ─────────

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December")
_MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_EN_DASH = "–"


def _dec(x: float) -> Decimal:
    return Decimal(repr(float(x)))


def _q(d: Decimal, places: int) -> Decimal:
    return d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _one_decimal(d: Decimal) -> str:
    s = f"{_q(d, 1):,.1f}"
    return s[:-2] if s.endswith(".0") else s


_SHORT_UNITS: Tuple[Tuple[Decimal, str], ...] = (
    (Decimal(10) ** 3, "K"), (Decimal(10) ** 6, "M"), (Decimal(10) ** 9, "B"), (Decimal(10) ** 12, "T"))
_WORD_UNITS: Tuple[Tuple[Decimal, str], ...] = (
    (Decimal(10) ** 6, "million"), (Decimal(10) ** 9, "billion"), (Decimal(10) ** 12, "trillion"))


def _scaled(a: Decimal, units: Tuple[Tuple[Decimal, str], ...], places: int) -> Tuple[Decimal, str]:
    """`a` in the largest unit it reaches, rounded half-up, rolled up when the rounding reaches
    1,000 of a unit (999.95M → 1B)."""
    i = 0
    for j, (base, _suffix) in enumerate(units):
        if a >= base:
            i = j
    scaled = _q(a / units[i][0], places)
    while scaled >= 1000 and i + 1 < len(units):
        i += 1
        scaled = _q(a / units[i][0], places)
    return scaled, units[i][1]


def money_image(value: float) -> str:
    """Image form: "$850", "$410K", "$74.4M", "$1B", "$47.9B", "$1.2T" (one decimal, ".0" dropped,
    rolled up; a negative value gets a leading "-")."""
    d = _dec(value)
    sign = "-" if d < 0 else ""
    a = abs(d)
    if _q(a, 0) < 1000:
        return f"{sign}${_q(a, 0):,}"
    scaled, suffix = _scaled(a, _SHORT_UNITS, 1)
    return f"{sign}${_one_decimal(scaled)}{suffix}"


def money_words(value: float) -> str:
    """Caption / narration form: "$410,000", "$74.4 million", "$1 billion" (misaki reads "$74.4
    million" as "seventy-four point four million dollars"). Callers pass magnitudes: a sign is
    carried by the words ("net loss"), never by "-"."""
    d = _dec(value)
    sign = "-" if d < 0 else ""
    a = abs(d)
    whole = _q(a, 0)
    if whole < Decimal(10) ** 6:
        return f"{sign}${whole:,}"
    scaled, word = _scaled(a, _WORD_UNITS, 1)
    return f"{sign}${_one_decimal(scaled)} {word}"


#: `money_cents`' figure for a non-zero amount that rounds to $0.00. Two words, not "less than
#: $0.01" (review round 3: the extra word pushed the Money Map hook past HOOK_WORDS for 4+-word names).
SUB_CENT = "under $0.01"


def money_cents(value: float) -> str:
    """A small amount with cents: "$2.94" (the "per $100" figure). Magnitude only. A NON-ZERO amount
    that would round to $0.00 is SUB_CENT, "under $0.01" (pct()'s rule, review round 2: "$0.00 of
    every $100" beside "net income was $4 million" would be two figures that cannot both be true);
    exactly zero stays "$0.00", which is then exact. Every frame reads either way ("kept under $0.01
    as net income", "a net loss of under $0.01 per $100", the cover figure "under $0.01")."""
    a = abs(_dec(value))
    cents = _q(a, 2)
    if a > 0 and cents == 0:
        return SUB_CENT
    return f"${cents:,.2f}"


def _about(exact: bool, text: str) -> str:
    return text if exact else f"about {text}"


def shares_image(value: float) -> str:
    """Image form: "850", "8,500", "48K" / "about 48K", "3M" / "about 3.2M" ("about" when rounded)."""
    d = _dec(value)
    if d < 10_000:
        w = _q(d, 0)
        return _about(w == d, f"{w:,}")
    if d < Decimal(10) ** 6:
        k = _q(d / 1000, 0)
        if k < 1000:
            return _about(k * 1000 == d, f"{k:,}K")
    scaled, suffix = _scaled(d, _SHORT_UNITS[1:], 1)
    base = dict((s, b) for b, s in _SHORT_UNITS)[suffix]
    return _about(scaled * base == d, f"{_one_decimal(scaled)}{suffix}")


def shares_words(value: float) -> str:
    """Caption / narration form: "850", "8,500", "about 48,000", "3 million", "about 3.2 million"."""
    d = _dec(value)
    if d < 10_000:
        w = _q(d, 0)
        return _about(w == d, f"{w:,}")
    if d < Decimal(10) ** 6:
        k = _q(d / 1000, 0)
        if k < 1000:
            return _about(k * 1000 == d, f"{k * 1000:,}")
    scaled, word = _scaled(d, _WORD_UNITS, 1)
    base = dict((w, b) for b, w in _WORD_UNITS)[word]
    return _about(scaled * base == d, f"{_one_decimal(scaled)} {word}")


def pct(share: float) -> str:
    """A share of a whole in (0, 1] as "57%", "4.3%", "less than 1%" or "more than 99%". Every use
    in prose is followed by " of …" (the "%" rule): a share of something, never a move. A share
    that would ROUND to 100% without being the whole (99.5-99.99%) is "more than 99%": "100%"
    beside another segment's "less than 1%" would be two figures that cannot both be true."""
    p = _dec(share) * 100
    if p < 1:
        return "less than 1%"
    if p < 100 and _q(p, 0) >= 100:
        return "more than 99%"
    if p < 10 and _q(p, 1) < 10:
        return f"{_one_decimal(p)}%"
    return f"{_q(p, 0)}%"


_ORDINAL_SUFFIX = {1: "st", 2: "nd", 3: "rd"}


def _ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{_ORDINAL_SUFFIX.get(n % 10, 'th')}"


def date_image(d: date) -> str:
    """"Oct 7, 2026" (images, cards, footers)."""
    return f"{_MON[d.month - 1]} {d.day}, {d.year}"


def date_caption(d: date, run_date: date) -> str:
    """"Oct 7" (", 2025" added when the year is not the run's)."""
    return f"{_MON[d.month - 1]} {d.day}" + ("" if d.year == run_date.year else f", {d.year}")


def date_spoken(d: date, run_date: date) -> str:
    """"October 7th" (", 2025" added when the year is not the run's)."""
    return f"{_MONTHS[d.month - 1]} {_ordinal(d.day)}" + ("" if d.year == run_date.year else f", {d.year}")


def month_image(d: date) -> str:
    return f"{_MON[d.month - 1]} {d.year}"


def month_words(d: date) -> str:
    return f"{_MONTHS[d.month - 1]} {d.year}"


def window_image(a: date, b: date) -> str:
    """"Oct 5–9, 2026", "Sep 28–Oct 2, 2026", "Dec 29, 2025–Jan 4, 2026" (en dash), "Oct 5, 2026"."""
    if a == b:
        return date_image(a)
    if a.year != b.year:
        return f"{date_image(a)}{_EN_DASH}{date_image(b)}"
    if a.month == b.month:
        return f"{_MON[a.month - 1]} {a.day}{_EN_DASH}{b.day}, {b.year}"
    return f"{_MON[a.month - 1]} {a.day}{_EN_DASH}{_MON[b.month - 1]} {b.day}, {b.year}"


def window_caption(a: date, b: date, run_date: date) -> str:
    """"Oct 5-9", "Sep 28-Oct 2" (ASCII hyphen; the year added when it is not the run's)."""
    if a == b:
        return date_caption(a, run_date)
    if a.year != b.year:
        return f"{date_image(a)}-{date_image(b)}"
    year = "" if b.year == run_date.year else f", {b.year}"
    if a.month == b.month:
        return f"{_MON[a.month - 1]} {a.day}-{b.day}{year}"
    return f"{_MON[a.month - 1]} {a.day}-{_MON[b.month - 1]} {b.day}{year}"


def window_upper(a: date, b: date) -> str:
    """The kicker's window, upper case and year-free: "NOV 9–15", "OCT 28–NOV 3"."""
    if a == b:
        return f"{_MON[a.month - 1]} {a.day}".upper()
    if a.month == b.month and a.year == b.year:
        return f"{_MON[a.month - 1]} {a.day}{_EN_DASH}{b.day}".upper()
    return f"{_MON[a.month - 1]} {a.day}{_EN_DASH}{_MON[b.month - 1]} {b.day}".upper()


def month_name(d: date) -> str:
    """"September" (the Congress hook: the year is the run's or the one before)."""
    return _MONTHS[d.month - 1]


def eps_image(value: float) -> str:
    """Per-share figure, image / caption form: "$4.86", "-$0.05", "$0.00", "$1,234.56" (two places,
    half-up; the sign carried by "-"). Callers refuse a non-zero figure that would read "$0.00"."""
    d = _dec(value)
    sign = "-" if d < 0 and _q(abs(d), 2) > 0 else ""
    return f"{sign}${_q(abs(d), 2):,.2f}"


def eps_words(value: float) -> str:
    """Per-share figure, narration form: the MAGNITUDE ("$0.05"): a sign is carried by the words
    (`er.neg`, "negative $0.05")."""
    return f"${_q(abs(_dec(value)), 2):,.2f}"


#: Earnings revenue (review round 9): the reported figure and the estimate are written as a PAIR, in
#: one unit (the larger figure's) at one precision — never each rounded on its own, which printed
#: $1.149B vs $1.15B as "$1.1 billion vs $1.2 billion" (a shortfall of about 8% where the true one is
#: 0.09%) and $1.18B vs $1.15B as "$1.2 billion vs $1.2 billion" (a 2.6% gap hidden). The decimals
#: grow from 1 to REVENUE_MAX_PLACES until the gap the pair SHOWS is within REVENUE_GAP_TOLERANCE of
#: the true gap (so it has the true sign, reads equal only when the figures are equal, and is not
#: far off in size); when no precision does, `revenue_pair` is None and the post leaves revenue out.
REVENUE_MAX_PLACES = 3
REVENUE_GAP_TOLERANCE = Decimal("0.1")
#: The pair's units (the image's suffix, the words' unit): the same scale in both forms. A pair
#: whose larger figure is under $1 million is never written (the word form would be whole dollars).
_PAIR_UNITS: Tuple[Tuple[Decimal, str, str], ...] = (
    (Decimal(10) ** 6, "M", "million"), (Decimal(10) ** 9, "B", "billion"), (Decimal(10) ** 12, "T", "trillion"))


def _pair_figure(scaled: Decimal, places: int) -> str:
    # one decimal keeps the single-figure form ("$25 billion", ".0" dropped); more keep every place,
    # so both figures of the pair show the same precision ("$1.150 billion" vs "$1.149 billion")
    return _one_decimal(scaled) if places == 1 else f"{scaled:,.{places}f}"


def revenue_pair(actual: float, estimate: float) -> Optional[Dict[str, str]]:
    """{"ra", "re"} (words: "$1.149 billion") and {"ra_s", "re_s"} (image: "$1.149B") for the
    reported revenue and its estimate, written at ONE shared precision whose shown gap is within
    REVENUE_GAP_TOLERANCE of the true gap — or None (revenue is then left out, never guessed)."""
    a, e = _dec(actual), _dec(estimate)
    if a <= 0 or e <= 0:
        return None
    big = max(a, e)
    if big < _PAIR_UNITS[0][0]:
        return None
    true_gap = a - e
    for places in range(1, REVENUE_MAX_PLACES + 1):
        i = max(j for j, (base, _s, _w) in enumerate(_PAIR_UNITS) if big >= base)
        sa, se = _q(a / _PAIR_UNITS[i][0], places), _q(e / _PAIR_UNITS[i][0], places)
        if max(sa, se) >= 1000 and i + 1 < len(_PAIR_UNITS):       # 999.96M → $1B, like `_scaled`
            i += 1
            sa, se = _q(a / _PAIR_UNITS[i][0], places), _q(e / _PAIR_UNITS[i][0], places)
        base, suffix, word = _PAIR_UNITS[i]
        shown_gap = (sa - se) * base
        if abs(shown_gap - true_gap) <= REVENUE_GAP_TOLERANCE * abs(true_gap):
            fa, fe = _pair_figure(sa, places), _pair_figure(se, places)
            return {"ra": f"${fa} {word}", "re": f"${fe} {word}", "ra_s": f"${fa}{suffix}", "re_s": f"${fe}{suffix}"}
    return None


def _monday(d: date) -> date:
    return date.fromordinal(d.toordinal() - d.weekday())


# ── names ─────────────────────────────────────────────────────────────────────

#: All-caps tokens of five letters or more that narration keeps spelled as written (an acronym
#: read letter by letter). Anything else is title-cased for Kokoro ("NVIDIA" → "Nvidia").
KEEP_CAPS: FrozenSet[str] = frozenset()
_CAPS_TOKEN_RE = re.compile(r"(?<![A-Za-z])[A-Z]{5,}(?![A-Za-z])")


def spoken_company(display: str) -> str:
    """A display name as narration reads it: an all-caps token of 5+ letters not in KEEP_CAPS is
    title-cased ("NVIDIA" → "Nvidia"). Narration only — cards, images and captions keep the display
    name. Tickers are never narrated."""
    return _CAPS_TOKEN_RE.sub(lambda m: m.group(0) if m.group(0) in KEEP_CAPS else m.group(0).capitalize(),
                              display)


def possessive(name: str) -> Optional[str]:
    """"Costco" → "Costco's", "Fisher Investments" → "Fisher Investments'", and None for a name that
    already ends in a possessive ("Lowe's", "Domino's"): that sentence uses its ".of" twin."""
    if not isinstance(name, str) or not name:
        return None
    if name.endswith(("'s", "'S", "’s", "s'", "S'")):
        return None
    if name.endswith(("s", "S")):
        return f"{name}'"
    return f"{name}'s"


def _join_list(names: Sequence[str], total: int, cap: int) -> str:
    """"A" / "A and B" / "A, B and C" / "A, B, C and 2 more" — at most `cap` names, `total` items."""
    shown = list(names[:cap])
    more = total - len(shown)
    n = len(shown)
    if n == 0:
        raise ValueError("an empty list")
    keys = ("a", "b", "c")
    fills = dict(zip(keys, shown))
    if more > 0:
        return _t(f"list.more.{n}", k=more, **fills)
    return _t(f"list.{n}", **fills)


def _list_variants(names: Sequence[str], total: int) -> List[str]:
    """Every list form from 3 names down to 1 (the line picks the first that fits)."""
    out: List[str] = []
    for cap in sorted({min(c, len(names)) for c in (3, 2, 1)}, reverse=True):
        s = _join_list(names, total, cap)
        if s not in out:
            out.append(s)
    return out


# ── banned words, verbs, structure (A8) ───────────────────────────────────────

#: The news-specific ban (templates design §4.5), on top of `copy_rules.BANNED_COPY` and
#: `FORECAST_COPY`, matched on `fold()` with the record's names masked.
NEWS_BANNED_RE = re.compile(
    r"\b(?:smart\s+money|follow(?:s|ing|ers)?|cop(?:y|ies|ying)|mirror(?:s|ing)?|"
    r"signal(?:s|ed|ing|led|ling)?|shock(?:er|ers|ing)?|breaking|just\s+in|alerts?|urgent|whales?|"
    r"legend(?:ary)?|gurus?|genius|massive|huge|soar(?:s|ed|ing)?|plung(?:e|es|ed|ing)|skyrocket\w*|"
    r"crush\w*|beat(?:s|ing)?|miss(?:es|ed)?|record|all-time|undervalued|overvalued|cheap(?:er|est)?|"
    r"bargain|upside|downside|targets?|rall(?:y|ied|ies)|surg(?:e|es|ed|ing)|tank(?:s|ed)?|"
    r"pric(?:e|es|ed|ing)|bought|sold|buys?|sells?|insider\s+trading|exclusive|premium|act\s+now|"
    r"don't\s+miss|opportunit(?:y|ies))\b",
    re.IGNORECASE,
)
#: Slot values (company, filer, segment names) pass this smaller list (§4.5) plus BANNED / FORECAST.
SLOT_BANNED_RE = re.compile(
    r"\b(?:smart\s+money|signal\w*|shock\w*|breaking|follow\w*|cop(?:y|ies|ying)|whales?)\b",
    re.IGNORECASE,
)
#: Class C (filings) speaks only the verb table: these verbs are off it. "buying" is allowed only
#: as "disclosed buying".
OFF_TABLE_VERBS_RE = re.compile(
    r"\b(?:bought|buys?|purchased|sold|sells?|selling|added|adds|adding|trimmed|trims?|cut|cuts|"
    r"exited|exits?|dumped|dumps?|loaded|initiated|acquired|acquires?|unloaded|snapped|"
    r"stake\s+bought|new\s+position)\b|(?<!disclosed )\bbuying\b",
    re.IGNORECASE,
)
#: Congress Count (class C, rules §1): no word that narrows the count to a member — a chamber, a
#: party, a state (by word or by name), a district, a committee or a title — matched on `fold()`
#: with the company name masked (a company may be called "State Street" or "Texas Instruments"),
#: on every public string, the code-owned suffix included.
_US_STATES = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
    "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
    "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi", "missouri",
    "montana", "nebraska", "nevada", r"new\s+hampshire", r"new\s+jersey", r"new\s+mexico", r"new\s+york",
    r"north\s+carolina", r"north\s+dakota", "ohio", "oklahoma", "oregon", "pennsylvania", r"rhode\s+island",
    r"south\s+carolina", r"south\s+dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", r"west\s+virginia", "wisconsin", "wyoming", r"puerto\s+rico", "guam",
    r"district\s+of\s+columbia",
)
CONGRESS_NARROWING_RE = re.compile(
    r"\b(?:senators?|senate|senatorial|representatives?|reps?|sen|congress(?:man|men|woman|women|person|"
    r"people)|lawmakers?|legislators?|politicians?|republicans?|democrats?|democratic|gop|dem|"
    r"independents?|part(?:y|ies)|partisan|bipartisan|house|chambers?|committees?|subcommittees?|caucus|"
    r"districts?|states?|delegations?|speaker|leaders?|whips?|chair(?:s|man|men|woman|women)?|"
    r"minority|majority|incumbents?|" + "|".join(_US_STATES) + r")\b",
    re.IGNORECASE,
)
#: Congress Count: the verbs a count may never use (owner rules 2026-09-28: "disclosed purchases",
#: never "bought" / "sold" / "purchased" / "traded"), on top of OFF_TABLE_VERBS_RE.
CONGRESS_OFF_VERBS_RE = re.compile(
    r"\b(?:bought|buys?|buying|purchased|purchasing|sold|sells?|selling|sales?|traded|trading|invested|"
    r"dumped|unloaded|loaded)\b",
    re.IGNORECASE,
)
#: Earnings (contract A1): no verdict or colour word beside the two figures (FORECAST_COPY already
#: bans "expected"; NEWS_BANNED_RE "beat", "miss", "crush"), matched with the names masked.
EARNINGS_BANNED_RE = re.compile(
    r"\b(?:expect\w*|beat\w*|miss\w*|surpris\w*|crush\w*|top(?:s|ped|ping)?|exceed\w*|fell\s+short|"
    r"short\s+of|disappoint\w*|strong(?:er|est|ly)?|weak(?:er|est|ness)?|green|red|outperform\w*|"
    r"underperform\w*|blow(?:out|-out)|smash\w*|stellar|solid|better|worse|impressive|whisper\w*)\b",
    re.IGNORECASE,
)
#: Theme Explainer (review round 9): the theme's title is Caydex's OWN wording ("This grouping is
#: Caydex's own"), drawn and narrated beside named companies — so it passes the news ban
#: (NEWS_BANNED_RE: "undervalued", "cheap", "soaring", "record", "upside", …) on top of the slot rules,
#: and none of these performance or verdict words (rules §1 decision 4: a theme's members and segment
#: facts only — never its performance or momentum). A hit refuses the theme (`slot_rejected`).
#: One row per word family; every row has a must-reject sample in tests/test_marketing_news_templates.py
#: (`THEME_TITLE_ROW_SAMPLES`), and the curated Emerging Frontiers titles must still pass.
THEME_TITLE_BANNED_ROWS: Tuple[str, ...] = (
    r"outperform\w*", r"underperform\w*", r"exceed\w*", r"disappoint\w*", r"stellar", r"winn(?:er|ers|ing)",
    r"losers?", r"momentum", r"best", r"worst", r"strong(?:er|est)?", r"weak(?:er|est)?", r"hot(?:test)?",
    r"leaders?", r"laggards?",
    # review round 10: the performance and ranking families ("Top Performers", "Top Gainers", "Big
    # Movers", "Highest Returns", "Market Beaters", "Rising Stars" all composed before)
    r"perform\w*", r"top", r"gain(?:s|er|ers|ing)?", r"mov(?:er|ers)", r"returns?", r"ris(?:e|es|ers?|ing)",
    r"breakout\w*", r"high[- ]?fl(?:y|i)ers?", r"beat(?:s|er|ers|ing)?", r"rall(?:y|ies|ied|ying)",
    r"surg(?:e|es|ed|ing)", r"soar\w*",
)
THEME_TITLE_BANNED_RE = re.compile(r"\b(?:" + "|".join(THEME_TITLE_BANNED_ROWS) + r")\b", re.IGNORECASE)
#: Company Stakes (A3): the stake's own verb only — never a bet, a worth, a purchase verb or control.
STAKES_BANNED_RE = re.compile(
    r"\b(?:bets?|betting|bet\s+on|wager\w*|worth|bought|buys?|backed|backs|pledged?|controls?|"
    r"controlling|acquired|owns|stake\s+bought)\b",
    re.IGNORECASE,
)
#: A stake background naming a per-share amount ("at $414.79 a share", "$20.00 each") reads as a
#: price: such a background is dropped (it is optional caption text, never a reason to refuse).
_PER_SHARE_RE = re.compile(r"\b(?:a|per|each)\s+(?:share|unit|ads)\b|\beach\b|\bshare\s+price\b",
                           re.IGNORECASE)
#: The one place a "%" may stand without " of" (templates design §4.5): a grid tile's share cell,
#: "<segment> · 57%" — its path and its exact shape.
_TILE_LINE_FIELD_RE = re.compile(r"image_spec\.tiles\[[0-9]{1,2}\]\.line")
_SHARE_CELL_RE = re.compile(r"[^\n·%]+ · (?:[0-9]{1,2}(?:\.[0-9])?%|100%|less than 1%|more than 99%)")
_CASHTAG_RE = re.compile(r"(?<![A-Za-z0-9$])\$\s?[A-Za-z]{1,6}(?![A-Za-z])")
_LINK_RE = re.compile(r"://|\bwww\.|@", re.IGNORECASE)
_SLOT_BAD_CHARS_RE = re.compile(r"[%#@!]|://|\bwww\.|\bhttp", re.IGNORECASE)
_MASK = "Zzslot"


def _slot_problem(value: str) -> Optional[str]:
    """Why a record string cannot fill a template slot (§4.5), or None."""
    if not isinstance(value, str) or not value or clean(value) != value:
        return "not clean text"
    if any(_is_emoji(ch) for ch in value):
        return "emoji"
    if any(ch.isalpha() and ord(ch) >= 0x250 for ch in value) or any(
            not ch.isascii() and ch.isdigit() for ch in value):
        return "non-Latin letter or digit"
    if _SLOT_BAD_CHARS_RE.search(value) or _CASHTAG_RE.search(value):
        return "a symbol, link or cashtag"
    # A domain-shaped name ("C3.ai", "1-800-FLOWERS.COM") is a bare link X / Threads / Facebook
    # autolink — and X refuses the post at publish time: refuse it here, by the SAME definition.
    if post_copy.x_link_tokens(value):
        return "a bare domain"
    if (copy_rules.contains_banned_copy(value) or copy_rules.contains_forecast(value)
            or SLOT_BANNED_RE.search(value)):
        return "banned or forecast wording"
    return None


def _mask_re(names: Iterable[str]) -> Optional["re.Pattern[str]"]:
    uniq = sorted({n for n in names if isinstance(n, str) and n.strip()}, key=len, reverse=True)
    if not uniq:
        return None
    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(n) for n in uniq) + r")(?![A-Za-z0-9])",
                      re.IGNORECASE)


def _masked(text: str, rx: Optional["re.Pattern[str]"]) -> str:
    return rx.sub(_MASK, text) if rx is not None else text


def _percent_ok(text: str) -> bool:
    """Every "%" is followed by " of" (a share of something, never a move)."""
    i = text.find("%")
    while i >= 0:
        if not text.startswith(" of", i + 1) or (len(text) > i + 4 and text[i + 4].isalpha()):
            return False
        i = text.find("%", i + 1)
    return True


# ── the parts a series builder hands the assembler ────────────────────────────


@dataclass
class _Parts:
    hook: List[str]                        # variants, preferred first
    lines: List[List[str]]                 # 4 × variants
    cards: List[Tuple[str, str]]           # 4 × (title, body)
    opening: Dict[str, Any]
    image_spec: Dict[str, Any]             # without "footer" (added by the assembler)
    alt_title: str
    alt_paragraphs: List[str]              # content paragraphs (the source line is appended)
    source: str
    as_of: str
    image_headlines: List[str]
    image_paragraphs: List[str]
    video_headlines: List[str]
    video_paragraphs: List[str]
    youtube_titles: List[str]
    persons: List[str]
    names: Dict[str, str]                  # logo key → company name (the wordmark)
    #: Caption fields whose body must keep at least `required_paragraphs` paragraphs, else the
    #: outlet is dropped (`over_budget`) — never posted without them (a theme's full member list).
    required_fields: Tuple[str, ...] = ()
    required_paragraphs: int = 0
    #: Headline text → the paragraphs that headline already states (review round 10: the long
    #: earnings headline carries the revenue pair, and its first paragraph said it again). A caption
    #: that picks the headline leaves those paragraphs out BEFORE its outlet's paragraph limit is
    #: applied, so the next paragraph moves up (`_caption_bodies`). Review round 11 widened it to every
    #: headline that carries a paragraph's statement: the earnings basis note, a stake's source, a
    #: Congress count's "each member once", a Form 4 row's role and company. Empty for the series whose
    #: headlines never repeat a paragraph (13F, Money Map, Theme): their captions are unchanged.
    stated_by_headline: Dict[str, FrozenSet[str]] = field(default_factory=dict)


def _first_fit(variants: Sequence[str], limit: int) -> str:
    for v in variants:
        if len(v) <= limit:
            return v
    return min(variants, key=len)


def _role(role: str, form: str) -> str:
    return LEXICON[f"role.{role}.{form}"]


def _group_role(roles: Sequence[str], form: str) -> str:
    """The plural (or singular) noun for a set of rows: their shared role, else "company insiders"."""
    uniq = sorted(set(roles))
    key = uniq[0] if len(uniq) == 1 else "mixed"
    if key == "mixed":
        return LEXICON[{"plural": "role.mixed.plural", "word": "role.mixed.word",
                        "sp": "role.mixed.sp", "sp.plural": "role.mixed.sp.plural"}[form]]
    return _role(key, form)


# ── Form 4: ceo_buys / insider_buys ───────────────────────────────────────────


def _ins_subject(row: rules.InsiderPurchase, *, spoken: bool) -> str:
    co = row.company.name
    co_sp = spoken_company(co)
    if row.role == "director":
        return _t("ins.subj_sp.dir", co_sp=co_sp) if spoken else _t("ins.subj.dir", co=co)
    poss = possessive(co)
    if poss is None:
        if spoken:
            return _t("ins.subj_sp.of", role_sp=_role(row.role, "sp"), co_sp=co_sp)
        return _t("ins.subj.of", role=_role(row.role, "word"), co=co)
    if spoken:
        return _t("ins.subj_sp.poss", co_sp_s=possessive(co_sp), role_sp=_role(row.role, "sp"))
    return _t("ins.subj.poss", co_s=poss, role=_role(row.role, "word"))


def _ins_flags(rows: Sequence[rules.InsiderPurchase]) -> Tuple[str, bool]:
    """("all" | "some" | "", amended?) over a set of rows."""
    holdings = [r.holding for r in rows]
    if all(h == "indirect" for h in holdings):
        indirect = "all"
    elif any(h != "direct" for h in holdings):
        indirect = "some"
    else:
        indirect = ""
    return indirect, any(r.amended for r in rows)


_INITIAL_RE = re.compile(r"[A-Za-z]\.")


def _person_key(name: Optional[str]) -> Optional[str]:
    """Who a named Form 4 row is, for counting: the folded name without a middle initial (one filing
    may carry the initial and the next not — "Monty J. Bennett" and "Monty Bennett" are one person
    here, fail closed). None for a row without a name."""
    if not isinstance(name, str) or not name.strip():
        return None
    return fold(" ".join(w for w in name.split() if not _INITIAL_RE.fullmatch(w))) or fold(name)


def _distinct_people(rows: Sequence[rules.InsiderPurchase]) -> int:
    """The number of PEOPLE a set of rows is about: distinct names where the rows carry one, each
    unnamed row counted once (it cannot be matched to another)."""
    keys = [_person_key(r.person_name) for r in rows]
    return len({k for k in keys if k is not None}) + sum(1 for k in keys if k is None)


def _insider_parts(rec: rules.InsiderBuysWeek, run_date: date, suppressed: FrozenSet[str]) -> _Parts:
    if not (rec.window_end < run_date and (run_date - rec.window_end).days <= INSIDER_MAX_AGE_DAYS):
        raise NewsTemplateRefused("stale_source", "the Form 4 window does not end in the week before the run")
    # Review round 10: the roundup's "At least {n} CEOs …" and "At least {m} more …" count PEOPLE, but a
    # row is one ISSUER — one person who bought at two affiliates sharing a CEO or a board would be
    # counted twice ("At least 2 CEOs" about one named man). The adapter dedupes by reporter; here a
    # record whose rows name the same person twice is refused, never counted twice — so every count
    # below (a row count) is a count of distinct people.
    if _distinct_people(rec.rows) != len(rec.rows):
        raise NewsTemplateRefused("record_invalid", "two rows name the same person")
    rows = [r for r in rec.rows if not (r.person_name and rules.is_congress_name(r.person_name))]
    if not rows:
        raise NewsTemplateRefused("too_few_rows", "every row named a member of Congress")
    names_ok = rules.person_names_allowed()

    def person(r: rules.InsiderPurchase) -> Optional[str]:
        return r.person_name if (names_ok and r.person_name and r.person_name not in suppressed) else None

    for r in rows:
        problem = _slot_problem(r.company.name)
        if problem:
            raise NewsTemplateRefused("slot_rejected", f"company name: {problem}")
    r0, n = rows[0], len(rows)
    co, sym = r0.company.name, r0.company.symbol
    co_sp = spoken_company(co)
    p0 = person(r0)
    filings = r0.filing_dates
    one_filing = len(filings) == 1
    amt, amt_s = money_words(r0.amount_usd), money_image(r0.amount_usd)
    shares_w, shares_s = shares_words(r0.shares), shares_image(r0.shares)
    # the share count as written (`shares_words` / `shares_image` round a figure under 10,000 to the
    # whole share): "1" / "about 1" takes "share", anything else "shares"; "about 0" is no figure
    whole_shares = _q(_dec(r0.shares), 0)
    if whole_shares == 0:
        raise NewsTemplateRefused("implausible_figures", "a share count that would read as about 0")
    share_noun = _t("ins.share.one") if whole_shares == 1 else _t("ins.share.many")
    k = r0.purchases
    k_txt = _t("ins.k.one") if k == 1 else _t("ins.k.many", k=k)
    subj, subj_sp = _ins_subject(r0, spoken=False), _ins_subject(r0, spoken=True)

    # kicker / footer
    monday = _monday(run_date)
    last_week = (rec.window_start.toordinal() == monday.toordinal() - 7
                 and rec.window_end.toordinal() <= monday.toordinal() - 1)
    kicker = (_t("ins.kicker.last_week") if last_week
              else _t("ins.kicker.window", window_upper=window_upper(rec.window_start, rec.window_end)))
    as_of = _t("ins.asof", window_img=window_image(rec.window_start, rec.window_end))

    # narration
    hook = [_t("ins.hook", subj_sp=subj_sp, amt=amt), _t("ins.hook.short", subj_sp=subj_sp, amt=amt),
            _t("ins.hook.min", role_sp=_role(r0.role, "sp"), amt=amt, co_sp=co_sp)]
    first_sp, last_sp = date_spoken(filings[0], run_date), date_spoken(filings[-1], run_date)
    role_np_sp = _role(r0.role, "np.sp")
    # Narration L1 and card 1 are role-only in EVERY case, named record or not (owner decision
    # 2026-10-09 "Role-only video": every narrated word is burned as a caption on a frame, every
    # card is drawn on one, and YouTube Shorts picks its own cover frame). The name stays only in
    # the caption paragraphs after the headline; `p0` never reaches the video.
    l1 = ([_t("ins.l1.role.one", role_np_sp=role_np_sp, filed_sp=first_sp),
           _t("ins.l1.role.short.one", role_np_sp=role_np_sp)] if one_filing else
          [_t("ins.l1.role.many", role_np_sp=role_np_sp, first_sp=first_sp, last_sp=last_sp),
           _t("ins.l1.role.short.many", role_np_sp=role_np_sp)])
    c1 = (_t("ins.c1.title"), _t("ins.c1.noname.dir", co=co) if r0.role == "director"
          else _t("ins.c1.noname.officer", role=_role(r0.role, "word"), co=co))
    l2_id = f"ins.l2.{'one' if one_filing else 'many'}.{'one' if k == 1 else 'many'}"
    l2 = [_t(l2_id, k=k, shares_w=shares_w, share_noun=share_noun, amt=amt)]
    c2 = (_t("ins.c2.title.one" if k == 1 else "ins.c2.title.many"),
          _t("ins.c2.body", k_txt=k_txt, shares_s=shares_s, share_noun=share_noun, amt_s=amt_s))
    indirect0, amended0 = _ins_flags([r0])
    if indirect0:
        l3_key = f"indirect.{indirect0}"
    elif amended0:
        l3_key = "amended"
    else:
        l3_key = "explainer"
    role_sp0 = _role(r0.role, "sp")
    l3 = [_t(f"ins.l3.{l3_key}", role_sp=role_sp0)] if l3_key == "indirect.all" else [_t(f"ins.l3.{l3_key}")]
    c3 = (_t(f"ins.c3.{l3_key}.title"),
          _t(f"ins.c3.{l3_key}.body", role=_role(r0.role, "word")) if l3_key == "indirect.all"
          else _t(f"ins.c3.{l3_key}.body"))
    others = rows[1:]
    m = len(others)
    if m:
        roles_rest = [r.role for r in others]
        if m == 1:
            l4 = [_t("ins.l4.more.one", role_sp=_group_role(roles_rest, "sp"))]
            c4 = (_t("ins.c4.more.title"), _t("ins.c4.more.one", role=_group_role(roles_rest, "word")))
        else:
            l4 = [_t("ins.l4.more.many", m=m, roles_sp=_group_role(roles_rest, "sp.plural"))]
            c4 = (_t("ins.c4.more.title"), _t("ins.c4.more.many", m=m, roles=_group_role(roles_rest, "plural")))
    else:
        if amended0 and l3_key.startswith("indirect"):
            l4_key = "amended"
        elif l3_key != "explainer":
            l4_key = "explainer"
        else:
            l4_key = "amounts"
        if l4_key == "amounts":
            l4 = [_t("ins.l4.amounts")]
            c4 = (_t("ins.c4.amounts.title"), _t("ins.c4.amounts.body"))
        else:
            l4 = [_t(f"ins.l3.{l4_key}")]
            c4 = (_t(f"ins.c3.{l4_key}.title"), _t(f"ins.c3.{l4_key}.body"))

    # opening card + image
    head = _first_fit([_t("ins.open.head", subj=subj, co=co), _t("ins.open.head.min", subj=subj)],
                      OPEN_HEADLINE_MAX_CHARS)
    opening = {"kicker": kicker, "logos": [sym], "chip": sym, "figure": amt_s, "headline": head}
    indirect_all, amended_all = _ins_flags(rows)
    if n >= 2:
        roles = _group_role([r.role for r in rows], "plural")
        title = _t("ins.img.title.many", n=n, roles=roles)
        # `indirect_all` is "all" | "some" | "" over every row: an all-indirect week never says "Some".
        notes = ([_t(f"ins.img.note.indirect.{indirect_all}")] if indirect_all else []) + (
            [_t("ins.img.note.amended")] if amended_all else [])
        spec: Dict[str, Any] = {
            "layout": "rows", "version": onscreen.SPEC_VERSION, "kicker": kicker, "title": title,
            "sections": [{"rows": [{"logo": r.company.symbol,
                                    "cells": [r.company.name, _role(r.role, "label"), money_image(r.amount_usd)]}
                                   for r in rows]}],
        }
        if notes:
            spec["notes"] = notes
        alt_rows = "; ".join(_t("ins.alt.row", co=r.company.name, role=_role(r.role, "label"),
                                amt_s=money_image(r.amount_usd)) for r in rows)
        alt_title = title
        alt_paragraphs = [_t("ins.alt.rows", rows=alt_rows)] + ([" ".join(notes)] if notes else [])
    else:
        flags = []
        if indirect0:
            flags.append(_t(f"ins.img.flag.indirect.{indirect0}"))
        if amended0:
            flags.append(_t("ins.img.flag.amended"))
        line2 = (" · ".join(flags) if flags
                 else _t("ins.img.spot.filed", filed_window_img=window_image(filings[0], filings[-1])))
        spec = {
            "layout": "spotlight", "version": onscreen.SPEC_VERSION, "kicker": kicker,
            "header": {"logo": sym, "name": co, "chip": sym}, "figure": amt_s, "headline": head,
            "lines": [_t("ins.img.spot.count", k_txt=k_txt, shares_s=shares_s, share_noun=share_noun), line2],
        }
        alt_title = head
        alt_paragraphs = [_t("ins.alt.spot", amt_s=amt_s, k_txt=k_txt, shares_s=shares_s,
                             share_noun=share_noun)] + (
            [f"{line2}."] if flags else [])

    # captions
    def row_item(r: rules.InsiderPurchase) -> str:
        p = person(r)
        role_lc = _role(r.role, "word")
        if p:
            return _t("ins.row.name", co=r.company.name, role=role_lc, person=p, amt=money_words(r.amount_usd))
        return _t("ins.row.noname", co=r.company.name, role=role_lc, amt=money_words(r.amount_usd))

    window_cap = window_caption(rec.window_start, rec.window_end, run_date)
    one_long = (_t("ins.ch.one.long.one", subj=subj, amt=amt, co=co, filed_cap=date_caption(filings[0], run_date))
                if one_filing else
                _t("ins.ch.one.long.many", subj=subj, amt=amt, co=co,
                   filed_window_cap=window_caption(filings[0], filings[-1], run_date)))
    one_heads = [one_long, _t("ins.ch.one.short", subj=subj, amt=amt),
                 _t("ins.ch.one.min", role=_role(r0.role, "word"), amt=amt)]
    flag_para = []
    if indirect_all:
        flag_para.append(_t(f"ins.p.flag.indirect.{indirect_all}"))
    if amended_all:
        flag_para.append(_t("ins.p.flag.amended"))
    role_np0 = _role(r0.role, "np")
    num = "one" if one_filing else "many"
    vp1 = (_t(f"ins.p.one.name.{num}", person=p0, role_np=role_np0, co=co) if p0
           else _t(f"ins.p.one.noname.{num}", role_np=role_np0, co=co))
    # review round 11: the long and short single-row headlines say the role and the company ("The CEO of
    # Lowe's disclosed buying …"), so the role-only paragraph ("The filing is by the CEO of Lowe's.") is
    # left out under them; ".min" names no company, so it keeps the paragraph. A named paragraph adds
    # the name and is never left out.
    one_stated = {} if p0 else {h: frozenset({vp1}) for h in one_heads[:2]}
    if n >= 2:
        roles = _group_role([r.role for r in rows], "plural")
        image_heads = [_t("ins.ch.many.long", n=n, roles=roles, window_cap=window_cap, co=co, amt=amt),
                       _t("ins.ch.many.short", n=n, roles=roles, window_cap=window_cap),
                       _t("ins.ch.many.min", n=n, roles=roles)]
        image_paras = [_t("ins.p.rows", rows="; ".join(row_item(r) for r in rows[:3]))]
        if rows[3:]:
            image_paras.append(_t("ins.p.rows2", rows="; ".join(row_item(r) for r in rows[3:])))
    else:
        image_heads = one_heads
        image_paras = [vp1]
    if flag_para:
        image_paras.append(" ".join(flag_para))
    image_paras.append(_t("ins.p.form4"))
    flags0 = ([_t(f"ins.p.flag.indirect.{indirect0}")] if indirect0 else []) + (
        [_t("ins.p.flag.amended")] if amended0 else [])
    video_paras = ([vp1] if p0 else []) + [_t("ins.p.form4")] + ([" ".join(flags0)] if flags0 else [])
    if m:
        roles_rest = [r.role for r in others]
        video_paras.append(_t("ins.vp.more.one", role=_group_role(roles_rest, "word")) if m == 1 else
                           _t("ins.vp.more.many", m=m, roles=_group_role(roles_rest, "plural")))
    yt = [_t("ins.yt.long", subj=subj, amt_s=amt_s, co=co), _t("ins.yt.short", subj=subj, amt_s=amt_s),
          _t("ins.yt.min", role=_role(r0.role, "word"), amt_s=amt_s)]
    persons: List[str] = []
    for r in rows:
        p = person(r)
        if p and p not in persons:
            persons.append(p)
    names: Dict[str, str] = {}
    for r in rows:
        names.setdefault(r.company.symbol, r.company.name)
    return _Parts(hook=hook, lines=[l1, l2, l3, l4], cards=[c1, c2, c3, c4], opening=opening, image_spec=spec,
                  alt_title=alt_title, alt_paragraphs=alt_paragraphs,
                  source=rules.source_label(rec), as_of=as_of,
                  image_headlines=image_heads, image_paragraphs=image_paras,
                  video_headlines=one_heads, video_paragraphs=video_paras, youtube_titles=yt,
                  persons=persons, names=names, stated_by_headline=one_stated)


# ── 13F Season ────────────────────────────────────────────────────────────────

#: The image's section order — new → more → fewer → no longer reported — and the tie-break of the
#: lead ranking (live-data finding L1(b), news-v5).
_F13_DISPLAY = ("newly_reported", "increased", "decreased", "no_longer_reported")
_F13_SHORT = {"newly_reported": "new", "no_longer_reported": "gone", "increased": "more", "decreased": "fewer"}
#: At most this many rows of one kind on the image (largest move first, then "+k more"). The image
#: draws the highest-ranked kinds with moves, within the fit bounds below.
F13_ROWS_PER_KIND = 3
#: The 13F image must lay out WHOLE in the worker's `rows` layout (`marketing/news_layouts.py`): a
#: CardOverflow skips the day at render, after the build was accepted — it never falls back to
#: another series (review round 8). These four bounds are what the fit tests in
#: tests/test_marketing_news_layouts.py prove against the worker's own code at its floor step, in
#: the widest glyphs (all-capital "W"), the worst word packing, an amended footer and 5-digit counts:
#:
#: * a title of at most F13_FULL_TITLE_CHARS characters is ONE line at the floor in any glyphs, and
#:   then the image holds up to MAX_SECTIONS kinds and F13_IMAGE_ROWS rows (one fewer than
#:   template_onscreen.MAX_ROWS: spare room at the floor even in the worst case);
#: * a longer title (a filer name up to company_news_rules.FILER_NAME_CHARS = 60 wraps to 5 lines
#:   in the worst packing) leaves room for F13_LONG_TITLE_SECTIONS kinds only: the lead and the
#:   next — the two kinds narration lines 2 and 3 cover; the subtitle still counts every kind;
#: * a word longer than F13_MAX_WORD_CHARS in the filer's or a holding's name cannot be drawn whole
#:   (the cover's headline and the image title are the tightest columns): the record is refused
#:   `slot_rejected` — one post lost, never the day.
F13_FULL_TITLE_CHARS = 31
F13_IMAGE_ROWS = 7
F13_LONG_TITLE_SECTIONS = 2
F13_MAX_WORD_CHARS = 20


def _holdings(n: int) -> str:
    return _t("f13.holding.one") if n == 1 else _t("f13.holding.many")


def _f13_move_value(mv: rules.ThirteenFMove) -> Optional[float]:
    """The dollar size of one move at the filing's own quarter-end values: what the lead kind and
    the row order rank by. Never drawn or said — a 13F value is the POSITION's, and beside
    "Reported more shares" it would read as the size of the increase.

    * newly reported: the position's value (all of it is new);
    * more / fewer shares: the shares that changed, at the value per share the filing gives
      (`value_usd × |shares − prev_shares| / shares`) — the adapter's own `flow` order;
    * no longer reported: `prev_value_usd`, the position's value on the PREVIOUS quarter's book
      (review round 8, the shared record contract; the adapter values every exit for its floor).
      Never `value_usd`: a position no longer reported has no quarter-end value of its own.

    None when the record carries no value: it ranks after every known one."""
    if mv.move == "no_longer_reported":
        return mv.prev_value_usd
    v = mv.value_usd
    if v is None:
        return None
    if mv.move in ("increased", "decreased"):
        if not mv.shares or mv.prev_shares is None:
            return None
        return v * abs(mv.shares - mv.prev_shares) / mv.shares
    return v


def _f13_by_value(moves: Sequence[rules.ThirteenFMove]) -> List[rules.ThirteenFMove]:
    """The largest move first, unknown values last, the record's order on a tie (a stable sort)."""
    def key(mv: rules.ThirteenFMove) -> Tuple[bool, float]:
        value = _f13_move_value(mv)
        return (value is None, -(value or 0.0))

    return sorted(moves, key=key)


def _f13_rank(by: Mapping[str, Sequence[rules.ThirteenFMove]]) -> List[str]:
    """The kinds with moves in the record, the one holding the largest move first (unknown values
    last, `_F13_DISPLAY` order on a tie). `by` holds each kind's moves largest first."""
    def key(kind: str) -> Tuple[bool, float, int]:
        top = _f13_move_value(by[kind][0])
        return (top is None, -(top or 0.0), _F13_DISPLAY.index(kind))

    return sorted((k for k in _F13_DISPLAY if by[k]), key=key)


def _f13_image_rows(ranked: Sequence[str], by: Mapping[str, Sequence[rules.ThirteenFMove]], *,
                    long_title: bool) -> Dict[str, int]:
    """Rows per drawn kind: the MAX_SECTIONS highest-ranked kinds — only F13_LONG_TITLE_SECTIONS
    (the lead and the next) under a title longer than F13_FULL_TITLE_CHARS — at most
    F13_ROWS_PER_KIND each; while the total is over F13_IMAGE_ROWS, the lowest-ranked kind still
    holding more than one row gives one up (its "+k more" grows)."""
    drawn = list(ranked[:F13_LONG_TITLE_SECTIONS if long_title else onscreen.MAX_SECTIONS])
    rows = {k: min(F13_ROWS_PER_KIND, len(by[k])) for k in drawn}
    while sum(rows.values()) > F13_IMAGE_ROWS:
        give = next((k for k in reversed(drawn) if rows[k] > 1), None)
        if give is None:
            raise NewsTemplateRefused("image_spec_invalid", "the kinds do not fit the image's rows")
        rows[give] -= 1
    return rows


def _thirteen_f_parts(rec: rules.ThirteenFFiling, run_date: date) -> _Parts:
    newest = rec.amended_on or rec.filed_on
    if rec.filed_on > run_date or newest > run_date or (run_date - rec.filed_on).days > THIRTEEN_F_MAX_AGE_DAYS:
        raise NewsTemplateRefused("stale_source", "the 13F is not filed in this season")
    filer = rec.filer_name
    problem = _slot_problem(filer)
    if problem:
        raise NewsTemplateRefused("slot_rejected", f"filer name: {problem}")
    for mv in rec.moves:
        problem = _slot_problem(mv.company.name)
        if problem:
            raise NewsTemplateRefused("slot_rejected", f"company name: {problem}")
    # every name the post may draw (the image rows, the cover, the video cards): a word the worker
    # cannot fit in its column is a CardOverflow at render — refuse the record here instead
    for name in (filer, *(mv.company.name for mv in rec.moves)):
        if max(len(w) for w in name.split()) > F13_MAX_WORD_CHARS:
            raise NewsTemplateRefused("slot_rejected", f"a word longer than {F13_MAX_WORD_CHARS} characters "
                                                       "cannot be drawn whole")
    counts: Dict[str, int] = dict(rec.counts)
    by: Dict[str, List[rules.ThirteenFMove]] = {
        k: _f13_by_value([m for m in rec.moves if m.move == k]) for k in _F13_DISPLAY}
    ranked = _f13_rank(by)
    if not ranked:
        raise NewsTemplateRefused("too_few_rows", "no move to show")
    # The lead kind sets the hook, the cover's figure and headline (and, without a filer symbol, its
    # logos), narration line 2 and the first caption count: the kind holding the LARGEST move by
    # value (live-data finding L1(b): Berkshire's 2026-Q2 13F led with a $0.6M new holding while a
    # $5.4B position grew). Which moves a record carries is the adapter's choice (its materiality
    # floors: a dollar floor on new and exited positions, a share-change floor on more / fewer);
    # the template ranks what it is given. A counted kind with no move in the record never leads —
    # the template cannot name what the record does not carry — and it is still stated (caption
    # counts, the subtitle, line 3 when no other kind has moves).
    lead = ranked[0]
    order = ranked + [k for k in _F13_DISPLAY if not by[k] and counts.get(k, 0) > 0]

    def total_of(kind: str) -> int:
        # the record refuses a count below its shown moves; max() keeps the figure honest anyway
        return max(counts.get(kind, 0), len(by[kind]))

    n_lead = total_of(lead)
    filer_sp = spoken_company(filer)
    poss, poss_sp = possessive(filer), possessive(filer_sp)
    if poss is None:
        subj = _t("f13.subj.of", filer=filer)
        subj_sp = [_t("f13.subj_sp.of", filer_sp=filer_sp), _t("f13.subj_sp.of.short", filer_sp=filer_sp)]
    else:
        subj = _t("f13.subj.poss", filer_s=poss)
        subj_sp = [_t("f13.subj_sp.poss", filer_sp_s=poss_sp), _t("f13.subj_sp.poss.short", filer_sp_s=poss_sp)]
    short = _F13_SHORT[lead]
    hook = [_t(f"f13.hook.{short}", subj_sp=s, n=n_lead, holdings=_holdings(n_lead)) for s in subj_sp]
    if lead != "newly_reported":
        hook += [_t(f"f13.hook.{short}.short", subj_sp=s, n=n_lead, holdings=_holdings(n_lead)) for s in subj_sp]
    period_img, filed_img = date_image(rec.period_end), date_image(rec.filed_on)
    period_sp, filed_sp = date_spoken(rec.period_end, run_date), date_spoken(rec.filed_on, run_date)

    # L1 / C1
    if rec.amended_on is not None:
        l1 = [_t("f13.l1.amended", period_sp=period_sp, amended_sp=date_spoken(rec.amended_on, run_date)),
              _t("f13.l1.amended.short", period_sp=period_sp)]
        c1 = (_t("f13.c1.title"), _t("f13.c1.body.amended", period_img=period_img,
                                      amended_img=date_image(rec.amended_on)))
        as_of = _t("f13.asof.amended", period_img=period_img, filed_img=filed_img,
                   amended_img=date_image(rec.amended_on))
    else:
        l1 = [_t("f13.l1", period_sp=period_sp, filed_sp=filed_sp), _t("f13.l1.short", period_sp=period_sp)]
        c1 = (_t("f13.c1.title"), _t("f13.c1.body", period_img=period_img, filed_img=filed_img))
        as_of = _t("f13.asof", period_img=period_img, filed_img=filed_img)

    def kind_lines(kind: str, *, first: bool) -> Tuple[List[str], Tuple[str, str]]:
        """One kind's narration variants and card: its moves listed (largest first), else its count."""
        moves, total, word = by[kind], total_of(kind), _F13_SHORT[kind]
        title = _t(f"f13.head.{word}")
        if moves:
            if kind == "newly_reported":
                line_id = "f13.l2.list.one" if total == 1 else "f13.l2.list.many"
            elif kind == "no_longer_reported":
                line_id = "f13.l3.gone.list.one" if total == 1 else "f13.l3.gone.list.many"
            else:
                line_id = f"f13.l3.{word}" if first else f"f13.l.{word}.next"
            lines = [_t(line_id, list=lst)
                     for lst in _list_variants([spoken_company(m.company.name) for m in moves], total)]
            return lines, (title, _list_variants([m.company.name for m in moves], total)[0])
        if kind == "newly_reported":
            line = _t("f13.l2.count.one") if total == 1 else _t("f13.l2.count.many", n=total)
        elif kind == "no_longer_reported":
            line = _t("f13.l3.gone.count.one") if total == 1 else _t("f13.l3.gone.count.many", n=total)
        else:
            line = _t(f"f13.l3.{word}.count", n=total, holdings=_holdings(total))
        return [line], (title, _t("f13.c.count.one") if total == 1 else _t("f13.c.count.many", n=total))

    # L2 / C2: the lead kind. L3 / C3: the next kind (a counted-only kind by its count), else the
    # second explainer.
    l2, c2 = kind_lines(lead, first=True)
    if len(order) > 1:
        l3, c3 = kind_lines(order[1], first=False)
    else:
        l3 = [_t("f13.l.explainer2")]
        c3 = (_t("f13.c.explainer2.title"), _t("f13.c.explainer2.body"))
    # L4 / C4 (a newly listed holding, else the 13F explainer)
    listed = next((mv for mv in by["newly_reported"] if mv.listed_on is not None), None)
    if listed is not None:
        l4 = [_t("f13.l4.listed", co_sp=spoken_company(listed.company.name),
                 month_sp=month_words(listed.listed_on))]
        c4 = (_t("f13.c4.listed.title"), _t("f13.c4.listed.body", co=listed.company.name,
                                              month_img=month_image(listed.listed_on)))
    else:
        l4 = [_t("f13.l4.explainer"), _t("f13.l4.explainer.short")]
        c4 = (_t("f13.c4.explainer.title"), _t("f13.c4.explainer.body"))

    # image: one section per kind with moves, in _F13_DISPLAY order, largest moves first (a title
    # that may wrap leaves room for the lead and the next kind only: see F13_FULL_TITLE_CHARS)
    def cells(mv: rules.ThirteenFMove) -> List[str]:
        out = [mv.company.name]
        if mv.move == "newly_reported" and mv.value_usd:
            out.append(money_image(mv.value_usd))
        if mv.move == "newly_reported" and mv.listed_on is not None:
            out.append(_t("f13.img.cell.listed", month_img=month_image(mv.listed_on)))
        return out

    rows_of = _f13_image_rows(ranked, by, long_title=len(subj) > F13_FULL_TITLE_CHARS)
    sections: List[Dict[str, Any]] = []
    for kind in _F13_DISPLAY:
        if kind not in rows_of:
            continue
        shown = by[kind][:rows_of[kind]]
        sec: Dict[str, Any] = {"heading": _t(f"f13.head.{_F13_SHORT[kind]}"),
                               "rows": [{"logo": mv.company.symbol, "cells": cells(mv)} for mv in shown]}
        more = total_of(kind) - len(shown)
        if more > 0:
            sec["more"] = _t("f13.img.more", k=more)
        sections.append(sec)
    # every counted kind, the lead first: a kind past MAX_SECTIONS is never silently missing
    subtitle = " · ".join(_t(f"f13.img.sub.{_F13_SHORT[k]}", n=total_of(k)) for k in order)
    spec: Dict[str, Any] = {"layout": "rows", "version": onscreen.SPEC_VERSION, "kicker": _t("f13.kicker"),
                            "title": subj, "subtitle": subtitle, "sections": sections}

    # opening card
    names: Dict[str, str] = {}
    for sec in sections:
        for row in sec["rows"]:
            mv = next(m for m in rec.moves if m.company.symbol == row["logo"])
            names.setdefault(mv.company.symbol, mv.company.name)
    if rec.filer_symbol is not None:
        if names.get(rec.filer_symbol, filer) != filer:
            raise NewsTemplateRefused("record_invalid", "the filer symbol is also a holding")
        names[rec.filer_symbol] = filer
        open_logos = [rec.filer_symbol]
    else:
        # only the lead kind's largest holdings: the logos sit beside its figure and headline
        open_logos = [m.company.symbol for m in by[lead][:2]]
        for mv in by[lead][:2]:
            names.setdefault(mv.company.symbol, mv.company.name)
    head_id = f"f13.open.{short}.{'of' if poss is None else 'poss'}"
    opening: Dict[str, Any] = {"kicker": _t("f13.kicker"), "logos": open_logos,
                               "figure": str(n_lead),
                               "headline": _t(head_id, holdings=_holdings(n_lead), filer=filer, filer_s=poss)}
    if rec.filer_symbol is not None:
        opening["chip"] = rec.filer_symbol

    # captions: the counts lead with the lead kind, carrying the noun
    count_phrases = []
    for i, kind in enumerate(order):
        n, word = total_of(kind), _F13_SHORT[kind]
        if i == 0:
            cid = "f13.cnt.new" if kind == "newly_reported" else f"f13.cnt.{word}.first"
        else:
            cid = "f13.cnt.new.rest" if kind == "newly_reported" else f"f13.cnt.{word}"
        count_phrases.append(_t(cid, n=n, holdings=_holdings(n)))
    counts_txt = ", ".join(count_phrases)
    lead_txt = (_t("f13.cnt.new", n=n_lead, holdings=_holdings(n_lead)) if lead == "newly_reported"
                else _t(f"f13.lead.{short}", n=n_lead, holdings=_holdings(n_lead)))
    period_cap = date_caption(rec.period_end, run_date)
    long_head = (_t("f13.ch.long.of", filer=filer, period_cap=period_cap, counts=counts_txt) if poss is None
                 else _t("f13.ch.long.poss", filer_s=poss, period_cap=period_cap, counts=counts_txt))
    heads = [long_head, _t("f13.ch.short", subj=subj, lead=lead_txt)]

    def items(kind: str, *, value: bool) -> str:
        out = []
        for mv in by[kind]:
            if value and mv.value_usd:
                out.append(_t("f13.item.value", co=mv.company.name, value=money_words(mv.value_usd)))
            else:
                out.append(mv.company.name)
        return "; ".join(out)

    para_of = {"newly_reported": lambda: _t("f13.p.new", items=items("newly_reported", value=True)),
               "no_longer_reported": lambda: _t("f13.p.gone", items=items("no_longer_reported", value=False)),
               "increased": lambda: _t("f13.p.more", items=items("increased", value=False)),
               "decreased": lambda: _t("f13.p.fewer", items=items("decreased", value=False))}
    # one paragraph per kind with moves, the lead's first (TikTok carries only that one)
    paras = [para_of[k]() for k in ranked[:3]] + [_t("f13.p.explainer")]
    yt = ([_t("f13.yt.of", filer=filer, lead=lead_txt)] if poss is None
          else [_t("f13.yt.poss", filer_s=poss, lead=lead_txt)]) + [_t("f13.yt.min", lead=lead_txt)]
    alt = [_t("f13.alt.section", heading=sec["heading"],
              items=", ".join(r["cells"][0] for r in sec["rows"])) for sec in sections][:3]
    return _Parts(hook=hook, lines=[l1, l2, l3, l4], cards=[c1, c2, c3, c4], opening=opening, image_spec=spec,
                  alt_title=subj, alt_paragraphs=alt, source=rules.source_label(rec), as_of=as_of,
                  image_headlines=heads, image_paragraphs=paras, video_headlines=heads, video_paragraphs=paras,
                  youtube_titles=yt, persons=[], names=names)


# ── Money Map ─────────────────────────────────────────────────────────────────


def _ratio(x: Decimal) -> float:
    r = float(_q(x, RATIO_PLACES))
    return min(max(r, 0.0), 1.0)


def _money_map_parts(rec: rules.MoneyMap, run_date: date) -> _Parts:
    if rec.period_end > run_date or (run_date - rec.period_end).days > MONEY_MAP_MAX_AGE_DAYS:
        raise NewsTemplateRefused("stale_source", "the fiscal year is not the latest reported")
    co, sym = rec.company.name, rec.company.symbol
    for value in [co] + [s.name for s in rec.segments]:
        problem = _slot_problem(value)
        if problem:
            raise NewsTemplateRefused("slot_rejected", f"name: {problem}")
    rev = _dec(rec.revenue_usd)
    ni = _dec(rec.net_income_usd)
    gp = None if rec.gross_profit_usd is None else _dec(rec.gross_profit_usd)
    op = None if rec.operating_profit_usd is None else _dec(rec.operating_profit_usd)
    if abs(ni) > rev or (gp is not None and abs(gp) > rev) or (op is not None and abs(op) > rev):
        raise NewsTemplateRefused("implausible_figures", "a profit or loss larger than revenue")
    segs = sorted(rec.segments, key=lambda s: (-s.value_usd, s.name.casefold()))
    other = _dec(rec.other_usd or 0.0)
    elim = _dec(rec.eliminations_usd or 0.0)
    seg_total = sum((_dec(s.value_usd) for s in segs), Decimal(0)) + other
    # Shares of revenue while eliminations are small; else (or when a segment alone would exceed
    # 100% of revenue) shares of the segments' own total — never a share above 100%.
    if abs(elim) <= Decimal("0.02") * rev and _dec(segs[0].value_usd) <= rev:
        basis, denom = _t("mm.basis.revenue"), rev
    else:
        basis, denom = _t("mm.basis.segments"), seg_total
    sh = [pct(float(_dec(s.value_usd) / denom)) for s in segs]
    fy = rec.fiscal_year
    co_sp = spoken_company(co)
    profit = ni >= 0
    per100 = money_cents(float(abs(ni) / rev * 100))
    rev_w, rev_s = money_words(rec.revenue_usd), money_image(rec.revenue_usd)
    ni_w, ni_s = money_words(abs(rec.net_income_usd)), money_image(abs(rec.net_income_usd))
    word = {True: _t("mm.word.profit"), False: _t("mm.word.loss")}

    if profit:
        hook = [_t("mm.hook.profit.long", fy=fy, co_sp=co_sp, per100=per100),
                _t("mm.hook.profit.short", co_sp=co_sp, per100=per100),
                _t("mm.hook.profit.min", co_sp=co_sp, per100=per100)]
    else:
        hook = [_t("mm.hook.loss.long", fy=fy, co_sp=co_sp, per100=per100),
                _t("mm.hook.loss.short", co_sp=co_sp, per100=per100),
                _t("mm.hook.loss.min", co_sp=co_sp, per100=per100)]
    if not any(HOOK_WORDS[0] <= _words(h) <= HOOK_WORDS[1] for h in hook):
        # For ANY figure (review round 4: a 6-word name's ordinary loss year had no hook of 5-14
        # words), but only when no longer variant fits: a package that composes with the longer
        # hooks keeps exactly them, and `_pick_script` can never step down to this one.
        hook.append(_t("mm.hook.profit.tiny" if profit else "mm.hook.loss.tiny", co_sp=co_sp, per100=per100))
    l1 =[_t("mm.l1", co_sp=co_sp, rev=rev_w, fy=fy)]
    l2 = [_t("mm.l2.two", s1=segs[0].name, sh1=sh[0], s2=segs[1].name, sh2=sh[1], basis=basis),
          _t("mm.l2.one", s1=segs[0].name, sh1=sh[0], basis=basis)]
    c2_body = [_t("mm.c2.two", s1=segs[0].name, sh1=sh[0], s2=segs[1].name, sh2=sh[1], basis=basis),
               _t("mm.c2.one", s1=segs[0].name, sh1=sh[0], basis=basis)]
    gp_fill = {} if gp is None else {"gp_word": word[gp >= 0], "gp": money_words(float(abs(gp))),
                                     "gp_s": money_image(float(abs(gp)))}
    op_fill = {} if op is None else {"op_word": word[op >= 0], "op": money_words(float(abs(op))),
                                     "op_s": money_image(float(abs(op)))}
    if gp is not None and op is not None:
        costs = "both"
    elif gp is not None:
        costs = "gross"
    elif op is not None:
        costs = "operating"
    else:
        costs = "none"
    l3 = [_t(f"mm.l3.{costs}", **gp_fill, **op_fill)] + (
        [_t("mm.l3.both.short", **gp_fill, **op_fill)] if costs == "both" else [])
    if costs == "none":
        c3 = (_t("mm.c3.none.title"), _t("mm.c3.none.body"))
    else:
        c3 = (_t("mm.c3.title"), _t(f"mm.c3.{costs}", **gp_fill, **op_fill))
    l4 = [_t("mm.l4.profit" if profit else "mm.l4.loss", ni=ni_w)]
    c1 = (_t("mm.c1.title"), _t("mm.c1.body", rev_s=rev_s, fy=fy))
    c4 = (_t("mm.c4.profit.title" if profit else "mm.c4.loss.title"), _t("mm.c4.body", ni_s=ni_s, fy=fy))

    # image (bars)
    bar_values = [_dec(s.value_usd) for s in segs] + ([other] if other > 0 else []) + (
        [abs(elim)] if elim < 0 else [])
    top = max(bar_values)
    segments = [{"label": s.name, "value": money_image(s.value_usd), "ratio": _ratio(_dec(s.value_usd) / top),
                 "style": "fill"} for s in segs]
    if other > 0:
        segments.append({"label": _t("mm.bar.other"), "value": money_image(float(other)),
                         "ratio": _ratio(other / top), "style": "fill"})
    if elim < 0:
        segments.append({"label": _t("mm.bar.elim"), "value": money_image(float(elim)),
                         "ratio": _ratio(abs(elim) / top), "style": "outline"})
    flow = [{"label": _t("mm.flow.revenue"), "value": rev_s, "ratio": 1.0, "style": "fill"}]
    for value, key in ((gp, "gross"), (op, "op")):
        if value is not None:
            flow.append({"label": _t(f"mm.flow.{key}.{'profit' if value >= 0 else 'loss'}"),
                         "value": money_image(float(abs(value))), "ratio": _ratio(abs(value) / rev),
                         "style": "fill" if value >= 0 else "outline"})
    flow.append({"label": _t("mm.flow.net.profit" if profit else "mm.flow.net.loss"), "value": ni_s,
                 "ratio": _ratio(abs(ni) / rev), "style": "fill" if profit else "outline"})
    callout = _t("mm.img.callout.profit" if profit else "mm.img.callout.loss", per100=per100)
    title = _t("mm.img.title", co=co)
    spec: Dict[str, Any] = {
        "layout": "bars", "version": onscreen.SPEC_VERSION, "kicker": _t("mm.kicker"),
        "header": {"logo": sym, "name": co}, "title": title, "subtitle": _t("mm.img.subtitle", fy=fy),
        "segments": segments, "flow": flow, "callout": callout,
    }
    opening = {"kicker": _t("mm.kicker"), "logos": [sym], "chip": sym, "figure": per100,
               "headline": _t("mm.open.profit" if profit else "mm.open.loss", co=co)}
    # captions
    seg_items = [_t("mm.item", name=s.name, value=money_words(s.value_usd)) for s in segs]
    if other > 0:
        seg_items.append(_t("mm.item.other", value=money_words(float(other))))
    seg_para = _t("mm.p.segments", fy=fy, items="; ".join(seg_items))
    if elim < 0:
        seg_para += " " + _t("mm.p.elim", elim=money_words(float(abs(elim))))
    paras = [seg_para]
    if costs != "none":
        paras.append(_t(f"mm.p.costs.{costs}", **gp_fill, **op_fill))
    paras.append(_t("mm.p.net.profit" if profit else "mm.p.net.loss", ni=ni_w, per100=per100))
    paras.append(_t("mm.p.source", fy=fy))
    heads = [_t("mm.ch.long.profit" if profit else "mm.ch.long.loss", co=co, rev=rev_w, fy=fy, per100=per100),
             _t("mm.ch.short", co=co, fy=fy)]
    alt = [_t("mm.alt.segments", items="; ".join(_t("mm.alt.item", label=b["label"], value=b["value"])
                                                  for b in segments)),
           _t("mm.alt.flow", items="; ".join(_t("mm.alt.item", label=b["label"], value=b["value"]) for b in flow)),
           _t("mm.alt.callout", callout=callout)]
    as_of = _t("mm.asof", fy=fy, period_img=date_image(rec.period_end))
    return _Parts(hook=hook, lines=[l1, l2, l3, l4],
                  cards=[c1, (_t("mm.c2.title"), c2_body[0]), c3, c4],
                  opening=opening, image_spec=spec, alt_title=title, alt_paragraphs=alt,
                  source=rules.source_label(rec), as_of=as_of, image_headlines=heads, image_paragraphs=paras,
                  video_headlines=heads, video_paragraphs=paras,
                  youtube_titles=[_t("mm.yt", co=co, fy=fy), _t("mm.yt.short", co=co)],
                  persons=[], names={sym: co})


# ── drop 2b: shared helpers ───────────────────────────────────────────────────

#: The alt text's title (`image_post.title`, Drop-1's writer bound): the first variant this short wins.
ALT_TITLE_MAX_CHARS = 70
ALT_TITLE_MAX_WORDS = 10


def _alt_title(variants: Sequence[str]) -> str:
    for v in variants:
        if len(v) <= ALT_TITLE_MAX_CHARS and len(v.split()) <= ALT_TITLE_MAX_WORDS:
            return v
    return min(variants, key=len)


def _longest_word(value: str) -> int:
    return max((len(w) for w in value.split()), default=0)


def _char_lines(text: str, per_line: int) -> int:
    """The lines a greedy wrap BY CHARACTERS needs at `per_line` characters a line (a word longer
    than a line: 10**6). With every character at most a "W" wide and `per_line` "W"s fitting the
    worker's column at the floor size, each such line fits in pixels too, and a greedy wrap is the
    fewest lines — so the worker's own wrap never needs more (tests/test_marketing_news_layouts.py)."""
    lines, cur = 0, 0
    for word in text.split():
        if len(word) > per_line:
            return 10 ** 6
        if cur and cur + 1 + len(word) <= per_line:
            cur += 1 + len(word)
        else:
            lines, cur = lines + 1, len(word)
    return lines


def _name_slot(value: str, what: str, word_cap: int = NAME_WORD_MAX_CHARS) -> None:
    """A drawn / narrated name: the slot rules (§4.5), and no word the worker cannot draw whole."""
    problem = _slot_problem(value)
    if problem:
        raise NewsTemplateRefused("slot_rejected", f"{what}: {problem}")
    if _longest_word(value) > word_cap:
        raise NewsTemplateRefused("slot_rejected", f"{what}: a word longer than {word_cap} characters")


def _month_start(month: str) -> date:
    try:
        return rules.month_end_of(month).replace(day=1)
    except (ValueError, TypeError) as e:
        raise NewsTemplateRefused("record_invalid", f"month: {e}") from None


# ── Congress Count (2b-1) ─────────────────────────────────────────────────────


def _congress_parts(rec: rules.CongressCount, run_date: date) -> _Parts:
    start = _month_start(rec.month)
    month_end = rules.month_end_of(rec.month)
    before = run_date.replace(day=1) - timedelta(days=1)
    # dated by the disclosure month, which is always the calendar month before the run
    if (start.year, start.month) != (before.year, before.month):
        raise NewsTemplateRefused("stale_source", "the disclosure month is not the month before the run")
    fetched = rec.fetched_on
    if (fetched > run_date or (fetched - month_end).days < CONGRESS_SETTLE_DAYS
            or (run_date - fetched).days > CONGRESS_MAX_AGE_DAYS):
        raise NewsTemplateRefused("stale_source", "the count was not read in the week before the run, a week "
                                                  "after its month ended")
    n = rec.members
    # ≥ 2 DISTINCT members: "1 member of Congress" points at one findable person (rules §1)
    if not isinstance(n, int) or isinstance(n, bool) or n < rules.CONGRESS_MIN_MEMBERS or n > 535:
        raise NewsTemplateRefused("too_few_rows", "a count of fewer than two members is never posted")
    co, sym = rec.company.name, rec.company.symbol
    _name_slot(co, "company name")
    co_sp = spoken_company(co)
    fills = dict(n=n, co=co, co_sp=co_sp, month_long=month_words(start), month_name=month_name(start),
                 month_upper=month_name(start).upper(), month_short=month_image(start),
                 as_of_img=date_image(fetched), as_of_sp=date_spoken(fetched, run_date),
                 as_of_cap=date_caption(fetched, run_date))
    kicker = _t("cg.kicker", **fills)
    as_of = _t("cg.asof", **fills)
    hook = [_t("cg.hook.year", **fills), _t("cg.hook", **fills), _t("cg.hook.min", **fills)]
    lines = [[_t("cg.l1", **fills)], [_t("cg.l2")], [_t("cg.l3")], [_t("cg.l4")]]
    cards = [(_t("cg.c1.title"), _t("cg.c1.body", **fills)), (_t("cg.c2.title"), _t("cg.c2.body")),
             (_t("cg.c3.title"), _t("cg.c3.body")), (_t("cg.c4.title"), _t("cg.c4.body"))]
    headline = _t("cg.headline", **fills)
    opening = {"kicker": kicker, "logos": [sym], "chip": sym, "figure": str(n), "headline": headline}
    spec: Dict[str, Any] = {
        "layout": "spotlight", "version": onscreen.SPEC_VERSION, "kicker": kicker,
        "header": {"logo": sym, "name": co, "chip": sym}, "figure": str(n), "headline": headline,
        "lines": [as_of, _t("cg.img.once")],
    }
    heads = [_t("cg.ch.long", **fills), _t("cg.ch.short", **fills), _t("cg.ch.min", **fills)]
    paras = [_t("cg.p.count", **fills), _t("cg.l2"), _t("cg.l3"), _t("cg.l4")]
    # review round 11: the long headline already counts each member once, so `cg.l2` is left out under it
    stated = {heads[0]: frozenset({_t("cg.l2")})}
    return _Parts(hook=hook, lines=lines, cards=cards, opening=opening, image_spec=spec,
                  alt_title=_alt_title([_t("cg.alt.title", **fills), _t("cg.alt.title.min")]),
                  alt_paragraphs=[_t("cg.alt.body", **fills)], source=rules.source_label(rec), as_of=as_of,
                  image_headlines=heads, image_paragraphs=paras, video_headlines=heads, video_paragraphs=paras,
                  youtube_titles=[_t("cg.yt", **fills), _t("cg.yt.short", **fills)], persons=[], names={sym: co},
                  stated_by_headline=stated)


# ── Company Stakes (2b-2) ─────────────────────────────────────────────────────

#: The bases a posted stake may carry (`schemas.trillion_club.VALUE_BASES`), each with its own verb.
STAKE_BASES: Tuple[str, ...] = ("invested", "committed_up_to", "carrying_value", "fair_value")


def _names_a_person(text: str) -> bool:
    """A capitalised given name followed by a capitalised word ("Jensen Huang said …"): fail closed
    — the line is treated as naming a person."""
    toks = re.findall(r"[A-Za-z][A-Za-z'-]*", text)
    known = given_names()
    return any(t[0].isupper() and t.lower() in known and nxt[0].isupper() for t, nxt in zip(toks, toks[1:]))


def _stake_background(text: Any, names: Sequence[str]) -> Optional[str]:
    """The stake's background line when every rule passes, else None (it is dropped, never fixed:
    hand-written free text with unnormalised numbers — A3: only ever a long-caption paragraph)."""
    if not isinstance(text, str) or not text or len(text) > STAKE_BACKGROUND_MAX_CHARS or not text.isascii():
        return None
    if _slot_problem(text) or not text[0].isupper() or not text.endswith("."):
        return None
    if _PER_SHARE_RE.search(text) or not _percent_ok(text) or rules.congress_name_hits(text):
        return None
    folded = fold(_masked(text, _mask_re(names)))
    if any(rx.search(folded) for rx in (copy_rules.BANNED_COPY, copy_rules.FORECAST_COPY, NEWS_BANNED_RE,
                                         STAKES_BANNED_RE)):
        return None
    if _names_a_person(text) or scan_text("stake.background", text):
        return None
    return text


def _stake_parts(rec: rules.CompanyStake, run_date: date) -> _Parts:
    if rec.as_of > run_date or rec.verified_on > run_date:
        raise NewsTemplateRefused("stale_source", "the stake is dated after the run")
    if (run_date - rec.verified_on).days > STAKE_MAX_VERIFIED_AGE_DAYS:
        raise NewsTemplateRefused("stale_source", "the stake was verified too long ago")
    if (run_date - rec.as_of).days > STAKE_MAX_AS_OF_AGE_DAYS:
        raise NewsTemplateRefused("stale_source", "the stake's figure is too old")
    basis = rec.value_basis
    # a disclosed dollar figure and its basis, always: an ownership share alone is never posted (the
    # record does not carry its basis — "of Class A", "held by a subsidiary" — so it cannot be said)
    if rec.value_usd is None or basis not in STAKE_BASES or not rec.value_usd > 0:
        raise NewsTemplateRefused("record_invalid", "a stake is posted only with a disclosed figure and its basis")
    ee = rec.investee_name
    if "(" in ee or "not named" in ee.lower():
        raise NewsTemplateRefused("record_invalid", "an aggregate or unnamed stake")
    if (rec.kind == "commitment") != (basis == "committed_up_to"):
        raise NewsTemplateRefused("record_invalid", "a commitment and 'committed up to' come together")
    inv, inv_sym = rec.investor.name, rec.investor.symbol
    investee = rec.investee
    if (investee is not None and investee.symbol == inv_sym) or fold(inv) == fold(ee):
        raise NewsTemplateRefused("record_invalid", "a company's stake in itself")
    if rec.listed_since is not None and rec.listed_since > run_date:
        raise NewsTemplateRefused("record_invalid", "listed after the run")
    _name_slot(inv, "investor name", PAIR_NAME_WORD_MAX_CHARS)
    _name_slot(ee, "investee name", PAIR_NAME_WORD_MAX_CHARS)
    if investee is not None:
        _name_slot(investee.name, "investee company name")
    if len(ee) > STAKE_NAME_MAX_CHARS:
        raise NewsTemplateRefused("slot_rejected", f"investee name over {STAKE_NAME_MAX_CHARS} characters")
    source = rules.source_label(rec)
    for value, what, cap in ((source, "source title", STAKE_SOURCE_MAX_CHARS),
                             (rec.local_listing, "local listing", rules.COMPANY_NAME_CHARS[1])):
        if value is None:
            continue
        problem = _slot_problem(value)
        if problem or len(value) > cap or _longest_word(value) > NAME_WORD_MAX_CHARS:
            raise NewsTemplateRefused("slot_rejected", f"{what}: {problem or 'too long to draw whole'}")
    inv_sp, ee_sp = spoken_company(inv), spoken_company(ee)
    amt, amt_s = money_words(rec.value_usd), money_image(rec.value_usd)
    h_ids = [f"st.h.{basis}"] + (["st.h.carrying_value.short"] if basis == "carrying_value" else []) + (
        [f"st.h.fair_value.short.{'of' if possessive(inv) is None else 'poss'}"] if basis == "fair_value" else [])
    h_sp = [_t(i, inv=inv_sp, inv_s=possessive(inv_sp), ee=ee_sp, amt=amt) for i in h_ids]
    h_cap = [_t(i, inv=inv, inv_s=possessive(inv), ee=ee, amt=amt) for i in h_ids]
    h_img = [_t(i, inv=inv, inv_s=possessive(inv), ee=ee, amt=amt_s) for i in h_ids]
    as_of_img = date_image(rec.as_of)
    as_of = _t("st.asof", as_of_img=as_of_img)
    as_of_cap = date_caption(rec.as_of, run_date)

    # L3 / C3: what the source says about the investee's listing — a claim only the record carries
    if rec.kind == "commitment":
        l3_key = "commitment"
    elif rec.listed_since is not None:
        l3_key = "listed"
    elif rec.kind == "private":
        l3_key = "private"
    elif rec.kind == "non_us_listed":
        l3_key = "non_us"
    elif investee is not None:     # us_listed_off_13f / on_13f_note with a verified U.S. listing
        l3_key = "us"
    else:
        l3_key = "source"
    month_fill = ({"month_sp": month_words(rec.listed_since), "month_img": month_image(rec.listed_since)}
                  if rec.listed_since is not None else {})
    l3_sp = _t(f"st.l3.{l3_key}", ee=ee_sp, **month_fill)
    l3_cap = _t(f"st.l3.{l3_key}", ee=ee, **month_fill)
    c3_body = (rec.local_listing if l3_key == "non_us" and rec.local_listing
               else _t(f"st.c3.{l3_key}.body", **month_fill))
    hook = [_t("st.hook", h=h) for h in h_sp]
    lines = [[_t("st.l1", as_of_sp=date_spoken(rec.as_of, run_date))], [_t(f"st.l2.{basis}")], [l3_sp],
             [_t("st.l4")]]
    cards = [(_t("st.c1.title"), _t("st.c1.body", as_of_img=as_of_img)),
             (_t(f"st.c2.{basis}.title"), _t(f"st.c2.{basis}.body")),
             (_t(f"st.c3.{l3_key}.title"), c3_body),
             (_t("st.c4.title"), _t("st.c4.body", source=source))]
    label = _t(f"st.label.{basis}")
    right: Dict[str, Any] = {"name": ee}
    names = {inv_sym: inv}
    if investee is not None:
        right["logo"] = investee.symbol
        names[investee.symbol] = investee.name
    spec: Dict[str, Any] = {
        "layout": "pair", "version": onscreen.SPEC_VERSION, "kicker": _t("st.kicker"),
        "left": {"logo": inv_sym, "name": inv}, "right": right, "figure": amt_s, "label": label,
        "lines": [as_of, _t("st.img.source", source=source)],
    }
    inv_s = possessive(inv)
    value_head = (_t("st.open.value.poss", inv_s=inv_s, ee=ee) if inv_s is not None
                  else _t("st.open.value.of", inv=inv, ee=ee))
    head = (_t(f"st.open.{basis}", inv=inv, ee=ee) if basis in ("invested", "committed_up_to") else value_head)
    opening = {"kicker": _t("st.kicker"), "logos": [inv_sym], "figure": amt_s, "headline": head}
    long_heads = [_t("st.ch.long", h=h, as_of_cap=as_of_cap, source=source) for h in h_cap]
    heads = long_heads + [_t("st.ch.short", h=h, as_of_cap=as_of_cap) for h in h_cap]
    background = _stake_background(rec.background, [inv, ee, source] + ([investee.name] if investee else []))
    source_para = _t("st.p.source", source=source, as_of_cap=as_of_cap)
    paras = ([background] if background else []) + [_t(f"st.l2.{basis}"), l3_cap, source_para]
    # review round 11: a ".long" headline cites the source and its as-of date, so `st.p.source` is left
    # out under it; a ".short" headline does not, and keeps it
    stated = {h: frozenset({source_para}) for h in long_heads}
    return _Parts(hook=hook, lines=lines, cards=cards, opening=opening, image_spec=spec,
                  alt_title=_alt_title(h_img + [value_head]),
                  alt_paragraphs=[_t("st.alt.body", inv=inv, ee=ee, amt_s=amt_s, label=label)],
                  source=source, as_of=as_of, image_headlines=heads, image_paragraphs=paras,
                  video_headlines=heads, video_paragraphs=paras, youtube_titles=h_img + [value_head],
                  persons=[], names=names, stated_by_headline=stated)


# ── Earnings vs Estimates (2b-3) ──────────────────────────────────────────────


def _earnings_parts(rec: rules.EarningsReport, run_date: date) -> _Parts:
    if not 1 <= (run_date - rec.report_date).days <= EARNINGS_MAX_AGE_DAYS:
        raise NewsTemplateRefused("stale_source", "the results were not reported in the week before the run")
    if rec.period_end is not None and (rec.period_end > rec.report_date or (
            rec.report_date - rec.period_end).days > EARNINGS_MAX_REPORT_LAG_DAYS):
        raise NewsTemplateRefused("implausible_figures", "the quarter's end is not before its report")
    co, sym = rec.company.name, rec.company.symbol
    _name_slot(co, "company name")
    a, e = rec.eps_actual, rec.eps_estimate
    if abs(e) < rules.EPS_MIN_ABS_ESTIMATE:
        raise NewsTemplateRefused("implausible_figures", "an estimate under ten cents")
    if a != 0 and _q(abs(_dec(a)), 2) == 0:
        raise NewsTemplateRefused("implausible_figures", "a non-zero EPS that would read $0.00")
    if eps_digit_shift_suspect(a, e):
        raise NewsTemplateRefused("implausible_figures", "a dropped or added digit")
    if abs(a - e) > EARNINGS_MAX_GAP_RATIO * abs(e):
        raise NewsTemplateRefused("implausible_figures", "an EPS gap ten times the estimate")
    # The revenue pair, written at one shared precision (`revenue_pair`); None = left out, never two
    # figures rounded on their own that misstate the gap.
    rev_fill: Optional[Dict[str, str]] = None
    if rec.revenue_actual is not None or rec.revenue_estimate is not None:
        ra, rv = rec.revenue_actual, rec.revenue_estimate
        lo, hi = rules.REVENUE_RATIO_BAND
        if ra is None or rv is None or not (ra > 0 and rv > 0 and lo <= ra / rv <= hi):
            raise NewsTemplateRefused("implausible_figures", "revenue outside the plausible band")
        rev_fill = revenue_pair(ra, rv)
        if rev_fill is None:
            logger.info("news template: earnings %s revenue left out — no shared precision up to %d places "
                        "shows its gap within %s of the true one", sym, REVENUE_MAX_PLACES, REVENUE_GAP_TOLERANCE)
    co_sp = spoken_company(co)

    def spoken(v: float) -> str:
        # the sign as a word: narration reads magnitudes, and "lost" / "earned" would state a profit or
        # loss the calendar's per-share figure (on the analysts' basis) does not establish
        return _t("er.neg", x=eps_words(v)) if v < 0 else eps_words(v)

    eps_w, est_w, eps_s, est_s = spoken(a), spoken(e), eps_image(a), eps_image(e)
    report_img = date_image(rec.report_date)
    report_sp, report_cap = date_spoken(rec.report_date, run_date), date_caption(rec.report_date, run_date)
    hook = [_t("er.hook.long", co_sp=co_sp, eps=eps_w, est=est_w),
            _t("er.hook.short", co_sp=co_sp, eps=eps_w, est=est_w)]
    if rec.period_end is not None:
        period_img = date_image(rec.period_end)
        l1 = [_t("er.l1.period", period_sp=date_spoken(rec.period_end, run_date), report_sp=report_sp)]
        c1 = (_t("er.c1.period.title"), _t("er.c1.period.body", period_img=period_img, report_img=report_img))
        subtitle = _t("er.img.sub.period", period_img=period_img, report_img=report_img)
        p_when = _t("er.p.period", period_cap=date_caption(rec.period_end, run_date), report_cap=report_cap)
    else:
        l1 = [_t("er.l1.report", report_sp=report_sp)]
        c1 = (_t("er.c1.report.title"), _t("er.c1.report.body", report_img=report_img))
        subtitle = _t("er.img.sub.report", report_img=report_img)
        p_when = _t("er.p.report", report_cap=report_cap)
    estimate_card = (_t("er.c.estimate.title"), _t("er.c.estimate.body"))
    rows = [{"cells": [_t("er.img.eps"), _t("er.img.vs", x=est_s), eps_s]}]
    if rev_fill is not None:
        l2, c2 = [_t("er.l2.revenue", **rev_fill)], (_t("er.c2.revenue.title"), _t("er.c2.revenue.body", **rev_fill))
        l4, c4 = [_t("er.estimate")], estimate_card
        # the REPORTED figure is the last cell (the right-aligned accent figure), the estimate the muted one
        rows.append({"cells": [_t("er.img.revenue"), _t("er.img.vs", x=rev_fill["re_s"]), rev_fill["ra_s"]]})
    else:
        l2, c2 = [_t("er.estimate")], estimate_card
        l4, c4 = [_t("er.l4.quarterly")], (_t("er.c4.quarterly.title"), _t("er.c4.quarterly.body"))
    l3, c3 = [_t("er.l3.basis")], (_t("er.c3.title"), _t("er.c3.body"))
    kicker = _t("er.kicker")
    spec: Dict[str, Any] = {
        "layout": "rows", "version": onscreen.SPEC_VERSION, "kicker": kicker,
        "header": {"logo": sym, "name": co, "chip": sym}, "title": _t("er.img.title"), "subtitle": subtitle,
        "sections": [{"rows": rows}], "notes": [_t("er.img.note")],
    }
    opening = {"kicker": kicker, "logos": [sym], "chip": sym, "figure": eps_s,
               "headline": _t("er.open.head", est_s=est_s)}
    figs = dict(co=co, eps_s=eps_s, est_s=est_s, **(rev_fill or {}))
    # long → short: the basis note in the headline wherever one with it fits (X / Bluesky / Threads
    # carry the headline alone), then the plain headlines for the tightest budgets
    with_rev = ["er.ch.long.basis"] if rev_fill is not None else []
    plain_rev = ["er.ch.long"] if rev_fill is not None else []
    head_ids = (*with_rev, "er.ch.short.basis", "er.ch.min.basis", *plain_rev, "er.ch.short", "er.ch.min")
    heads = [_t(i, **figs) for i in head_ids]
    # no paragraph restates the hook: the headline states the figures, the paragraphs what they are
    rev_para = _t("er.l2.revenue", **rev_fill) if rev_fill is not None else None
    # the basis note and the estimate definition are two paragraphs (review round 11), so a ".basis"
    # headline can leave the note out and keep the definition
    basis_para, estimate_para = _t("er.l3.basis"), _t("er.estimate")
    paras = ([rev_para] if rev_para is not None else []) + [p_when, basis_para, estimate_para]
    # ... and no paragraph repeats what the picked headline states: a ".long" headline carries the
    # revenue pair (review round 10), a ".basis" headline the basis note (review round 11)
    stated: Dict[str, FrozenSet[str]] = {}
    for i, h in zip(head_ids, heads):
        said = ({rev_para} if rev_para is not None and i in ("er.ch.long.basis", "er.ch.long") else set()) | (
            {basis_para} if i.endswith(".basis") else set())
        if said:
            stated[h] = frozenset(said)
    alt = _t("er.alt.eps", eps_s=eps_s, est_s=est_s) + (
        f" {_t('er.alt.revenue', **rev_fill)}" if rev_fill is not None else "")
    return _Parts(hook=hook, lines=[l1, l2, l3, l4], cards=[c1, c2, c3, c4], opening=opening, image_spec=spec,
                  alt_title=_alt_title([_t("er.alt.title", co=co), _t("er.img.title")]), alt_paragraphs=[alt],
                  source=rules.source_label(rec), as_of=_t("er.asof", report_img=report_img),
                  image_headlines=heads, image_paragraphs=paras, video_headlines=heads, video_paragraphs=paras,
                  youtube_titles=[_t("er.yt", **figs), _t("er.yt.short", **figs)], persons=[], names={sym: co},
                  stated_by_headline=stated)


# ── Theme Explainer (2b-4) ────────────────────────────────────────────────────

#: The four long captions that list EVERY member (contract A4): their first paragraph is the member
#: list, and an outlet whose budget cannot hold it is dropped, never posted without it.
THEME_LIST_FIELDS: Tuple[str, ...] = ("facebook", "linkedin", "instagram", "youtube_description")


def _theme_parts(rec: rules.ThemeExplainer, run_date: date) -> _Parts:
    if rec.tickers_as_of > run_date or (run_date - rec.tickers_as_of).days > THEME_MAX_AGE_DAYS:
        raise NewsTemplateRefused("stale_source", "the theme's member list is too old")
    theme = rec.title
    _name_slot(theme, "theme title")
    # Caydex's own words, never masked like a company name: the news ban and the verdict words too
    folded_title = fold(theme)
    if NEWS_BANNED_RE.search(folded_title) or THEME_TITLE_BANNED_RE.search(folded_title):
        raise NewsTemplateRefused("slot_rejected", "theme title: a news-banned, performance or verdict word")
    if _char_lines(theme, THEME_TITLE_LINE_CHARS) > THEME_TITLE_MAX_LINES:
        raise NewsTemplateRefused("slot_rejected", f"theme title wraps past {THEME_TITLE_MAX_LINES} lines")
    members = list(rec.members)
    n = len(members)
    if n < rules.THEME_MEMBERS[0] or len({m.company.symbol for m in members}) != n:
        raise NewsTemplateRefused("record_invalid", "a theme of fewer than six distinct members")
    # The theme's own count before the adapter's gates (`ThemeExplainer.theme_size`, the shared
    # record contract; None = not recorded). Larger than the members → "{n} of its {m}" wherever the
    # count is given; equal → the list is the whole theme and may say so; None → never a size claim.
    size = rec.theme_size
    if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < n):
        raise NewsTemplateRefused("record_invalid", "the theme's size is not a count of at least its members")
    part = size is not None and size > n
    for m in members:
        _name_slot(m.company.name, "member name")
    # A member's segment fact: shown and narrated only when it passes the slot rules, fits a tile
    # line and is recent — otherwise the member stays, without the fact (never guessed).
    facts: List[Tuple[rules.ThemeMember, str, Optional[str]]] = []
    for m in members:
        seg, fy = m.top_segment, m.fiscal_year
        if seg is None or fy is None or not fy.isdigit():
            continue
        if not run_date.year - THEME_FACT_MAX_YEARS <= int(fy) <= run_date.year:
            continue
        if (_slot_problem(seg) or len(seg) > THEME_SEGMENT_MAX_CHARS
                or _char_lines(seg, GRID_LINE_WORD_MAX_CHARS) > GRID_MAX_LINES):
            continue
        facts.append((m, seg, pct(m.top_segment_share) if m.top_segment_share is not None else None))
    if len(facts) < THEME_MIN_FACTS:
        raise NewsTemplateRefused("too_few_rows", f"fewer than {THEME_MIN_FACTS} members with a segment fact")
    theme_sp = spoken_company(theme)
    hook = ([_t(i, n=n, m=size, theme=theme_sp) for i in ("th.hook.part", "th.hook.part.short", "th.hook.part.min")]
            if part else [_t("th.hook", n=n, theme=theme_sp), _t("th.hook.short", n=n, theme=theme_sp)])

    def sp(fact: Tuple[rules.ThemeMember, str, Optional[str]]) -> Tuple[str, Optional[str], str]:
        name = spoken_company(fact[0].company.name)
        return name, possessive(name), spoken_company(fact[1])

    def line_of(group: Sequence[Tuple[rules.ThemeMember, str, Optional[str]]]) -> str:
        m1, m1_s, g1 = sp(group[0])
        if len(group) == 1:
            return _t(f"th.one.{'poss' if m1_s else 'of'}", m1=m1, m1_s=m1_s, g1=g1)
        m2, m2_s, g2 = sp(group[1])
        return _t(f"th.pair.{'poss' if m1_s else 'of'}.{'poss' if m2_s else 'of'}",
                  m1=m1, m1_s=m1_s, g1=g1, m2=m2, m2_s=m2_s, g2=g2)

    def fits(text: str) -> bool:
        return LINE_WORDS[0] <= _words(text) <= LINE_WORDS[1]

    # Narration: the facts in theme order, two to a line when the pair fits, at most three lines and
    # THEME_NARRATED_MAX facts; a fact whose own line cannot fit is not narrated.
    narrated = facts[:THEME_NARRATED_MAX]
    groups: List[Tuple[Tuple[rules.ThemeMember, str, Optional[str]], ...]] = []
    i = 0
    while i < len(narrated) and len(groups) < 3:
        if i + 1 < len(narrated) and fits(line_of(narrated[i:i + 2])):
            groups.append(tuple(narrated[i:i + 2]))
            i += 2
        elif fits(line_of(narrated[i:i + 1])):
            groups.append(tuple(narrated[i:i + 1]))
            i += 1
        else:
            i += 1
    if len(groups) == 1 and len(groups[0]) == 2 and all(fits(line_of([f])) for f in groups[0]):
        groups = [(groups[0][0],), (groups[0][1],)]
    explainer, grouping = _t("th.explainer"), _t("th.grouping")
    min_hook = min((_words(h) for h in hook if HOOK_WORDS[0] <= _words(h) <= HOOK_WORDS[1]),
                   default=HOOK_WORDS[1])

    def total(gs: Sequence[Any]) -> int:
        return (min_hook + sum(_words(line_of(g)) for g in gs) + (_words(explainer) if len(gs) < 3 else 0)
                + _words(grouping))

    while len(groups) > 2 and total(groups) > NARRATION_WORDS[1]:
        groups.pop()
    if len(groups) < 2:
        raise NewsTemplateRefused("too_few_rows", "fewer than two narration lines of segment facts")

    def card_of(group: Sequence[Tuple[rules.ThemeMember, str, Optional[str]]]) -> Tuple[str, str]:
        # a title names the members only when it fits CARD_TITLE_MAX_WORDS and every word can be drawn
        # whole in the title's style; else the generic title, the names in the (wider-reaching) body
        a, s1 = group[0][0].company.name, group[0][1]
        if len(group) == 1:
            if _longest_word(a) > CARD_TITLE_WORD_MAX_CHARS:
                return _t("th.c.one.title.min"), _t("th.c.one.body.min", m1=a, g1=s1)
            return _t("th.c.one.title", m1=a), _t("th.c.one.body", g1=s1)
        b, s2 = group[1][0].company.name, group[1][1]
        title = _t("th.c.pair.title", m1=a, m2=b)
        if _words(title) > CARD_TITLE_MAX_WORDS or _longest_word(title) > CARD_TITLE_WORD_MAX_CHARS:
            return _t("th.c.pair.title.min"), _t("th.c.pair.body.min", m1=a, g1=s1, m2=b, g2=s2)
        return title, _t("th.c.pair.body", g1=s1, g2=s2)

    lines = [[line_of(g)] for g in groups] + ([[explainer]] if len(groups) < 3 else []) + [[grouping]]
    cards = [card_of(g) for g in groups] + (
        [(_t("th.c.explainer.title"), _t("th.c.explainer.body"))] if len(groups) < 3 else []) + [
        (_t("th.c.grouping.title"), _t("th.c.grouping.body"))]
    # image: the first MAX_TILES members (theme order) whose name a tile can draw whole, "+k more" for
    # every other member — each one still listed in the long captions
    fact_of = {f[0].company.symbol: f for f in facts}
    drawable = [m for m in members if _char_lines(m.company.name, GRID_NAME_WORD_MAX_CHARS) <= GRID_MAX_LINES]
    if len(drawable) < onscreen.MIN_TILES:
        raise NewsTemplateRefused("slot_rejected", f"fewer than {onscreen.MIN_TILES} member names fit a grid tile")
    tiles: List[Dict[str, Any]] = []
    for m in drawable[:onscreen.MAX_TILES]:
        tile: Dict[str, Any] = {"logo": m.company.symbol, "name": m.company.name}
        fact = fact_of.get(m.company.symbol)
        if fact is not None:
            # the share when the line still wraps to GRID_MAX_LINES with it, else the segment alone
            with_share = None if fact[2] is None else _t("th.img.line.share", seg=fact[1], share=fact[2])
            tile["line"] = (with_share if with_share is not None
                            and _char_lines(with_share, GRID_LINE_WORD_MAX_CHARS) <= GRID_MAX_LINES
                            else _t("th.img.line", seg=fact[1]))
        tiles.append(tile)
    sfx = ".part" if part else ""
    fill = {"theme": theme, "n": n, "m": size}
    spec: Dict[str, Any] = {"layout": "grid", "version": onscreen.SPEC_VERSION, "kicker": _t("th.kicker"),
                            "title": theme, "subtitle": _t(f"th.img.sub{sfx}", **fill), "tiles": tiles}
    if n > len(tiles):      # every other LISTED member (the long captions list them); never the gated ones
        spec["more"] = _t("th.img.more", k=n - len(tiles))
    first = drawable[0].company           # the cover's logo is the first tile's (among the image's keys)
    opening = {"kicker": _t("th.kicker"), "logos": [first.symbol], "figure": str(n),
               "headline": _t(f"th.open.head{sfx}", **fill)}
    # "each one's largest reported segment" only when every listed member has its fact in the post; the
    # bare "{theme}: {n} companies." states the theme's size, so only a whole (or "of its") list offers it
    each = ".each" if len(facts) == n else ""
    heads = [_t(f"th.ch.long{sfx}{each}", **fill), _t(f"th.ch.short{sfx}", **fill)] + (
        [_t(f"th.ch.min{sfx}", **fill)] if size is not None else [])
    items = "; ".join(_t("th.item.share", co=f[0].company.name, seg=f[1], share=f[2]) if f[2] is not None
                      else _t("th.item", co=f[0].company.name, seg=f[1]) for f in facts)
    members_id = "th.p.members.part" if part else ("th.p.members" if size == n else "th.p.members.some")
    paras = [_t(members_id, **fill, names="; ".join(m.company.name for m in members)),
             _t("th.p.facts", items=items), grouping]
    shown = "; ".join(t["name"] for t in tiles)
    if size is None:
        alt_body = _t("th.alt.body.some", k=len(tiles), theme=theme)
    elif size > len(tiles):     # "{k} of the {m}": the whole theme's count (≥ the listed members)
        alt_body = _t("th.alt.body", k=len(tiles), n=size, theme=theme)
    else:
        alt_body = _t("th.alt.body.all", n=n, theme=theme)
    with_line = sum(1 for t in tiles if "line" in t)
    if with_line:
        alt_body += " " + (_t("th.alt.segs.each") if with_line == len(tiles) else _t("th.alt.segs", j=with_line))
    yt = ([_t("th.yt.part", **fill), _t("th.yt.part.short", **fill)] if part
          else [_t("th.yt", **fill)] + ([_t("th.yt.short", **fill)] if size is not None else []))
    return _Parts(hook=hook, lines=lines, cards=cards, opening=opening, image_spec=spec,
                  alt_title=_alt_title([theme, _t("th.alt.title.min")]),
                  alt_paragraphs=[alt_body, _t("th.alt.names", names=shown)], source=rules.source_label(rec),
                  as_of=_t("th.asof", tickers_img=date_image(rec.tickers_as_of)),
                  image_headlines=heads, image_paragraphs=paras, video_headlines=heads, video_paragraphs=paras,
                  youtube_titles=yt, persons=[], names={t["logo"]: t["name"] for t in tiles},
                  required_fields=THEME_LIST_FIELDS, required_paragraphs=1)


_BUILDERS: Dict[str, Callable[..., _Parts]] = {
    "ceo_buys": _insider_parts, "insider_buys": _insider_parts,
    "thirteen_f": _thirteen_f_parts, "congress_count": _congress_parts, "company_stakes": _stake_parts,
    "earnings": _earnings_parts, "money_map": _money_map_parts, "theme_explainer": _theme_parts,
}


# ── assembly ──────────────────────────────────────────────────────────────────


def _words(text: str) -> int:
    return len(text.split())


def _pick_script(hook: Sequence[str], lines: Sequence[Sequence[str]]) -> Tuple[str, List[str]]:
    """The narration: per line the first variant inside its word range; then, while the total is
    over NARRATION_WORDS, the line whose next variant saves the most words steps down (earliest line
    on a tie). Deterministic. `script_shape` when a line has no fitting variant or the total stays
    outside the range."""
    groups = [list(hook)] + [list(v) for v in lines]
    ranges = [HOOK_WORDS] + [LINE_WORDS] * NEWS_SCRIPT_LINES
    fitting = []
    for i, (g, (lo, hi)) in enumerate(zip(groups, ranges)):
        f = [v for v in g if lo <= _words(v) <= hi]
        if not f:
            raise NewsTemplateRefused("script_shape", f"{'hook' if i == 0 else f'line {i}'} has no variant of "
                                                      f"{lo}-{hi} words")
        fitting.append(f)
    idx = [0] * len(fitting)

    def total() -> int:
        return sum(_words(f[i]) for f, i in zip(fitting, idx))

    while total() > NARRATION_WORDS[1]:
        best: Optional[Tuple[int, int]] = None
        for gi, f in enumerate(fitting):
            if idx[gi] + 1 < len(f):
                saving = _words(f[idx[gi]]) - _words(f[idx[gi] + 1])
                if saving > 0 and (best is None or saving > best[0]):
                    best = (saving, gi)
        if best is None:
            break
        idx[best[1]] += 1
    n = total()
    if not NARRATION_WORDS[0] <= n <= NARRATION_WORDS[1]:
        raise NewsTemplateRefused("script_shape", f"narration is {n} words (range {NARRATION_WORDS})")
    chosen = [f[i] for f, i in zip(fitting, idx)]
    return chosen[0], chosen[1:]


def _caption_bodies(parts: _Parts, spec: SeriesSpec, run_date: date, store_state: str, allow_x_url: bool
                    ) -> Tuple[Dict[str, str], Dict[str, List[Dict[str, str]]]]:
    """A9: the body of every caption field, or the outlet dropped (`over_budget`)."""
    def budget(f: str) -> int:
        return post_copy.body_budget(f, spec.category, run_date, allow_x_url=allow_x_url,
                                     store_state=store_state, authorship=AUTHORSHIP)

    def fit(f: str, heads: Sequence[str], paras: Sequence[str], limit: int) -> Optional[str]:
        b = budget(f)
        need = parts.required_paragraphs if f in parts.required_fields else 0
        # Each headline's OWN paragraphs: the ones it does not already state, then the outlet's limit
        # (review round 10 — assembled after the headline is picked, so a paragraph never repeats it).
        own = [[p for p in paras if p not in parts.stated_by_headline.get(h, frozenset())][:limit]
               for h in heads]
        # Long → short headline, then one trailing paragraph fewer: `cut` counts the paragraphs
        # dropped from each headline's own list (with no stated paragraphs, every list is the same
        # and this is the plain "most paragraphs first, then the longest headline" order).
        for cut in range(max((len(o) for o in own), default=0) + 1):
            for h, mine in zip(heads, own):
                kept = len(mine) - cut
                if kept < need or kept < 0:
                    continue
                body = "\n\n".join([h, *mine[:kept]])
                if post_copy.measured_length(f, body) <= b:
                    return body
        return None

    plan: Dict[str, Tuple[Sequence[str], Sequence[str], int]] = {
        "x": (parts.image_headlines, (), 0), "bluesky": (parts.image_headlines, (), 0),
        "threads": (parts.image_headlines, (), 0),
        "facebook": (parts.image_headlines, parts.image_paragraphs, 4),
        "linkedin": (parts.image_headlines, parts.image_paragraphs, 4),
        "tiktok": (parts.video_headlines, parts.video_paragraphs, 1),
        "instagram": (parts.video_headlines, parts.video_paragraphs, 4),
        "youtube_description": (parts.video_headlines, parts.video_paragraphs, 4),
        "youtube_title": (parts.youtube_titles, (), 0),
    }
    bodies: Dict[str, str] = {}
    dropped: Dict[str, List[Dict[str, str]]] = {}
    for f in post_copy.CAPTION_FIELDS:
        heads, paras, limit = plan[f]
        body = fit(f, heads, paras, limit)
        platform = "youtube" if f.startswith("youtube") else f
        if body is None:
            dropped.setdefault(platform, []).append(
                {"field": f, "code": "over_budget", "detail": f"no variant fits {budget(f)} characters"})
        else:
            bodies[f] = body
    return bodies, dropped


def _logo_refs(spec_image: Mapping[str, Any], opening: Mapping[str, Any], names: Mapping[str, str]
               ) -> List[Dict[str, str]]:
    """{key, name} of every logo the image and then the opening card reference, in draw order."""
    keys: List[str] = []

    def add(k: Any) -> None:
        if isinstance(k, str) and k not in keys:
            keys.append(k)

    layout = spec_image.get("layout")
    if layout == "rows":
        if isinstance(spec_image.get("header"), dict):
            add(spec_image["header"].get("logo"))
        for sec in spec_image.get("sections") or ():
            for row in sec.get("rows") or ():
                add(row.get("logo"))
    elif layout in ("spotlight", "bars"):
        add(spec_image["header"].get("logo"))
    elif layout == "pair":
        add(spec_image["left"].get("logo"))
        add(spec_image["right"].get("logo"))
    elif layout == "grid":
        for tile in spec_image.get("tiles") or ():
            add(tile.get("logo"))
    for k in opening.get("logos") or ():
        add(k)
    missing = [k for k in keys if k not in names]
    if missing:
        raise NewsTemplateRefused("image_spec_invalid", "a referenced logo has no company name")
    return [{"key": k, "name": names[k]} for k in keys]


def _compose_once(record: Any, spec: SeriesSpec, run_date: date, store_state: str, allow_x_url: bool,
                  suppressed: FrozenSet[str]) -> Dict[str, Any]:
    builder = _BUILDERS[spec.series]
    parts = (builder(record, run_date, suppressed) if builder is _insider_parts else builder(record, run_date))
    hook, lines = _pick_script(parts.hook, parts.lines)
    footer = post_copy.image_footer(run_date, AUTHORSHIP, source=parts.source, as_of=parts.as_of)
    image_spec = dict(parts.image_spec)
    image_spec["footer"] = footer
    logo_refs = _logo_refs(image_spec, parts.opening, parts.names)
    bodies, dropped = _caption_bodies(parts, spec, run_date, store_state, allow_x_url)
    posts: Dict[str, Dict[str, Any]] = {}
    for platform in post_copy.PLATFORMS:
        fields_ = ("youtube_title", "youtube_description") if platform == "youtube" else (platform,)
        if platform in dropped or not all(f in bodies for f in fields_):
            continue
        post = post_copy.compose(platform, bodies, category=spec.category, run_date=run_date,
                                 allow_x_url=allow_x_url, store_state=store_state, authorship=AUTHORSHIP)
        problems = post_copy.check_composed(post, run_date, AUTHORSHIP)
        if problems:
            dropped.setdefault(platform, []).extend(v.as_dict() for v in problems)
        else:
            posts[platform] = post.as_dict()
    alt = [*parts.alt_paragraphs[:3], _t("alt.source", source=parts.source, as_of=parts.as_of)]
    return {
        "hook": hook,
        "video_script": lines,
        "cards": [{"title": t, "body": b} for t, b in parts.cards],
        "opening_card": dict(parts.opening),
        "carousel_slides": [],
        "captions": bodies,
        "posts": posts,
        "dropped_outlets": dropped,
        "disclaimer_card": post_copy.disclaimer_card(run_date, AUTHORSHIP),
        "image_post": {"title": parts.alt_title, "paragraphs": alt},
        "image_footer": footer,
        "image_spec": image_spec,
        "video_layout": VIDEO_LAYOUT,
        "authorship": AUTHORSHIP,
        "content_class": spec.content_class,
        "series": spec.series,
        "template_version": TEMPLATE_VERSION,
        "source_ref": rules.ledger_key(record),
        "run_date": run_date.isoformat(),
        "store_state": store_state,
        "allow_x_url": allow_x_url,
        "persons": list(parts.persons),
        "logo_refs": logo_refs,
    }


#: Violation code → refusal code (`compose` raises the first violation's).
_REFUSAL_OF: Dict[str, str] = {
    "schema": "script_shape", "provenance": "record_invalid", "script_shape": "script_shape",
    "placement": "placement", "congress_name": "congress_name", "banned_word": "slot_rejected",
    "structure": "slot_rejected", "off_table_verb": "slot_rejected",
    "image_spec_invalid": "image_spec_invalid", "opening_card_invalid": "image_spec_invalid",
    "image_post_invalid": "image_spec_invalid", "logo_mismatch": "image_spec_invalid",
    "disclaimer_invalid": "script_shape", "caption_invalid": "script_shape",
    "too_few_outlets": "too_few_outlets",
    # drop 2b: a narrowing word in a Congress Count points at a member (placement); a missing
    # "disclosed", a dollar figure, an off-table Congress verb or a series' banned word is our wording
    "congress_narrowing": "placement", "congress_wording": "slot_rejected", "series_word": "slot_rejected",
}


def compose(record: Any, *, run_date: date, store_state: str, allow_x_url: bool) -> Dict[str, Any]:
    """The accepted-output dict of one record (script_service adds `logos` and the frozen formats).
    Deterministic and pure. Raises `NewsTemplateRefused(code)` when the record cannot be rendered
    within every rule; a `ValueError` / `TypeError` for a caller bug (a datetime, a non-bool
    `allow_x_url`)."""
    if not isinstance(run_date, date) or isinstance(run_date, datetime):
        raise TypeError("run_date must be a date")
    if not isinstance(allow_x_url, bool):
        raise TypeError("allow_x_url must be a bool")
    store_state = post_copy.normalize_store_state(store_state)
    if not isinstance(record, rules.RECORD_TYPES):
        raise NewsTemplateRefused("record_invalid", f"not a news record ({type(record).__name__})")
    spec = SERIES_SPECS.get(getattr(record, "series", None))
    if spec is None:
        raise NewsTemplateRefused("record_invalid", f"series {getattr(record, 'series', None)!r} is not shipped")
    suppressed: FrozenSet[str] = frozenset()
    out = _compose_once(record, spec, run_date, store_state, allow_x_url, suppressed)
    # A person whose name collides with restricted text (a surname that is also a template word)
    # is rendered role-only — the one placement fix that never guesses. Bounded by the row count.
    for _ in range(rules.INSIDER_MAX_ROWS):
        leaking = _leaking_persons(out, record)
        if not leaking or leaking <= suppressed:
            break
        suppressed = suppressed | leaking
        out = _compose_once(record, spec, run_date, store_state, allow_x_url, suppressed)
    violations = validate_package(out, record=record)
    if violations:
        first = violations[0]
        code = _REFUSAL_OF.get(first["code"], "script_shape")
        logger.info("news template REFUSED series=%s source_ref=%s code=%s violations=%s",
                    spec.series, out.get("source_ref"), code, sorted({v["code"] for v in violations}))
        raise NewsTemplateRefused(code, f"{first['field']}: {first['code']} ({first['detail'][:120]})")
    return out


# ── validation (compose's own check; revalidate's last step) ──────────────────

_REQUIRED: Dict[str, type] = {
    "hook": str, "video_script": list, "cards": list, "opening_card": dict, "carousel_slides": list,
    "captions": dict, "posts": dict, "dropped_outlets": dict, "disclaimer_card": str, "image_post": dict,
    "image_footer": str, "image_spec": dict, "video_layout": str, "authorship": str, "content_class": str,
    "series": str, "template_version": str, "source_ref": str, "run_date": str, "store_state": str,
    "allow_x_url": bool, "persons": list, "logo_refs": list,
}
#: A template footer's fixed head and tail (`post_copy.image_footer`); checked by prefix / suffix and a
#: length cap — linear on any stored value, however long.
_FOOTER_HEAD = "Educational only · not investment advice · Source: "
_FOOTER_TAIL = " · Caydex · Not affiliated with anyone named"
_FOOTER_MAX_CHARS = 2 * post_copy.FOOTER_SLOT_MAX_CHARS + len(_FOOTER_HEAD) + len(_FOOTER_TAIL) + 3
_PERSON_RE = re.compile(r"[A-Z][A-Za-z]+(?: [A-Z]\.)? [A-Z][A-Za-z]*(?:-[A-Z][A-Za-z]*)?")
_NOT_DRAWN_KEYS = frozenset({"logo", "logos", "layout", "style", "version", "ratio"})
#: No public string a template writes is longer (the longest caption budget is 1,200 characters);
#: a longer stored value is refused without being scanned (compliance's own scan cap).
MAX_STRING_CHARS = 6000
_COMPUTED = post_copy.COMPUTED_BUDGET_FIELDS
_FOLD_TOKEN_RE = re.compile(r"[a-z0-9]+(?:['-][a-z0-9]+)*")


def _drawn_strings(obj: Any, path: str) -> List[Tuple[str, str]]:
    """Every drawable string of an image_spec / opening_card (never a logo key, layout or style)."""
    out: List[Tuple[str, str]] = []
    if isinstance(obj, dict):
        for k in obj:
            if k in _NOT_DRAWN_KEYS:
                continue
            out += _drawn_strings(obj[k], f"{path}.{k}")
    elif isinstance(obj, list):
        for i, x in enumerate(obj):
            out += _drawn_strings(x, f"{path}[{i}]")
    elif isinstance(obj, str):
        out.append((path, obj))
    return out


def _company_tokens(record: Any, output: Mapping[str, Any]) -> FrozenSet[str]:
    names: List[str] = []
    for ref in output.get("logo_refs") or ():
        if isinstance(ref, dict) and isinstance(ref.get("name"), str):
            names.append(ref["name"])
    if record is not None:
        names += _record_names(record, companies_only=True)
    toks = set()
    for n in names:
        for tok in _FOLD_TOKEN_RE.findall(fold(n)):
            toks.add(tok[:-2] if tok.endswith("'s") else tok)
            toks.update(p for p in tok.split("-") if p)
    return frozenset(toks)


def _record_names(record: Any, *, companies_only: bool = False) -> List[str]:
    """Every name a record carries that may reach a slot (masked before the word scan): company and
    filer names, and — unless `companies_only` (the placement rule's "a token of a company name") —
    segment names and the rendered person names too. Display and spoken forms."""
    out: List[str] = []
    if isinstance(record, rules.InsiderBuysWeek):
        out = [r.company.name for r in record.rows]
        if not companies_only:
            out += [r.person_name for r in record.rows if r.person_name]
    elif isinstance(record, rules.ThirteenFFiling):
        out = [record.filer_name] + [m.company.name for m in record.moves]
    elif isinstance(record, rules.MoneyMap):
        out = [record.company.name] + ([] if companies_only else [s.name for s in record.segments])
    elif isinstance(record, (rules.CongressCount, rules.EarningsReport)):
        out = [record.company.name]
    elif isinstance(record, rules.CompanyStake):
        out = [record.investor.name, record.investee_name] + ([record.investee.name] if record.investee else [])
        if not companies_only:      # the stake's own slot values (§4.5), never the free-text background
            out += [record.source_title] + ([record.local_listing] if record.local_listing else [])
    elif isinstance(record, rules.ThemeExplainer):
        out = [m.company.name for m in record.members]
        # the theme's TITLE is never masked (review round 9): it is Caydex's own wording, not a name, so
        # the full-output word scan reads it wherever it lands (the hook, the image, every caption)
        if not companies_only:
            out += [m.top_segment for m in record.members if m.top_segment]
    return out + [spoken_company(n) for n in out]


def _person_patterns(person: str, company_tokens: FrozenSet[str]) -> List["re.Pattern[str]"]:
    toks = fold(person).split()
    if len(toks) < 2:
        return []
    forms = [" ".join(toks), f"{toks[0]} {toks[-1]}"]
    surname = toks[-1]
    if len(surname) >= 3 and surname not in company_tokens:
        forms.append(surname)
    uniq = list(dict.fromkeys(forms))
    return [re.compile(r"(?<![a-z0-9])" + re.escape(f) + r"(?![a-z0-9])") for f in uniq]


def _restricted_strings(output: Mapping[str, Any]) -> List[Tuple[str, str]]:
    """A7: every place a person may NOT appear — the whole video (hook, narration lines, cards,
    opening card: owner decision 2026-10-09 "Role-only video") and every image / headline string."""
    out: List[Tuple[str, str]] = [("hook", output.get("hook") or "")]
    # every narration line: each narrated word is burned as a caption on a video frame
    script = output.get("video_script") if isinstance(output.get("video_script"), list) else []
    out += [(f"video_script[{i}]", s) for i, s in enumerate(script) if isinstance(s, str)]
    out += _drawn_strings(output.get("opening_card") or {}, "opening_card")
    out += _drawn_strings(output.get("image_spec") or {}, "image_spec")
    ip = output.get("image_post") if isinstance(output.get("image_post"), dict) else {}
    out += _drawn_strings(ip, "image_post")
    # every card, title AND body: each is drawn on a video frame (YouTube Shorts picks its own cover)
    for i, c in enumerate(output.get("cards") or ()):
        if isinstance(c, dict):
            out += [(f"cards[{i}].{k}", c.get(k) if isinstance(c.get(k), str) else "") for k in ("title", "body")]
    captions = output.get("captions") if isinstance(output.get("captions"), dict) else {}
    for f, body in captions.items():
        if not isinstance(body, str):
            continue
        if f in _COMPUTED or f == "youtube_title":
            out.append((f"captions.{f}", body))
        else:
            out.append((f"captions.{f}[0]", body.split("\n", 1)[0]))
    posts = output.get("posts") if isinstance(output.get("posts"), dict) else {}
    for p, post in posts.items():
        if not isinstance(post, dict):
            continue
        cap = post.get("caption") if isinstance(post.get("caption"), str) else ""
        field_ = "youtube_description" if p == "youtube" else p
        body = captions.get(field_) if isinstance(captions.get(field_), str) else ""
        if p in _COMPUTED:
            out.append((f"posts.{p}", cap))
        else:
            out.append((f"posts.{p}[0]", cap.split("\n", 1)[0]))
            # the code-owned suffix: hashtags, CTA, disclaimer
            out.append((f"posts.{p}.suffix", cap[len(body):] if body and cap.startswith(body) else cap))
        if isinstance(post.get("title"), str):
            out.append((f"posts.{p}.title", post["title"]))
    return out


def _placement_hits(output: Mapping[str, Any], record: Any) -> List[Tuple[str, str]]:
    """(field, person) for every person found in a restricted field."""
    persons = [p for p in output.get("persons") or () if isinstance(p, str)]
    if not persons:
        return []
    tokens = _company_tokens(record, output)
    restricted = [(f, fold(s)) for f, s in _restricted_strings(output)
                  if isinstance(s, str) and s and len(s) <= MAX_STRING_CHARS]
    hits = []
    for person in persons:
        pats = _person_patterns(person, tokens)
        for f, s in restricted:
            if any(p.search(s) for p in pats):
                hits.append((f, person))
    return hits


def _leaking_persons(output: Mapping[str, Any], record: Any) -> FrozenSet[str]:
    return frozenset(p for _f, p in _placement_hits(output, record))


def _public_strings(output: Mapping[str, Any]) -> List[Tuple[str, str, bool]]:
    """(field, text, is_body) for every public string: is_body is False only for a composed caption
    (its code-owned suffix carries hashtags and a link)."""
    out: List[Tuple[str, str, bool]] = [("hook", output.get("hook") or "", True)]
    out += [(f"video_script[{i}]", s, True) for i, s in enumerate(output.get("video_script") or ())
            if isinstance(s, str)]
    for i, c in enumerate(output.get("cards") or ()):
        if isinstance(c, dict):
            out += [(f"cards[{i}].{k}", c.get(k), True) for k in ("title", "body") if isinstance(c.get(k), str)]
    out += [(f, s, True) for f, s in _drawn_strings(output.get("opening_card") or {}, "opening_card")]
    out += [(f, s, True) for f, s in _drawn_strings(output.get("image_spec") or {}, "image_spec")
            if f != "image_spec.footer"]
    ip = output.get("image_post") if isinstance(output.get("image_post"), dict) else {}
    out += [(f, s, True) for f, s in _drawn_strings(ip, "image_post")]
    out.append(("image_footer", output.get("image_footer") or "", False))
    out.append(("disclaimer_card", output.get("disclaimer_card") or "", False))
    captions = output.get("captions") if isinstance(output.get("captions"), dict) else {}
    out += [(f"captions.{f}", s, True) for f, s in captions.items() if isinstance(s, str)]
    posts = output.get("posts") if isinstance(output.get("posts"), dict) else {}
    for p, post in posts.items():
        if isinstance(post, dict):
            if isinstance(post.get("caption"), str):
                out.append((f"posts.{p}.caption", post["caption"], False))
            if isinstance(post.get("title"), str):
                out.append((f"posts.{p}.title", post["title"], True))
    return out


def _parse_day(value: Any) -> Optional[date]:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def validate_package(output: Any, *, record: Any = None) -> List[Dict[str, str]]:
    """Every rule a template output must meet, as `[{field, code, detail}]` ([] = ok). Pure, never
    raises. `record` (when known: compose and revalidate pass it) lets the word scan mask the
    record's names and the provenance check compare the ledger key; without it the scan is
    stricter, never looser."""
    out: List[Dict[str, str]] = []

    def v(field_: str, code: str, detail: str = "") -> None:
        out.append({"field": field_, "code": code, "detail": detail[:200]})

    try:
        _validate(output, record, v)
    except Exception as e:  # noqa: BLE001 — a malformed stored row must read as a violation, not a 500
        logger.exception("news template: validate_package failed on a malformed output (%s: %s)",
                         type(e).__name__, e)
        v("package", "schema", f"unreadable ({type(e).__name__})")
    return out


def _validate(output: Any, record: Any, v: Callable[..., None]) -> None:
    if not isinstance(output, Mapping):
        v("package", "schema", "not an object")
        return
    bad = [k for k, t in _REQUIRED.items() if not isinstance(output.get(k), t)
           or (t is not bool and isinstance(output.get(k), bool))]
    if bad:
        for k in bad:
            v(k, "schema", f"missing or not a {_REQUIRED[k].__name__}")
        return
    spec = SERIES_SPECS.get(output["series"])
    run_date = _parse_day(output["run_date"])
    if spec is None or run_date is None:
        v("series" if spec is None else "run_date", "provenance", "unknown series or bad run date")
        return
    # provenance
    if output["content_class"] != spec.content_class:
        v("content_class", "provenance", "not the series' class")
    if output["authorship"] != AUTHORSHIP:
        v("authorship", "provenance", "not template authorship")
    if output["video_layout"] != VIDEO_LAYOUT:
        v("video_layout", "provenance", "not per_line")
    if output["template_version"] != TEMPLATE_VERSION:
        v("template_version", "provenance", "another template version")
    if output["store_state"] not in post_copy.STORE_STATES:
        v("store_state", "provenance", "unknown store state")
    if output["carousel_slides"]:
        v("carousel_slides", "schema", "a template carries no carousel")
    if not output["source_ref"].startswith(f"{rules.LEDGER_PREFIX}{spec.series}:"):
        v("source_ref", "provenance", "not this series' ledger key")
    if record is not None:
        if getattr(record, "series", None) != spec.series:
            v("series", "provenance", "not the record's series")
        elif output["source_ref"] != rules.ledger_key(record):
            v("source_ref", "provenance", "not the record's ledger key")

    _validate_script(output, v)
    persons = _validate_persons(output, v)
    keys = _validate_logos(output, v)
    # opening card, image, alt text, disclaimer, footer
    problem = onscreen.validate_opening_card(output["opening_card"], keys)
    if problem:
        v("opening_card", "opening_card_invalid", problem)
    expected_layouts = ("rows", "spotlight") if spec.series in ("ceo_buys", "insider_buys") else (spec.layout,)
    if output["image_spec"].get("layout") not in expected_layouts:
        v("image_spec.layout", "image_spec_invalid", "not this series' layout")
    problem = onscreen.validate_image_spec(output["image_spec"], keys, footer=output["image_footer"])
    if problem:
        v("image_spec", "image_spec_invalid", problem)
    problem = image_post_problem(output["image_post"])
    if problem:
        v("image_post", "image_post_invalid", problem)
    elif set(output["image_post"]) != {"title", "paragraphs"}:
        v("image_post", "image_post_invalid", "unknown keys")
    if output["disclaimer_card"] != post_copy.disclaimer_card(run_date, AUTHORSHIP):
        v("disclaimer_card", "disclaimer_invalid", "not the template disclaimer card")
    footer = output["image_footer"]
    if (len(footer) > _FOOTER_MAX_CHARS or not footer.startswith(_FOOTER_HEAD)
            or not footer.endswith(_FOOTER_TAIL) or " · " not in footer[len(_FOOTER_HEAD):-len(_FOOTER_TAIL)]):
        v("image_footer", "disclaimer_invalid", "not a template image footer")
    _validate_captions(output, spec, run_date, v)
    _validate_words(output, spec, record, persons, v)
    for f, person in _placement_hits(output, record):
        v(f, "placement", "a person in a restricted field")


def _validate_script(output: Mapping[str, Any], v: Callable[..., None]) -> None:
    hook, lines, cards = output["hook"], output["video_script"], output["cards"]
    lo, hi = HOOK_WORDS
    if not lo <= _words(hook) <= hi:
        v("hook", "script_shape", f"{_words(hook)} words (range {HOOK_WORDS})")
    if len(lines) != NEWS_SCRIPT_LINES or not all(isinstance(s, str) for s in lines):
        v("video_script", "script_shape", f"needs exactly {NEWS_SCRIPT_LINES} lines")
        return
    for i, s in enumerate(lines):
        if not LINE_WORDS[0] <= _words(s) <= LINE_WORDS[1]:
            v(f"video_script[{i}]", "script_shape", f"{_words(s)} words (range {LINE_WORDS})")
    total = _words(hook) + sum(_words(s) for s in lines)
    if not NARRATION_WORDS[0] <= total <= NARRATION_WORDS[1]:
        v("video_script", "script_shape", f"narration is {total} words (range {NARRATION_WORDS})")
    for name, text in [("hook", hook)] + [(f"video_script[{i}]", s) for i, s in enumerate(lines)]:
        if any(len(tok) > AUDIO_WORD_MAX_CHARS for tok in text.split()):
            v(name, "script_shape", f"a word over {AUDIO_WORD_MAX_CHARS} characters")
    if len(cards) != NEWS_SCRIPT_LINES:
        v("cards", "script_shape", f"needs exactly {NEWS_SCRIPT_LINES} cards (one per line)")
    for i, c in enumerate(cards):
        if not isinstance(c, dict) or set(c) != {"title", "body"} or not all(
                isinstance(c.get(k), str) for k in ("title", "body")):
            v(f"cards[{i}]", "script_shape", "a card is {title, body}")
            continue
        if _words(c["title"]) > CARD_TITLE_MAX_WORDS:
            v(f"cards[{i}].title", "script_shape", f"over {CARD_TITLE_MAX_WORDS} words")
        if _words(c["body"]) > CARD_BODY_MAX_WORDS:
            v(f"cards[{i}].body", "script_shape", f"over {CARD_BODY_MAX_WORDS} words")
        for k in ("title", "body"):
            problem = onscreen.drawable_problem(c[k])
            if problem:
                v(f"cards[{i}].{k}", "script_shape", problem)


def _validate_persons(output: Mapping[str, Any], v: Callable[..., None]) -> List[str]:
    persons = []
    for i, p in enumerate(output["persons"]):
        if not isinstance(p, str) or not _PERSON_RE.fullmatch(p):
            v(f"persons[{i}]", "schema", "not a rendered name")
            continue
        if rules.is_congress_name(p):
            v(f"persons[{i}]", "congress_name", "a member of Congress")
            continue
        persons.append(p)
    if output["series"] not in ("ceo_buys", "insider_buys") and output["persons"]:
        v("persons", "placement", "this series names no person")
    return persons


def _validate_logos(output: Mapping[str, Any], v: Callable[..., None]) -> FrozenSet[str]:
    """The logo keys the image and opening card may reference: `logo_refs`, cross-checked against
    `output.logos` once script_service attached them (a logo's name is drawable text: it must be
    the record's company name)."""
    refs: Dict[str, str] = {}
    for i, ref in enumerate(output["logo_refs"]):
        if (not isinstance(ref, dict) or set(ref) != {"key", "name"} or not isinstance(ref.get("key"), str)
                or not isinstance(ref.get("name"), str) or ref["key"] in refs
                or onscreen.drawable_problem(ref["name"]) is not None):
            v(f"logo_refs[{i}]", "logo_mismatch", "a malformed or duplicate logo ref")
            continue
        refs[ref["key"]] = ref["name"]
    if len(refs) > onscreen.MAX_LOGOS:
        v("logo_refs", "logo_mismatch", f"over {onscreen.MAX_LOGOS} logos")
    try:
        expected = _logo_refs(output["image_spec"], output["opening_card"], refs)
    except NewsTemplateRefused:
        expected = None
    if expected is None or expected != [{"key": k, "name": n} for k, n in refs.items()]:
        v("logo_refs", "logo_mismatch", "not the image's then the opening card's logos in draw order")
    if "logos" not in output:
        return frozenset(refs)
    logos = output.get("logos")
    if not isinstance(logos, list):
        v("logos", "logo_mismatch", "not a list")
        return frozenset()
    seen = set()
    for i, entry in enumerate(logos):
        key = entry.get("key") if isinstance(entry, dict) else None
        if key not in refs or key in seen or entry.get("name") != refs[key]:
            v(f"logos[{i}]", "logo_mismatch", "a logo the record does not name, or another name")
        seen.add(key)
    if seen != set(refs):
        v("logos", "logo_mismatch", "not one entry per referenced logo")
    return onscreen.logo_keys(logos) & frozenset(refs)


def _validate_captions(output: Mapping[str, Any], spec: SeriesSpec, run_date: date,
                       v: Callable[..., None]) -> None:
    captions, posts = output["captions"], output["posts"]
    if any(f not in post_copy.CAPTION_FIELDS or not isinstance(b, str) for f, b in captions.items()):
        v("captions", "caption_invalid", "an unknown caption field")
        return
    for f, body in captions.items():
        if not body.strip():
            v(f"captions.{f}", "caption_invalid", "empty")
            continue
        budget = post_copy.body_budget(f, spec.category, run_date, allow_x_url=output["allow_x_url"],
                                       store_state=output["store_state"], authorship=AUTHORSHIP)
        if post_copy.measured_length(f, body) > budget:
            v(f"captions.{f}", "caption_invalid", f"over its {budget}-character budget")
    for p, post in posts.items():
        fields_ = ("youtube_title", "youtube_description") if p == "youtube" else (p,)
        if p not in post_copy.PLATFORMS or not isinstance(post, dict) or not all(f in captions for f in fields_):
            v(f"posts.{p}", "caption_invalid", "an unknown platform or a post without its caption")
            continue
        again = post_copy.compose(p, captions, category=spec.category, run_date=run_date,
                                  allow_x_url=output["allow_x_url"], store_state=output["store_state"],
                                  authorship=AUTHORSHIP)
        if post != again.as_dict():
            v(f"posts.{p}", "caption_invalid", "not its caption composed by post_copy")
            continue
        for problem in post_copy.check_composed(again, run_date, AUTHORSHIP):
            v(f"posts.{p}", "caption_invalid", problem.code)
    if len(posts) < NEWS_MIN_OUTLETS:
        v("posts", "too_few_outlets", f"{len(posts)} outlets (minimum {NEWS_MIN_OUTLETS})")


def _validate_words(output: Mapping[str, Any], spec: SeriesSpec, record: Any, persons: Sequence[str],
                    v: Callable[..., None]) -> None:
    names = [r["name"] for r in output["logo_refs"] if isinstance(r, dict) and isinstance(r.get("name"), str)]
    names += list(persons)
    if record is not None:
        names += _record_names(record)
    names += [spoken_company(n) for n in names]
    mask = _mask_re(names)
    class_c = spec.content_class == "C"
    for f, text, is_body in _public_strings(output):
        if not text:
            v(f, "schema", "an empty string")
            continue
        if len(text) > MAX_STRING_CHARS:
            v(f, "schema", f"{len(text)} characters (max {MAX_STRING_CHARS})")
            continue
        if clean(text) != text:
            v(f, "structure", "not clean text")
        if any(_is_emoji(ch) for ch in text):
            v(f, "structure", "an emoji")
        if "!" in text:
            v(f, "structure", "an exclamation mark")
        if _CASHTAG_RE.search(text):
            v(f, "structure", "a cashtag")
        # a grid tile's share cell ("Data Center · 88%") is the one "%" that stands alone (§4.5)
        if not _percent_ok(text) and not (_TILE_LINE_FIELD_RE.fullmatch(f) and _SHARE_CELL_RE.fullmatch(text)):
            v(f, "structure", "a % not followed by ' of'")
        if is_body and ("#" in text or _LINK_RE.search(text) or post_copy.x_link_tokens(text)):
            v(f, "structure", "a hashtag, handle or link (a bare domain too) in a body")
        if rules.congress_name_hits(text):
            v(f, "congress_name", "a member of Congress")
        masked = _masked(text, mask)
        folded = fold(masked)
        for rx, what in ((copy_rules.BANNED_COPY, "banned copy"), (copy_rules.FORECAST_COPY, "forecast"),
                         (NEWS_BANNED_RE, "news-banned")):
            m = rx.search(folded)
            if m:
                v(f, "banned_word", f"{what}: {m.group(0)[:40]}")
        if class_c:
            m = OFF_TABLE_VERBS_RE.search(folded)
            if m:
                v(f, "off_table_verb", m.group(0)[:40])
        _validate_series_words(spec.series, f, text, folded, v)
    if spec.series == "congress_count":
        _validate_congress_wording(output, v)


def _validate_series_words(series: str, f: str, text: str, folded: str, v: Callable[..., None]) -> None:
    """The 2b series' own word rules on one public string (`folded`: the names masked)."""
    if series == "congress_count":
        m = CONGRESS_NARROWING_RE.search(folded)
        if m:
            v(f, "congress_narrowing", f"a word that narrows the count to a member: {m.group(0)[:30]}")
        m = CONGRESS_OFF_VERBS_RE.search(folded)
        if m:
            v(f, "congress_wording", f"not 'disclosed purchases': {m.group(0)[:30]}")
        if "$" in text:
            v(f, "congress_wording", "a dollar figure (a count never totals the amounts)")
    elif series == "earnings":
        m = EARNINGS_BANNED_RE.search(folded)
        if m:
            v(f, "series_word", f"a verdict beside the figures: {m.group(0)[:30]}")
    elif series == "company_stakes":
        m = STAKES_BANNED_RE.search(folded)
        if m:
            v(f, "series_word", f"not the stake's own verb: {m.group(0)[:30]}")


_CONGRESS_COUNT_RE = re.compile(r"([0-9]{1,3}) members of Congress disclosed\b")


def _validate_congress_wording(output: Mapping[str, Any], v: Callable[..., None]) -> None:
    """A Congress Count says "disclosed" wherever it states the count — the hook, the image and cover
    headline, every caption's first line — and the count it states is ≥ 2 distinct members."""
    captions = output["captions"] if isinstance(output.get("captions"), dict) else {}
    places = [("hook", output.get("hook")),
              ("image_spec.headline", (output.get("image_spec") or {}).get("headline")),
              ("opening_card.headline", (output.get("opening_card") or {}).get("headline"))]
    places += [(f"captions.{k}[0]", b.split("\n", 1)[0]) for k, b in captions.items() if isinstance(b, str)]
    for f, text in places:
        if not isinstance(text, str) or "disclosed" not in text:
            v(f, "congress_wording", "the count is not stated as disclosed")
    m = _CONGRESS_COUNT_RE.match(output.get("hook") or "")
    if m is None or int(m.group(1)) < rules.CONGRESS_MIN_MEMBERS:
        v("hook", "congress_wording", "the hook does not state a count of at least two members")


# ── revalidate (create_posts and the build's own re-check) ────────────────────

#: The fields `revalidate` re-composes and compares (everything compose writes but its inputs).
COMPARED_FIELDS: Tuple[str, ...] = (
    "hook", "video_script", "cards", "opening_card", "carousel_slides", "captions", "posts",
    "dropped_outlets", "disclaimer_card", "image_post", "image_footer", "image_spec", "video_layout",
    "authorship", "content_class", "series", "template_version", "source_ref", "persons", "logo_refs",
)


def _canon(value: Any) -> Any:
    """Numbers as floats (JSONB re-serialises 1.0 ↔ 1), tuples as lists, keys sorted by dumps."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Mapping):
        return {str(k): _canon(x) for k, x in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canon(x) for x in value]
    return repr(value)


def _same(a: Any, b: Any) -> bool:
    return json.dumps(_canon(a), sort_keys=True, ensure_ascii=False) == json.dumps(
        _canon(b), sort_keys=True, ensure_ascii=False)


def revalidate(output: Mapping[str, Any], *, fact_sheet: Mapping[str, Any], run_date: date
               ) -> List[Dict[str, str]]:
    """[] when `output` is exactly what this code composes from the stored fact sheet on `run_date`
    and passes every rule; otherwise the violations. Run before the script is stored and again at
    create_posts. Never raises."""
    out: List[Dict[str, str]] = []

    def v(field_: str, code: str, detail: str = "") -> None:
        out.append({"field": field_, "code": code, "detail": detail[:200]})

    try:
        if not isinstance(output, Mapping):
            v("package", "schema", "not an object")
            return out
        if output.get("template_version") != TEMPLATE_VERSION:
            v("template_version", "template_changed",
              f"composed by {str(output.get('template_version'))[:20]!r}, this code is {TEMPLATE_VERSION}")
            return out
        try:
            record = rules.record_from_fact_sheet(fact_sheet)
        except ValueError as e:
            v("fact_sheet", "facts_invalid", str(e))
            return out
        if not isinstance(run_date, date) or isinstance(run_date, datetime):
            v("run_date", "provenance", "not a date")
            return out
        if output.get("run_date") != run_date.isoformat():
            v("run_date", "provenance", "not the run's date")
        store_state, allow = output.get("store_state"), output.get("allow_x_url")
        if store_state not in post_copy.STORE_STATES or not isinstance(allow, bool):
            v("store_state", "provenance", "unknown store state or allow_x_url")
            return out
        try:
            again = compose(record, run_date=run_date, store_state=store_state, allow_x_url=allow)
        except NewsTemplateRefused as e:
            v("package", "recompose_refused", e.code)
            return out
        for key in COMPARED_FIELDS:
            if not _same(output.get(key), again.get(key)):
                v(key, "field_mismatch", "differs from the template's own composition")
        out.extend(validate_package(output, record=record))
    except Exception as e:  # noqa: BLE001 — a malformed stored row must read as a violation, not a 500
        logger.exception("news template: revalidate failed (%s: %s)", type(e).__name__, e)
        v("package", "schema", f"unreadable ({type(e).__name__})")
    return out


def logo_refs(output: Mapping[str, Any]) -> List[Dict[str, str]]:
    """The `{key, name}` logos script_service fetches for a composed output, in draw order (image
    then opening card), at most `template_onscreen.MAX_LOGOS`. Each becomes one `output.logos`
    entry with exactly this key and name (url None when the logo cannot be had: a wordmark)."""
    refs = output.get("logo_refs") if isinstance(output, Mapping) else None
    return [dict(r) for r in refs or () if isinstance(r, dict)][:onscreen.MAX_LOGOS]


__all__ = [
    "TEMPLATE_VERSION", "AUTHORSHIP", "NEWS_SCRIPT_LINES", "HOOK_WORDS", "LINE_WORDS", "NARRATION_WORDS",
    "NEWS_MIN_OUTLETS", "CARD_TITLE_MAX_WORDS", "CARD_BODY_MAX_WORDS", "VIDEO_LAYOUT", "SeriesSpec",
    "SERIES_SPECS", "REFUSAL_CODES", "NewsTemplateRefused", "LEXICON", "NEWS_BANNED_RE", "SLOT_BANNED_RE",
    "OFF_TABLE_VERBS_RE", "KEEP_CAPS", "COMPARED_FIELDS", "compose", "revalidate", "validate_package",
    "logo_refs", "F13_ROWS_PER_KIND", "money_image", "money_words", "money_cents", "SUB_CENT", "shares_image", "shares_words", "pct",
    "date_image", "date_caption", "date_spoken", "month_image", "month_words", "window_image",
    "window_caption", "window_upper", "spoken_company", "possessive",
    # drop 2b
    "CONGRESS_NARROWING_RE", "CONGRESS_OFF_VERBS_RE", "EARNINGS_BANNED_RE", "STAKES_BANNED_RE", "STAKE_BASES",
    "THEME_LIST_FIELDS", "CONGRESS_SETTLE_DAYS", "CONGRESS_MAX_AGE_DAYS", "STAKE_MAX_VERIFIED_AGE_DAYS",
    "STAKE_MAX_AS_OF_AGE_DAYS", "STAKE_NAME_MAX_CHARS", "STAKE_SOURCE_MAX_CHARS", "STAKE_BACKGROUND_MAX_CHARS",
    "NAME_WORD_MAX_CHARS", "CARD_TITLE_WORD_MAX_CHARS", "PAIR_NAME_WORD_MAX_CHARS", "GRID_NAME_WORD_MAX_CHARS",
    "GRID_LINE_WORD_MAX_CHARS",
    "GRID_MAX_LINES", "THEME_TITLE_LINE_CHARS", "THEME_TITLE_MAX_LINES", "EARNINGS_MAX_AGE_DAYS",
    "EARNINGS_MAX_REPORT_LAG_DAYS", "EARNINGS_MAX_GAP_RATIO", "THEME_MAX_AGE_DAYS", "THEME_FACT_MAX_YEARS",
    "THEME_SEGMENT_MAX_CHARS", "THEME_NARRATED_MAX", "THEME_MIN_FACTS", "month_name", "eps_image", "eps_words",
    # review round 9
    "revenue_pair", "REVENUE_MAX_PLACES", "REVENUE_GAP_TOLERANCE", "THEME_TITLE_BANNED_RE", "THEME_TITLE_BANNED_ROWS",
]
