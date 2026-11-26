"""
Company Weekly (Drop 2) — the code-owned NEWS TEMPLATES (contract D7, owner decisions 2026-10-09).

A news day's post is never written by a model. `compose(record, …)` turns ONE validated record of
`company_news_rules` (an `InsiderBuysWeek`, a `ThirteenFFiling`, a `MoneyMap` — drop 2a; a
`CongressCount`, a `CompanyStake`, an `EarningsReport`, a `ThemeExplainer` — drop 2b, composed here
but run only once `selection.SHIPPED_SERIES` and the per-series switch MARKETING_NEWS_SERIES list
them, and drawn only once `template_onscreen.SHIPPED_LAYOUTS` holds `pair` / `grid`; an `InsiderSpotlight`, a
`ThirteenFSpotlight` — news-v22, the spotlight lane's series; since news-v23 both 13F series draw the ONE 13F list,
two to eight holdings on the `rows` picture, never a single-holding post) into the
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
  holding is "New holding", "Increased to / Reduced to <N> shares", "Closed out" (news-v23 — never "no
  longer reported", never "sold" / "exited" / "dumped"); a Money Map
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
  with restricted text is rendered role-only in the captions too. Inside the members-elect window
  (news-v19, owner decision 4: an even year's Election Day through January 3, judged over every day a
  run's posts can go out — the run date and the two after it, `company_news_rules.members_elect_for_run`)
  no one is named at all — every row role-only, `persons` empty, and no record person in any
  public string (`validate_package`).
* **Members of Congress** are never named: a row whose rendered name is a member is dropped, and
  every output string is scanned against the block-list. A Congress Count (2b) is a count of ≥ 2
  distinct members who "disclosed purchases" of one company's stock, dated by the disclosure month:
  never "bought" / "sold" / "purchased", no dollar figure, and no word that narrows the count to a
  member (chamber, party, state, district, committee, title — `CONGRESS_NARROWING_RE`, the company
  name masked).
* **2b series name companies only** (`persons` is always empty): a stake says its own verb and
  basis ("invested", "committed up to", a carrying or fair value) with its as-of date, never
  "bet" / "worth", and is refused when its drawn source title, local listing or UNLISTED investee name
  names a person (Batch 1, `_stake_person_problem`; the record's own companies masked first); a
  DESCRIPTIVE investee name ("Louisiana data center venture") takes "the" in running text, never on a
  drawn label (`company_news_rules.takes_name_article`); the catalogue's background stays out of the
  captions unless STAKE_BACKGROUND_IN_CAPTIONS (off); earnings state the reported EPS beside the
  analyst estimate on the analysts'
  basis, its sign a word or a "-" — never "earned" / "lost" / "earnings of" (the calendar's figure is
  not the official accounting result) and never "expected" / "beat" / "miss" / "surprise"
  (`EARNINGS_BANNED_RE`) — and no revenue figure at all (news-v21, `EARNINGS_REVENUE_IN_POSTS`: the feed
  never says which basis a revenue ESTIMATE is on), though a record WITHOUT a plausible revenue pair is
  still refused (news-v19: the pair is the quarter check, never printed); a theme lists every member in the long
  captions and narrates at most THEME_NARRATED_MAX
  segment facts, says "{n} of its {m} companies" wherever it gives the count when the record's
  `theme_size` is larger than its members (a completeness claim only when the two are equal), and
  its title — Caydex's own wording — passes the news ban and `THEME_TITLE_BANNED_RE`, unmasked.
* **Counts and articles** — a Form 4 roundup's row count is a lower bound ("At least 3 CEOs …",
  "At least one more …"), never the week's total (the record holds at most five rows, after the
  adapter's gates), and a count of distinct PEOPLE: a record whose rows name one person twice (one
  person, two issuers) is refused `record_invalid`; a sentence whose subject names the company never
  names it again ("GameStop's CEO disclosed buying … of the company's stock"); no "a" /
  "an" stands before a company name or a figure (its sound would decide it), only before a role
  word whose sound is fixed ("A CEO", "A director of Oracle").
* **Covers** — the video's opening card carries a chip under its logo, at most OPENING_CHIP_MAX_CHARS
  long: a Form 4, Congress or listed-filer 13F cover the ticker; a stake its basis and as-of date;
  news-v19 (Batch 1) dated the figure on the other covers — earnings "Quarter ended …", Money Map "Fiscal {fy}"
  (news-v23: both left — the earnings cover is its table, the Money Map cover carries the ticker and its short
  footer the fiscal year); a 13F filer with no symbol carries none.
* **Banned words (A8)** — `copy_rules.BANNED_COPY` + `FORECAST_COPY` + `NEWS_BANNED_RE` on every
  output string (company, filer, segment and person names masked: they are names, not our words;
  the slots pass their own, smaller list), plus the structural rules: no emoji, no cashtag, no "#"
  in a body, no "!", every "%" followed by " of", `clean(s) == s`. The ONE exemption (news-v22) is positional:
  the declared first-line prefix (`FIRST_LINE_PREFIXES`, "🚨 BREAKING:" …) and its space at position 0 of a
  caption body or composed caption (never the YouTube title) — `_validate_prefix` checks the prefix itself.
* **Formats (A9)** — X / Bluesky / Threads carry the headline only; Facebook / LinkedIn the
  headline + paragraphs; TikTok the video headline + its first paragraph; Instagram and the
  YouTube description the video headline + up to four paragraphs — counted after the paragraphs the
  picked headline already states are left out (no paragraph repeats its headline's figure pair).
  Variants are tried long → short, then trailing paragraphs are dropped, then the outlet is
  (`over_budget`). Fewer than
  `NEWS_MIN_OUTLETS` survivors → `too_few_outlets`.
* **Extra video posts** (Batch 3, 2026-10-20) — Facebook / LinkedIn / Threads may also post the day's
  video, captioned with the platform's own accepted caption under the narrated-video disclaimer
  (`post_copy.extra_video_caption`). `extra_video_problems` re-checks such a caption with this
  module's own rules (the word scan, placement, the members-elect window) at write time and again at
  create_posts; `compose`, `revalidate`, `COMPARED_FIELDS` and TEMPLATE_VERSION are untouched by it.
  B3-LIVE-1: a Form 4 ROUNDUP's Facebook / LinkedIn / Threads caption speaks of the picture's rows ("The
  purchases: …", "The largest of these: …" — format-neutral since news-v20, but the rows only the picture
  draws) while the video is about the lead row, so it rides the video only when the video draws the picture
  (its data card) — `picture_caption` otherwise (`_Parts.picture_bound`).

Pure: stdlib + the pure marketing modules (`company_news_rules`, `post_copy`, `template_onscreen`,
`compliance`) + `trillion_club.copy_rules` + `schemas.marketing`. FMP-free (`PURE_MODULES`).
"""

from __future__ import annotations

import copy
import json
import logging
import math
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

from app.schemas.marketing import AUDIO_WORD_MAX_CHARS, image_post_problem
from app.services._earnings_common import eps_digit_shift_suspect
from app.services.marketing import company_news_rules as rules
from app.services.marketing import post_copy
from app.services.marketing import template_onscreen as onscreen
from app.services.marketing import text_metrics
from app.services.marketing.compliance import _is_emoji, clean, fold, given_names, scan_text
from app.services.trillion_club import copy_rules
# the SEC's federal business-day calendar (pure: stdlib + market_hours, no FMP — selection imports the module)
from app.services.trillion_club.rules import is_federal_business_day
# the exchange's holiday TABLE (stdlib-only module) — the earnings freshness counts trading days (C0, 2026-11-16);
# never `market_hours.is_trading_day`, whose process-local observed closures would let compose and re-check disagree
from app.utils.market_hours import US_MARKET_HOLIDAYS

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
#: news-v12 (post-commit copy check, 2026-10-10): a stake's cover carries its basis and as-of date in the
#: opening chip (`st.open.chip`) and a headline that never reads as a
#: value today ("… committed up to this amount to …", "… stake in … as of that date"); a Money Map says
#: "business" / "product or business line" / "Revenue mix" / "combined sales", never "segment" (FMP's
#: product breakdown is not always the company's reportable segments); a Form 4 row whose earliest trade is
#: more than INSIDER_LATE_TRADE_DAYS before its last filing states its trade dates in place of the
#: two-business-day explainer (`ins.l3.late.*`, `ins.c3.late.*`, `ins.p.trade.*` + `ins.p.form4.late`), and a
#: roundup with such a row drops the rule from its paragraphs; a roundup's indirect-holding paragraph is
#: plural (`ins.p.flag.indirect.*.many`) and its image note says the SHARES are held; a theme's one-fact
#: line says the fact's fiscal year, never "its latest reported year"; the Congress cards keep the
#: narration's hedges ("Can include …", "Due within 45 days …; some are filed later").
#: news-v13 (review round 13, 2026-10-10): the stake cover's chip says "as of" ("Committed up to · as of Nov
#: 18, 2025"): the date is the source's or the period's, never the deal's, and a bare date after "Committed
#: up to" read as a deadline; a theme says "largest revenue line" / "product or business line", never
#: "segment" — its facts come from the same product breakdown as Money Map, which is not always the
#: company's reportable segments (`th.p.facts` "… from each company's revenue breakdown", `th.explainer`
#: "A revenue line is …"); a Form 4 roundup whose lead row is timely but another row is late narrates and
#: draws the Form 4 without the two-business-day rule (`ins.l3.form4`, `ins.c3.form4.body`), the same
#: choice its video paragraph makes — the "At least {m} more …" line right after it covers the late row.
#: news-v14 (review round 14, 2026-10-10): the 13F explainer states the deadline as a deadline — "is due
#: about 45 days after the quarter's end" (`f13.l4.explainer`, `.short`, `f13.c4.explainer.body`,
#: `f13.p.explainer`), never "filed up to 45 days later": the due date rolls past weekends and federal
#: holidays (47 days for 2026-Q3), the season admits late filers and an amendment can come months later,
#: so the same post could show a filing date past the stated bound; a theme's source label is "company
#: revenue breakdowns; grouping by Caydex" (`company_news_rules.SOURCE_LABELS`, burned into the image footer
#: and the alt text's source line), never "segment reporting"; a Form 4 row is late when MORE than
#: INSIDER_LATE_BUSINESS_DAYS federal business days follow its earliest trade up to its last filing
#: (`_late_trades`), not 4 calendar days — a Monday trade filed on Thursday is late, a Friday trade filed on
#: Tuesday is not, and a federal holiday (Veterans Day, Columbus Day) is not a business day.
#: news-v15 (review round 15, 2026-10-10): the Congress narration states 45 days as a deadline ("Reports are
#: due within 45 days of a trade; some come later, …", `cg.l4`), never a bound — the count includes every
#: in-month disclosure whatever its trade date, so a late report is counted; a 13F caption paragraph and an
#: alt-text section say how many of their kind they leave out ("…; and 5 more", `f13.p.rest`; the alt joins
#: the drawn names with `list.*` / `list.more.*`, as the image draws "+5 more"); the Form 4 roundup's alt
#: text lists each buyer's TOTAL (`ins.alt.rows`: a row adds up all of one person's purchase lines); and a
#: Form 4 row with ONE filing date but several purchases is worded count-neutrally (`_filing_count`
#: "neutral": "The filer is …", "on Form 4, filed …", "The purchases are reported with …") — the record
#: keeps distinct filing DAYS, not documents, so two Form 4s filed the same day would read as "a Form 4";
#: the amounts line says "each filing's share count" (`ins.l4.amounts`), as the caption does.
#: news-v16 (review round 16, 2026-10-10): a theme uses a member's revenue-line fact only while it can still
#: be that company's latest fiscal year (from THEME_FACT_LATE_MONTH, never fiscal run_year - 2) and only
#: within THEME_FACT_SPREAD_YEARS of the newest fact beside it — a fiscal 2024 share no longer sits, yearless,
#: beside fiscal 2026 ones on a tile; a narration pair joins two facts of ONE fiscal year and says it
#: (`th.pair.*` "… in fiscal {fy} …"), as the one-fact line does; the caption list states each fact's year
#: (`th.item.share` "… 62% of fiscal 2025 revenue", `th.item`); and no stake string — above all the
#: catalogue background — values a company by a verb or ranks one (`STAKE_CLAIM_RE`: "which values X at $90
#: billion", "the largest AI startup in Europe", "the fastest-growing cloud company").
#: news-v17 (review round 17, 2026-10-10): a stake's carrying or fair value is the SOURCE's figure — its
#: headlines name no filer ("{inv_s} {ee} stake had a carrying value of …" / "… was carried at …", "… had a
#: fair value of …", each with its ".of" twin; `st.h.*`), never "{inv} reported its … stake" / "{inv} carried
#: its … stake": a catalogue row's source can be another filer's document (the Berkshire Hathaway Energy 10-Q
#: rows sit under the investor Berkshire Hathaway, which does not itemize them), and `st.l2.carrying_value`
#: says "the source says", never "the investor's accounts"; the Form 4 spotlight's alt text states its share
#: figure as the TOTAL ("in 2 purchases, 3M shares in total", `ins.alt.spot.many`), never "2 purchases of 3M
#: shares" (read as 3M each) — one purchase keeps "of" (`ins.alt.spot.one`).
#: news-v19 (Batch 1, 2026-10-15; later Batch-1 template edits keep this version): an earnings record without a
#: revenue pair is refused (`implausible_figures`) — the pair is the only sign the calendar row is the quarter
#: the estimate is for, and the adapter now refuses such a report whole (`revenue_implausible`) and sets the
#: quarter's end from the statements (`company_news_rules.earnings_period`), which the "quarter that ended …"
#: wording already reads; and the members-elect window (owner decision 4: an even year's Election Day through
#: January 3, `company_news_rules.members_elect_window`, the RUN date) names no person in a Form 4 post — every
#: row role-only — with `validate_package` refusing a name there in `persons` or any public string. These two
#: changes (plan items 1 and 6) add or change no lexicon sentence; a later Batch-1 wording edit re-pins its own.
#: Money Map (plan item 2, same version, lexicon re-pinned): the breakdown is neutral — "How fiscal {fy} revenue
#: broke down: …" (`mm.p.segments`, `mm.alt.segments` "Bars show how revenue broke down: …"), "by region" only
#: when EVERY named part is on `company_news_rules.REGION_NAMES` (`.region` twins), never "by product or
#: business line" (McDonald's parts are markets, Costco's countries) — and narration L2 is "{s} made up {sh} of
#: {basis}", never "Its {s} business"; a `REGION_ARTICLE` name reads "the United States" in running text
#: (`mm.region.the`, ".cap" opening a sentence), never on a bar, card or alt-text label; the figures are the
#: "annual results" (`mm.img.subtitle`) that "come from the company's annual financial statements"
#: (`mm.p.source`), never "as reported" — FMP fills gross profit as revenue minus cost of revenue for an issuer
#: with no gross subtotal (Amazon) — and a map showing a gross line adds "Gross profit is revenue minus the
#: cost of sales." (`mm.p.gross`); the eliminations sentence says "parts of the company", not "business lines".
#: (McDonald's parts above: since the review of 2026-10-16 its retired "High-Growth Markets" label is shown as
#: its International Operated Markets — `company_news_rules.SEGMENT_SYMBOL_OVERRIDES`.)
#: Stakes (plan item 4, same version, no lexicon change): a record whose source title, local listing or UNLISTED
#: investee name names a person ("Jensen Huang keynote", "Warren Buffett's 2025 shareholder letter") is refused
#: `slot_rejected` — those texts are drawn on every stake post, and a person is named only in body text.
#: Names and wording (plan item 5, same version, lexicon re-pinned): a single-row Form 4 headline, its YouTube
#: title and its cover never name the company twice ("GameStop's CEO disclosed buying $74.4 million of the
#: company's stock …", cover "GameStop's CEO disclosed buying stock" — `ins.open.head.min` and the cover's
#: length fallback are gone with the second name); "Some of the shares" in the narration and on the card, as the
#: caption says; the lower bound's "one" is a word ("At least one more …"); a DESCRIPTIVE investee name takes
#: "the" in running text (`st.ee.the`, the `{ee_np}` / `{ee_np_cap}` slots, `company_news_rules.
#: takes_name_article`); the catalogue background leaves the captions (STAKE_BACKGROUND_IN_CAPTIONS, off); and
#: the covers' chips date their figures — earnings "Quarter ended {d}" for a verified quarter, else "Reported
#: {d}"; Money Map "Fiscal {fy}"; a 13F filer with no symbol "Quarter ended {d}" — each at most
#: OPENING_CHIP_MAX_CHARS, the widest laid out by the worker's own code (tests/test_marketing_news_layouts.py).
#: The review of 2026-10-16 (same version — Batch 1 is undeployed; lexicon re-pinned): the Form 4 cover and spotlight
#: headline is number-neutral, "{subj} disclosed buying stock" (INS-1: "shares" above "1 purchase · 1 share"); an
#: earnings record whose EPS and estimate PRINT as one figure ("$0.10 vs $0.10") is refused `implausible_figures`
#: (ER-1; the adapter's `clear_surprise` judges the printed figures too); and the members-elect window is judged
#: over every day a run's posts can go out — the run date and the two after it
#: (`company_news_rules.members_elect_for_run`, ME-1), in `_insider_parts` and `validate_package` alike.
#: Money Map, stakes and names (the same review, same version, lexicon re-pinned): the rest bar and its caption
#: item say "Everything else" (F6: "Other or not broken out" was false for lines the company DOES break out that
#: the post cannot name, and a plain "Other" reads as a reported line); "by region" also needs the rest to be at
#: most MONEY_MAP_REGION_MAX_OTHER_SHARE of revenue (MM-6); a gross LOSS is explained by `mm.p.gross.loss` (MM-5);
#: more region names take "the" in running text ("The U.S. made up …", "the rest of the world" — MM-3, data in
#: `company_news_rules.REGION_ARTICLE`); a stake text naming a person on the stake-source surname list is
#: refused (ST-1, `company_news_rules.stake_person_surname_hits`); a theme's revenue line is "a product, business or
#: region" with a region fact read "the United States" (TH-1, `th.explainer`, `th.region.the`, `th.ch.long*`); a 13F
#: record that states no more / fewer count says the post does not show those changes — since the re-review of
#: 2026-10-19 (F13-1-R) in every headline (`f13.ch.*.withheld`; the paragraph `f13.p.withheld` is gone);
#: and the Form 4 and Money Map templates refuse a company-name word the cover cannot draw, as the others did.
#: news-v20 (the round-3 review of 2026-10-20 — the carried open question on the Form 4 roundup's wording, lexicon
#: re-pinned): a roundup's caption parts are FORMAT-NEUTRAL — "The purchases: {rows}." / "More purchases: {rows}."
#: (`ins.p.rows`, `ins.p.rows2`) and "… The largest of these: {co}, {amt}." (`ins.ch.many.long`), never "The
#: purchases shown", "Also shown" or "The largest here": they ride TEXT posts too (X is text only; Facebook, LinkedIn,
#: Threads and Bluesky post text when no picture goes out), where nothing is shown. Every sentence is true with or
#: without the picture: the two row paragraphs together list every row of the record (Facebook / LinkedIn drop the
#: flag and Form 4 paragraphs before the second one — pinned), and "of these" keeps the superlative to the rows the
#: count is about (a larger purchase an adapter gate dropped is never claimed). The B3-LIVE-1 rule is unchanged: the
#: caption still lists rows the video never draws, so a roundup's extra video post needs its data card.
#: news-v21 (main-session decision of 2026-10-20, owner-delegated; no lexicon change): an earnings post prints NO
#: revenue comparison — no narration line, card, picture row, alt-text sentence, headline or caption paragraph
#: (`EARNINGS_REVENUE_IN_POSTS`, off): the feed never says which basis its revenue ESTIMATE is on (GE Aerospace's
#: consensus is its adjusted revenue, its calendar actual the GAAP figure — "revenue of $12.2 billion vs $10.4
#: billion" in Q3 2025 held on neither basis). Its post is the no-revenue one the unwritable pair always took —
#: narration L2 / card 2 the estimate definition, L4 / card 4 the quarterly explainer, the picture's EPS row only,
#: the headlines without revenue — and its data card replaces the date card (`template_onscreen.DATA_CARD_SLOTS`).
#: The record must still carry a plausible revenue pair (`implausible_figures`): the quarter check, never printed.
#: news-v22 (owner decisions of 2026-11-15, the second daily post; lexicon re-pinned, every 2a/2b entry and output
#: unchanged byte for byte): the spotlight lane's two single-company series, both on the `feature` picture —
#: `insider_spotlight` (`isp.*`: ONE issuer's largest qualifying insider and up to four more of the same issuer, a
#: lower bound — "at least {m} other insiders"; the ticker in the caption's first line, "(CTSO)"; the filing's own
#: cost per share, "as filed", in the CAPTIONS and on the PICTURE's facts only — never the word "price", never in the
#: narration, a burned caption, a video card, the opening card, the alt text or the YouTube title (owner decision
#: "Captions + picture"); "How {co} makes money" only when the record carries the revenue line the adapter vouched
#: for; role-only through the members-elect window, as every Form 4 post) and `thirteen_f_spotlight` (`f13s.*`: ONE
#: filer's ONE holding — share counts only, no %, no "sold", no dollar figure but a NEW holding's filed value; the
#: picture and the cover are the HOLDING's; the DIRECTION first — owner feedback 2026-11-19, re-pinned without a
#: version bump as news-v22 had not shipped: "Fabrikam Capital reduced its Northwind holding (NWT) to 41.9 million
#: shares, from 60 million the quarter before, per its latest 13F.", the picture's and the cover's big figure the
#: signed change, "−18.1M shares"; and, the same day, a holding no longer listed says so of the FILING — "{filer}'s
#: latest 13F lists no {co} shares", "−4.3M shares" over "all of them: none on its latest 13F" — a new one "+5.6M
#: shares" over "new holding: none listed the quarter before", and two to five holdings make a LIST on the `rows`
#: picture, `f13s.list.*`: the counts the filing's only while its rows are every change it reports; a list row no
#: longer listed is "None in this 13F"). Both close
#: their long captions with a fixed question (`isp.p.question`, `f13s.p.question`), the last paragraph, kept where
#: an outlet's paragraph limit allows. The same version (owner decision 2026-11-15 "Loosen caption hype words", scope
#: "Spotlights + Form 4 + earnings"): a closed, code-owned first-line PREFIX (`FIRST_LINE_PREFIXES`: "🚨 BREAKING:",
#: "🚨 Insider purchase alert:", "📊 13F filing:") may open a caption's first line — per series
#: (`PREFIX_IDS_BY_SERIES`: the insider spotlight BREAKING while fresh, else the alert; the weekly Form 4 roundups the
#: alert only, never BREAKING; earnings BREAKING while fresh, else none; the 13F spotlight its filing, with after-prefix
#: headline twins `f13s.hp.*`), content first (a tight outlet drops the prefix before a longer headline), never on the
#: YouTube title, the picture, the alt text or the video — behind MARKETING_FIRST_LINE_PREFIX (off: every caption byte
#: for byte the unprefixed composition), frozen per build into `output.first_line_prefix_on`.
#: news-v23 (the restyle, owner-approved 2026-11-19 — "good. now, build all. i need exactly these styles, colors,
#: words" and, of the footer, "if we can't remove, then say it shortly"; lexicon re-pinned: four `foot.*` entries
#: added; every other entry, caption, card, narration line, opening-card string and picture string but the footer
#: unchanged byte for byte): the picture's footer is ONE short line per family (`post_copy.image_footer`,
#: RESTYLE_SPEC §1) —
#: "Not investment advice · Source: SEC Form 4 · filed Nov 9–15, 2026", "… · Source: SEC Form 13F · filed Nov 13,
#: 2026" (an amended filing: "amended <date>"), "… · Source: company financial statements · fiscal 2025",
#: "… · Source: company results and analyst consensus", "… · Source: congressional disclosures · November 2026",
#: "… · Source: company filings" (stakes, themes). "Educational only", the brand (now the picture's top row) and
#: "Not affiliated with anyone named" leave the PICTURE; every caption keeps its full disclaimer, the template note
#: and NON_AFFILIATION, and the alt text keeps its "Source: {source}. {as_of}." line (`alt.source`), unchanged.
#: news-v23, the 13F content (RESTYLE_SPEC §2 items 1-2, owner 2026-11-19 — "i need more companies, similar with
#: Berkshire hathaway latest 13F. and remove what users don't care such as quarter before, quarter ended" / "the range
#: from 2 to 6 are okay"; the same release, so the same version; lexicon re-pinned): both 13F series draw ONE list
#: (`_f13_list_parts`, f13_list_berkshire.jpg / f13_list_pershing.jpg) — kicker "13F FILING", "{filer}'s latest 13F",
#: the filer's plate when it is listed, "Change in shares", 2-6 rows ranked by dollar size (each: logo, ticker chip,
#: name, "Increased to 40M shares" / "Reduced to 20M shares" / "New holding" / "Closed out", the signed change "+5.6M"
#: / "−14.6M") and "+N more changes in the caption"; no subtitle, no fact rows, no quarter on the picture. The cover
#: draws the list's first four rows (cover_13f_berkshire.jpg, the opening card's `list`). "Closed out" replaces "no
#: longer reported" / "lists no … shares" / "none listed" everywhere — after the adapter's BUY-OUT CHECK (owner:
#: "Closed out + buy-out check"); NO single-holding 13F post (`f13s.h.*`, `f13s.img.*`, `f13s.fact.*` and the
#: roundup's per-kind sections, `f13.hook.*`, `f13.cnt.*`, `f13.ch.*` … are gone); captions list SHARE COUNTS only
#: (a new holding's filed value is no longer written), and every caption that rides the picture (or the cover) lists
#: the holdings its "+N more" leaves out, else that outlet is dropped (`over_budget`).
#: news-v23, the Form 4 lists (owner decisions 2026-11-20 — "for insider trade, i think we discussed that there is no
#: single company already, each post or each image/video will have more than 2,3, ideally are 3 - 5 companies at
#: once."; "i pick option 1: 3-5, but if it's a quite week - if we don't have enough data, then use different topic
#: like posting the theme"; the video cover "All rows, compact (Recommended)"; per-post art "Drop it from insider posts
#: (Recommended)"; news-v23 is unshipped, so the same version; lexicon re-pinned): EVERY Form 4 post shows 3 to 5
#: companies (`rules.INSIDER_MIN_ROWS`, INS_PICTURE_ROWS), the largest first — the weekly roundups (form4_roundup5.jpg)
#: and the spotlight's insider LIST (`_insider_spotlight_parts`, insider_multi3.jpg / insider_multi5.jpg: "INSIDERS AT
#: {k} COMPANIES" / "DISCLOSED BUYING STOCK", "Total purchases", roles only, the GREEN total and the grey "… a share,
#: as filed" detail line); a record of more draws five and lists the rest in every caption riding the picture or the
#: cover ("+N more companies in the caption"). The single-company Form 4 picture (`spotlight` layout) and the
#: one-issuer `feature` post (`ins.open.head`, `ins.img.spot.*`, `ins.img.flag.*`, `ins.alt.spot.*`, `isp.fact.*`,
#: `isp.h.cluster.*`, `isp.p.cluster`, `isp.p.revenue` …) are gone; the covers are the compact all-rows lists (no
#: detail line — the cost per share never reaches the video).
TEMPLATE_VERSION = "news-v23"
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
#: The video cover's chip (`opening_card.chip`, drawn under the logo) is at most this long:
#: `template_onscreen.OPENING_CHIP_MAX_CHARS` refuses a longer one. The longest a template writes is a
#: stake's "Committed up to · as of Nov 18, 2025" (36); news-v19's period chips are at most 26
#: ("Quarter ended Sep 30, 2026"). tests/test_marketing_news_layouts.py lays the widest of each out with
#: the worker's own code.
OPENING_CHIP_MAX_CHARS = onscreen.OPENING_CHIP_MAX_CHARS
#: Ratios in a `bars` image are rounded to this many places (JSONB re-serialises numbers).
RATIO_PLACES = 4

#: A record older than this (days from its newest date to the run date) is a stale source.
INSIDER_MAX_AGE_DAYS = 7
#: Owner decisions 2026-11-20 ("3 - 5 companies at once"; the video cover "All rows, compact (Recommended)"): EVERY
#: Form 4 post — the weekly roundups and the spotlight's insider list — shows at least `rules.INSIDER_MIN_ROWS`
#: companies (a record of fewer is refused `too_few_rows`, whatever built it) and, since the final review of 2026-11-26,
#: at most `rules.INSIDER_MAX_ROWS` (5): its PICTURE and its video COVER draw EVERY row (the cover compact:
#: insider_multi5_cover_alt_all5.jpg — logo, name + chip, roles, the GREEN total, no detail line). The "+N more
#: companies in the caption" branch below is unreachable while the cap equals INS_PICTURE_ROWS; if the cap is ever
#: raised, every caption riding the picture or cover must list the rest
#: lists (`_Parts.field_required`, the 13F list's promise rules).
INS_PICTURE_ROWS = rules.INSIDER_PICTURE_ROWS
INS_COVER_ROWS = min(INS_PICTURE_ROWS, onscreen.MAX_OPENING_ROWS)
#: The weekly ROUNDUP's picture carries its "held indirectly" note panel up to this many rows; a five-row roundup
#: (form4_roundup5.jpg: the approved chip-above row, its headline stepped down to three lines) has no room for it
#: (the designers' notes, 2026-11-20: "With the weekly roundup's approved chip-above row it does not fit at all") —
#: the note MOVES to the caption: its paragraph is then required on every caption riding the picture. The insider
#: list keeps its note on a five-row picture with the tighter rows (insider_multi5_alt_with_note.jpg).
INS_ROUNDUP_NOTE_MAX_ROWS = 4
#: A Form 4 row is "late" for the copy when MORE than this many federal business days (weekdays, federal
#: holidays excluded: the SEC's calendar, `trillion_club.rules.is_federal_business_day`) follow its
#: earliest trade up to and including its LAST filing — the Form 4 deadline is the end of the second
#: business day after the trade. A late row's trade dates are stated, never the two-business-day rule,
#: which would date the trade to the filing. The record admits a trade up to
#: `company_news_rules.INSIDER_MAX_FILING_LAG_DAYS` before the first filing (a 10b5-1 plan's deemed
#: execution date can make that gap legal). The last filing, not the first: every trade of the row then
#: sits within this bound of its own filing, whichever filing reported it. news-v14: business days, not
#: the old 4 calendar days, which let a Monday trade filed on Friday (four business days) read as timely.
INSIDER_LATE_BUSINESS_DAYS = 2
THIRTEEN_F_MAX_AGE_DAYS = 120
#: ~18 months: the adapter's `money_map_stale` gate, kept here as the template's own floor.
MONEY_MAP_MAX_AGE_DAYS = 550
#: A Money Map reads "by region" only while its rest bar ("Everything else": unattributed revenue plus lines
#: the post cannot name) is at most this share of revenue (the review of 2026-10-16, MM-6): a larger rest is
#: not shown to be regions — Coca-Cola's 22% "Unallocated" is mostly businesses — so the neutral wording,
#: true of anything, is used instead. Exact at the boundary (Decimal).
MONEY_MAP_REGION_MAX_OTHER_SHARE = Decimal("0.10")
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
#: The investee's name and the stake's source title are drawn on the picture's list and its video cover (news-v23:
#: the name on its row, the source on the row's as-of line): longer ones are refused `slot_rejected` (the fit tests in
#: tests/test_marketing_news_layouts.py prove these bounds against the worker). news-v23: the name's bound is the
#: read's own (`company_news_rules.company_name_problem`, 32 characters) — the list's compact cover rows hold it.
STAKE_NAME_MAX_CHARS = rules.COMPANY_NAME_CHARS[1]
STAKE_SOURCE_MAX_CHARS = 80
#: news-v23: a row's as-of line names its source only when the source as drawn (`_stake_source_shown`) is at most this
#: long — else the line is "As of {date}" alone (`st.asof`) and the source stays in every caption: an 80-character
#: source of the widest glyph cannot share a tile with its date in two lines (measured against the worker's list).
STAKE_ROW_SOURCE_MAX_CHARS = 60
STAKE_BACKGROUND_MAX_CHARS = 300
#: news-v19 (Batch 1, plan item 5): whether a stake's catalogue BACKGROUND may be a long-caption paragraph
#: (`_stake_background` still decides when it is on). Off: the live preview of 2026-10-14 printed the
#: hand-kept fragments as they are ("Paid $30.0M for a convertible note …", "Rest of a $20.0B facility …"),
#: in their own number style beside the headline's "$35 million" — notes for the catalogue's reviewer, not
#: copy. A code constant, never a setting: `revalidate` recomposes the stored output, so turning it on is a
#: template change — bump TEMPLATE_VERSION with it, once the backgrounds are rewritten as full sentences.
STAKE_BACKGROUND_IN_CAPTIONS = False
#: Name words the worker must draw whole, in characters (every bound proved in the widest glyph, "W",
#: at the floor size against marketing/news_layouts.py by tests/test_marketing_news_layouts.py):
#: a header's company name, a theme title and the cover's headline (NAME_WORD_MAX_CHARS); a stake's
#: investor and investee names under their plates, each in HALF the column (PAIR_NAME_WORD_MAX_CHARS);
#: a grid tile's company name and its segment line beside the plate (GRID_NAME_WORD_MAX_CHARS,
#: GRID_LINE_WORD_MAX_CHARS). A longer word is refused `slot_rejected` — or, for a theme member, the
#: member is left off the grid and counted in its "+k more" (it is still listed in every long caption).
#: Since news-v23 no template reads the two GRID_* caps or GRID_MAX_LINES: the theme tiles are measured
#: with `text_metrics` (an upper bound under both layout engines). They stay as tested bounds.
NAME_WORD_MAX_CHARS = 20
PAIR_NAME_WORD_MAX_CHARS = 15
GRID_NAME_WORD_MAX_CHARS = 14
#: The grid LINE's cap is no longer read by any template (news-v23 measures each tile line with `text_metrics`, which
#: bounds both layout engines); it stays the widest "W" run that fits at the floor under BOTH engines — 17, the
#: production worker's RAQM (HarfBuzz kerning widens "WW": 18 "W" draw 419.1 px in the 416 px column; BASIC 414 px).
GRID_LINE_WORD_MAX_CHARS = 17
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
#: Earnings (2b): the report day is in the week before the run (the adapter's window) — since news-v23 phase 3 the
#: window's own length, `rules.earnings_window_days` (eight days on a Thursday: Wednesday posts the weekly theme first);
#: this stays the default length.
EARNINGS_MAX_AGE_DAYS = rules.EARNINGS_WINDOW_DAYS
#: A quarter's results are reported within this many days of its end, else the period is implausible.
EARNINGS_MAX_REPORT_LAG_DAYS = 120
#: |actual − estimate| above this many times |estimate| is a feed error (`eps_gap_implausible`).
EARNINGS_MAX_GAP_RATIO = 10.0
#: news-v21 (main-session decision of 2026-10-20, owner-delegated): whether an earnings post may print its REVENUE
#: against the revenue estimate (narration L2, card 2, the picture's Revenue row, the alt text, the ".long"
#: headlines and their paragraph). Off: FMP gives no way to know which basis a revenue ESTIMATE is on. GE Aerospace's
#: consensus is its ADJUSTED revenue (run-off insurance excluded) while the calendar's revenue ACTUAL is usually the
#: GAAP figure (the statement's): Q3 2025's "revenue of $12.2 billion vs $10.4 billion" (+17.7%) held on neither
#: basis. A gate reading the issuer's earlier calendar actuals (2026-10-20, removed) caught only the quarters whose
#: ACTUALS change basis in the feed, never GE's or Travelers' 2025 ones. The pair stays REQUIRED (`implausible_figures`
#: here; the adapter's `revenue_implausible` and its period check): the data-quality and quarter check, never printed.
#: A code constant, never a setting: `revalidate` recomposes the stored output, so turning it on is a template change
#: — bump TEMPLATE_VERSION with it.
#: news-v23 (2026-11-19) — ON, by the OWNER's decision: "Revenue always" (RESTYLE_SPEC §2 item 3: "add Revenue reported
#: and estimate too"). The owner ACCEPTS the GE-type risk recorded above: FMP still never says which basis a revenue
#: ESTIMATE is on, so a consensus on an ADJUSTED basis (GE Aerospace's) can stand beside a GAAP reported figure and
#: the two (and the surprise between them) hold on neither basis — the picture's note and the captions' basis
#: paragraph say only that the estimate's basis can differ. Every company of the post prints its revenue pair (the
#: picture's Revenue line, the cover, its card, its caption paragraph, the alt text): `_earnings_row` writes it at one
#: shared precision (`_revenue_figures`), and a company whose pair cannot be written at all is dropped
#: (`drop_refused_rows`), never shown without it. Kept as a named constant so the decision stays greppable; the copy
#: no longer branches on it (tests pin it True).
EARNINGS_REVENUE_IN_POSTS = True
#: Theme Explainer (2b): the record's member-list day is at most this old (the adapter's `theme_stale`; a list with no
#: review date of its own carries the run day — owner decision 2026-11-20, "Trust the app's lists"); a member's
#: segment fact is drawn and narrated only when its fiscal year is recent and its name is short
#: enough for a grid tile line; narration covers at most THEME_NARRATED_MAX facts, the image at most
#: `template_onscreen.MAX_TILES` members ("+k more" for the rest), the long captions every member.
THEME_MAX_AGE_DAYS = 70
THEME_FACT_MAX_YEARS = 2
#: news-v16 (review round 16): a fact is used only while it can still be its company's LATEST fiscal year,
#: and beside the theme's other facts only when it is at most THEME_FACT_SPREAD_YEARS older than the newest
#: of them. A fiscal year labelled Y ends by about Jan 31 of Y + 1 at the latest (a retailer labels its year
#: by the year it starts) and its annual report follows within 60-120 days (10-K, 20-F) — so from
#: THEME_FACT_LATE_MONTH of the run year, every company has reported fiscal run_year - 1 and a fiscal
#: run_year - 2 fact is a year stale (a Nov 2026 run drew "Gaming · 62%" from fiscal 2024 beside fiscal 2026
#: facts, no year on the tile, the pair line or the caption). Fiscal calendars alone put the members' latest
#: years one label apart (a December year end's fiscal 2025 beside a January year end's fiscal 2026), never two.
THEME_FACT_SPREAD_YEARS = 1
THEME_FACT_LATE_MONTH = 6
#: news-v23 phase 3 (owner 2026-11-19: "for AI CHIPS, i don't see ARM, Intel and SNPS? add for me"): a fact older than
#: those years is no longer dropped from a tile — it is drawn with its OWN year ("Client Computing, FY2022 · 55% of
#: revenue", `th.img.seg.fy`), never implied current, down to `rules.THEME_TAGGED_FACT_MAX_AGE_YEARS`; the sub line
#: states only the untagged facts' years. Phase 4 (2026-11-20): a fact the adapter WALKED BACK to (the company's newest
#: stack was gross — `ThemeMember.latest_fiscal_year`) is ALWAYS tagged, whatever the sub line's years.
THEME_SEGMENT_MAX_CHARS = 36
#: news-v23 phase 3: a theme member's segment name may be as long as its record allows (`ThemeMember.top_segment`,
#: 40) — a long filed name draws its short label, and whether a tile can draw it is `_tile_line`'s measured answer.
#: (The insider spotlight's revenue line keeps THEME_SEGMENT_MAX_CHARS.)
THEME_MEMBER_SEGMENT_MAX_CHARS = 40
THEME_NARRATED_MAX = 6
THEME_MIN_FACTS = 3


@dataclass(frozen=True)
class SeriesSpec:
    """One shipped series: its class, its `post_copy` category (hashtag key) and its image."""

    series: str
    content_class: str        # "C" | "F"
    category: str             # "news:<series>"
    kicker: str               # the static kicker (Form 4 posts swap in their filing window)
    layout: str               # template_onscreen layout ("spotlight"/"feature" are drawn by no series since 2026-11-20)


#: Every series the templates compose: keys == `company_news_rules.NEWS_SERIES` ⊇
#: `selection.SHIPPED_SERIES` (pinned). The 2b series (congress_count, company_stakes, earnings,
#: theme_explainer) are composed here in code, but a run reaches them only once their id joins
#: SHIPPED_SERIES and the per-series switch (MARKETING_NEWS_SERIES, default the 2a four); `pair` and
#: `grid` validate only once `template_onscreen.SHIPPED_LAYOUTS` lists them (until then a stake or a
#: theme is refused `image_spec_invalid` — fail closed). news-v22 (2026-11-15) drew the spotlight lane's two
#: single-company series on the `feature` picture; since the owner's 2026-11-20 decisions both are LISTS on `rows`
#: (the insider list of 3-5 companies, the 13F list of 2-6 holdings) and `script_service.FEATURE_LAYOUT_SERIES` is
#: empty, so `feature` is drawn by no series. The insider list's kicker here is the static fallback: its picture
#: dates its kicker.
SERIES_SPECS: Dict[str, SeriesSpec] = {
    "ceo_buys": SeriesSpec("ceo_buys", "C", "news:ceo_buys", "FILED LAST WEEK · FORM 4", "rows"),
    "insider_buys": SeriesSpec("insider_buys", "C", "news:insider_buys", "FILED LAST WEEK · FORM 4", "rows"),
    # news-v23 (the restyle, owner 2026-11-19): both 13F series draw ONE list on `rows`, kicker "13F FILING"
    "thirteen_f": SeriesSpec("thirteen_f", "C", "news:thirteen_f", "13F FILING", "rows"),
    # news-v23 (owner 2026-11-19): 2-3 companies a post — a `rows` list (congress_multi3.jpg), no longer a spotlight
    "congress_count": SeriesSpec("congress_count", "C", "news:congress_count", "CONGRESS · DISCLOSED", "rows"),
    # news-v23 (owner 2026-11-19 "public only" + "add 1-3 more companies"): one investor's stakes — a `rows` list
    # (stakes_nvidia.jpg), no longer the one-stake `pair`
    "company_stakes": SeriesSpec("company_stakes", "F", "news:company_stakes", "COMPANY STAKES", "rows"),
    "earnings": SeriesSpec("earnings", "F", "news:earnings", "EARNINGS VS ESTIMATES", "rows"),
    "money_map": SeriesSpec("money_map", "F", "news:money_map", "MONEY MAP", "bars"),
    "theme_explainer": SeriesSpec("theme_explainer", "F", "news:theme_explainer", "INSIDE A THEME", "grid"),
    # owner decisions 2026-11-20 ("3 - 5 companies at once"): the spotlight lane's insider LIST, a `rows` picture
    # (insider_multi3.jpg / insider_multi5.jpg) — no longer the one-issuer `feature` picture of news-v22
    "insider_spotlight": SeriesSpec("insider_spotlight", "C", "news:insider_spotlight", "FORM 4", "rows"),
    "thirteen_f_spotlight": SeriesSpec("thirteen_f_spotlight", "C", "news:thirteen_f_spotlight", "13F FILING",
                                       "rows"),
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


# ── first-line prefixes (news-v22; owner decision 2026-11-15 "Loosen caption hype words") ─────────────
#
# The ONLY hype words a post may carry: a closed, code-owned set, each one emoji and a label, written at POSITION 0 of
# a caption body (and so of the composed caption that starts with it) — the first line's start, never a later line,
# never the YouTube title, the picture, the alt text, the video (hook, narration, burned captions, cards, the cover):
# the worker never receives captions (marketing.md §7), and an emoji on a card would draw as a tofu box (§5). Scope
# "Spotlights + Form 4 + earnings": a Form 4 ROUNDUP (weekly) is never BREAKING — the alert only; earnings is BREAKING
# only while fresh, else it carries none; the 13F spotlight names its filing; every other series none. Pinned with
# the lexicon (`tests/data/marketing_news_lexicon_v1.json` key "first_line_prefixes", kept out of LEXICON so the
# lexicon's word scan needs no exemption). The validator's only exemption is POSITIONAL (`_validate_words`: position 0
# of a `_PREFIX_FIELDS` string, the declared prefix and one space) and `_validate_prefix` stands behind it: the
# prefix anywhere else, another emoji, "BREAKING" / "alert" without it, or a prefix the series may not carry is
# refused. Switch MARKETING_FIRST_LINE_PREFIX (False), read ONCE per template build and frozen into
# `output.first_line_prefix_on` (`revalidate` re-composes from the frozen value); off, every caption is byte for byte
# the unprefixed composition. A change to any of these changes copy: it needs a TEMPLATE_VERSION bump.

#: id → the prefix text, without its trailing space. Single-code-point emoji with default emoji presentation (no
#: VS16), so `compliance.clean(p) == p`; X weighs each emoji 2 (13 / 27 / 15 weighted with the space after it).
FIRST_LINE_PREFIXES: Dict[str, str] = {
    "insider.breaking": "\U0001F6A8 BREAKING:",
    "insider.alert": "\U0001F6A8 Insider purchase alert:",
    "thirteen_f.filing": "\U0001F4CA 13F filing:",
    "earnings.breaking": "\U0001F6A8 BREAKING:",
}
#: series → the prefix ids it may carry, in preference order (the first that applies is used; a BREAKING id applies
#: only while `_breaking_fresh`). A series not listed carries none.
PREFIX_IDS_BY_SERIES: Dict[str, Tuple[str, ...]] = {
    "insider_spotlight": ("insider.breaking", "insider.alert"),
    "ceo_buys": ("insider.alert",),
    "insider_buys": ("insider.alert",),
    "earnings": ("earnings.breaking",),
    "thirteen_f_spotlight": ("thirteen_f.filing",),
}
#: The ids that say BREAKING: allowed only when the source is fresh (`_breaking_fresh`) — and so never without the
#: record (`validate_package` without it refuses them: stricter, never looser).
BREAKING_PREFIX_IDS: FrozenSet[str] = frozenset({"insider.breaking", "earnings.breaking"})
#: BREAKING's freshness bound — `company_news_rules`' one constant (never a second definition): at most this many
#: federal business days in (the lead's last Form 4 filing / the earnings report date, run_date]. The publisher may
#: send a post on the run day + 1, so a BREAKING post is at most about two business days after its source.
BREAKING_MAX_BUSINESS_DAYS = rules.BREAKING_MAX_BUSINESS_DAYS
#: Threads' PICTURE-post caption cap in UTF-8 bytes — `script_service.THREADS_IMAGE_CAPTION_MAX_BYTES` (a longer caption
#: freezes Threads as text), mirrored here because this module is pure and pinned equal by
#: tests/test_marketing_news_templates.py. A prefix never pushes a Threads caption over it (its emoji is 4 bytes, one
#: code point): the picture is content, the prefix is not (`_caption_bodies`).
THREADS_PICTURE_CAPTION_MAX_BYTES = 500


# ── the lexicon (pinned verbatim in tests/data/marketing_news_lexicon_v1.json) ─

LEXICON: Dict[str, str] = {
    # shared
    "list.1": "{a}",
    "list.2": "{a} and {b}",
    "list.3": "{a}, {b} and {c}",
    # news-v23: an earnings post names up to four companies
    "list.4": "{a}, {b}, {c} and {d}",
    "list.more.1": "{a} and {k} more",
    "list.more.2": "{a}, {b} and {k} more",
    "list.more.3": "{a}, {b}, {c} and {k} more",
    "alt.source": "Source: {source}. {as_of}.",
    # news-v23 phase 3 fix round (2026-11-20): a theme whose member list has no date of its own
    # (`ThemeExplainer.tickers_as_of_from_row`, the app's live list) — the source alone, never "Members as of …"
    "alt.source.bare": "Source: {source}.",
    # news-v23 (restyle 2026-11-19): the date a family's short picture footer ends with (`post_copy.image_footer`;
    # its head and source words are post_copy's code-owned FOOTER_SOURCE_* table)
    "foot.filed": "filed {date_img}",
    "foot.amended": "amended {date_img}",
    "foot.fiscal": "fiscal {fy}",
    "foot.month": "{month_long}",
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
    # news-v15: ONE filing date but several purchase lines — the record keeps distinct filing DAYS, not
    # documents, so this may be one Form 4 or several filed the same day: no entry here counts them
    # (`_filing_count` "neutral"; a single purchase is provably one filing, two dates provably several)
    "ins.l1.role.neutral": "The filer is {role_np_sp}, and the filing date is {filed_sp}.",
    # ≥ 8 words for every role (LINE_WORDS), so a long narration can still step down to it
    "ins.l1.role.short.neutral": "The filer in this case is {role_np_sp}.",
    # the share noun agrees with the figure as written ("1 share", "about 1 share", "850 shares")
    "ins.share.one": "share",
    "ins.share.many": "shares",
    "ins.l2.one.one": "It reports one purchase of {shares_w} {share_noun}, for {amt}.",
    "ins.l2.one.many": "It reports {k} purchases, {shares_w} {share_noun} in total, for {amt}.",
    "ins.l2.many.one": "They report one purchase of {shares_w} {share_noun}, for {amt}.",
    "ins.l2.many.many": "They report {k} purchases, {shares_w} {share_noun} in total, for {amt}.",
    "ins.l2.neutral.many": "{k} purchases are reported, {shares_w} {share_noun} in total, for {amt}.",
    "ins.l3.indirect.all": "The shares are reported as held indirectly, not in the {role_sp}'s own name.",
    # news-v19 (Batch 1): "Some of the shares", as the caption says (`ins.p.flag.indirect.some`) — "Part of the
    # shares are" read as a typo beside it
    "ins.l3.indirect.some": "Some of the shares are reported as held indirectly.",
    "ins.l3.amended": "The figures shown here include an amended Form 4.",
    "ins.l3.explainer": "Officers and directors must file a Form 4 within two business days of a trade.",
    # news-v12: the explainer's late twin. A row whose earliest trade is more than INSIDER_LATE_BUSINESS_DAYS
    # business days before its last filing states the trade dates the record carries instead of the two-business-day
    # rule, which would date the trade to the filing ("filed Nov 12" + "within two business days").
    "ins.l3.late.one.date": "The filing gives {first_trade_sp} as the trade date.",
    "ins.l3.late.one.window": "The filing gives trade dates from {first_trade_sp} to {last_trade_sp}.",
    "ins.l3.late.many.date": "The filings give {first_trade_sp} as the trade date.",
    "ins.l3.late.many.window": "The filings give trade dates from {first_trade_sp} to {last_trade_sp}.",
    "ins.l3.late.neutral.date": "The purchases are reported with {first_trade_sp} as the trade date.",
    "ins.l3.late.neutral.window": "The purchases are reported with trade dates from {first_trade_sp} to "
                                  "{last_trade_sp}.",
    # news-v13: a roundup whose lead row is timely but another row is late — the Form 4 without the rule (the
    # "At least {m} more …" line after it covers the late row, whose trade the rule would date to its filing)
    "ins.l3.form4": "A Form 4 is the report officers and directors file on their own trades.",
    # A count of rows is a LOWER BOUND, never the week's total (review round 9): the record holds at most
    # INSIDER_MAX_ROWS rows, after the adapter's dollar, listing, cap, role and amendment gates. Owner decisions
    # 2026-11-20: a roundup holds at least INSIDER_MIN_ROWS rows, so the bound is "At least {m} more" with m ≥ 2 (the
    # ".one" twins left the lexicon; news-v19's "one" word still holds for the insider list's "at least one other").
    "ins.l4.more.many": "At least {m} more {roles_sp} disclosed buying their own company's stock in the same week.",
    # news-v15: "each filing's", as the caption says (`ins.p.form4`) — never "the filing's" beside a row
    # whose purchases may come from several Form 4s
    "ins.l4.amounts": "Amounts are each filing's share count times the cost it reports for each share.",
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
    "ins.c3.indirect.some.body": "Some of the shares, per the filing",
    "ins.c3.amended.title": "Amended filing",
    "ins.c3.amended.body": "The figures include an amended Form 4 (Form 4/A)",
    "ins.c3.explainer.title": "What a Form 4 is",
    "ins.c3.explainer.body": "Filed within two business days of an insider's trade",
    "ins.c3.form4.body": "The report officers and directors file on their own trades",
    "ins.c3.late.date.title": "Trade date",
    "ins.c3.late.window.title": "Trade dates",
    "ins.c3.late.body": "{trade_window_img} · filed {filed_window_img}",
    "ins.c4.more.title": "Same week",
    "ins.c4.more.many": "At least {m} more {roles} disclosed buying their own company's stock",
    "ins.c4.amounts.title": "How the amount is counted",
    "ins.c4.amounts.body": "Shares times the cost of each share, per the filing",
    # owner decisions 2026-11-20: {n} is the rows the picture (and the cover) DRAWS — a lower bound ("At least")
    "ins.img.title.many": "At least {n} {roles} disclosed buying their own company's stock",
    # owner decisions 2026-11-20 ("3 - 5 companies at once"): the picture and the cover of EVERY Form 4 post draw the
    # first INS_PICTURE_ROWS rows; the rest are listed in every caption riding them (the 13F list's promise rules)
    "ins.more.one": "+1 more company in the caption",
    "ins.more.many": "+{k} more companies in the caption",
    "ins.alt.more.one": "One more company is listed in the caption.",
    "ins.alt.more.many": "{k} more companies are listed in the caption.",
    # news-v12: it is the SHARES that are held, not the purchases (never "shares bought": "bought" is
    # off the verb table)
    "ins.img.note.indirect.all": "The shares in these purchases are reported as held indirectly.",
    "ins.img.note.indirect.some": "Some of the shares in these purchases are reported as held indirectly.",
    "ins.img.note.amended": "The figures include an amended Form 4 (Form 4/A).",
    # news-v20: format-neutral — "of these", never "here": X posts this as text, and the picture platforms fall
    # back to text without their picture; "of these" keeps the superlative to the rows the count is about
    "ins.ch.many.long": "At least {n} {roles} disclosed buying their own company's stock in Form 4s filed "
                        "{window_cap}. The largest of these: {co}, {amt}.",
    "ins.ch.many.short": "At least {n} {roles} disclosed buying their own company's stock in Form 4s filed "
                         "{window_cap}.",
    "ins.ch.many.min": "At least {n} {roles} disclosed buying their own company's stock.",
    # news-v19 (Batch 1): "of the company's stock" — the subject names the company, so the headline never names
    # it twice ("Accelevation Holdings' CEO disclosed buying $3.7 million of Accelevation Holdings stock")
    "ins.ch.one.long.one": "{subj} disclosed buying {amt} of the company's stock in a Form 4 filed {filed_cap}.",
    "ins.ch.one.long.many": "{subj} disclosed buying {amt} of the company's stock in Form 4s filed "
                            "{filed_window_cap}.",
    "ins.ch.one.long.neutral": "{subj} disclosed buying {amt} of the company's stock on Form 4, filed {filed_cap}.",
    "ins.ch.one.short": "{subj} disclosed buying {amt} of the company's stock.",
    "ins.ch.one.min": "A {role} disclosed buying {amt} of their company's stock.",
    # news-v20: never "shown" — the paragraphs ride text posts too; together they list every row of the record
    "ins.p.rows": "The purchases: {rows}.",
    "ins.p.rows2": "More purchases: {rows}.",
    "ins.row.name": "{co}, {role} {person}, {amt}",
    "ins.row.noname": "{co}, {role}, {amt}",
    "ins.p.one.name.one": "The filing names {person}, {role_np} of {co}.",
    "ins.p.one.name.many": "The filings name {person}, {role_np} of {co}.",
    "ins.p.one.noname.one": "The filing is by {role_np} of {co}.",
    "ins.p.one.noname.many": "The filings are by {role_np} of {co}.",
    "ins.p.one.name.neutral": "The filer is {person}, {role_np} of {co}.",
    "ins.p.one.noname.neutral": "The filer is {role_np} of {co}.",
    "ins.p.flag.indirect.all": "The shares are reported as held indirectly rather than in the buyer's own name.",
    "ins.p.flag.indirect.some": "Some of the shares are reported as held indirectly rather than in the buyer's own "
                                "name.",
    # news-v12: a roundup (2+ rows) is about several buyers — its flag paragraph is plural
    "ins.p.flag.indirect.all.many": "The shares are reported as held indirectly rather than in the buyers' own "
                                    "names.",
    "ins.p.flag.indirect.some.many": "Some of the shares are reported as held indirectly.",
    "ins.p.flag.amended": "The figures include an amended Form 4 (Form 4/A).",
    "ins.p.form4": "A Form 4 is the report a company's officers and directors file within two business days of a "
                   "trade. Amounts are each filing's share count times the cost it reports for each share.",
    # news-v12: the late twins (see `ins.l3.late.*`): the trade dates the record carries, then the Form 4
    # without the two-business-day rule
    "ins.p.trade.one.date": "The filing gives {first_trade_cap} as the trade date.",
    "ins.p.trade.one.window": "The filing gives trade dates from {first_trade_cap} to {last_trade_cap}.",
    "ins.p.trade.many.date": "The filings give {first_trade_cap} as the trade date.",
    "ins.p.trade.many.window": "The filings give trade dates from {first_trade_cap} to {last_trade_cap}.",
    "ins.p.trade.neutral.date": "The purchases are reported with {first_trade_cap} as the trade date.",
    "ins.p.trade.neutral.window": "The purchases are reported with trade dates from {first_trade_cap} to "
                                  "{last_trade_cap}.",
    "ins.p.form4.late": "A Form 4 is the report a company's officers and directors file on their own trades. Amounts "
                        "are each filing's share count times the cost it reports for each share.",
    "ins.vp.more.many": "At least {m} more {roles} disclosed buying their own company's stock in Form 4s filed the "
                        "same week.",
    "ins.yt.long": "{subj} disclosed buying {amt_s} of the company's stock",
    "ins.yt.short": "{subj} disclosed buying {amt_s} of stock",
    "ins.yt.min": "A {role} disclosed buying {amt_s} of stock",
    # news-v15: a row is one buyer's TOTAL over all their purchase lines in the window, never one purchase
    "ins.alt.rows": "The image lists each buyer's total by company, role and amount: {rows}.",
    "ins.alt.row": "{co}, {role}, {amt_s}",
    # 13F (news-v23, the restyle — owner 2026-11-19). Both 13F series draw ONE list (`_f13_list_parts`; the f13s.list.*
    # entries below): the main lane's roundup (a lead filer first: Berkshire) and the spotlight lane's filer. These are
    # the shared pieces: the subject, the period and the explainers
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
    # the filing's quarter: a list of ONE kind narrates it third (the picture and the cover carry no quarter)
    "f13.l1": "It covers the quarter that ended {period_sp} and was filed on {filed_sp}.",
    "f13.l1.amended": "It covers the quarter that ended {period_sp}; an amended version was filed on {amended_sp}.",
    "f13.l1.short": "This filing covers the quarter that ended {period_sp}.",
    "f13.l1.amended.short": "An amended version covers the quarter that ended {period_sp}.",
    # news-v14: a deadline, never a bound — the due date rolls past weekends and holidays (2026-Q3's is 47
    # days on), late filers and amendments come later, and the post shows its own filing date beside this
    "f13.l4.explainer": "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later.",
    "f13.l4.explainer.short": "A 13F is due about 45 days after a quarter's end.",
    "f13.l.explainer2": "A 13F shows holdings at the quarter's end, not when shares changed hands.",
    "f13.c1.title": "Quarter ended",
    "f13.c1.body": "{period_img} · filed {filed_img}",
    "f13.c1.body.amended": "{period_img} · amended {amended_img}",
    "f13.c4.explainer.title": "About 13F filings",
    "f13.c4.explainer.body": "U.S. stock holdings, due about 45 days after the quarter",
    "f13.c.explainer2.title": "What a 13F shows",
    "f13.c.explainer2.body": "Holdings at the quarter's end, not trade dates",
    "f13.p.listed": "{co} first appears in this filing; it was listed in {month_cap}.",
    "f13.p.explainer": "A 13F lists U.S. stock holdings at a quarter's end and is due about 45 days later; "
                       "it does not show when shares changed hands.",
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
    # news-v12: the breakdown is FMP's product segmentation — product or service lines for some companies,
    # reportable segments for others — so no line calls a part a "segment" (Apple's iPhone is not one).
    # news-v19 (Batch 1): nor a "business" — McDonald's parts are markets, Costco's are countries — so the
    # line says only the part's name; a region name that takes "the" says it in running text
    # (`mm.region.the`, "The" when it opens the sentence), never on a bar, a card or the alt text.
    "mm.l2.two": "{s1} made up {sh1} of {basis}, and {s2} made up {sh2} of {basis}.",
    "mm.l2.one": "{s1} made up {sh1} of {basis} that year.",
    "mm.region.the": "the {name}",
    "mm.region.the.cap": "The {name}",
    "mm.basis.revenue": "revenue",
    "mm.basis.segments": "combined sales",
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
    "mm.c2.title": "Revenue mix",
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
    # news-v19 (Batch 1): never "as reported" — FMP fills gross profit as revenue minus cost of revenue for a
    # company that reports no gross subtotal (Amazon), so the figures are the annual results, explained
    "mm.img.subtitle": "Fiscal {fy} · annual results",
    # the rest bar (review of 2026-10-16, live preview F6): it holds revenue the breakdown never attributes AND
    # lines the company does break out that the post cannot name (a code, a label too long to draw, a sixth
    # line) — "not broken out" was false for the second, and a plain "Other" reads as a reported "Other" line
    # (the 2026-10-10 sweep); "Everything else" is true of both
    "mm.bar.other": "Everything else",
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
    # news-v23 (2026-11-20: the restyled cover read backwards — "OF EVERY $100 OF COSTCO REVENUE WAS NET INCOME" over
    # "$2.94"): the cover's headline is the picture's own ("mm.img.title", "How Costco makes money") and these are the
    # LABEL drawn under the "per $100" figure inside its card (`opening_card.figure_label`), the picture callout's
    # words ("For every $100 of revenue, …"); the chip is the ticker again, as on the picture's plate (news-v19's
    # "Fiscal {fy}" chip left: the cover's short footer ends "fiscal {fy}")
    "mm.open.profit": "of every $100 of revenue was net income",
    "mm.open.loss": "net loss for every $100 of revenue",
    "mm.ch.long.profit": "How {co} makes money: {rev} of revenue in fiscal {fy}, and {per100} of every $100 kept "
                         "as net income.",
    "mm.ch.long.loss": "How {co} makes money: {rev} of revenue in fiscal {fy}, and a net loss of {per100} for "
                       "every $100.",
    "mm.ch.short": "How {co} makes money, from its fiscal {fy} results.",
    # news-v19 (Batch 1): the neutral breakdown — true of product lines and of markets alike; "by region" only
    # when EVERY named part is on `company_news_rules.REGION_NAMES` (Costco's countries), never a guess
    "mm.p.segments": "How fiscal {fy} revenue broke down: {items}.",
    "mm.p.segments.region": "How fiscal {fy} revenue broke down by region: {items}.",
    "mm.p.elim": "Sales between parts of the company, {elim}, are taken out of that total.",
    "mm.item": "{name}, {value}",
    "mm.item.other": "everything else, {value}",
    "mm.p.costs.both": "After the cost of sales, gross {gp_word} was {gp}. After operating costs, operating "
                       "{op_word} was {op}.",
    "mm.p.costs.gross": "After the cost of sales, gross {gp_word} was {gp}.",
    "mm.p.costs.operating": "After operating costs, operating {op_word} was {op}.",
    "mm.p.net.profit": "Net income was {ni}: {per100} of every $100 of revenue.",
    "mm.p.net.loss": "The net loss was {ni}: {per100} for every $100 of revenue.",
    "mm.p.source": "Figures come from the company's annual financial statements for fiscal {fy}.",
    # appended to `mm.p.source` whenever the map shows gross profit — or, for a gross LOSS, its twin (review of
    # 2026-10-16, MM-5: a post showing only "Gross loss" defined "gross profit")
    "mm.p.gross": "Gross profit is revenue minus the cost of sales.",
    "mm.p.gross.loss": "Gross profit or loss is revenue minus the cost of sales.",
    "mm.yt": "How {co} makes money: fiscal {fy} results",
    "mm.yt.short": "How {co} makes money",
    "mm.alt.segments": "Bars show how revenue broke down: {items}.",
    "mm.alt.segments.region": "Bars show revenue by region: {items}.",
    "mm.alt.flow": "A second set of bars shows {items}.",
    "mm.alt.item": "{label} {value}",
    "mm.alt.callout": "{callout}.",
    # owner decision 2026-11-20 ("for companies who are loss, it's a bit hard to understand … a cost or loss will have a
    # longer red bar than the revenue", and "A: costs vs revenue (Recommended)" — money_map_loss_A_*.jpg): a LOSS year's
    # COSTS card, on the picture and the video cover, in place of the gross / operating steps. Revenue (navy) over the
    # longer red "Costs and expenses" bar on one scale, the gap past revenue labelled "Net loss"; its parts — cost of
    # sales = revenue − gross profit, operating expenses = gross profit − operating income, and interest, tax and other
    # items = net income − operating income when they were a cost — or, when they were income, the one line saying how
    # much they made the loss smaller (`_money_map_costs`). The callout is one subtraction: costs per $100 − $100 = the
    # net loss per $100 (its figure is 100 + the net loss's, so the picture's arithmetic is exact).
    "mm.cost.total": "Costs and expenses",
    "mm.cost.sales": "Cost of sales",
    "mm.cost.opex": "Operating expenses",
    "mm.cost.other": "Interest, tax and other items",
    "mm.cost.note": "+{other_s} from interest, tax and other items made the loss smaller",
    "mm.cost.callout.lead": "For every $100 of revenue, costs were {c100},",
    "mm.cost.callout.net": "so the net loss was {per100}",
    "mm.alt.costs": "A second set of bars compares revenue, {rev_s}, with costs and expenses, {costs_s}: {items}.",
    "mm.alt.costs.bare": "A second set of bars compares revenue, {rev_s}, with costs and expenses, {costs_s}.",
    "mm.alt.costs.net": "The part of the costs bar past revenue is the net loss, {ni_s}.",
    # Congress Count (2b-1). A count of ≥ 2 distinct members who DISCLOSED purchases (never "bought"),
    # dated by the disclosure month; no member is named or narrowed (no chamber, party, state,
    # district, committee or title), no dollar figure. news-v23 (the restyle, owner 2026-11-19: "2-3 companies at
    # once", congress_multi3.jpg + theme_congress_defense.jpg): ONE post of 2-3 companies, each counted from 2+
    # distinct members, the most members first — the picture and the cover list one card per company (the count,
    # GREEN; the logo; the ticker chip; the name; "members disclosed purchases of {co} stock", its "purchases" on a
    # solid GREEN pill — a drawing rule of the worker, never a string); each member is counted once PER COMPANY (a
    # member who disclosed purchases of two of the companies is in both counts).
    "cg.kicker": "CONGRESS · DISCLOSED IN {month_upper}",
    "cg.asof": "{month_long} · as of {as_of_img}",
    "cg.hook.year": "{n} members of Congress disclosed purchases of {co_sp} stock in {month_long}.",
    "cg.hook": "{n} members of Congress disclosed purchases of {co_sp} stock in {month_name}.",
    "cg.hook.min": "{n} members of Congress disclosed {co_sp} stock purchases in {month_name}.",
    "cg.l3": "These reports can include a spouse's or dependent child's trades, and give amounts only as ranges.",
    # news-v15: a deadline, never a bound — the count takes every in-month disclosure whatever its trade
    # date, so a late report (past 45 days) is counted; the card already says "some are filed later"
    "cg.l4": "Reports are due within 45 days of a trade; some come later, so the trades may be older.",
    "cg.c1.title": "Disclosed in",
    "cg.c1.body": "{month_long} · as of {as_of_img}",
    "cg.c4.title": "Reporting delay",
    "cg.c4.body": "Due within 45 days of the trade; some are filed later",
    # news-v23: the several-company post — the picture's and the cover's words, the narration after its hook (the
    # lead company's count), the cards and the captions
    "cg.m.title": "Members of Congress disclosed purchases",
    "cg.m.sub": "{month_long} · disclosures as of {as_of_img}",
    "cg.m.row": "members disclosed purchases of {co} stock",
    "cg.m.once": "Each member counted once per company",
    "cg.m.l.other.one": "{n} members disclosed purchases of {co_sp} stock in the same month.",
    "cg.m.l.other.two": "{n} members disclosed purchases of {co_sp} stock, and {n2} of {co2_sp} stock.",
    "cg.m.l.other.two.short": "Also disclosed: {co_sp}, {n} members; {co2_sp}, {n2} members.",
    "cg.m.l.count": "The counts cover periodic transaction reports disclosed during {month_long}, as of {as_of_sp}.",
    "cg.m.l.count.short": "The counts cover reports disclosed in {month_long}, as of {as_of_sp}.",
    "cg.m.l.once": "Each member is counted once per company; reports can include a spouse's or child's trades.",
    "cg.m.l4.short": "Reports are due within 45 days of a trade; some are filed later.",
    "cg.m.c.other.title": "Also disclosed",
    "cg.m.c.other.body.one": "{co}: {n} members",
    "cg.m.c.other.body.two": "{co}: {n} members · {co2}: {n2} members",
    "cg.m.c.once.title": "Counted once per company",
    "cg.m.c.once.body": "Can include spouse and dependent-child trades",
    "cg.m.h.2": "{n} members of Congress disclosed purchases of {co} stock in {month_long}, and {n2} of {co2} stock.",
    "cg.m.h.3": "{n} members of Congress disclosed purchases of {co} stock in {month_long}, {n2} of {co2} and {n3} of "
                "{co3}.",
    "cg.m.h.names": "Members of Congress disclosed purchases of {names} stock in {month_long}.",
    "cg.m.h.min": "Members of Congress disclosed purchases of {names} stock in {month_short}.",
    # the tightest budgets (X with a link): the lead company's count alone — the picture lists every company
    "cg.m.h.lead": "{n} members of Congress disclosed purchases of {co} stock in {month_long}.",
    "cg.m.h.lead.min": "{n} members of Congress disclosed purchases of {co} stock in {month_short}.",
    "cg.m.p.row": "{co} ({sym}): {n} members of Congress disclosed purchases in {month_long}.",
    "cg.m.p.count": "The counts cover periodic transaction reports disclosed during {month_long}, as of {as_of_cap}.",
    "cg.m.p.once": "Each member is counted once per company, however many reports they filed that month.",
    "cg.m.yt": "Members of Congress disclosed purchases of {names} stock in {month_long}",
    "cg.m.yt.short": "Members of Congress disclosed purchases of {names} stock in {month_short}",
    "cg.m.yt.lead": "{n} members of Congress disclosed purchases of {co} stock in {month_short}",
    "cg.m.yt.min": "Members of Congress disclosed stock purchases in {month_long}",
    "cg.m.alt": "The image shows, for {n} companies, how many members of Congress disclosed purchases of each "
                "company's stock in {month_long}, each member counted once per company.",
    "cg.m.alt.rows": "{items}.",
    "cg.m.alt.row": "{co}, {n} members",
    # Company Stakes (2b-2). The stake's own verb and basis with its as-of date; a figure is always a
    # disclosed dollar amount (an ownership share alone is never posted: its basis is not recorded).
    "st.kicker": "COMPANY STAKES",
    "st.asof": "As of {as_of_img}",
    # news-v19 (Batch 1, plan item 5): the investee as a NOUN PHRASE — `{ee_np}`, and `{ee_np_cap}` opening a
    # sentence — is the name, or "the {ee}" for a DESCRIPTIVE name (`company_news_rules.takes_name_article`:
    # "As of that date, the Louisiana data center venture was not listed …"). `{ee}` stays the bare name where
    # a determiner already stands ("{inv_s} {ee} stake", "The {ee} stake of …") and on every drawn label.
    "st.ee.the": "the {ee}",
    "st.ee.the.cap": "The {ee}",
    "st.h.invested": "{inv} invested {amt} in {ee_np}",
    "st.h.committed_up_to": "{inv} committed up to {amt} to {ee_np}",
    # news-v17: a carrying or fair value names no filer. The source may be ANOTHER filer's document (the
    # catalogue's Berkshire Hathaway Energy 10-Q rows sit under the investor Berkshire Hathaway), so no
    # entry says "{inv} reported its …" or "{inv} carried its …": the figure is the source's — the
    # caption cites it ("Source: …"), L1 and L2 say "the source".
    "st.h.carrying_value.poss": "{inv_s} {ee} stake had a carrying value of {amt}",
    "st.h.carrying_value.of": "The {ee} stake of {inv} had a carrying value of {amt}",
    "st.h.carrying_value.short.poss": "{inv_s} {ee} stake was carried at {amt}",
    "st.h.carrying_value.short.of": "The {ee} stake of {inv} was carried at {amt}",
    "st.h.fair_value.poss": "{inv_s} {ee} stake had a fair value of {amt}",
    "st.h.fair_value.of": "The {ee} stake of {inv} had a fair value of {amt}",
    "st.hook": "{h}.",
    "st.l1": "The figure is as of {as_of_sp}, from the source named in this post.",
    "st.l2.invested": "That is the amount the source reports as invested, not a figure for today.",
    "st.l2.committed_up_to": "That is the most the agreement allows; the source says up to that amount.",
    "st.l2.carrying_value": "Carrying value is the amount the source says the stake was recorded at on that date.",
    "st.l2.fair_value": "Fair value is the amount the source says the stake was measured at on that date.",
    "st.l3.us": "Shares of {ee_np} are listed on a U.S. exchange.",
    "st.l3.listed": "{ee_np_cap} has been listed on an exchange since {month_sp}.",
    "st.l3.commitment": "A commitment is money agreed to be invested, which may be paid over time.",
    "st.l4": "Companies disclose stakes like this in filings and official releases.",
    "st.c1.title": "As of",
    "st.c1.body": "{as_of_img}",
    "st.c2.invested.title": "Invested",
    "st.c2.invested.body": "The amount put in, not a figure for today",
    "st.c2.committed_up_to.title": "Committed up to",
    "st.c2.committed_up_to.body": "The most the agreement allows",
    "st.c2.carrying_value.title": "Carrying value",
    "st.c2.carrying_value.body": "The source's recorded value on that date",
    "st.c2.fair_value.title": "Fair value",
    "st.c2.fair_value.body": "The source's measure of the stake on that date",
    "st.c3.us.title": "Listed in the U.S.",
    "st.c3.us.body": "Shares trade on a U.S. exchange",
    "st.c3.listed.title": "Listed since",
    "st.c3.listed.body": "{month_img}",
    "st.c3.commitment.title": "A commitment",
    "st.c3.commitment.body": "Money agreed to be invested, which may be paid over time",
    "st.c4.title": "Source",
    "st.c4.body": "{source}",
    "st.label.invested": "invested",
    "st.label.committed_up_to": "committed up to",
    "st.label.carrying_value": "carrying value",
    "st.label.fair_value": "fair value",
    # the undated stake: the YouTube title's last fallback of a one-stake post (never the cover)
    "st.open.value.poss": "{inv_s} stake in {ee_np}",
    "st.open.value.of": "The stake of {inv} in {ee_np}",
    "st.ch.long": "{h}, as of {as_of_cap}. Source: {source}.",
    "st.ch.short": "{h}, as of {as_of_cap}.",
    "st.p.source": "Source: {source}, as of {as_of_cap}.",
    # news-v23 (the restyle, owner 2026-11-19: "filter for companies that public only. no private" + "add 1-3 more
    # companies for the NVIDIA (or the other trillion companies)"; stakes_nvidia.jpg / stakes_nvidia3.jpg): ONE
    # investor's stakes in 1-4 PUBLIC companies a post (review 2026-11-20: the spec's 1-4, every one on the picture).
    # The picture (and the video cover): the title — "{inv_s} stakes" white, "in public companies" gold
    # (`look.GOLD_TAILS`) — the investor's plate, one row per stake (its logo, ticker chip, name, the basis word
    # `st.label.*` small over the amount, and "As of {date} · {source}" on a tile) and — the COVER, which draws
    # STAKES_COVER_ROWS — "+N more stakes in the caption". A source title's trailing "(<date>)" / "(quarter ended <date>)" /
    # "(fiscal year ended <date>)" is left off the picture ONLY when that date is the row's own as-of date (printed
    # beside it: nothing is lost); the captions keep the title whole.
    "st.m.title.poss": "{inv_s} stakes in public companies",
    "st.m.title.of": "Stakes of {inv} in public companies",
    "st.m.row.asof": "As of {as_of_img} · {source}",
    "st.m.more.one": "+1 more stake in the caption",
    "st.m.more.many": "+{k} more stakes in the caption",
    # the narration of a post of two or more: the count, the lead stake with its date, the next one or two, what each
    # figure is, the listing
    "st.m.hook.poss": "{inv_s} stakes in {n} public companies, each with its disclosed figure.",
    "st.m.hook.poss.short": "{inv_s} stakes in {n} public companies.",
    "st.m.hook.of": "The stakes of {inv} in {n} public companies, each with its disclosed figure.",
    "st.m.hook.of.short": "The stakes of {inv} in {n} public companies.",
    "st.m.l.lead": "{h}, as of {as_of_sp}.",
    "st.m.l.also.one": "Also on the list: {a}.",
    "st.m.l.also.two": "Also on the list: {a}, and {b}.",
    "st.m.o.invested": "{amt} invested in {ee_np}",
    "st.m.o.committed_up_to": "up to {amt} committed to {ee_np}",
    "st.m.o.carrying_value": "a stake in {ee_np} carried at {amt}",
    "st.m.o.fair_value": "a stake in {ee_np} with a fair value of {amt}",
    "st.m.l.basis.invested": "Each figure is the amount its source reports as invested, not a figure for today.",
    "st.m.l.basis.committed_up_to": "Each figure is the most its agreement allows, as its source reports it.",
    "st.m.l.basis.carrying_value": "Each figure is the carrying value its source reports for that date.",
    "st.m.l.basis.fair_value": "Each figure is the fair value its source reports for that date.",
    "st.m.l.basis.mixed": "Each figure is shown with its own basis and date, from the source named beside it.",
    "st.m.l.public": "Each of these companies is listed on a U.S. exchange.",
    "st.m.c.row": "{basis_title} {amt_s} · as of {as_of_img}",
    "st.m.c.also.title": "Also on the list",
    "st.m.c.item": "{ee} · {amt_s} {label}",
    "st.m.c.basis.mixed.title": "Each figure's basis",
    "st.m.c.basis.mixed.body": "Labelled beside it, as of its own date",
    "st.m.c.public.title": "Public companies",
    "st.m.c.public.body": "Each one is listed on a U.S. exchange",
    # the captions of a post of two or more: the first line, one paragraph per stake (`st.m.p.row`: the stake's own
    # verb, its as-of date and its WHOLE source title), what each figure is, the listing
    "st.m.h.poss": "{inv_s} stakes in {n} public companies, each with its source and date.",
    "st.m.h.poss.min": "{inv_s} stakes in {n} public companies.",
    "st.m.h.of": "The stakes of {inv} in {n} public companies, each with its source and date.",
    "st.m.h.of.min": "The stakes of {inv} in {n} public companies.",
    "st.m.p.row": "{h}, as of {as_of_cap}. Source: {source}.",
    # the SHORT row paragraph of the short captions (X, Bluesky, Threads): the stakes the picture's "+N more" promises,
    # in a few words each — the company, its figure with its basis, its date (the long captions cite each source)
    "st.m.p.short.invested": "{ee}: {amt} invested, as of {as_of_cap}.",
    "st.m.p.short.committed_up_to": "{ee}: up to {amt} committed, as of {as_of_cap}.",
    "st.m.p.short.carrying_value": "{ee}: carried at {amt}, as of {as_of_cap}.",
    "st.m.p.short.fair_value": "{ee}: a fair value of {amt}, as of {as_of_cap}.",
    "st.m.yt.poss": "{inv_s} stakes in {n} public companies",
    "st.m.yt.of": "The stakes of {inv} in {n} public companies",
    "st.m.yt.min": "Company stakes in public companies",
    # the alt text (every post): what the PICTURE lists, the rest "in the caption"
    "st.m.alt.one": "The image lists one stake of {inv} in a public company, with its figure, basis, date and source.",
    "st.m.alt.many": "The image lists {n} stakes of {inv} in public companies, each with its figure, basis, date "
                     "and source.",
    "st.m.alt.rows": "{items}.",
    "st.m.alt.row": "{ee}, {amt_s} {label}",
    "st.m.alt.more.one": "1 more stake is listed in the caption.",
    "st.m.alt.more.many": "{k} more stakes are listed in the caption.",
    "st.m.asof.span": "As of {first} to {last}",
    # Earnings vs Estimates (2b-3). The reported EPS beside the analyst estimate; never "expected",
    # "beat" or "miss"; no "adjusted" claim (contract A1): the basis note says the figure can be on a
    # different basis. Review round 9: the calendar's EPS is the figure on the basis
    # analysts estimate (often not the official accounting result), so no line says the company
    # "earned", "lost" or had "earnings of" / "a loss of" — the figure is said with its sign
    # ("negative $0.05" spoken, "-$0.05" written, "−$0.05" in the picture's table) and "EPS" where it is written. No
    # "a"/"an" stands before a figure: the article would depend on how the figure is read ("an $8.1B").
    # news-v23 (the restyle, owner 2026-11-19: "add Revenue reported and estimate too" + "combine 2 to 4 companies at
    # once" + "add +X% surprised (in green) or -X% surprised (in red)" + column order "Estimate, Reported, Surprise";
    # earnings_multi4.jpg / earnings_multi2.jpg): ONE post of 2-4 companies, the largest first, each with an EPS and a
    # Revenue line of three columns. "Surprise" is the column's LABEL and "+8.0%" / "−11.4%" its cells — the one place
    # either stands (`_validate_words`' positional exemptions); never a verb, never in a caption or the narration.
    "er.kicker": "EARNINGS VS ESTIMATES",
    "er.asof": "Reported {report_img}",
    "er.neg": "negative {x}",
    "er.estimate": "An analyst estimate is the average figure analysts published before the report.",
    "er.c.estimate.title": "The estimate",
    "er.c.estimate.body": "The average analyst figure published before the report",
    "er.c3.title": "About the figures",
    "er.c3.body": "The consensus basis can differ from the official accounts",
    "er.img.title": "Reported vs analyst estimates",
    "er.img.sub.report": "Reported {report_img}",
    "er.img.eps": "EPS",
    "er.img.revenue": "Revenue",
    "er.img.note": "The estimate is the analyst consensus; its basis can differ from the official accounts.",
    # news-v23: the table — its column labels (left to right), a negative figure (U+2212, the picture's one minus),
    # the surprise cell (one decimal: (reported − estimate) / |estimate| × 100; "—" when no % is meaningful)
    "er.img.col.estimate": "Estimate",
    "er.img.col.reported": "Reported",
    "er.img.col.surprise": "Surprise",
    "er.img.neg": "−{x}",
    "er.img.sur.plus": "+{p}%",
    "er.img.sur.minus": "−{p}%",
    "er.img.sur.zero": "0.0%",
    "er.img.sur.none": "—",
    # news-v23: the narration (a line per company, then the basis note and the estimate's definition while lines are
    # left), the cards (one per line), the captions (a paragraph per company — every long caption carries them all)
    # (review 2026-11-20: no hook says the results are "on the analysts' basis" — since news-v23 they include the
    # revenue pair, whose estimate can be on another basis (GE: adjusted vs GAAP); the basis line is narrated instead)
    "er.m.hook": "{n} companies reported quarterly results against analyst estimates.",
    "er.m.l": "{co_sp} reported {eps} a share against an estimate of {est}, and revenue of {ra} against {re}.",
    "er.m.l.short": "{co_sp}: {eps} a share against {est}; revenue {ra} against {re}.",
    "er.m.l.eps": "{co_sp} reported {eps} a share against an analyst estimate of {est}.",
    "er.m.basis": "Each estimate is the analyst consensus; a reported figure can be on a different basis from the "
                  "official accounts.",
    "er.m.c.title": "{co}",
    "er.m.c.title.generic": "Reported vs estimates",
    "er.m.c.body": "EPS {eps_s} vs an estimate of {est_s} · revenue {ra_s} vs {re_s}",
    "er.m.c.body.named": "{co}: EPS {eps_s} vs an estimate of {est_s} · revenue {ra_s} vs {re_s}",
    # (review 2026-11-20: the caveat covers EVERY figure — revenue too, since news-v23 prints it — never "the EPS
    # basis" alone; under these heads the body leaves out `er.m.basis`, which says the same thing)
    "er.m.h.names.basis": "{names} reported results against analyst estimates; an estimate's basis can differ from "
                          "the official accounts.",
    "er.m.h.basis": "{n} companies reported results against analyst estimates; an estimate's basis can differ from "
                    "the official accounts.",
    "er.m.h.names": "{names} reported results against analyst estimates.",
    "er.m.h": "{n} companies reported results against analyst estimates.",
    "er.m.p": "{co} ({sym}): EPS of {eps_s} vs an analyst estimate of {est_s}, and revenue of {ra} vs an estimate of "
              "{re}.",
    "er.m.p.day": "Each company reported these figures on {day_cap}.",
    "er.m.p.window": "Each company reported these figures between {first_cap} and {last_cap}.",
    "er.m.yt.names": "{names}: results vs analyst estimates",
    "er.m.yt": "{n} companies: results vs analyst estimates",
    "er.m.alt": "The image compares reported results with analyst estimates for {n} companies: EPS and revenue, each "
                "beside its estimate and the gap as a percentage of the estimate.",
    "er.m.alt.rows": "{items}.",
    "er.m.alt.row": "{co}, EPS {eps_s} vs an estimate of {est_s}, revenue {ra_s} vs {re_s}",
    # Theme Explainer (2b-4). Each member's largest revenue line in its revenue breakdown — no performance,
    # momentum, ETF or AI-written text. ("sell" is a NEWS_BANNED_RE word: the hook asks where the revenue
    # comes from.) news-v13: the facts come from the same product breakdown as Money Map — product or
    # service lines for some companies (Apple's iPhone), reportable segments for others — so no string
    # calls a fact a "segment" or says it is "as each company reports them".
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
    # news-v16: a pair is two facts of ONE fiscal year, and says it (as the one-fact line does)
    "th.pair.poss.poss": "{m1_s} largest revenue line in fiscal {fy} was {g1}, and {m2_s} was {g2}.",
    "th.pair.poss.of": "{m1_s} largest revenue line in fiscal {fy} was {g1}, and that of {m2} was {g2}.",
    "th.pair.of.poss": "The largest revenue line of {m1} in fiscal {fy} was {g1}, and {m2_s} was {g2}.",
    "th.pair.of.of": "The largest revenue line of {m1} in fiscal {fy} was {g1}, and that of {m2} was {g2}.",
    # news-v12: the fact's own fiscal year — up to THEME_FACT_MAX_YEARS old, so never "its latest reported year"
    "th.one.poss": "{m1_s} largest revenue line in fiscal {fy} was {g1}.",
    "th.one.of": "The largest revenue line of {m1} in fiscal {fy} was {g1}.",
    # news-v19 (the review of 2026-10-16, TH-1): a member's breakdown can be regional (Costco's countries), so a
    # revenue line is a product, a business OR a region — "one product or business" was false for it — and a
    # region fact reads "the United States" in the narrated sentence (`th.region.the`, the Money Map rule)
    "th.explainer": "A revenue line is one product, business or region in a company's revenue breakdown.",
    "th.region.the": "the {name}",
    "th.grouping": "This grouping is Caydex's own; its members can change.",
    # news-v16: a card stays on screen for its whole line, so it keeps the line's fiscal year
    "th.c.pair.title": "{m1} and {m2}",
    "th.c.pair.body": "Fiscal {fy}: {g1} · {g2}",
    "th.c.pair.title.min": "Largest revenue lines",
    "th.c.pair.body.min": "Fiscal {fy} · {m1}: {g1} · {m2}: {g2}",
    "th.c.one.title": "{m1}",
    "th.c.one.body": "Largest revenue line in fiscal {fy}: {g1}",
    "th.c.one.title.min": "Largest revenue line",
    "th.c.one.body.min": "{m1}, fiscal {fy}: {g1}",
    "th.c.explainer.title": "What a revenue line is",
    "th.c.explainer.body": "One product, business or region in a revenue breakdown",
    "th.c.grouping.title": "A Caydex grouping",
    "th.c.grouping.body": "Its members can change",
    # news-v23 phase 3 (the weekly theme post, owner 2026-11-19; theme_ai_chips.jpg): the picture's sub line says what
    # each tile's line is — "{count} · each company's largest business" over "and its share of total revenue · fiscal
    # {fys}" (the break placed by hand: `sub_break`, never splitting "share of total revenue"); a tile reads
    # "<segment> · 88% of revenue" (a long filed name by its code-owned short label, THEME_SEGMENT_SHORT_LABELS; a fact
    # older than the sub line's years carries its own ", FY2022" tag — never implied current)
    "th.img.count": "{n} companies",
    "th.img.count.part": "{n} of its {m} companies",
    "th.mm.sub.lead": "{count} · each company's largest business",
    "th.mm.sub.tail": "and its share of total revenue · fiscal {fys}",
    "th.fys.2": "{a} or {b}",
    "th.img.seg.fy": "{seg}, FY{fy}",
    "th.img.line.share": "{seg} · {share} of revenue",
    "th.img.line": "{seg}",
    "th.img.more": "+{k} more",
    # "each one's" only when every listed member has its revenue-line fact in the post
    # news-v19 (the review of 2026-10-16, TH-1): "revenue line", as the explainer defines it — never "product or
    # business line", false for a member whose breakdown is regional
    "th.ch.long": "{theme}: where {n} companies get their revenue, by largest revenue line.",
    "th.ch.long.each": "{theme}: where {n} companies get their revenue, by each one's largest revenue line.",
    "th.ch.long.part": "{theme}: where {n} of its {m} companies get their revenue, by largest revenue line.",
    "th.ch.long.part.each": "{theme}: where {n} of its {m} companies get their revenue, by each one's largest "
                            "revenue line.",
    "th.ch.short": "{theme}: where {n} companies get their revenue.",
    "th.ch.short.part": "{theme}: where {n} of its {m} companies get their revenue.",
    # "{theme}: {n} companies." reads as the theme's size: offered only when the record says it is
    "th.ch.min": "{theme}: {n} companies.",
    "th.ch.min.part": "{theme}: {n} of its {m} companies.",
    # news-v23 phase 3: ONE caption paragraph per company, its figure with it (the "+N more" promises of the picture and
    # the cover: every caption that rides them lists the companies they leave out). The full filed segment name here,
    # never the tile's short label; every fact its own fiscal year.
    "th.mm.p.share": "{co}: {seg}, {share} of fiscal {fy} revenue.",
    "th.mm.p.seg": "{co}: {seg}, its largest revenue line in fiscal {fy}.",
    "th.mm.p.none": "{co} is also in {theme}.",
    # the SHORT form of each company paragraph (X, Bluesky, Threads, TikTok: a "+N more" promise must fit them) — the
    # tile's short label, the fiscal year as "FY2025"
    "th.mm.s.share": "{co}: {seg}, {share} of FY{fy} revenue",
    "th.mm.s.seg": "{co}: {seg}, FY{fy}",
    "th.mm.s.none": "{co}",
    "th.yt": "{theme}: where {n} companies get their revenue",
    "th.yt.short": "{theme}: {n} companies",
    "th.yt.part": "{theme}: where {n} of its {m} companies get their revenue",
    "th.yt.part.short": "{theme}: {n} of its {m} companies",
    "th.alt.title.min": "Companies in a theme",
    # the alt text: which companies (the whole-theme claim only when the record makes it), then how many
    # of the shown tiles carry their largest revenue line ("each one's" only when every tile does)
    "th.alt.body": "The image shows {k} of the {n} companies in {theme}.",
    "th.alt.body.all": "The image shows the {n} companies in {theme}.",
    "th.alt.body.some": "The image shows {k} companies in {theme}.",
    "th.alt.segs": "It gives the largest revenue line and its share of revenue in fiscal {fys} for {j} of them.",
    "th.alt.segs.each": "It gives each one's largest revenue line and its share of revenue in fiscal {fys}.",
    "th.alt.segs.tagged": "A line from an earlier fiscal year is marked with its year.",
    "th.alt.names": "Shown: {names}.",
    # news-v23 phase 3, angle 2 "who's biggest" (theme_biggest_power.jpg): the members ranked by their dated MARKET VALUE
    # (owner 2026-09-28: market cap may be shown; never a price, a % move or a verdict — "largest" is the size fact,
    # never "biggest", "leader" or "top"). The picture: rank, logo, ticker chip, name, a bar in proportion and the value.
    "th.big.sub": "{count} · market value as of {date_img}",
    "th.big.hook": "{theme}: its {n} companies, ranked by market value.",
    "th.big.hook.part": "{theme}: {n} of its {m} companies, ranked by market value.",
    "th.big.hook.min": "{theme}, ranked by market value.",
    "th.big.l1": "{co1} had the largest market value, about {v1}, on {day_sp}.",
    "th.big.l1.short": "{co1} had the largest market value, about {v1}.",
    "th.big.l2": "{co2} came next at about {v2}, then {co3} at about {v3}.",
    "th.big.l2.short": "{co2} came next, at about {v2}.",
    "th.big.l3": "A company's market value is the value of all of its shares together.",
    "th.big.c1.title": "Largest market value",
    "th.big.c1.body": "{co1}: {v1_img} on {date_img}",
    "th.big.c2.title": "Second and third",
    "th.big.c2.body": "{co2}: {v2_img} · {co3}: {v3_img}",
    "th.big.c3.title": "What market value is",
    "th.big.c3.body": "All of a company's shares, valued together",
    "th.big.ch.long": "{theme}: its {n} companies by market value, as of {date_cap}.",
    "th.big.ch.long.part": "{theme}: {n} of its {m} companies by market value, as of {date_cap}.",
    "th.big.ch.short": "{theme}: {n} companies by market value.",
    "th.big.ch.short.part": "{theme}: {n} of its {m} companies by market value.",
    # the caption rows carry no rank number: the order and the headline ("by market value") say it, and a "14." would
    # read as a sentence of its own beside a headline that counts "14" companies (the picture draws the rank)
    "th.big.p.row": "{co}: about {v}.",
    "th.big.p.short": "{co}: {v}",
    "th.big.p.explainer": "Market value is the value of all of a company's shares together, as of {date_cap}.",
    "th.big.yt": "{theme}: {n} companies by market value",
    "th.big.yt.part": "{theme}: {n} of its {m} companies by market value",
    "th.big.alt.body": "The image ranks {k} of the {n} companies in {theme} by market value as of {date_img}.",
    "th.big.alt.body.all": "The image ranks the {n} companies in {theme} by market value as of {date_img}.",
    "th.big.alt.body.some": "The image ranks {k} companies in {theme} by market value as of {date_img}.",
    # news-v23 phase 3, angle 3 "growth + profit" (theme_growth_profit_silicon.jpg; owner 2026-11-19 combined "Profit
    # check" and "Growth check"): each member's latest fiscal year — its revenue against the year before's and its net
    # income per $100 of revenue — in the THEME's order (no ranking word). A growth figure is a revenue fact, never a
    # price move: a "%" stands only on the picture's growth cell ("+114%", `_GROWTH_CELL_FIELD_RE`) and in the one
    # phrase "revenue … % higher / lower than the year before" (`_GROWTH_PHRASE_RE`), on this angle only.
    "th.gp.sub": "{count} · revenue growth and profit · latest fiscal year",
    "th.gp.head.growth": "Revenue growth",
    "th.gp.head.profit.1": "Profit per $100",
    "th.gp.head.profit.2": "of revenue",
    "th.gp.note": "Profit = net income; growth = revenue vs the year before",
    "th.gp.up": "{g}% higher than the year before",
    "th.gp.down": "{g}% lower than the year before",
    "th.gp.flat": "about the same as the year before",
    "th.gp.keep": "{p} of profit per $100 of revenue",
    "th.gp.loss": "a loss of {p} per $100 of revenue",
    "th.gp.zero": "no profit and no loss",
    "th.gp.keep.short": "{p} profit per $100",
    "th.gp.loss.short": "{p} loss per $100",
    "th.gp.hook": "{theme}: revenue growth and profit at {n} companies.",
    "th.gp.hook.part": "{theme}: revenue growth and profit at {n} of its {m} companies.",
    "th.gp.hook.min": "{theme}: revenue growth and profit, company by company.",
    "th.gp.l": "At {co}, fiscal {fy} revenue was {growth}, with {profit}.",
    "th.gp.l.short": "{co}: revenue {growth}, and {profit}.",
    "th.gp.l.min": "{co}: fiscal {fy} revenue was {growth}.",
    "th.gp.l3": "Profit here is net income; growth compares each company's latest fiscal year with the year before.",
    "th.gp.l3.short": "Profit is net income; growth compares revenue with the year before.",
    "th.gp.c.title.min": "Revenue growth and profit",
    "th.gp.c.body": "Fiscal {fy} revenue {growth}; {profit}",
    "th.gp.c.body.min": "{co}, fiscal {fy}: revenue {growth}; {profit}",
    "th.gp.c3.title": "What the figures are",
    "th.gp.c3.body": "Profit is net income per $100 of revenue; growth compares revenue with the year before",
    "th.gp.ch.long": "{theme}: revenue growth and profit at {n} companies, each in its latest fiscal year.",
    "th.gp.ch.long.part": "{theme}: revenue growth and profit at {n} of its {m} companies, each in its latest fiscal "
                          "year.",
    "th.gp.ch.short": "{theme}: revenue growth and profit at {n} companies.",
    "th.gp.ch.short.part": "{theme}: revenue growth and profit at {n} of its {m} companies.",
    "th.gp.p.row": "{co}: fiscal {fy} revenue {growth}; {profit}.",
    "th.gp.p.short": "{co}: revenue {g_img}, {profit}",
    "th.gp.p.explainer": "Growth compares each company's latest fiscal year of revenue with the year before; profit is "
                         "its net income for every $100 of that revenue.",
    "th.gp.yt": "{theme}: revenue growth and profit at {n} companies",
    "th.gp.yt.part": "{theme}: revenue growth and profit at {n} of its {m} companies",
    "th.gp.alt.body": "The image lists {k} of the {n} companies in {theme}, each with its revenue growth over the year "
                      "before and its net income per $100 of revenue, in its latest fiscal year.",
    "th.gp.alt.body.all": "The image lists the {n} companies in {theme}, each with its revenue growth over the year "
                          "before and its net income per $100 of revenue, in its latest fiscal year.",
    "th.gp.alt.body.some": "The image lists {k} companies in {theme}, each with its revenue growth over the year "
                           "before and its net income per $100 of revenue, in its latest fiscal year.",
    "th.fys.span": "{a} to {b}",
    # news-v23 phase 3, angle 4 "insider scorecard" (theme_insiders_cyber.jpg; owner 2026-11-19: "remove 3 companies and
    # 9 companies cards. Make the Disclosed Purchase and Disclosed sales more clearly. use color too. green and red."):
    # each listed member's Form 4 purchases (code P) and sales (code S) filed in the 90 days before the run, in dollars as
    # filed — class C wording ("disclosed purchases / sales", never "bought" / "sold"), company totals only: never a
    # person, a role or a name of anyone. "—" stands in an empty cell. Never "open-market" (compliance F2: code P / S is
    # "open market OR private" and the feed cannot tell them apart — the mock's "Open-market trades only" note is
    # reworded "Purchases and sales only").
    "th.in.sub": "{count} · insider filings · last 90 days",
    "th.in.head.1": "Disclosed",
    "th.in.head.buy": "purchases",
    "th.in.head.sell": "sales",
    "th.in.note": "Purchases and sales only · sales can be pre-planned (10b5-1)",
    "th.in.more": "+{k} more with filings",
    "th.in.hook": "{theme}: insider purchases and sales disclosed in the last 90 days.",
    "th.in.hook.min": "{theme}: insider purchases and sales, company by company.",
    "th.in.l1": "Insiders at {k} of the {c} companies checked disclosed purchases of their stock, the most at {co}, "
                "about {amt}.",
    "th.in.l1.short": "Insiders at {k} of the {c} companies checked disclosed purchases of their stock.",
    "th.in.l1.one": "Insiders at one of the {c} companies checked, {co}, disclosed purchases of its stock, about {amt}.",
    "th.in.l1.one.short": "Insiders at one company checked, {co}, disclosed purchases of its stock.",
    "th.in.l1.none": "No insider at the {c} companies checked disclosed a purchase of the stock.",
    "th.in.l2": "Insiders at {k} of the {c} companies disclosed sales of their stock, the most at {co}, about {amt}.",
    "th.in.l2.short": "Insiders at {k} of the {c} companies checked disclosed sales of their stock.",
    "th.in.l2.one": "Insiders at one of the {c} companies checked, {co}, disclosed sales of its stock, about {amt}.",
    "th.in.l2.one.short": "Insiders at one company checked, {co}, disclosed sales of its stock.",
    "th.in.l2.none": "No insider at the {c} companies checked disclosed a sale of the stock.",
    "th.in.l3": "Only purchases and sales count here, and sales can be pre-planned under Rule 10b5-1.",
    "th.in.c1.title": "Disclosed purchases",
    "th.in.c2.title": "Disclosed sales",
    "th.in.c.one": "{co}: {amt}",
    "th.in.c.two": "{co}: {amt} · {co2}: {amt2}",
    "th.in.c.none": "None in the last 90 days",
    "th.in.c3.title": "Purchases and sales only",
    "th.in.c3.body": "Sales can be pre-planned under Rule 10b5-1",
    "th.in.ch.long": "{theme}: insider purchases and sales disclosed on Form 4, filed {window_cap}.",
    "th.in.ch.short": "{theme}: insider purchases and sales disclosed on Form 4.",
    "th.in.p.both": "{co}: about {b} of disclosed purchases and about {s} of disclosed sales.",
    "th.in.p.buy": "{co}: about {b} of disclosed purchases.",
    "th.in.p.sell": "{co}: about {s} of disclosed sales.",
    "th.in.s.both": "{co}: purchases {b}, sales {s}",
    "th.in.s.buy": "{co}: purchases {b}",
    "th.in.s.sell": "{co}: sales {s}",
    "th.in.p.how": "The totals are Form 4 purchases (code P) and sales (code S) of each company's stock filed "
                   "{window_cap}, in dollars as filed: shares times each line's per-share amount.",
    "th.in.p.plans": "Sales can be pre-planned under Rule 10b5-1; awards, option exercises, gifts and tax withholding "
                     "are not counted.",
    "th.in.p.quiet": "No purchase or sale of the stock was disclosed at the other {k} companies checked.",
    "th.in.p.quiet.one": "No purchase or sale of the stock was disclosed at the other company checked.",
    # fix round 2026-11-20: no reason given — the {u} companies are a mix of members never read (past the 16 lookups, a
    # name the table cannot draw, a listing gate, a foreign issuer that files no Form 4) and reads that could not be
    # used (failed, partial, empty, a Form 4/A); "could not be fully checked" was false for the first group
    "th.in.p.unchecked": "{u} of its {m} companies are not included.",
    "th.in.yt": "{theme}: insider purchases and sales disclosed on Form 4",
    "th.in.alt.body": "The image lists {k} companies in {theme} with insider purchases or sales of their stock "
                      "disclosed on Form 4 in the last 90 days, each total in dollars as filed.",
    # news-v23 phase 3, angle 5 "Congress" (theme_congress_defense.jpg; owner 2026-11-19: "use green color for
    # 'purchases'. also, numbers like 6,4,3,2,2, use green color"): the theme's companies whose stock at least two
    # DISTINCT members of Congress disclosed purchases of in one disclosure month — the Congress Count's own counts and
    # words (`cg.*`), never a member's name, party, state or chamber; never "bought", never a dollar figure
    "th.cg.sub": "Members of Congress who disclosed purchases",
    "th.cg.unit": "members",
    "th.cg.note": "{month_long} · each member counted once per company",
    "th.cg.hook": "{theme}: stock purchases disclosed by members of Congress in {month_name}.",
    "th.cg.hook.min": "{theme}: stock purchases disclosed by members of Congress.",
    "th.cg.l1": "{n} members of Congress disclosed purchases of {co_sp} stock in {month_long}.",
    "th.cg.c1.title": "{co}",
    "th.cg.c1.title.min": "Disclosed purchases",
    "th.cg.c1.body": "{n} members disclosed purchases in {month_long}",
    "th.cg.c1.body.min": "{co}: {n} members, {month_long}",
    "th.cg.ch.long": "{theme}: stock purchases disclosed by members of Congress in {month_long}, company by company.",
    "th.cg.ch.short": "{theme}: stock purchases disclosed by members of Congress in {month_long}.",
    "th.cg.s": "{co}: {n} members",
    "th.cg.p.floor": "Only companies with purchases disclosed by at least two members of Congress are listed.",
    "th.cg.yt": "{theme}: stock purchases disclosed by members of Congress in {month_long}",
    "th.cg.alt.body": "The image lists {k} companies in {theme} and how many members of Congress disclosed purchases of "
                      "each one's stock in {month_long}.",
    # The insider LIST (the spotlight lane; owner decisions 2026-11-20 — "each post or each image/video will have more
    # than 2,3, ideally are 3 - 5 companies at once"; it replaces the one-issuer Insider Spotlight of news-v22):
    # INSIDER_MIN_ROWS to INSIDER_MAX_ROWS companies, each with its insiders by ROLE ("CEO + at least 2 other insiders"
    # — a lower bound: the adapter's gates drop people), their TOTAL in GREEN under "Total purchases", and the filing's
    # own cost per share ONLY on the picture's grey detail line and in a caption paragraph ("… a share, as filed") —
    # never the word "price" (NEWS_BANNED_RE), never narrated, never on a video card, the cover, the alt text or the
    # YouTube title. Names and roles only in the narration; a person's name only in a caption paragraph.
    "isp.img.title": "Insiders at {n} companies disclosed buying stock",
    "isp.img.heading": "Total purchases",
    "isp.who.named": "{who}, {person},",
    "isp.who.label.plus.one": "{who} + at least one other insider",
    "isp.who.label.plus.many": "{who} + at least {m} other insiders",
    "isp.who.np.plus.one": "{who} and at least one other insider",
    "isp.who.np.plus.many": "{who} and at least {m} other insiders",
    "isp.who.sp.plus.one": "{who} and at least one other insider",
    "isp.who.sp.plus.many": "{who} and at least {m} other insiders",
    "isp.sp.ceo": "its chief executive",
    "isp.sp.cfo": "its chief financial officer",
    "isp.sp.director": "a director",
    # "a share", never "per share" (the public-copy scan reads "per share" as a valuation metric, class B)
    "isp.detail.one": "{shares_s} {share_noun} · {cost_s} a share, as filed",
    "isp.detail.range": "{shares_s} {share_noun} · {cost_min_s}–{cost_max_s} a share, as filed",
    "isp.alt.rows": "The image lists each company's total purchases by its insiders: {rows}.",
    "isp.alt.row": "{co}, {who}, {amt_s}",
    "isp.hook": "Insiders at {n} companies disclosed buying their own company's stock.",
    "isp.hook.short": "Insiders at {n} companies disclosed buying stock.",
    "isp.l1": "The largest total is at {co_sp}: {amt}, by {who_sp}.",
    "isp.l1.short": "The largest total disclosed is at {co_sp}: {amt}.",
    "isp.l2": "Insiders also disclosed purchases at {list}.",
    "isp.l3.indirect.all": "The shares are reported as held indirectly, not in the buyers' own names.",
    "isp.c1.title": "Largest total",
    "isp.c1.body": "{co} · {who} · {amt_s}",
    "isp.c2.title": "Also on the list",
    "isp.c3.indirect.all.body": "Not in the buyers' own names, per the filings",
    "isp.h.long": "Insiders at {n} companies disclosed buying their own company's stock in Form 4s filed {window_cap}. "
                  "The largest total: {co} ({sym}), {amt}.",
    "isp.h.short": "Insiders at {n} companies disclosed buying their own company's stock in Form 4s filed "
                   "{window_cap}.",
    "isp.h.min": "Insiders at {n} companies disclosed buying their own company's stock.",
    "isp.p.row.one": "{co} ({sym}): {who} disclosed buying {amt} of stock, {shares_w} {share_noun} at {cost_s} a share, "
                     "as filed.",
    "isp.p.row.range": "{co} ({sym}): {who} disclosed buying {amt} of stock, {shares_w} {share_noun} at {cost_min_s} "
                       "to {cost_max_s} a share, as filed.",
    "isp.p.question": "Which detail of a Form 4 do you read first: who filed, the trade dates or the amount?",
    # the companies the picture and the cover leave out, in ONE short paragraph (a tight caption's "+N more" promise)
    "isp.p.more": "More purchases: {rows}.",
    "isp.p.more.row": "{co} ({sym}), {amt}",
    "isp.yt": "Insiders at {n} companies disclosed buying their own company's stock",
    "isp.yt.min": "Insiders at {n} companies disclosed buying stock",
    # The 13F LIST (news-v23, the restyle — owner 2026-11-19: "i need more companies, similar with Berkshire hathaway
    # latest 13F. and remove what users don't care such as quarter before, quarter ended" / "the range from 2 to 6 are
    # okay"): ONE design for both 13F series (`_f13_list_parts`), the single-holding post gone. Two to six holdings of ONE
    # filer's latest 13F on the picture, the largest dollar size first, the direction on every row; a record holding
    # more says "+N more changes in the caption" and every caption riding the picture lists them. SHARE COUNTS only — no
    # %, no dollar figure, never "sold" / "exited" / "dumped" (NEWS_BANNED_RE / OFF_TABLE_VERBS_RE, unchanged). A holding
    # the latest 13F no longer lists is "Closed out" (owner: "Closed out + buy-out check") — only after the adapter's
    # buy-out check dropped every holding whose company stopped trading during or after the quarter. "reduced" /
    # "increased" / "closed out" compare the HOLDING at the two quarter ends the filing and the one before it report.
    # Its counts are stated AS THE FILING'S ("it reduced 2 holdings") only when the rows are every change its filing
    # reports (`company_news_rules.thirteen_f_spotlight_lists_every_change`; never the roundup's); otherwise the copy
    # says "{n} of the holding changes" — never a count that reads as the whole filing, and never "largest" (the rows
    # are ranked by dollar size and drawn in shares). The subject is the filing ENTITY.
    "f13s.kicker": "13F FILING",
    "f13s.p.period": "The filing covers the quarter that ended {period_cap} and was filed on {filed_cap}.",
    "f13s.p.period.amended": "The filing covers the quarter that ended {period_cap}; it was filed on {filed_cap} and "
                             "amended on {amended_cap}.",
    "f13s.p.question": "What do you look at first in a 13F: new holdings, or changes in share counts?",
    # the picture (`rows`, f13_list_*.jpg) and the cover (cover_13f_berkshire.jpg): the column label, each row's status
    # line (the ticker is its chip) and its signed figure (U+2212 for a decrease), and the "+N more" line
    "f13s.list.heading": "Change in shares",
    "f13s.list.row.fewer": "Reduced to {shares_s} {share_noun}",
    "f13s.list.row.more": "Increased to {shares_s} {share_noun}",
    "f13s.list.row.new": "New holding",
    "f13s.list.row.gone": "Closed out",
    "f13s.list.fig.minus": "−{diff_s}",
    "f13s.list.fig.plus": "+{diff_s}",
    "f13s.list.more.one": "+1 more change in the caption",
    "f13s.list.more.many": "+{k} more changes in the caption",
    # where the changes are: the possessive form and its ".of" twin, long and short (hooks, captions)
    "f13s.list.in.poss": "{filer_s} latest 13F",
    "f13s.list.in.poss.short": "{filer_s} 13F",
    "f13s.list.in.of": "the latest 13F of {filer}",
    "f13s.list.in.of.short": "the 13F of {filer}",
    # the video's kind cards (one per narrated kind)
    "f13s.list.head.new.one": "New holding",
    "f13s.list.head.new.many": "New holdings",
    "f13s.list.head.more": "Increased",
    "f13s.list.head.fewer": "Reduced",
    "f13s.list.head.gone": "Closed out",
    # the hook: every change counted (the filing's own), its verbs; else "{n} of the holding changes"
    "f13s.list.cl.fewer.first": "reduced {n} {holdings}",
    "f13s.list.cl.more.first": "increased {n} {holdings}",
    "f13s.list.cl.new.first": "reported {n} new {holdings}",
    "f13s.list.cl.gone.first": "closed out {n} {holdings}",
    "f13s.list.cl.fewer": "reduced {n}",
    "f13s.list.cl.more": "increased {n}",
    "f13s.list.cl.new": "reported {n} new {ones}",
    "f13s.list.cl.gone": "closed out {n}",
    "f13s.list.one": "one",
    "f13s.list.ones": "ones",
    "f13s.list.join.2": "{a} and {b}",
    "f13s.list.join.3": "{a}, {b}, and {c}",
    "f13s.list.join.4": "{a}, {b}, {c}, and {d}",
    "f13s.list.hook.all": "{subj_sp}: it {clauses}.",
    "f13s.list.hook.all.counts": "{subj_sp}: {counts}.",
    # a long filer name past HOOK_WORDS: the first two kinds' counts, then the first kind's (review 2026-11-19)
    "f13s.list.hook.all.incl": "{n} holding changes in {where}, including {incl}.",
    "f13s.list.hook.all.n": "{n} holding changes in {where}.",
    # "some": the rows' counts, then — past HOOK_WORDS — the first two kinds' counts ("including 2 reduced and 1 new"),
    # then the first kind's alone (the after-prefix twins `f13s.list.hp.some.*` sit before them), and only last the
    # counts-free forms
    "f13s.list.hook.some.counts": "{n} of the holding changes in {where}: {counts}.",
    "f13s.list.hook.some.incl.poss": "{n} of {filer_s} holding changes, including {incl}.",
    "f13s.list.hook.some.incl.of": "{n} of the holding changes by {filer}, including {incl}.",
    "f13s.list.hook.some": "{n} of the holding changes in {where}.",
    # the narration: one line per kind in row order, its holdings named (the first three kinds; every holding is on the
    # picture or in the captions)
    "f13s.list.l.fewer.one": "Compared with the quarter before, it reduced its {list} holding.",
    "f13s.list.l.fewer.many": "Compared with the quarter before, it reduced its holdings of {list}.",
    "f13s.list.l.more.one": "Compared with the quarter before, it increased its {list} holding.",
    "f13s.list.l.more.many": "Compared with the quarter before, it increased its holdings of {list}.",
    "f13s.list.l.new.one": "Compared with the quarter before, it reported a new {list} holding.",
    "f13s.list.l.new.many": "Compared with the quarter before, it reported new holdings of {list}.",
    "f13s.list.l.gone.one": "Compared with the quarter before, it closed out its {list} holding.",
    "f13s.list.l.gone.many": "Compared with the quarter before, it closed out its holdings of {list}.",
    "f13s.list.l.fewer.one.next": "Compared with the quarter before, it also reduced its {list} holding.",
    "f13s.list.l.fewer.many.next": "Compared with the quarter before, it also reduced its holdings of {list}.",
    "f13s.list.l.more.one.next": "Compared with the quarter before, it also increased its {list} holding.",
    "f13s.list.l.more.many.next": "Compared with the quarter before, it also increased its holdings of {list}.",
    "f13s.list.l.new.one.next": "Compared with the quarter before, it also reported a new {list} holding.",
    "f13s.list.l.new.many.next": "Compared with the quarter before, it also reported new holdings of {list}.",
    "f13s.list.l.gone.one.next": "Compared with the quarter before, it also closed out its {list} holding.",
    "f13s.list.l.gone.many.next": "Compared with the quarter before, it also closed out its holdings of {list}.",
    "f13s.list.l.fewer.one.short": "It reduced its {list} holding in this filing.",
    "f13s.list.l.fewer.many.short": "It reduced its holdings of {list}.",
    "f13s.list.l.more.one.short": "It increased its {list} holding in this filing.",
    "f13s.list.l.more.many.short": "It increased its holdings of {list}.",
    "f13s.list.l.new.one.short": "It reported a new {list} holding in this filing.",
    "f13s.list.l.new.many.short": "It reported new holdings of {list}.",
    "f13s.list.l.gone.one.short": "It closed out its {list} holding in this filing.",
    "f13s.list.l.gone.many.short": "It closed out its holdings of {list}.",
    # the captions' first line: the filing's own counts (".all", the first kind carrying the noun), else "{n} of the
    # holding changes" with the rows' counts; "min" drops the counts
    "f13s.list.c.fewer.first": "{n} {holdings} reduced",
    "f13s.list.c.more.first": "{n} {holdings} increased",
    "f13s.list.c.new.first": "{n} new {holdings}",
    "f13s.list.c.gone.first": "{n} {holdings} closed out",
    "f13s.list.c.fewer": "{n} reduced",
    "f13s.list.c.more": "{n} increased",
    "f13s.list.c.new": "{n} new",
    "f13s.list.c.gone": "{n} closed out",
    "f13s.list.h.all": "{subj}: {counts}.",
    "f13s.list.h.all.min": "{subj}: {n} holding changes.",
    "f13s.list.h.some": "{n} of the holding changes in {where}: {counts}.",
    "f13s.list.h.some.min": "{n} of the holding changes in {where}.",
    # the AFTER-PREFIX twins ("📊 13F filing:" says "13F"): the filer the subject of the hook's verbs; the same facts
    "f13s.list.hp.all": "{filer} {clauses}.",
    "f13s.list.hp.all.min": "{n} holding changes by {filer}.",
    "f13s.list.hp.some.poss": "{n} of {filer_s} holding changes: {counts}.",
    "f13s.list.hp.some.of": "{n} of the holding changes by {filer}: {counts}.",
    "f13s.list.hp.some.min.poss": "{n} of {filer_s} holding changes.",
    "f13s.list.hp.some.min.of": "{n} of the holding changes by {filer}.",
    # one caption paragraph per holding, in the picture's order (the first more / fewer row says "the quarter before");
    # share counts only — a new holding's filed value is no longer written (news-v23: "share counts only")
    "f13s.list.p.fewer": "{co} ({sym}): reduced to {shares_w} {share_noun}, from {prev_w} the quarter before.",
    "f13s.list.p.more": "{co} ({sym}): increased to {shares_w} {share_noun}, from {prev_w} the quarter before.",
    "f13s.list.p.fewer.next": "{co} ({sym}): reduced to {shares_w} {share_noun}, from {prev_w}.",
    "f13s.list.p.more.next": "{co} ({sym}): increased to {shares_w} {share_noun}, from {prev_w}.",
    "f13s.list.p.new": "{co} ({sym}): a new holding of {shares_w} {share_noun}.",
    "f13s.list.p.gone": "{co} ({sym}): closed out, from {prev_w} {prev_noun} the quarter before.",
    "f13s.list.yt": "{subj}: {n} holding changes",
    "f13s.list.yt.some": "{subj}: {n} of its holding changes",
    "f13s.list.yt.min": "13F filing: {n} holding changes",
    "f13s.list.yt.min.some": "13F filing: {n} of the holding changes",
    # the alt text: what the PICTURE lists (name-free of persons: the filer is the entity, the rows companies)
    "f13s.list.alt.title.min": "Holding changes in a 13F filing",
    "f13s.list.alt.all": "It lists the filing's {n} holding changes from the quarter before: {counts}.",
    "f13s.list.alt.some": "It lists {n} of the filing's holding changes from the quarter before: {counts}.",
    "f13s.list.alt.rounded": "Share counts are rounded.",
    "f13s.list.alt.row.fewer": "{co}, reduced to {shares_s} {share_noun} from {prev_s}",
    "f13s.list.alt.row.more": "{co}, increased to {shares_s} {share_noun} from {prev_s}",
    "f13s.list.alt.row.new": "{co}, a new holding of {shares_s} {share_noun}",
    "f13s.list.alt.row.gone": "{co}, closed out, from {prev_s} {prev_noun}",
    "f13s.list.alt.rows": "{items}.",
    "f13s.list.alt.more.one": "1 more change is listed in the caption.",
    "f13s.list.alt.more.many": "{k} more changes are listed in the caption.",
}


#: A plain slot directly before a period of the entry: "… was {g2}." (the re-review of 2026-10-19, TH-PUNCT-1).
_SLOT_PERIOD_RE = re.compile(r"\{([A-Za-z0-9_]+)\}\.")


def _t(entry_id: str, **fills: Any) -> str:
    """LEXICON[entry_id] with its slots filled. A missing slot is a bug (KeyError), never a gap. A filled value that
    already ENDS in a period ("Walmart U.S.", "the U.S.") and stands before the entry's own period ends the sentence
    itself (the re-review of 2026-10-19, TH-PUNCT-1: "… and Walmart's was Walmart U.S.." was narrated and burned
    into the captions) — the lexicon entry is unchanged, only the doubled stop is not written."""
    template = LEXICON[entry_id]
    if "}." in template:
        template = _SLOT_PERIOD_RE.sub(
            lambda m: ("{" + m.group(1) + "}" if isinstance(fills.get(m.group(1)), str)
                       and fills[m.group(1)].endswith(".") else m.group(0)), template)
    return template.format_map(fills)


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
    rolled up; a negative value gets a leading U+2212 MINUS, "−$4.3B" — every drawn negative figure's sign, the
    earnings table's and the theme's alike; visual-fidelity round 2026-11-20: a Money Map's eliminations drew an
    ASCII "-"). Plain text (the alt text) takes `_alt_money`, which gives the hyphen back."""
    d = _dec(value)
    sign = onscreen.MINUS if d < 0 else ""
    a = abs(d)
    if _q(a, 0) < 1000:
        return f"{sign}${_q(a, 0):,}"
    scaled, suffix = _scaled(a, _SHORT_UNITS, 1)
    return f"{sign}${_one_decimal(scaled)}{suffix}"


def _alt_money(image_value: str) -> str:
    """A drawn money figure in its plain-text (alt text) form: U+2212 back to an ASCII "-" ("−$4.3B" → "-$4.3B")."""
    return image_value.replace(onscreen.MINUS, "-")


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


#: The cost per share is printed AS FILED (review of 2026-11-16): every decimal place the Form 4 line carries, two at
#: least ("$142.10", "$142.1234", "$0.004"), never rounded under the words "as filed" — a weighted-average price filed
#: as $142.105 printed "$142.11", a sub-dollar $0.125 printed "$0.13" (4% off), and two different filed prices that
#: rounded alike were written as ONE price. A cost the filing states to more than COST_MAX_PLACES places cannot be
#: printed as filed in a post: `cost_pair` refuses it (`implausible_figures` — that candidate, never the day).
COST_MAX_PLACES = 4


def _cost_places(value: float) -> int:
    """The decimal places of the cost AS FILED — its shortest round-trip form (`_dec`: the JSON text FMP sent), two at
    least: 142.1 → 2, 142.1234 → 4, 0.004 → 3, 100.0 → 2."""
    exponent = abs(_dec(value)).normalize().as_tuple().exponent
    return max(2, -exponent) if isinstance(exponent, int) else 2


def cost_image(value: float) -> str:
    """A Form 4's own cost per share, as filed (news-v22, `InsiderPurchase.cost_per_share_min_usd` / ``_max_usd``):
    "$142.10", "$1,142.1234", "$0.004" — every place the filing carries (two at least, `_cost_places`), never
    rounded; one form for the captions and the picture's facts (it is never narrated). `cost_pair` refuses a cost
    filed to more than COST_MAX_PLACES places before anything is written."""
    d = abs(_dec(value))
    return f"${d:,.{_cost_places(value)}f}"


def cost_pair(low: float, high: float) -> Tuple[str, Optional[str]]:
    """(low, high) as written by `cost_image`, high None only when the two ends are the SAME filed figure — a range
    whose ends both read "$142.10" is written once ("at $142.10 a share"), never "$142.10 to $142.10"; two filed prices
    that differ are always a range ("$142.101 to $142.104"), never one rounded price. `implausible_figures` when either
    end is not a positive figure, is filed to more than COST_MAX_PLACES decimal places (it cannot be printed as filed),
    or the low end is above the high one."""
    for v in (low, high):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not v > 0 or v != v or v == float("inf"):
            raise NewsTemplateRefused("implausible_figures", "a cost per share that is not a positive figure")
    if any(_cost_places(v) > COST_MAX_PLACES for v in (low, high)):
        raise NewsTemplateRefused("implausible_figures",
                                  f"a cost per share filed to more than {COST_MAX_PLACES} decimal places "
                                  f"(it cannot be printed as filed)")
    a, b = _dec(low), _dec(high)
    if a > b:
        raise NewsTemplateRefused("implausible_figures", "the lowest cost per share is above the highest")
    return cost_image(low), (None if a == b else cost_image(high))


#: Earnings revenue (review round 9): the reported figure and the estimate are written as a PAIR, in
#: one unit (the larger figure's) at one precision — never each rounded on its own, which printed
#: $1.149B vs $1.15B as "$1.1 billion vs $1.2 billion" (a shortfall of about 8% where the true one is
#: 0.09%) and $1.18B vs $1.15B as "$1.2 billion vs $1.2 billion" (a 2.6% gap hidden). The decimals
#: grow from 1 to REVENUE_MAX_PLACES until the gap the pair SHOWS is within REVENUE_GAP_TOLERANCE of
#: the true gap (so it has the true sign, reads equal only when the figures are equal, and is not
#: far off in size); when no precision does, `revenue_pair` is None and the post leaves revenue out.
#: news-v21: written only while `EARNINGS_REVENUE_IN_POSTS` is on (off: the estimate's basis is unknown).
REVENUE_MAX_PLACES = 3
REVENUE_GAP_TOLERANCE = Decimal("0.1")
#: The pair's units (the image's suffix, the words' unit): the same scale in both forms. A pair
#: whose larger figure is under $1 million is never written (the word form would be whole dollars).
_PAIR_UNITS: Tuple[Tuple[Decimal, str, str], ...] = (
    (Decimal(10) ** 6, "M", "million"), (Decimal(10) ** 9, "B", "billion"), (Decimal(10) ** 12, "T", "trillion"))


def _pair_figure(scaled: Decimal, places: int) -> str:
    # every place kept, so both figures of the pair show the same precision ("$1.150 billion" vs "$1.149 billion")
    return f"{scaled:,.{places}f}"


def _pair_figures(sa: Decimal, se: Decimal, places: int) -> Tuple[str, str]:
    """The pair's two figures at ONE visible precision: at one decimal the ".0" is dropped only when BOTH end in it
    ("$860M" / "$880M"; never "$7B" beside "$7.1B" — fidelity review 2026-11-20: the approved earnings mocks print
    each pair alike), else every place is kept ("$7.0B" / "$7.1B")."""
    fa, fe = _pair_figure(sa, places), _pair_figure(se, places)
    if places == 1 and fa.endswith(".0") and fe.endswith(".0"):
        return fa[:-2], fe[:-2]
    return fa, fe


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
            fa, fe = _pair_figures(sa, se, places)
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
    keys = ("a", "b", "c", "d")
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
#: news-v16 (review round 16): no stake string — above all the hand-written catalogue BACKGROUND
#: (`_stake_background`, the first caption paragraph) — states a company's worth by a verb or ranks a
#: company (rules §1 "Companies": never a verdict, record or worth claim). The noun forms are caught
#: elsewhere ("valued at" / "valuation": compliance's valuation rows; "worth": STAKES_BANNED_RE; "record":
#: NEWS_BANNED_RE); "which values Relecloud at $90 billion", "the largest AI startup in Europe" and "the
#: fastest-growing cloud company" passed every one. Each row is POSITIONAL — a frame, never a bare word
#: ("Market value was $9.21B", "the largest tranche closed on …" and "the strategic lead of …" stay) —
#: matched on `fold()` with the stake's names masked, and has a must-reject sample only it matches and an
#: honest twin it leaves (`STAKE_CLAIM_ROW_SAMPLES` in tests/test_marketing_news_templates.py). Bounded
#: repetitions only: linear on any input.
_STAKE_AMOUNT = (r"(?:[a-z]{0,3}\$\s?[0-9]|(?:usd|eur|gbp|jpy|krw|inr|sar|chf|cny|rmb|hkd)\s?[0-9]|"
                 r"[0-9][0-9.,]{0,15}\s?(?:trillion|billion|million|thousand|tn|bn|mn|[tbmk])\b)")
_STAKE_SUPERLATIVE = (r"(?:largest|biggest|greatest|richest|wealthiest|strongest|fastest(?:[- ]growing)?|"
                      r"most[- ](?:valuable|successful|profitable|innovative|advanced|powerful|important|"
                      r"influential)|highest[- ]valued|leading(?!\s+(?:the|a|an|its|their|his|her)\b)|top|"
                      r"dominant|foremost|premier|best|no\.\s?1|number[- ]one|(?:world|market|industry)[- ]leading)")
_STAKE_COMPANY_NOUN = (r"(?:compan(?:y|ies)|start-?ups?|firms?|[a-z]{0,12}makers?|providers?|suppliers?|"
                       r"manufacturers?|producers?|developers?|labs?|laborator(?:y|ies)|players?|"
                       r"business(?:es)?|brands?|platforms?|banks?|lenders?|insurers?|operators?|retailers?|"
                       r"vendors?|carriers?|utilit(?:y|ies)|foundr(?:y|ies)|unicorns?|enterprises?|"
                       r"corporations?|conglomerates?)")
STAKE_CLAIM_ROWS: Tuple[str, ...] = (
    # a valuation by a verb: "which values Relecloud at $90 billion", "valuing it at about $90B" —
    # "at", up to three qualifiers, then an amount, within six words of the verb
    r"\bvalu(?:es|ed|ing)\s+(?:[^\s.;:]{1,40}\s+){0,6}?at\s+(?:(?:about|around|roughly|nearly|almost|over|"
    r"under|some|approximately|more\s+than|less\s+than|up\s+to|as\s+much\s+as|an?)\s+){0,3}" + _STAKE_AMOUNT,
    # a superlative that ranks a company: "the largest AI startup in Europe", "the fastest-growing cloud
    # company", "the world's biggest chipmaker" — the rank word, up to three words, a company noun
    r"\b" + _STAKE_SUPERLATIVE + r"\s+(?:[^\s.;:,]{1,40}\s+){0,3}?" + _STAKE_COMPANY_NOUN + r"\b",
    # the same rank with no company noun: "Relecloud is the largest in Europe", "the biggest among them"
    r"\b" + _STAKE_SUPERLATIVE + r"\s+(?:in|among|of\s+all)\b",
)
STAKE_CLAIM_RE = re.compile("|".join(f"(?:{r})" for r in STAKE_CLAIM_ROWS), re.IGNORECASE)
#: A stake background naming a per-share amount ("at $414.79 a share", "$20.00 each") reads as a
#: price: such a background is dropped (it is optional caption text, never a reason to refuse).
_PER_SHARE_RE = re.compile(r"\b(?:a|per|each)\s+(?:share|unit|ads)\b|\beach\b|\bshare\s+price\b",
                           re.IGNORECASE)
#: The one place a "%" may stand without " of" (templates design §4.5): a grid tile's share cell,
#: "<segment> · 57%" — its path and its exact shape.
_TILE_LINE_FIELD_RE = re.compile(r"image_spec\.tiles\[[0-9]{1,2}\]\.line")
_SHARE_CELL_RE = re.compile(r"[^\n·%]+ · (?:[0-9]{1,2}(?:\.[0-9])?%|100%|less than 1%|more than 99%)")
#: news-v23 (the restyle, owner 2026-11-19: "add +X% surprised (in green) or -X% surprised (in red)" + column order
#: "Estimate, Reported, Surprise"; earnings_multi*.jpg) — the earnings TABLE's two POSITIONAL exemptions, on an
#: earnings post only (`_validate_words`), each with must-reject twins in tests/test_marketing_news_templates.py:
#: * the surprise CELL — the last cell of a table line, on the picture or its cover — is the one other "%" that stands
#:   alone, and only in exactly `surprise_cell`'s shapes ("+8.0%", "−11.4%", "0.0%", "—"); any other string there, and
#:   a surprise-shaped string anywhere else, still answers to the "%" rule;
#: * the column LABEL "Surprise" — exactly `er.img.col.surprise`, at the last column of the picture's or the cover's
#:   table — is the one place EARNINGS_BANNED_RE's `surpris\w*` lets the word stand (a label over the figures, never a
#:   verb: "surprised" stays refused everywhere, captions and narration included).
_SURPRISE_CELL_FIELD_RE = re.compile(
    r"(?:image_spec\.sections\[0\]|opening_card\.list)\.rows\[[0-9]\]\.table\[[01]\]\.cells\[3\]")
_SURPRISE_CELL_RE = re.compile(r"(?:\+|−)[0-9]{1,3}(?:,[0-9]{3})*\.[0-9]%|0\.0%|—")
_SURPRISE_COLUMN_FIELD_RE = re.compile(r"(?:image_spec\.sections\[0\]|opening_card\.list)\.columns\[2\]")
#: news-v23 phase 3 (2026-11-20), the theme's "growth + profit" angle (a `figures` picture) — its two POSITIONAL "%"
#: exemptions, each with must-reject twins in tests/test_marketing_news_templates.py: the picture's (and the cover's)
#: growth CELL at its own path, in its own shape ("+114%", "−5%", "0%": template_onscreen.GROWTH_FIGURE_RE), and two
#: phrases with the word "revenue" right before the figure — "revenue (was) N% higher / lower than the year before"
#: and the short caption row's "revenue +114%," (signed, then a comma) — so a price phrase ("shares were 14% higher
#: than the year before", "the stock +14%,") is never exempt. A REVENUE fact (financial statements), never a price
#: move.
_GROWTH_CELL_FIELD_RE = re.compile(r"(?:image_spec|opening_card\.list)\.rows\[[0-9]{1,2}\]\.growth")
_GROWTH_PHRASE_RE = re.compile(
    r"\brevenue (?:(?:was )?[1-9][0-9]{0,2}(?:,[0-9]{3})*% (?:higher|lower) than the year before\b"
    r"|(?:\+|−)[1-9][0-9]{0,2}(?:,[0-9]{3})*%(?=,)|0%(?=,))")
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
    #: B3-LIVE-1 (2026-10-20): the PICTURE parts speak of what only the picture shows — a Form 4 roundup's rows
    #: ("The largest of these: …", "The purchases: …", "More purchases: …", worded format-neutrally since
    #: news-v20: they ride text posts too) — while the video is about the lead row
    #: alone. The Facebook / LinkedIn / Threads captions are built from the picture parts (`_caption_bodies`), so
    #: such a caption may ride the day's VIDEO as an extra post only when the video draws the picture (its data
    #: card): `extra_video_problems`. Never part of the composed output.
    picture_bound: bool = False
    #: news-v22 (first-line prefixes): caption headline → the twin written AFTER the prefix, where the prefix already
    #: says some of its words ("📊 13F filing:" says "13F", so the 13F spotlight's twin drops "latest 13F"). The same
    #: facts as its base headline: `stated_by_headline` stays keyed by the BASE. A headline not listed is written as
    #: it is after the prefix. Read only by `_caption_bodies`, only while a prefix applies.
    after_prefix: Dict[str, str] = field(default_factory=dict)
    #: The paragraph limit of the long-form fields (Facebook, LinkedIn, Instagram, the YouTube description), None = 4,
    #: as before; the 13F list sets its own count (a paragraph per row, then the listing, the period, the explainer and
    #: the question — each field's character budget still cuts from the end). TikTok keeps 1.
    paragraph_limit: Optional[int] = None
    #: news-v23 (the 13F list, owner 2026-11-19): a caption field's OWN paragraphs, in place of the plan's default
    #: (X / Bluesky / Threads the headline alone; TikTok the first video paragraph) — the field takes as many WHOLE
    #: ones as fit, content first (`_caption_bodies`); a Threads body carrying any is never let past
    #: THREADS_PICTURE_CAPTION_MAX_BYTES (the picture). Empty for every other series.
    field_paragraphs: Dict[str, List[str]] = field(default_factory=dict)
    #: news-v23 (the 13F list): the fewest paragraphs a caption field must keep, else the outlet is dropped
    #: (`over_budget`) — the holdings the picture's or the cover's "+N more changes in the caption" promises. Read in
    #: place of `required_fields` / `required_paragraphs` for a field it names. Empty for every other series.
    field_required: Dict[str, int] = field(default_factory=dict)
    #: news-v23 (the 13F list): when a field's own paragraphs (with their `field_required`) fit no variant, the
    #: shorter plans it tries next, in order — (paragraphs, fewest to keep) — before the outlet is dropped: a long
    #: caption that cannot list EVERY holding lists the ones the video cover leaves out, then the ones the picture
    #: leaves out (each a whole promise; the headline still counts every change). Empty for every other series.
    field_fallbacks: Dict[str, List[Tuple[List[str], int]]] = field(default_factory=dict)
    #: news-v23 (the 13F list): the caption paragraphs the VIDEO COVER's "+N more changes in the caption" promises (the
    #: holdings it leaves out) — an EXTRA video post's caption must carry each one (`extra_video_problems`,
    #: ``cover_caption``). Empty for every other series. Never part of the composed output.
    video_promise: Tuple[str, ...] = ()
    #: news-v23 (company stakes): the SHORT form of each `video_promise` paragraph (the same index), which keeps that
    #: promise too — a short caption (Threads) lists the stakes the cover leaves out in a few words each. Empty: only
    #: the paragraph itself keeps it (the 13F list). Never part of the composed output.
    video_promise_short: Tuple[str, ...] = ()
    #: news-v23 (restyle 2026-11-19): the date the picture's short footer ends with (a `foot.*` entry — the filing
    #: dates, the filing's or its amendment's date, the fiscal year, the disclosure month), or None for a family whose
    #: footer ends at its source (earnings, stakes, themes). The source words are the series' own
    #: (`FOOTER_SOURCE_OF_SERIES`).
    footer_when: Optional[str] = None
    #: Review 2026-11-20 (the list series: 13F, company stakes): the caption paragraphs of a TEXT post — every row from
    #: the TOP, none required. X and Bluesky (TEXT_PLAN_OUTLETS) write them when their post does not carry the picture
    #: (`compose`'s `picture_outlets`), and — a pictured one whose budget cannot keep the picture's "+N more" promise —
    #: in place of the dropped outlet, the post then frozen as TEXT (`text_outlets`); Threads, when it is not pictured,
    #: tries them first while EVERY row fits (a superset of every promise). Empty: no such plan (every other series).
    text_paragraphs: List[str] = field(default_factory=list)
    #: news-v23 phase 3 (the weekly theme post): the picture footer's family source when it is not the series' own
    #: (`FOOTER_SOURCE_OF_SERIES`) — the "biggest" angle's "company filings and market data"
    #: (`THEME_FOOTER_SOURCE_OF_LAYOUT`). None: the series' own.
    footer_source: Optional[str] = None
    #: Review of 2026-11-26: the content paragraphs of the alt text that describes the picture a worker WITHOUT
    #: WORKER_CAPABILITY_LOOK_V23 draws (`template_onscreen.pre_v23_image_spec`) when that picture differs from the
    #: stored one in what the alt text says — a Money Map loss year (the flow steps and their callout, no costs card)
    #: and a 13F list of more than PRE_V23_MAX_SECTION_ROWS rows (its first five rows, no "+N more" line). None: the
    #: stored alt text describes every picture of this record. Composed into `image_post_pre_v23`; the publisher picks
    #: the one that describes the REGISTERED picture (`picture_alt`).
    alt_paragraphs_pre_v23: Optional[List[str]] = None


def _row_chip(symbol: str) -> Dict[str, str]:
    """A `rows` row's ticker chip (news-v23, mock_form4.wide_row_card: the navy chip over the company's name) —
    {"chip": symbol}, or {} for a symbol longer than template_onscreen.ROW_CHIP_MAX_CHARS (the row then draws its
    name alone, never a refused picture)."""
    return {"chip": symbol} if isinstance(symbol, str) and 0 < len(symbol) <= onscreen.ROW_CHIP_MAX_CHARS else {}


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


def _business_days_after(start: date, end: date, *, stop_above: int) -> int:
    """The federal business days in (start, end] — weekdays, federal holidays excluded — counted only
    until the count passes ``stop_above`` (the loop is bounded whatever the gap). 0 when end <= start."""
    count, probe = 0, start
    while probe < end and count <= stop_above:
        probe += timedelta(days=1)
        if is_federal_business_day(probe):
            count += 1
    return count


def _earnings_day_counts(d: date) -> bool:
    """Does ET day `d` count toward an earnings report's age (C0, 2026-11-16)? A weekday counts unless BOTH the exchange
    and the federal government close on it: every NYSE trading day counts (Veterans Day and Columbus Day included —
    the exchanges trade, so a report is a session older), and so does a federal business day the exchange closes on
    (Good Friday) — so the count is never LOWER than the federal business-day count, and the switch from the federal
    calendar can only remove a BREAKING label, never add one. The exchange's closures come from its fixed holiday
    table (`market_hours.US_MARKET_HOLIDAYS`); a year the table does not cover has no exchange holiday — every weekday
    counts there (stricter still, never looser). Deterministic: compose and the re-check at create_posts agree."""
    if d.weekday() >= 5:
        return False
    return (d.year, d.month, d.day) not in US_MARKET_HOLIDAYS or is_federal_business_day(d)


def _earnings_days_after(start: date, end: date, *, stop_above: int) -> int:
    """The days in (start, end] that count toward an earnings report's age (`_earnings_day_counts`), counted only until
    the count passes ``stop_above`` (bounded whatever the gap). 0 when end <= start."""
    count, probe = 0, start
    while probe < end and count <= stop_above:
        probe += timedelta(days=1)
        if _earnings_day_counts(probe):
            count += 1
    return count


def _late_trades(row: rules.InsiderPurchase) -> bool:
    """A row whose LAST filing comes more than INSIDER_LATE_BUSINESS_DAYS federal business days after its
    earliest trade (news-v12; business days since news-v14): its copy states the trade dates, never the
    two-business-day rule."""
    return _business_days_after(row.earliest_trade_date, row.filing_dates[-1],
                                stop_above=INSIDER_LATE_BUSINESS_DAYS) > INSIDER_LATE_BUSINESS_DAYS


def _filing_count(row: rules.InsiderPurchase) -> str:
    """How many Form 4 documents a row's lines come from, as far as the record can tell (news-v15):
    "many" with two filing dates or more; "one" with one date and ONE purchase line (a line belongs to
    exactly one document); "neutral" with one date and several lines — the record keeps distinct filing
    DAYS, not documents (no accession number), so two Form 4s filed the same day are one date. A
    "neutral" row is worded without counting its filings."""
    if len(row.filing_dates) > 1:
        return "many"
    return "one" if row.purchases == 1 else "neutral"


def _trade_paragraph(first: date, last: date, *, filings: str, run_date: date) -> str:
    """The late caption paragraph (news-v12): the trade dates the record carries, then the Form 4
    without the two-business-day rule. `filings` is a `_filing_count` value."""
    shape = "date" if first == last else "window"
    dates = _t(f"ins.p.trade.{filings}.{shape}",
               first_trade_cap=date_caption(first, run_date), last_trade_cap=date_caption(last, run_date))
    return f"{dates} {_t('ins.p.form4.late')}"


def _ins_lead_video(r0: rules.InsiderPurchase, run_date: date, *, late_other: bool
                    ) -> Tuple[List[str], Tuple[List[str], List[str], List[str]],
                               Tuple[Tuple[str, str], Tuple[str, str], Tuple[str, str]],
                               Tuple[List[str], Tuple[str, str]]]:
    """One Form 4 row's ROLE-ONLY video (owner decision 2026-10-09): its hook variants, narration lines 1-3 and
    cards 1-3, and the line-4 / card-4 pair a post uses when no other row follows (the amended note, the explainer
    or the amounts line). The Form 4 roundup's lead row and the insider spotlight's lead (news-v22) share it — moved
    here unchanged from `_insider_parts`. `late_other`: another row of the post is late (`_late_trades`), so the
    explainer drops the two-business-day rule (news-v13). Never a name, never a cost per share."""
    co = r0.company.name
    co_sp = spoken_company(co)
    filings = r0.filing_dates
    num = _filing_count(r0)
    amt, amt_s = money_words(r0.amount_usd), money_image(r0.amount_usd)
    shares_w, shares_s = shares_words(r0.shares), shares_image(r0.shares)
    whole_shares = _q(_dec(r0.shares), 0)
    if whole_shares == 0:
        raise NewsTemplateRefused("implausible_figures", "a share count that would read as about 0")
    share_noun = _t("ins.share.one") if whole_shares == 1 else _t("ins.share.many")
    k = r0.purchases
    k_txt = _t("ins.k.one") if k == 1 else _t("ins.k.many", k=k)
    subj_sp = _ins_subject(r0, spoken=True)
    # narration
    hook = [_t("ins.hook", subj_sp=subj_sp, amt=amt), _t("ins.hook.short", subj_sp=subj_sp, amt=amt),
            _t("ins.hook.min", role_sp=_role(r0.role, "sp"), amt=amt, co_sp=co_sp)]
    first_sp, last_sp = date_spoken(filings[0], run_date), date_spoken(filings[-1], run_date)
    role_np_sp = _role(r0.role, "np.sp")
    # Narration L1 and card 1 are role-only in EVERY case, named record or not (owner decision
    # 2026-10-09 "Role-only video": every narrated word is burned as a caption on a frame, every
    # card is drawn on one, and YouTube Shorts picks its own cover frame). The name stays only in
    # the caption paragraphs after the headline; no name ever reaches the video (this helper reads none).
    l1 = ([_t(f"ins.l1.role.{num}", role_np_sp=role_np_sp, filed_sp=first_sp),
           _t(f"ins.l1.role.short.{num}", role_np_sp=role_np_sp)] if num != "many" else
          [_t("ins.l1.role.many", role_np_sp=role_np_sp, first_sp=first_sp, last_sp=last_sp),
           _t("ins.l1.role.short.many", role_np_sp=role_np_sp)])
    c1 = (_t("ins.c1.title"), _t("ins.c1.noname.dir", co=co) if r0.role == "director"
          else _t("ins.c1.noname.officer", role=_role(r0.role, "word"), co=co))
    # a "neutral" row has two purchases or more by construction (`_filing_count`)
    l2_id = f"ins.l2.{num}.{'one' if k == 1 else 'many'}"
    l2 = [_t(l2_id, k=k, shares_w=shares_w, share_noun=share_noun, amt=amt)]
    c2 = (_t("ins.c2.title.one" if k == 1 else "ins.c2.title.many"),
          _t("ins.c2.body", k_txt=k_txt, shares_s=shares_s, share_noun=share_noun, amt_s=amt_s))
    # The explainer (line and card), wherever it lands (L3, or L4 after a flag line). news-v12: a LATE
    # row (`_late_trades`) states its own trade dates instead of the two-business-day rule — beside
    # "was filed on November 12th", the rule would date a trade from weeks before to about Nov 10.
    # news-v13: a roundup whose lead row is timely but another row is late draws and narrates the Form 4
    # WITHOUT the rule (`ins.l3.form4`) — the "At least {m} more … in the same week" line after it covers
    # the late row, so the rule would date that row's trade to its filing week. The same three-way choice
    # as the video paragraph (`form4_video` below).
    late0 = _late_trades(r0)
    if late0:
        t_first, t_last = r0.earliest_trade_date, r0.latest_trade_date
        shape = "date" if t_first == t_last else "window"
        explainer_line = _t(f"ins.l3.late.{num}.{shape}", first_trade_sp=date_spoken(t_first, run_date),
                            last_trade_sp=date_spoken(t_last, run_date))
        explainer_card = (_t(f"ins.c3.late.{shape}.title"),
                          _t("ins.c3.late.body", trade_window_img=window_image(t_first, t_last),
                             filed_window_img=window_image(filings[0], filings[-1])))
    elif late_other:
        explainer_line = _t("ins.l3.form4")
        explainer_card = (_t("ins.c3.explainer.title"), _t("ins.c3.form4.body"))
    else:
        explainer_line = _t("ins.l3.explainer")
        explainer_card = (_t("ins.c3.explainer.title"), _t("ins.c3.explainer.body"))
    indirect0, amended0 = _ins_flags([r0])
    if indirect0:
        l3_key = f"indirect.{indirect0}"
    elif amended0:
        l3_key = "amended"
    else:
        l3_key = "explainer"
    role_sp0 = _role(r0.role, "sp")
    if l3_key == "explainer":
        l3, c3 = [explainer_line], explainer_card
    else:
        l3 = [_t(f"ins.l3.{l3_key}", role_sp=role_sp0)] if l3_key == "indirect.all" else [_t(f"ins.l3.{l3_key}")]
        c3 = (_t(f"ins.c3.{l3_key}.title"),
              _t(f"ins.c3.{l3_key}.body", role=_role(r0.role, "word")) if l3_key == "indirect.all"
              else _t(f"ins.c3.{l3_key}.body"))
    # the line-4 / card-4 pair of a lone row
    if amended0 and l3_key.startswith("indirect"):
        l4_key = "amended"
    elif l3_key != "explainer":
        l4_key = "explainer"
    else:
        l4_key = "amounts"
    if l4_key == "amounts":
        l4_alone = [_t("ins.l4.amounts")]
        c4_alone = (_t("ins.c4.amounts.title"), _t("ins.c4.amounts.body"))
    elif l4_key == "explainer":
        l4_alone, c4_alone = [explainer_line], explainer_card
    else:
        l4_alone = [_t(f"ins.l3.{l4_key}")]
        c4_alone = (_t(f"ins.c3.{l4_key}.title"), _t(f"ins.c3.{l4_key}.body"))
    return hook, (l1, l2, l3), (c1, c2, c3), (l4_alone, c4_alone)


def _ins_more_line(k: int) -> str:
    """"+1 more company in the caption" / "+{k} more companies in the caption" (a Form 4 list's `more` line: the picture
    and the cover draw the first INS_PICTURE_ROWS rows, every caption riding them lists the rest)."""
    return _t("ins.more.one") if k == 1 else _t("ins.more.many", k=k)


def _insider_parts(rec: rules.InsiderBuysWeek, run_date: date, suppressed: FrozenSet[str]) -> _Parts:
    """A weekly Form 4 ROUNDUP (ceo_buys / insider_buys). Owner decisions 2026-11-20 ("3 - 5 companies at once"):
    always `rules.INSIDER_MIN_ROWS` companies or more (fewer: `too_few_rows` — the one-company spotlight picture of
    before is gone), the largest first. The PICTURE (form4_roundup5.jpg: the approved chip-above row) draws the first
    INS_PICTURE_ROWS rows under "At least {n} …" (n = EVERY row, the count the hook and every caption say — the review
    of 2026-11-26: one post never shows two counts), "+N more companies in the caption" for the rest,
    its note panel up to INS_ROUNDUP_NOTE_MAX_ROWS rows (a five-row picture moves the note to the caption); the video
    COVER the same rows, compact ("All rows, compact"); the narration the lead row role-only (`_ins_lead_video`) and
    "At least {m} more …"; the captions every row, and every promise the picture and the cover make."""
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
    if len(rows) < rules.INSIDER_MIN_ROWS:
        # owner decisions 2026-11-20: never a one- or two-company Form 4 post (a member's row dropped included)
        raise NewsTemplateRefused("too_few_rows", f"{len(rows)} compan(ies) left — a Form 4 post shows at least "
                                                  f"{rules.INSIDER_MIN_ROWS}")
    # news-v19 (owner decision 4, 2026-10-15): from an even year's Election Day through January 3 the
    # roster (the SITTING Congress) cannot know a member-elect, so no person is named — every row is
    # role-only, the post still runs. Read from the RUN date: `revalidate` recomposes on the same date,
    # so a stored output and its re-check agree. The member drop above still runs on the record's names.
    # Review of 2026-10-16 (ME-1): judged over every day the run's posts can go out (`members_elect_for_run`:
    # the run date and the two after it — a Monday-before-Election-Day post can go out on Election Day).
    names_ok = rules.person_names_allowed() and not rules.members_elect_for_run(run_date)

    def person(r: rules.InsiderPurchase) -> Optional[str]:
        return r.person_name if (names_ok and r.person_name and r.person_name not in suppressed) else None

    for r in rows:
        # the review of 2026-10-16: no word the cover's headline cannot draw whole (the record refuses it too,
        # `company_news_rules.COMPANY_NAME_WORD_MAX_CHARS`; this stands behind it, as the other series' checks do)
        _name_slot(r.company.name, "company name")
    r0, n = rows[0], len(rows)
    co, sym = r0.company.name, r0.company.symbol
    p0 = person(r0)
    filings = r0.filing_dates
    # "one" / "many" / "neutral" (news-v15): one filing DATE is not one filing — see `_filing_count`
    num = _filing_count(r0)
    amt, amt_s = money_words(r0.amount_usd), money_image(r0.amount_usd)
    # the share count as written (`shares_words` / `shares_image` round a figure under 10,000 to the
    # whole share): "1" / "about 1" takes "share", anything else "shares"; "about 0" is no figure
    whole_shares = _q(_dec(r0.shares), 0)
    if whole_shares == 0:
        raise NewsTemplateRefused("implausible_figures", "a share count that would read as about 0")
    subj = _ins_subject(r0, spoken=False)

    # kicker / footer
    monday = _monday(run_date)
    last_week = (rec.window_start.toordinal() == monday.toordinal() - 7
                 and rec.window_end.toordinal() <= monday.toordinal() - 1)
    kicker = (_t("ins.kicker.last_week") if last_week
              else _t("ins.kicker.window", window_upper=window_upper(rec.window_start, rec.window_end)))
    as_of = _t("ins.asof", window_img=window_image(rec.window_start, rec.window_end))
    footer_when = _t("foot.filed", date_img=window_image(rec.window_start, rec.window_end))

    # narration and cards 1-3 (and the line-4 / card-4 pair of a lone row): the lead row's role-only video,
    # shared with the insider spotlight (news-v22, `_ins_lead_video`, moved unchanged)
    late0 = _late_trades(r0)
    late_other = any(_late_trades(r) for r in rows[1:])
    hook, (l1, l2, l3), (c1, c2, c3), (_l4_alone, _c4_alone) = _ins_lead_video(r0, run_date, late_other=late_other)
    indirect0, amended0 = _ins_flags([r0])
    others = rows[1:]
    m = len(others)
    roles_rest = [r.role for r in others]
    # owner decisions 2026-11-20: at least INSIDER_MIN_ROWS rows, so at least two others — always the plural bound
    l4 = [_t("ins.l4.more.many", m=m, roles_sp=_group_role(roles_rest, "sp.plural"))]
    c4 = (_t("ins.c4.more.title"), _t("ins.c4.more.many", m=m, roles=_group_role(roles_rest, "plural")))

    # the picture (form4_roundup5.jpg): the first INS_PICTURE_ROWS rows, "At least {n} …" — n and the role noun over
    # EVERY row, the same count and noun as the captions' first lines (`image_heads`) and the narration's "At least
    # {m} more" (review of 2026-11-26: a seven-row post drew "At least 5 CEOs" above captions saying "At least 7"); the
    # "+N more companies in the caption" line for the rest, the note panel about THESE purchases (the drawn rows) —
    # up to INS_ROUNDUP_NOTE_MAX_ROWS rows; a five-row picture's note moves to the caption (required there)
    shown, rest = rows[:INS_PICTURE_ROWS], rows[INS_PICTURE_ROWS:]
    k_shown = len(shown)
    title = _t("ins.img.title.many", n=n, roles=_group_role([r.role for r in rows], "plural"))
    indirect_shown, amended_shown = _ins_flags(shown)
    notes = ([_t(f"ins.img.note.indirect.{indirect_shown}")] if indirect_shown else []) + (
        [_t("ins.img.note.amended")] if amended_shown else [])
    note_moved = bool(notes) and k_shown > INS_ROUNDUP_NOTE_MAX_ROWS
    picture_rows = [{"logo": r.company.symbol, **_row_chip(r.company.symbol),
                     "cells": [r.company.name, _role(r.role, "label"), money_image(r.amount_usd)]} for r in shown]
    section: Dict[str, Any] = {"rows": picture_rows}
    if rest:
        section["more"] = _ins_more_line(len(rest))
    spec: Dict[str, Any] = {"layout": "rows", "version": onscreen.SPEC_VERSION, "kicker": kicker, "title": title,
                            "sections": [section]}
    if notes and not note_moved:
        spec["notes"] = notes
    # the COVER (owner 2026-11-20 "All rows, compact"): the picture's headline and the same rows — logo, chip, name,
    # role, the GREEN amount — and the same "+N more"; its first row is the lead row the hook narrates. A worker that
    # cannot draw a list is sent the card without it (`script_service._without_list_cover`).
    cover_list: Dict[str, Any] = {"rows": picture_rows[:INS_COVER_ROWS]}
    if n > INS_COVER_ROWS:
        cover_list["more"] = _ins_more_line(n - INS_COVER_ROWS)
    opening = {"kicker": kicker, "logos": [sym], "headline": title, "list": cover_list}
    alt_rows = "; ".join(_t("ins.alt.row", co=r.company.name, role=_role(r.role, "label"),
                            amt_s=money_image(r.amount_usd)) for r in shown)
    alt_paragraphs = [_t("ins.alt.rows", rows=alt_rows)] + (
        [_t("ins.alt.more.one") if len(rest) == 1 else _t("ins.alt.more.many", k=len(rest))] if rest else []) + (
        [" ".join(notes)] if notes and not note_moved else [])

    # captions
    def row_item(r: rules.InsiderPurchase, named: bool = True) -> str:
        p = person(r) if named else None
        role_lc = _role(r.role, "word")
        if p:
            return _t("ins.row.name", co=r.company.name, role=role_lc, person=p, amt=money_words(r.amount_usd))
        return _t("ins.row.noname", co=r.company.name, role=role_lc, amt=money_words(r.amount_usd))

    window_cap = window_caption(rec.window_start, rec.window_end, run_date)
    one_long = (_t(f"ins.ch.one.long.{num}", subj=subj, amt=amt, co=co,
                   filed_cap=date_caption(filings[0], run_date))
                if num != "many" else
                _t("ins.ch.one.long.many", subj=subj, amt=amt, co=co,
                   filed_window_cap=window_caption(filings[0], filings[-1], run_date)))
    one_heads = [one_long, _t("ins.ch.one.short", subj=subj, amt=amt),
                 _t("ins.ch.one.min", role=_role(r0.role, "word"), amt=amt)]
    indirect_all, amended_all = _ins_flags(rows)
    flag_para = []
    if indirect_all:
        # news-v12: a roundup's paragraph speaks of several buyers ("the buyers' own names")
        flag_para.append(_t(f"ins.p.flag.indirect.{indirect_all}.many"))
    if amended_all:
        flag_para.append(_t("ins.p.flag.amended"))
    flag = " ".join(flag_para)
    # news-v12: the Form 4 paragraph — the two-business-day rule only when no row it speaks for is late;
    # else the trade dates (the lead row's, or the whole roundup's window) and the Form 4 without the rule
    form4_r0 = (_trade_paragraph(r0.earliest_trade_date, r0.latest_trade_date, filings=num,
                                 run_date=run_date) if late0 else _t("ins.p.form4"))
    if any(_late_trades(r) for r in rows):
        form4_image = _trade_paragraph(min(r.earliest_trade_date for r in rows),
                                       max(r.latest_trade_date for r in rows), filings="many",
                                       run_date=run_date)
    else:
        form4_image = form4_r0
    # the video paragraphs are about the lead row (and "At least {m} more …"): its dates when it is late;
    # when only another row is, the Form 4 without the rule and without dates
    form4_video = form4_r0 if (late0 or not late_other) else _t("ins.p.form4.late")
    role_np0 = _role(r0.role, "np")
    vp1 = (_t(f"ins.p.one.name.{num}", person=p0, role_np=role_np0, co=co) if p0
           else _t(f"ins.p.one.noname.{num}", role_np=role_np0, co=co))
    # review round 11: the long and short single-row headlines say the role and the company ("The CEO of
    # Lowe's disclosed buying …"), so the role-only paragraph ("The filing is by the CEO of Lowe's.") is
    # left out under them; ".min" names no company, so it keeps the paragraph. A named paragraph adds
    # the name and is never left out.
    one_stated = {} if p0 else {h: frozenset({vp1}) for h in one_heads[:2]}
    roles = _group_role([r.role for r in rows], "plural")
    image_heads = [_t("ins.ch.many.long", n=n, roles=roles, window_cap=window_cap, co=co, amt=amt),
                   _t("ins.ch.many.short", n=n, roles=roles, window_cap=window_cap),
                   _t("ins.ch.many.min", n=n, roles=roles)]
    shown_para = _t("ins.p.rows", rows="; ".join(row_item(r) for r in shown))
    rest_para = _t("ins.p.rows2", rows="; ".join(row_item(r) for r in rest)) if rest else None
    # the NAME-FREE twins: X, Bluesky and Threads carry no person anywhere in their caption (`_restricted_strings`:
    # the whole body of a computed-budget field), so their rows are role-only
    shown_anon = _t("ins.p.rows", rows="; ".join(row_item(r, False) for r in shown))
    rest_anon = _t("ins.p.rows2", rows="; ".join(row_item(r, False) for r in rest)) if rest else None
    image_paras = [shown_para] + ([rest_para] if rest_para else []) + ([flag] if flag else []) + [form4_image]
    flags0 = ([_t(f"ins.p.flag.indirect.{indirect0}")] if indirect0 else []) + (
        [_t("ins.p.flag.amended")] if amended0 else [])
    video_paras = ([vp1] if p0 else []) + [form4_video] + ([" ".join(flags0)] if flags0 else [])
    video_paras.append(_t("ins.vp.more.many", m=m, roles=_group_role(roles_rest, "plural")))
    # The promises (the 13F list's rules, news-v23): the picture's "+N more companies in the caption" (and, on a
    # five-row picture, its moved note) on every caption that can ride the PICTURE — X, Bluesky, Threads, Facebook,
    # LinkedIn; the cover's "+N more" on every caption that can ride the VIDEO — TikTok, Instagram, the YouTube
    # description (Facebook / LinkedIn / Threads videos are re-checked at freeze, `extra_video_problems`). An outlet
    # that cannot keep them is dropped (`over_budget`), never posted beside a promise it does not keep.
    picture_promise = ([rest_para] if rest_para else []) + ([flag] if note_moved else [])
    picture_promise_anon = ([rest_anon] if rest_anon else []) + ([flag] if note_moved else [])
    cover_promise = [rest_para] if n > INS_COVER_ROWS and rest_para else []
    field_paragraphs: Dict[str, List[str]] = {}
    field_required: Dict[str, int] = {}
    field_fallbacks: Dict[str, List[Tuple[List[str], int]]] = {}
    text_paragraphs: List[str] = []
    if picture_promise:
        for f in ("x", "bluesky", "threads"):
            field_paragraphs[f] = list(picture_promise_anon)
            field_required[f] = len(picture_promise_anon)
        last = max(image_paras.index(p) for p in picture_promise)
        for f in ("facebook", "linkedin"):
            field_required[f] = last + 1
            field_fallbacks[f] = [(list(picture_promise) + [form4_image], len(picture_promise))]
        text_paragraphs = [shown_anon] + ([rest_anon] if rest_anon else [])
    if cover_promise:
        field_paragraphs["tiktok"] = list(cover_promise)
        field_required["tiktok"] = len(cover_promise)
        # the long video captions list EVERY row first (the cover's and the rest), then the lead row's story — the rows
        # lead, so a headline that already states a paragraph (`stated_by_headline`) never pushes the promise out
        for f in ("instagram", "youtube_description"):
            field_paragraphs[f] = [shown_para] + list(cover_promise) + video_paras
            field_required[f] = 1 + len(cover_promise)
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
                  alt_title=title, alt_paragraphs=alt_paragraphs,
                  source=rules.source_label(rec), as_of=as_of,
                  image_headlines=image_heads, image_paragraphs=image_paras,
                  video_headlines=one_heads, video_paragraphs=video_paras, youtube_titles=yt,
                  persons=persons, names=names, stated_by_headline=one_stated, picture_bound=True,
                  footer_when=footer_when, field_paragraphs=field_paragraphs, field_required=field_required,
                  field_fallbacks=field_fallbacks, video_promise=tuple(cover_promise),
                  video_promise_short=tuple(rest_anon for _ in cover_promise), text_paragraphs=text_paragraphs)


# ── Insider list (the spotlight lane; owner decisions 2026-11-20, "3 - 5 companies at once") ───────────────


@dataclass(frozen=True)
class _IspRow:
    """One drawn / captioned row of the insider list, after the template's own drops (a member of Congress among a
    row's others): its company, its people (the lead first), and their figures summed."""

    company: rules.CompanyRef
    people: Tuple[rules.InsiderPurchase, ...]
    total: float
    shares: float
    cost_lo: str
    cost_hi: Optional[str]

    @property
    def lead(self) -> rules.InsiderPurchase:
        return self.people[0]


def _isp_who(row: _IspRow, form: str, person: Optional[str] = None) -> str:
    """Who disclosed buying, by ROLE (owner decisions 2026-11-20): the lead's role, and the others as a LOWER BOUND —
    "CEO + at least 2 other insiders" (the picture, form "label"), "the CEO and at least 2 other insiders" (a caption,
    form "np"; with the lead's name when it may be printed: "the CEO, Jane Doe, and …"), "its chief executive and at
    least 2 other insiders" (the narration, form "sp": "its", never "the chief executive" — that phrase is the
    judge rubric's example of a person described by role, kept out of every template string). Never a complete count: the adapter's gates drop people, and
    the roles read (CEO, CFO, director) are not every insider."""
    lead = row.lead
    m = len(row.people) - 1
    base = _t(f"isp.sp.{lead.role}") if form == "sp" else _role(lead.role, form)
    if person and form == "np":
        base = _t("isp.who.named", who=base, person=person)
    if m == 0:
        return base
    return _t(f"isp.who.{form}.plus.one", who=base) if m == 1 else _t(f"isp.who.{form}.plus.many", who=base, m=m)


def _isp_detail(row: _IspRow) -> str:
    """The row's grey detail line (insider_multi3.jpg): "28K shares · $71.20–$71.80 a share, as filed" — the filing's OWN
    cost per share (owner decision 2026-11-15, "Captions + picture"), one price when every line carries the same."""
    whole = _q(_dec(row.shares), 0)
    noun = _t("ins.share.one") if whole == 1 else _t("ins.share.many")
    if row.cost_hi is None:
        return _t("isp.detail.one", shares_s=shares_image(row.shares), share_noun=noun, cost_s=row.cost_lo)
    return _t("isp.detail.range", shares_s=shares_image(row.shares), share_noun=noun, cost_min_s=row.cost_lo,
              cost_max_s=row.cost_hi)


def _isp_row_check(row: rules.InsiderSpotlightCompany) -> None:
    """The insider list's per-company gates (`drop_refused_rows`, and `_insider_spotlight_parts` itself): a ticker our
    caption can print in brackets ("(BUY)" reads as the news ban's "buy", "(CUT)" as an off-table verb: `slot_rejected`,
    B decision 9's fail-closed rule), a name the picture can draw whole, a filed cost per share it can print as filed
    (`cost_pair`), a share count that is not "about 0". `NewsTemplateRefused` otherwise."""
    sym = row.company.symbol
    if NEWS_BANNED_RE.search(sym) or OFF_TABLE_VERBS_RE.search(sym):
        raise NewsTemplateRefused("slot_rejected", f"the ticker {sym!r} reads as a banned word or verb in a caption")
    _name_slot(row.company.name, "company name")
    cost_pair(row.cost_per_share_min_usd, row.cost_per_share_max_usd)
    if _q(_dec(row.shares), 0) == 0:
        raise NewsTemplateRefused("implausible_figures", "a share count that would read as about 0")


def _insider_spotlight_parts(rec: rules.InsiderSpotlight, run_date: date, suppressed: FrozenSet[str]) -> _Parts:
    """The spotlight lane's insider LIST (owner decisions 2026-11-20 — "each post or each image/video will have more
    than 2,3, ideally are 3 - 5 companies at once"; the video cover "All rows, compact (Recommended)"; per-post art
    "Drop it from insider posts (Recommended)"). It replaces the one-issuer `feature` post of 2026-11-15.

    * the PICTURE (`rows`, insider_multi3.jpg / insider_multi5.jpg): the kicker "FILED {dates} · FORM 4", the title
      "Insiders at {n} companies" + "disclosed buying stock" (n = EVERY company of the list — the hook's and every
      caption's count, as the r6 mock's cover counts them; review of 2026-11-26), the grey column label "Total
      purchases", at most INS_PICTURE_ROWS rows — the logo, the name with its ticker chip, who (roles only: "CEO + at
      least 2 other insiders"), the GREEN total and ONE grey detail line "<shares> shares · $<low>–$<high> a share, as
      filed" — "+N more companies in the caption" for the rest, and the "held indirectly" note panel (a five-row
      picture keeps it with the tighter rows: insider_multi5_alt_with_note.jpg);
    * the COVER (insider_multi5_cover_alt_all5.jpg): the same headline, label and rows, compact — NO detail line: the
      cost per share never reaches the video (owner decision 5, 2026-11-15);
    * the narration: the companies by NAME and ROLE only — the count, the largest total, the other companies, the
      holding note or the Form 4 explainer;
    * the captions: the first line (the count, the window, the largest total), one paragraph per company (its ticker,
      who, the total, the shares at the filed cost "a share, as filed"), the holding note, the Form 4 explainer and
      the question; every caption riding the picture or the video lists the companies they leave out.

    `too_few_rows` below `rules.INSIDER_MIN_ROWS` companies (after a member's row is dropped) — never a one- or
    two-company post."""
    ws, we = rec.window_start, rec.window_end
    if not (we < run_date and (run_date - we).days <= INSIDER_MAX_AGE_DAYS
            and (run_date - ws).days <= rules.SPOTLIGHT_MAX_WINDOW_DAYS):
        raise NewsTemplateRefused("stale_source", "the spotlight's window does not end in the week before the run")
    everyone = [p for c in rec.companies for p in c.people]
    # one person counted once (a middle initial on one filing and not the next is one person, fail closed)
    if _distinct_people(everyone) != len(everyone):
        raise NewsTemplateRefused("record_invalid", "two people of the list are one person")
    if any(p.amended for p in everyone):
        raise NewsTemplateRefused("record_invalid", "an amended filing (the adapter refuses every amended person)")
    if any(p.cost_per_share_min_usd is None or p.cost_per_share_max_usd is None for p in everyone):
        raise NewsTemplateRefused("record_invalid", "a person without the filing's own cost per share")
    # a purchase line belongs to ONE filing: fewer lines than filing days is no record the copy can count
    if any(p.purchases < len(p.filing_dates) for p in everyone):
        raise NewsTemplateRefused("record_invalid", "fewer purchase lines than filing days")
    # a member of Congress never leads a row (the row is dropped, as a roundup drops its row) and never stands among a
    # row's others (that person is dropped — the others are a lower bound)
    rows: List[_IspRow] = []
    for c in rec.companies:
        if c.lead.person_name and rules.is_congress_name(c.lead.person_name):
            continue
        people = (c.lead,) + tuple(p for p in c.others if not (p.person_name and rules.is_congress_name(p.person_name)))
        _isp_row_check(c)
        lo, hi = cost_pair(min(p.cost_per_share_min_usd for p in people),
                           max(p.cost_per_share_max_usd for p in people))
        if _q(_dec(math.fsum(p.shares for p in people)), 0) == 0:
            raise NewsTemplateRefused("implausible_figures", "a share count that would read as about 0")
        rows.append(_IspRow(company=c.company, people=people, total=math.fsum(p.amount_usd for p in people),
                            shares=math.fsum(p.shares for p in people), cost_lo=lo, cost_hi=hi))
    if len(rows) < rules.INSIDER_MIN_ROWS:
        raise NewsTemplateRefused("too_few_rows", f"{len(rows)} compan(ies) left — an insider list shows at least "
                                                  f"{rules.INSIDER_MIN_ROWS}")
    rows.sort(key=lambda r: (-r.total, r.company.name.casefold(), r.company.symbol))
    names_ok = rules.person_names_allowed() and not rules.members_elect_for_run(run_date)

    def person(r: rules.InsiderPurchase) -> Optional[str]:
        return r.person_name if (names_ok and r.person_name and r.person_name not in suppressed) else None

    n = len(rows)
    lead_row = rows[0]
    co, sym = lead_row.company.name, lead_row.company.symbol
    kept_people = [p for r in rows for p in r.people]
    first = min(p.filing_dates[0] for p in kept_people)
    last = max(p.filing_dates[-1] for p in kept_people)
    kicker = _t("ins.kicker.window", window_upper=window_upper(first, last))
    as_of = _t("ins.asof", window_img=window_image(first, last))
    footer_when = _t("foot.filed", date_img=window_image(first, last))
    window_cap = window_caption(first, last, run_date)

    # the picture and the cover: the first INS_PICTURE_ROWS rows, "+N more companies in the caption" for the rest
    shown, rest = rows[:INS_PICTURE_ROWS], rows[INS_PICTURE_ROWS:]
    k_shown = len(shown)
    # the headline counts EVERY company (n), never the rows drawn: the cover is shown alone over the narrated hook
    # ("Insiders at {n} companies …", burned into the same frame) and every caption's first line says n — the
    # "+N more companies in the caption" line below the rows says where the rest are (review of 2026-11-26)
    title = _t("isp.img.title", n=n)
    heading = _t("isp.img.heading")
    cover_rows = [{"logo": r.company.symbol, **_row_chip(r.company.symbol),
                   "cells": [r.company.name, _isp_who(r, "label"), money_image(r.total)]} for r in shown]
    picture_rows = [{**cr, "detail": _isp_detail(r)} for cr, r in zip(cover_rows, shown)]
    section: Dict[str, Any] = {"heading": heading, "rows": picture_rows}
    indirect_shown, _amended = _ins_flags([p for r in shown for p in r.people])
    notes = [_t(f"ins.img.note.indirect.{indirect_shown}")] if indirect_shown else []
    if rest:
        section["more"] = _ins_more_line(len(rest))
    spec: Dict[str, Any] = {"layout": "rows", "version": onscreen.SPEC_VERSION, "kicker": kicker, "title": title,
                            "sections": [section]}
    if notes:
        spec["notes"] = notes
    cover_list: Dict[str, Any] = {"heading": heading, "rows": cover_rows[:INS_COVER_ROWS]}
    if n > INS_COVER_ROWS:
        cover_list["more"] = _ins_more_line(n - INS_COVER_ROWS)
    opening = {"kicker": kicker, "logos": [sym], "headline": title, "list": cover_list}
    # the alt text describes the picture WITHOUT its cost per share (owner decision "Captions + picture")
    alt_items = "; ".join(_t("isp.alt.row", co=r.company.name, who=_isp_who(r, "label"), amt_s=money_image(r.total))
                          for r in shown)
    alt_paragraphs = [_t("isp.alt.rows", rows=alt_items)]
    if rest:
        alt_paragraphs.append(_t("ins.alt.more.one") if len(rest) == 1 else _t("ins.alt.more.many", k=len(rest)))
    if notes:
        alt_paragraphs.append(" ".join(notes))

    # the narration: names and roles only, never a cost per share; the count of every company the post lists
    lead_amt = money_words(lead_row.total)
    others_sp = [spoken_company(r.company.name) for r in rows[1:]]
    hook = [_t("isp.hook", n=n), _t("isp.hook.short", n=n)]
    l1 = [_t("isp.l1", co_sp=spoken_company(co), who_sp=_isp_who(lead_row, "sp"), amt=lead_amt),
          _t("isp.l1.short", co_sp=spoken_company(co), amt=lead_amt)]
    l2 = [_t("isp.l2", list=v) for v in _list_variants(others_sp, len(others_sp))]
    late_any = any(_late_trades(p) for p in kept_people)
    indirect_all, _amended_all = _ins_flags(kept_people)
    explainer_line = _t("ins.l3.form4") if late_any else _t("ins.l3.explainer")
    explainer_card = ((_t("ins.c3.explainer.title"), _t("ins.c3.form4.body")) if late_any
                      else (_t("ins.c3.explainer.title"), _t("ins.c3.explainer.body")))
    c1 = (_t("isp.c1.title"), _t("isp.c1.body", co=co, who=_isp_who(lead_row, "label"), amt_s=money_image(lead_row.total)))
    c2 = (_t("isp.c2.title"), _list_variants([r.company.name for r in rows[1:]], n - 1)[0])
    if indirect_all:
        l3 = [_t("isp.l3.indirect.all")] if indirect_all == "all" else [_t("ins.l3.indirect.some")]
        c3 = ((_t("ins.c3.indirect.all.title"), _t("isp.c3.indirect.all.body")) if indirect_all == "all"
              else (_t("ins.c3.indirect.some.title"), _t("ins.c3.indirect.some.body")))
        l4, c4 = [explainer_line], explainer_card
    else:
        l3, c3 = [explainer_line], explainer_card
        l4, c4 = [_t("ins.l4.amounts")], (_t("ins.c4.amounts.title"), _t("ins.c4.amounts.body"))

    # the captions: the first line, one paragraph per company, the holding note, the Form 4 explainer, the question
    heads = [_t("isp.h.long", n=n, window_cap=window_cap, co=co, sym=sym, amt=lead_amt),
             _t("isp.h.short", n=n, window_cap=window_cap), _t("isp.h.min", n=n)]
    def row_para(r: _IspRow, named: bool) -> str:
        whole = _q(_dec(r.shares), 0)
        noun = _t("ins.share.one") if whole == 1 else _t("ins.share.many")
        who = _isp_who(r, "np", person(r.lead) if named else None)
        fill = dict(co=r.company.name, sym=r.company.symbol, who=who, amt=money_words(r.total),
                    shares_w=shares_words(r.shares), share_noun=noun)
        return (_t("isp.p.row.one", cost_s=r.cost_lo, **fill) if r.cost_hi is None
                else _t("isp.p.row.range", cost_min_s=r.cost_lo, cost_max_s=r.cost_hi, **fill))

    row_paras = [row_para(r, True) for r in rows]
    # the NAME-FREE twins for X, Bluesky and Threads (a computed-budget field's whole caption is restricted)
    row_anon = [row_para(r, False) for r in rows]
    # news-v12's late rule: a late filing's trade dates are stated (the list's whole trade window), never the
    # two-business-day rule (it would date a trade to its filing week)
    form4 = (_trade_paragraph(min(p.earliest_trade_date for p in kept_people),
                              max(p.latest_trade_date for p in kept_people), filings="many", run_date=run_date)
             if late_any else _t("ins.p.form4"))
    tail = ([_t(f"ins.p.flag.indirect.{indirect_all}.many")] if indirect_all else []) + [form4, _t("isp.p.question")]
    paras = row_paras + tail
    picture_rest, cover_rest = row_paras[k_shown:], row_paras[INS_COVER_ROWS:]
    # the SHORT form of the rest (one paragraph, every company the picture and the cover leave out: its name, ticker and
    # total) — what a tight caption (X, Bluesky, Threads, TikTok) carries to keep the "+N more" promise; a long caption
    # carries each company's own paragraph
    short_rest = (_t("isp.p.more", rows="; ".join(_t("isp.p.more.row", co=r.company.name, sym=r.company.symbol,
                                                      amt=money_words(r.total)) for r in rows[k_shown:]))
                  if picture_rest else None)
    text = [short_rest] if short_rest else row_anon
    # the 13F list's promise rules (news-v23): X / Bluesky the picture's rest (or every row), Threads and TikTok the
    # cover's; every long caption every company, else the cover's rest, else the picture's (`field_fallbacks`)
    field_paragraphs: Dict[str, List[str]] = {"x": text, "bluesky": text,
                                              "threads": [short_rest] if short_rest else row_anon,
                                              "tiktok": [short_rest] if short_rest else row_paras[:1]}
    field_required: Dict[str, int] = {f: n for f in ("facebook", "linkedin", "instagram", "youtube_description")}
    field_required.update({f: 1 if short_rest else 0 for f in ("x", "bluesky", "threads", "tiktok")})
    field_fallbacks: Dict[str, List[Tuple[List[str], int]]] = {}
    for f, rests in (("facebook", (cover_rest, picture_rest)), ("linkedin", (cover_rest, picture_rest)),
                     ("instagram", (cover_rest,)), ("youtube_description", (cover_rest,))):
        for r_ in rests:
            plan = (list(r_) + tail, len(r_))
            if len(r_) < n and plan not in field_fallbacks.setdefault(f, []):
                field_fallbacks[f].append(plan)
    yt = [_t("isp.yt", n=n), _t("isp.yt.min", n=n)]
    persons: List[str] = []
    for r in rows:
        name = person(r.lead)
        if name and name not in persons:
            persons.append(name)
    return _Parts(hook=hook, lines=[l1, l2, l3, l4], cards=[c1, c2, c3, c4], opening=opening, image_spec=spec,
                  alt_title=title, alt_paragraphs=alt_paragraphs, source=rules.source_label(rec), as_of=as_of,
                  image_headlines=heads, image_paragraphs=paras, video_headlines=heads, video_paragraphs=paras,
                  youtube_titles=yt, persons=persons, names={r.company.symbol: r.company.name for r in rows},
                  paragraph_limit=len(paras), field_paragraphs=field_paragraphs, field_required=field_required,
                  field_fallbacks=field_fallbacks, video_promise=tuple(cover_rest),
                  video_promise_short=tuple(short_rest for _ in cover_rest), footer_when=footer_when,
                  text_paragraphs=list(row_anon))


# ── 13F Season ────────────────────────────────────────────────────────────────

#: The kinds' order — new → more → fewer → closed out (the record's `no_longer_reported`) — for the narration's and the
#: captions' counts, and the tie-break of the dollar ranking (live-data finding L1(b), news-v5; news-v23: the picture
#: is ONE list ranked by dollar change, no longer a section per kind).
_F13_DISPLAY = ("newly_reported", "increased", "decreased", "no_longer_reported")
_F13_SHORT = {"newly_reported": "new", "no_longer_reported": "gone", "increased": "more", "decreased": "fewer"}
#: A word longer than this in the filer's or a holding's name cannot be drawn whole (the picture's title and row
#: name columns, the cover's): the record is refused `slot_rejected` — one post lost, never the day.
F13_MAX_WORD_CHARS = 20
#: news-v23 (the restyle, owner 2026-11-19): ONE list design for both 13F series — at most this many rows on the
#: picture (f13_list_berkshire.jpg / f13_list_pershing.jpg) and on the video cover (cover_13f_berkshire.jpg); a record
#: holding more says "+N more changes in the caption" under them, and every caption listing those changes is required
#: (`_f13_list_parts`). The record's own minimum is two (`rules.THIRTEEN_F_LIST_MIN_MOVES`): no single-holding post.
F13_PICTURE_ROWS = rules.THIRTEEN_F_LIST_ROWS
F13_COVER_ROWS = rules.THIRTEEN_F_COVER_ROWS


def _holdings(n: int) -> str:
    return _t("f13.holding.one") if n == 1 else _t("f13.holding.many")


#: The dollar size of one move at the filing's own quarter-end values: what the list ranks by. ONE definition, in the
#: pure rules — `company_news_rules.thirteen_f_move_flow` documents it (a new holding's value; more / fewer shares: the
#: shares that changed at the filing's value per share; a closed-out holding: its value on the PREVIOUS quarter's
#: book; None — no value — ranks after every known one, in the record's order); never drawn or said.
_f13_move_value = rules.thirteen_f_move_flow


def _f13_by_value(moves: Sequence[rules.ThirteenFMove]) -> List[rules.ThirteenFMove]:
    """The largest move first, unknown values last, the record's order on a tie (a stable sort)."""
    def key(mv: rules.ThirteenFMove) -> Tuple[bool, float]:
        value = _f13_move_value(mv)
        return (value is None, -(value or 0.0))

    return sorted(moves, key=key)


def _f13_footer_when(rec: Any) -> str:
    """news-v23 (restyle 2026-11-19): the date a 13F picture's short footer ends with — the filing's date, or its
    amendment's when the post reads an amended filing (owner: "remove what users don't care such as quarter before,
    quarter ended"; the period stays in the captions and the alt text)."""
    if rec.amended_on is not None:
        return _t("foot.amended", date_img=date_image(rec.amended_on))
    return _t("foot.filed", date_img=date_image(rec.filed_on))


def _share_figure(value: Optional[float], what: str) -> Tuple[str, str, str]:
    """(words, image, noun) of one 13F share count: "41.9 million" / "41.9M" / "shares" ("1 share"). A missing
    count is `record_invalid`; one that would read "about 0" is `implausible_figures` (never "0 shares")."""
    if value is None:
        raise NewsTemplateRefused("record_invalid", f"no {what} share count")
    whole = _q(_dec(value), 0)
    if whole <= 0:
        raise NewsTemplateRefused("implausible_figures", f"a {what} share count that would read as about 0")
    return shares_words(value), shares_image(value), _t("ins.share.one") if whole == 1 else _t("ins.share.many")


def _bare_figure(value: str) -> Tuple[str, bool]:
    """(a `shares_image` figure without its leading "about ", whether it had one — i.e. was rounded)."""
    about = _about(False, "")
    return (value[len(about):], True) if value.startswith(about) else (value, False)


#: The cover's logo key for a 13F post whose filer has no listing of its own (most 13F filers: a fund has no symbol):
#: the filer's name as a WORDMARK plate — drawn only by a worker that cannot draw the list cover (the cover's `list`
#: stripped for a holder without `look_v23`: `script_service`); the list cover draws a filer plate only beside a chip
#: (a listed filer). Never a canonical symbol (`company_news_rules.canonical_symbol` refuses the digits and the
#: length), so it never names, fetches or leads as a company: `script_service._attach_logos` sends it as a wordmark
#: without a fetch, and `post_art` reads symbols only.
F13S_FILER_LOGO_KEY = "13F-FILER"


def _f13_checked(rec: Any, run_date: date) -> None:
    """The checks every 13F post makes before it composes (both series): filed in this season, every name a drawable
    slot (no word longer than F13_MAX_WORD_CHARS), no holding that is the filer itself (its own stock on its own 13F),
    and — a 13F spotlight — every more / fewer holding past the spotlight's floor (the record's own rule, stood
    behind)."""
    newest = rec.amended_on or rec.filed_on
    if rec.filed_on > run_date or newest > run_date or (run_date - rec.filed_on).days > THIRTEEN_F_MAX_AGE_DAYS:
        raise NewsTemplateRefused("stale_source", "the 13F is not filed in this season")
    filer = rec.filer_name
    for what, name in (("filer name", filer), *(("company name", mv.company.name) for mv in rec.moves)):
        problem = _slot_problem(name)
        if problem:
            raise NewsTemplateRefused("slot_rejected", f"{what}: {problem}")
        if max(len(w) for w in name.split()) > F13_MAX_WORD_CHARS:
            raise NewsTemplateRefused("slot_rejected", f"a word longer than {F13_MAX_WORD_CHARS} characters "
                                                       "cannot be drawn whole")
    spotlight = isinstance(rec, rules.ThirteenFSpotlight)
    for mv in rec.moves:
        _name_slot(mv.company.name, "company name")
        if fold(mv.company.name) == fold(filer) or (rec.filer_symbol is not None
                                                    and rec.filer_symbol == mv.company.symbol):
            raise NewsTemplateRefused("record_invalid", "a holding is the filer itself")
        if (spotlight and _F13_SHORT[mv.move] in ("more", "fewer")
                and not rules.thirteen_f_change_clear(mv.shares, mv.prev_shares)):
            raise NewsTemplateRefused("record_invalid", "a share-count change below the spotlight's floor")
    if len(rec.moves) < rules.THIRTEEN_F_LIST_MIN_MOVES:      # never: both records refuse it (pinned)
        raise NewsTemplateRefused("too_few_rows", "a 13F post shows at least two holdings")


def _f13s_period(rec: Any, run_date: date) -> Tuple[List[str], Tuple[str, str], str, str]:
    """(narration line 1's variants, its card, the alt text's as-of, the caption's period paragraph) — the filing's
    quarter and its filing (or amendment) date. The narration line and its card are the single-kind list's third
    line only (news-v23: the picture and the cover carry no quarter)."""
    period_img, filed_img = date_image(rec.period_end), date_image(rec.filed_on)
    period_sp, filed_sp = date_spoken(rec.period_end, run_date), date_spoken(rec.filed_on, run_date)
    period_cap, filed_cap = date_caption(rec.period_end, run_date), date_caption(rec.filed_on, run_date)
    if rec.amended_on is not None:
        l1 = [_t("f13.l1.amended", period_sp=period_sp, amended_sp=date_spoken(rec.amended_on, run_date)),
              _t("f13.l1.amended.short", period_sp=period_sp)]
        c1 = (_t("f13.c1.title"), _t("f13.c1.body.amended", period_img=period_img,
                                      amended_img=date_image(rec.amended_on)))
        as_of = _t("f13.asof.amended", period_img=period_img, filed_img=filed_img,
                   amended_img=date_image(rec.amended_on))
        period_p = _t("f13s.p.period.amended", period_cap=period_cap, filed_cap=filed_cap,
                      amended_cap=date_caption(rec.amended_on, run_date))
    else:
        l1 = [_t("f13.l1", period_sp=period_sp, filed_sp=filed_sp), _t("f13.l1.short", period_sp=period_sp)]
        c1 = (_t("f13.c1.title"), _t("f13.c1.body", period_img=period_img, filed_img=filed_img))
        as_of = _t("f13.asof", period_img=period_img, filed_img=filed_img)
        period_p = _t("f13s.p.period", period_cap=period_cap, filed_cap=filed_cap)
    return l1, c1, as_of, period_p


def _f13s_join(items: Sequence[str]) -> str:
    """"a" / "a and b" / "a, b, and c" / "a, b, c, and d" (the hook's verb clauses: one per kind, at most four)."""
    if len(items) == 1:
        return items[0]
    return _t(f"f13s.list.join.{len(items)}", **dict(zip("abcd", items)))


def _f13_more_line(k: int) -> str:
    """"+1 more change in the caption" / "+{k} more changes in the caption" (f13_list's `more` line)."""
    return _t("f13s.list.more.one") if k == 1 else _t("f13s.list.more.many", k=k)


def _thirteen_f_parts(rec: rules.ThirteenFFiling, run_date: date) -> _Parts:
    """The main lane's 13F roundup (a `THIRTEEN_F_LEAD_FILERS` filer first: Berkshire): the ONE 13F list design
    (`_f13_list_parts`, news-v23). Its moves are ranked by dollar size here (`_f13_by_value`); its counts are never
    stated as the filing's ("{n} of the holding changes in …"): the record does not say whether they are every change
    the filing reports (the adapter's ``counts_partial``)."""
    return _f13_list_parts(rec, run_date, moves=_f13_by_value(rec.moves), every=False)


def _thirteen_f_spotlight_parts(rec: rules.ThirteenFSpotlight, run_date: date) -> _Parts:
    """The spotlight lane's 13F post — the same list design (`_f13_list_parts`, news-v23): the record's moves are
    already in `rules.thirteen_f_spotlight_order` (the largest dollar size first); its counts are the filing's only
    when its rows are every change the filing reports (`rules.thirteen_f_spotlight_lists_every_change`)."""
    return _f13_list_parts(rec, run_date, moves=list(rec.moves),
                           every=rules.thirteen_f_spotlight_lists_every_change(rec))


def _f13_list_parts(rec: Any, run_date: date, *, moves: Sequence[rules.ThirteenFMove], every: bool) -> _Parts:
    """ONE filer's latest 13F as a ranked LIST (news-v23, the restyle — owner 2026-11-19: "i need more companies,
    similar with Berkshire hathaway latest 13F. and remove what users don't care such as quarter before, quarter
    ended" / "the range from 2 to 6 are okay"), both 13F series, the largest dollar change first (`moves`, ranked by
    the caller: `rules.thirteen_f_move_flow` — a new holding's value, the changed shares at the filing's value per
    share, a closed-out holding's previous-quarter value; a move without a value after every sized one, in the
    record's order). Share counts only — no %, no dollar figure, never "sold" / "exited" / "dumped". The subject is
    the filing ENTITY.

    * the PICTURE (`rows`, f13_list_berkshire.jpg / f13_list_pershing.jpg): the kicker "13F FILING", the title
      "{filer}'s latest 13F", the FILER's plate (its logo and ticker chip) only when it is listed, the column label
      "Change in shares", at most F13_PICTURE_ROWS rows — the logo, the ticker chip, the name, the status
      ("Increased to 40M shares", "Reduced to 20M shares", "New holding", "Closed out") and the signed change in
      shares ("+5.6M", "−14.6M") — and "+N more changes in the caption" when the record holds more. No subtitle, no
      fact rows, no quarter;
    * the COVER (cover_13f_berkshire.jpg): the same list's first F13_COVER_ROWS rows on the opening card's `list`,
      "+N more changes in the caption" under them when the record holds more;
    * the narration: the hook (the counts, the direction first), one line per kind in row order (its holdings named,
      "it closed out its holdings of …"), what a 13F shows, the 13F explainer — no person, the filer an entity;
    * the captions: the first line (the filing's counts, or "{n} of the holding changes in …"), one paragraph per
      holding, its listing, the period, the explainer, the closing question. EVERY caption that rides the picture lists
      the holdings the picture leaves out, and every caption that rides the video those the cover leaves out — an
      outlet that cannot is dropped (`over_budget`), never posted beside a "+N more" it does not keep.

    A holding no longer listed is "Closed out" only after the adapter's BUY-OUT CHECK (a company bought out, merged
    or delisted during or after the quarter is dropped before it reaches a record)."""
    _f13_checked(rec, run_date)
    filer = rec.filer_name
    n = len(moves)
    filer_sp = spoken_company(filer)
    poss, poss_sp = possessive(filer), possessive(filer_sp)
    tag = "of" if poss is None else "poss"
    if poss is None:
        subj = _t("f13.subj.of", filer=filer)
        subj_sp = [_t("f13.subj_sp.of", filer_sp=filer_sp), _t("f13.subj_sp.of.short", filer_sp=filer_sp)]
    else:
        subj = _t("f13.subj.poss", filer_s=poss)
        subj_sp = [_t("f13.subj_sp.poss", filer_sp_s=poss_sp), _t("f13.subj_sp.poss.short", filer_sp_s=poss_sp)]
    where = _t(f"f13s.list.in.{tag}", filer=filer, filer_s=poss)
    where_sp = [_t(f"f13s.list.in.{tag}{size}", filer=filer_sp, filer_s=poss_sp) for size in ("", ".short")]
    l_period, c_period, as_of, period_p = _f13s_period(rec, run_date)

    # the rows, in rank order: the picture's and the cover's row, the caption paragraph, the alt text's item; the kinds
    # in the order they first appear
    by: Dict[str, List[rules.ThirteenFMove]] = {}
    rows: List[Dict[str, Any]] = []
    row_paras: List[str] = []
    alt_rows: List[str] = []
    listed_paras: List[str] = []
    rounded: List[bool] = []
    said_before = False
    for mv in moves:
        kind, co, sym = _F13_SHORT[mv.move], mv.company.name, mv.company.symbol
        by.setdefault(kind, []).append(mv)
        if kind == "gone":
            prev_w, prev_s, prev_noun = _share_figure(mv.prev_shares, "previous")
            fig, r1 = _bare_figure(prev_s)
            status, r2 = _t("f13s.list.row.gone"), False
            fig_id = "f13s.list.fig.minus"
            row_paras.append(_t("f13s.list.p.gone", co=co, sym=sym, prev_w=prev_w, prev_noun=prev_noun))
            alt_rows.append(_t("f13s.list.alt.row.gone", co=co, prev_s=prev_s, prev_noun=prev_noun))
        elif kind == "new":
            shares_w, shares_s, share_noun = _share_figure(mv.shares, "reported")
            fig, r1 = _bare_figure(shares_s)
            status, r2 = _t("f13s.list.row.new"), False
            fig_id = "f13s.list.fig.plus"
            row_paras.append(_t("f13s.list.p.new", co=co, sym=sym, shares_w=shares_w, share_noun=share_noun))
            alt_rows.append(_t("f13s.list.alt.row.new", co=co, shares_s=shares_s, share_noun=share_noun))
            if mv.listed_on is not None:
                listed_paras.append(_t("f13.p.listed", co=co, month_cap=month_words(mv.listed_on)))
        else:
            shares_w, shares_s, share_noun = _share_figure(mv.shares, "reported")
            prev_w, prev_s, _prev_noun = _share_figure(mv.prev_shares, "previous")
            _diff_w, diff_s, _diff_noun = _share_figure(float(abs(_dec(mv.shares) - _dec(mv.prev_shares))), "change")
            fig, r1 = _bare_figure(diff_s)
            latest, r2 = _bare_figure(shares_s)
            status = _t(f"f13s.list.row.{kind}", shares_s=latest, share_noun=share_noun)
            fig_id = "f13s.list.fig.minus" if kind == "fewer" else "f13s.list.fig.plus"
            row_paras.append(_t(f"f13s.list.p.{kind}{'.next' if said_before else ''}", co=co, sym=sym,
                                shares_w=shares_w, share_noun=share_noun, prev_w=prev_w))
            said_before = True
            alt_rows.append(_t(f"f13s.list.alt.row.{kind}", co=co, shares_s=shares_s, share_noun=share_noun,
                               prev_s=prev_s))
        rounded.append(r1 or r2)
        rows.append({"logo": sym, **_row_chip(sym), "cells": [co, status, _t(fig_id, diff_s=fig)]})
    kinds = list(by)

    def counted(prefix: str, *, first_noun: bool) -> List[str]:
        """Each kind's count in row order (`prefix`.<kind>), the first one carrying the noun when `first_noun`."""
        out = []
        for i, kind in enumerate(kinds):
            k = len(by[kind])
            cid = f"{prefix}.{kind}.first" if first_noun and i == 0 else f"{prefix}.{kind}"
            out.append(_t(cid, n=k, holdings=_holdings(k), ones=_t("f13s.list.one" if k == 1 else "f13s.list.ones")))
        return out

    # the picture: the list's first F13_PICTURE_ROWS rows, "+N more changes in the caption" for the rest
    shown = rows[:F13_PICTURE_ROWS]
    more = n - len(shown)
    section: Dict[str, Any] = {"heading": _t("f13s.list.heading"), "rows": shown}
    if more > 0:
        section["more"] = _f13_more_line(more)
    spec: Dict[str, Any] = {"layout": "rows", "version": onscreen.SPEC_VERSION, "kicker": _t("f13s.kicker"),
                            "title": subj, "sections": [section]}
    names: Dict[str, str] = {mv.company.symbol: mv.company.name for mv in moves}
    if rec.filer_symbol is not None:
        # the FILER's plate (its logo, its ticker chip) beside the title — only for a listed filer (else no plate)
        spec["header"] = {"logo": rec.filer_symbol, "name": filer, "chip": rec.filer_symbol}
    logo_key = rec.filer_symbol if rec.filer_symbol is not None else F13S_FILER_LOGO_KEY
    if logo_key in names:                       # never: a holding that is the filer is refused first
        raise NewsTemplateRefused("record_invalid", "the filer's logo key is also a holding")
    names[logo_key] = filer
    # the cover (cover_13f_berkshire.jpg): the same title, plate and list — its first F13_COVER_ROWS rows
    cover_rows = rows[:F13_COVER_ROWS]
    cover_more = n - len(cover_rows)
    cover_list: Dict[str, Any] = {"heading": _t("f13s.list.heading"), "rows": cover_rows}
    if cover_more > 0:
        cover_list["more"] = _f13_more_line(cover_more)
    opening: Dict[str, Any] = {"kicker": _t("f13s.kicker"), "logos": [logo_key], "headline": subj, "list": cover_list}
    if rec.filer_symbol is not None:
        opening["chip"] = rec.filer_symbol

    # the hook, the DIRECTION first: the filing's own counts with their verbs ("it reduced 2 holdings, increased 1, and
    # closed out 1"); else the rows' counts ("5 of the holding changes in …: 2 reduced, 1 new, …"); then, past
    # HOOK_WORDS (three or four kinds, a long filer name), the first two kinds' counts ("…, including 2 reduced and 1
    # new") and the first kind's alone, and only last the counts-free "{n} (of the) holding changes in …"
    clauses = _f13s_join(counted("f13s.list.cl", first_noun=True))
    rest = counted("f13s.list.c", first_noun=False)
    rest_counts = ", ".join(rest)
    incls = [_f13s_join(rest[:k]) for k in ((2, 1) if len(rest) > 2 else (1,) if len(rest) == 2 else ())]
    if every:
        hook = [_t("f13s.list.hook.all", subj_sp=s, clauses=clauses) for s in subj_sp]
        hook += [_t("f13s.list.hook.all.counts", subj_sp=subj_sp[1], counts=rest_counts)]
        hook += [_t("f13s.list.hook.all.incl", n=n, where=w, incl=i) for i in incls for w in where_sp]
        hook += [_t("f13s.list.hook.all.n", n=n, where=w) for w in where_sp]
        hook += [_t("f13s.list.hp.all.min", n=n, filer=filer_sp)]
    else:
        spoken = dict(n=n, filer=filer_sp, filer_s=poss_sp)
        hook = [_t("f13s.list.hook.some.counts", n=n, where=w, counts=rest_counts) for w in where_sp]
        hook += [_t(f"f13s.list.hp.some.{tag}", counts=rest_counts, **spoken)]
        hook += [_t(f"f13s.list.hook.some.incl.{tag}", incl=i, **spoken) for i in incls]
        hook += [_t("f13s.list.hook.some", n=n, where=w) for w in where_sp]
        hook += [_t(f"f13s.list.hp.some.min.{tag}", **spoken)]

    # narration and cards: one line per kind in row order (its holdings named, the largest first) — the first three
    # kinds — then what a 13F shows and the 13F explainer; a list of one kind narrates the filing's quarter third
    def kind_lines(kind: str, *, nxt: bool) -> Tuple[List[str], Tuple[str, str]]:
        mvs = by[kind]
        size = "one" if len(mvs) == 1 else "many"
        variants = _list_variants([spoken_company(m.company.name) for m in mvs], len(mvs))
        lines = [_t(f"f13s.list.l.{kind}.{size}{'.next' if nxt else ''}", list=v) for v in variants]
        lines += [_t(f"f13s.list.l.{kind}.{size}.short", list=v) for v in variants]
        title = _t(f"f13s.list.head.new.{size}") if kind == "new" else _t(f"f13s.list.head.{kind}")
        card = (title, _list_variants([m.company.name for m in mvs], len(mvs))[0])
        return list(dict.fromkeys(lines)), card

    explainer2 = ([_t("f13.l.explainer2")], (_t("f13.c.explainer2.title"), _t("f13.c.explainer2.body")))
    slots = [kind_lines(k, nxt=i > 0) for i, k in enumerate(kinds[:3])]
    slots.append(explainer2)
    if len(slots) < 3:
        slots.append((l_period, c_period))
    l1, c1 = slots[0]
    l2, c2 = slots[1]
    l3, c3 = slots[2]
    l4 = [_t("f13.l4.explainer"), _t("f13.l4.explainer.short")]
    c4 = (_t("f13.c4.explainer.title"), _t("f13.c4.explainer.body"))

    # captions: the first line (the filing's own counts, with their noun — or "{n} of the holding changes in …"), one
    # paragraph per holding in rank order, a newly listed holding's listing, the period, the 13F explainer and the
    # closing question. What each field MUST carry (`_Parts.field_required`): the picture's "+N more" promises the
    # holdings it leaves out on every caption that can ride the picture (x, bluesky, threads, facebook, linkedin); the
    # cover's promises those it leaves out on every caption that can ride the video (tiktok, instagram, the YouTube
    # description — facebook / linkedin / threads videos are re-checked at freeze, `extra_video_problems`)
    if every:
        heads = [_t("f13s.list.h.all", subj=subj, counts=", ".join(counted("f13s.list.c", first_noun=True))),
                 _t("f13s.list.h.all.min", subj=subj, n=n)]
        after = [_t("f13s.list.hp.all", filer=filer, clauses=clauses), _t("f13s.list.hp.all.min", n=n, filer=filer)]
        yt = [_t("f13s.list.yt", subj=subj, n=n)]
        yt += [_t("f13s.list.yt", subj=_t("f13s.list.in.poss.short", filer_s=poss), n=n)] if poss is not None else []
        yt += [_t("f13s.list.yt.min", n=n)]
    else:
        heads = [_t("f13s.list.h.some", n=n, where=where, counts=rest_counts),
                 _t("f13s.list.h.some.min", n=n, where=where)]
        after = [_t(f"f13s.list.hp.some.{tag}", n=n, filer=filer, filer_s=poss, counts=rest_counts),
                 _t(f"f13s.list.hp.some.min.{tag}", n=n, filer=filer, filer_s=poss)]
        yt = [_t("f13s.list.yt.some", subj=subj, n=n)]
        yt += ([_t("f13s.list.yt.some", subj=_t("f13s.list.in.poss.short", filer_s=poss), n=n)]
               if poss is not None else [])
        yt += [_t("f13s.list.yt.min.some", n=n)]
    tail = listed_paras + [period_p, _t("f13.p.explainer"), _t("f13s.p.question")]
    paras = row_paras + tail
    picture_rest, cover_rest = row_paras[len(shown):], row_paras[len(cover_rows):]
    text = picture_rest if picture_rest else row_paras
    # Threads also carries an EXTRA video post (Batch 3) whose caption is this one: it lists the COVER's rest when it
    # fits (the cover's promise — a superset of the picture's), else the picture's rest (`field_fallbacks` below)
    field_paragraphs: Dict[str, List[str]] = {"x": text, "bluesky": text,
                                              "threads": cover_rest if cover_rest else row_paras,
                                              "tiktok": cover_rest if cover_rest else row_paras[:1]}
    field_required: Dict[str, int] = {f: n for f in ("facebook", "linkedin", "instagram", "youtube_description")}
    field_required.update({f: len(picture_rest) for f in ("x", "bluesky")})
    field_required["threads"] = len(cover_rest)
    field_required["tiktok"] = len(cover_rest)
    # a long caption that cannot list every holding (eight at the widest names pass Facebook's 900 characters) keeps
    # the cover's rest — still every promise of both the cover and the picture — then the picture's rest alone (a
    # picture post's own promise; its extra video is then re-checked and skipped, `extra_video_problems`)
    field_fallbacks: Dict[str, List[Tuple[List[str], int]]] = {}
    for f, rests in (("facebook", (cover_rest, picture_rest)), ("linkedin", (cover_rest, picture_rest)),
                     ("instagram", (cover_rest,)), ("youtube_description", (cover_rest,))):
        for r in rests:
            plan = (list(r) + tail, len(r))
            if len(r) < n and plan not in field_fallbacks.setdefault(f, []):
                field_fallbacks[f].append(plan)
    if cover_rest and picture_rest != cover_rest:
        field_fallbacks["threads"] = [(list(picture_rest), len(picture_rest))]

    # the alt text: what the PICTURE lists (its rows; the rest are "in the caption")
    family = "all" if every and more == 0 else "some"
    drawn_counts: Dict[str, int] = {}
    for mv in moves[:len(shown)]:
        drawn_counts[_F13_SHORT[mv.move]] = drawn_counts.get(_F13_SHORT[mv.move], 0) + 1
    alt_counts = ", ".join(_t(f"f13s.list.c.{k}", n=drawn_counts[k]) for k in kinds if drawn_counts.get(k))
    alt_head = _t(f"f13s.list.alt.{family}", n=len(shown), counts=alt_counts)
    if any(rounded[:len(shown)]):
        alt_head = f"{alt_head} {_t('f13s.list.alt.rounded')}"
    alt = [alt_head, _t("f13s.list.alt.rows", items="; ".join(alt_rows[:len(shown)]))]
    if more > 0:
        alt.append(_t("f13s.list.alt.more.one") if more == 1 else _t("f13s.list.alt.more.many", k=more))
    # review of 2026-11-26: a worker without look_v23 draws at most PRE_V23_MAX_SECTION_ROWS rows and no "+N more" line
    # (`template_onscreen.pre_v23_section`) — the alt text of THAT picture: its rows, "some" of the changes, and no
    # promise about the caption (a tight caption lists only the rows past the sixth)
    alt_pre_v23: Optional[List[str]] = None
    k_old = onscreen.PRE_V23_MAX_SECTION_ROWS
    if len(shown) > k_old:
        old_counts: Dict[str, int] = {}
        for mv in moves[:k_old]:
            old_counts[_F13_SHORT[mv.move]] = old_counts.get(_F13_SHORT[mv.move], 0) + 1
        old_head = _t("f13s.list.alt.some", n=k_old, counts=", ".join(
            _t(f"f13s.list.c.{k}", n=old_counts[k]) for k in kinds if old_counts.get(k)))
        if any(rounded[:k_old]):
            old_head = f"{old_head} {_t('f13s.list.alt.rounded')}"
        alt_pre_v23 = [old_head, _t("f13s.list.alt.rows", items="; ".join(alt_rows[:k_old]))]
    return _Parts(hook=hook, lines=[l1, l2, l3, l4], cards=[c1, c2, c3, c4], opening=opening, image_spec=spec,
                  alt_title=_alt_title([subj, _t("f13s.list.alt.title.min")]), alt_paragraphs=alt,
                  source=rules.source_label(rec), as_of=as_of,
                  image_headlines=heads, image_paragraphs=paras, video_headlines=heads, video_paragraphs=paras,
                  youtube_titles=list(dict.fromkeys(yt)), persons=[], names=names,
                  after_prefix=dict(zip(heads, after)), paragraph_limit=len(paras),
                  field_paragraphs=field_paragraphs, field_required=field_required, field_fallbacks=field_fallbacks,
                  video_promise=tuple(cover_rest), footer_when=_f13_footer_when(rec), text_paragraphs=list(row_paras),
                  alt_paragraphs_pre_v23=alt_pre_v23)


# ── Money Map ─────────────────────────────────────────────────────────────────


def _ratio(x: Decimal) -> float:
    r = float(_q(x, RATIO_PLACES))
    return min(max(r, 0.0), 1.0)


#: Owner decision 2026-11-20 (the Money Map LOSS year, "A: costs vs revenue"): the costs card's figures are drawn at
#: one decimal (`money_image`'s own form) unless the reader's arithmetic does not land at that precision (the designers'
#: production notes: "check that the shown parts add within rounding slack … If they do not, use two decimals for that
#: post") — then at two (`_money_map_costs`).
MONEY_MAP_COSTS_PLACES: Tuple[int, ...] = (1, 2)
_UNIT_BASE: Dict[str, Decimal] = {suffix: base for base, suffix in _SHORT_UNITS}


def _costs_figure(value: Decimal, places: int, unit: str) -> Tuple[str, Decimal, Decimal]:
    """A costs-card figure — a MAGNITUDE — as drawn, the value it shows and half its last shown digit (the most the
    rounding hides). `places` 1: exactly `money_image`'s form ("$5.9B", "$110M", "$4B", "$850"); 2: two decimals, the
    trailing zero kept ("$5.88B", "$5.80B"), for a figure in `unit` (the costs figure's own unit) — a figure in a smaller
    unit keeps the one-decimal form, already finer than the post's last digit. Pure."""
    a = abs(value)
    whole = _q(a, 0)
    if whole < 1000:
        return f"${whole:,}", whole, Decimal("0.5")
    if places == 2:
        scaled, suffix = _scaled(a, _SHORT_UNITS, 2)
        if suffix == unit:
            base = _UNIT_BASE[suffix]
            return f"${scaled:,.2f}{suffix}", scaled * base, Decimal("0.005") * base
    scaled, suffix = _scaled(a, _SHORT_UNITS, 1)
    base = _UNIT_BASE[suffix]
    return f"${_one_decimal(scaled)}{suffix}", scaled * base, Decimal("0.05") * base


def _lands(terms: Sequence[Tuple[int, Tuple[str, Decimal, Decimal]]], result: Tuple[str, Decimal, Decimal]) -> bool:
    """Whether the reader's arithmetic on the SHOWN figures (`terms`: (sign, figure)) lands on the shown `result` — within
    half the last digit of the COARSEST figure in the sum ("9.7 − 5.8 = 3.9" holds; "9.7 − 5.8" beside "$3.8B" does
    not; "4.7 − 4 = 0.7" beside "$720M" does: the reader sees one digit of rounding). Stricter than the approved
    `check_sum`'s slack (every figure's half digit summed), which any honest rounding always meets."""
    got = sum((sign * fig[1] for sign, fig in terms), Decimal(0))
    tol = max([fig[2] for _sign, fig in terms] + [result[2]])
    return abs(got - result[1]) <= tol


@dataclass(frozen=True)
class _Costs:
    """A loss year's costs card (`_money_map_costs`): the `image_spec` / opening card block, the alt text's sentence, the
    precision its figures were drawn at, and whether the reader's arithmetic lands at it (`_lands`; False only when even
    two decimals do not, which the post then draws anyway: every figure is still the true value rounded)."""

    block: Dict[str, Any]
    alt: str
    places: int
    adds_up: bool


def _money_map_costs(rev: Decimal, gp: Optional[Decimal], op: Optional[Decimal], ni: Decimal) -> _Costs:
    """The COSTS card of a LOSS year (owner decision 2026-11-20, "A: costs vs revenue"; money_map_loss_A_rivian.jpg and
    the designers' production notes), from the statement lines the record already holds — derived, so the chain is exact
    whatever FMP's own `operatingExpenses` or its restructuring lines say:

    * costs and expenses = revenue − net income (revenue + the net loss): the longer red bar, its SOLID red;
    * cost of sales = revenue − gross profit; operating expenses = gross profit − operating income — the bar's parts,
      only when the record has both lines and each part is positive (else ONE undivided costs bar, no legend);
    * interest, tax and other items = net income − operating income: when a COST, a third part (maroon); when INCOME,
      no part — the bar's last stretch, the costs it covered, is hatched and ONE note says "+$X from interest, tax and
      other items made the loss smaller" (the parts then add up to costs + X, exactly what the note states); zero, or
      under half the costs figure's last shown digit (it cannot move a shown total), it is omitted and operating
      expenses' BAR (never its value) absorbs it, so the bar still ends where costs do.

    Figures at one decimal, else two (`MONEY_MAP_COSTS_PLACES`, `_lands`: parts ± other = costs, costs − revenue = the
    net loss). Every ratio on ONE scale (the longest bar = 1): the template's `implausible_figures` refuses a net,
    gross or operating loss larger than revenue (`money_map_loss_above_revenue` at the read), so the costs bar is at most
    twice the revenue bar and the revenue bar at least half the scale. The callout's costs per $100 is 100 + the net loss
    per $100 (`money_cents`), so its subtraction on the picture is exact. Pure; `ni` must be negative."""
    if ni >= 0:
        raise ValueError("a costs card is drawn for a loss year only")
    costs = rev - ni
    other = None if op is None else ni - op
    cos = opex = None
    if gp is not None and op is not None and rev - gp > 0 and gp - op > 0:
        cos, opex = rev - gp, gp - op
    unit = _scaled(costs, _SHORT_UNITS, 2)[1] if _q(costs, 0) >= 1000 else ""
    per100 = money_cents(float(abs(ni) / rev * 100))
    cents = _q(abs(_dec(float(abs(ni) / rev * 100))), 2)
    c100 = f"${Decimal(100) + cents:,.2f}"
    plan: Optional[_Costs] = None
    for places in MONEY_MAP_COSTS_PLACES:
        rev_t, costs_t, net_t = (_costs_figure(rev, places, unit), _costs_figure(costs, places, unit),
                                 _costs_figure(ni, places, unit))
        parts: List[Tuple[str, Decimal, Tuple[str, Decimal, Decimal]]] = []
        note_t: Optional[Tuple[str, Decimal, Decimal]] = None
        if cos is not None and opex is not None:
            parts = [("mm.cost.sales", cos, _costs_figure(cos, places, unit)),
                     ("mm.cost.opex", opex, _costs_figure(opex, places, unit))]
            if other is not None and other != 0 and abs(other) >= costs_t[2]:
                if other < 0:
                    parts.append(("mm.cost.other", -other, _costs_figure(other, places, unit)))
                else:
                    note_t = _costs_figure(other, places, unit)
            elif costs - cos <= 0:
                parts = []                       # a sliver of other income past every cost: no part to absorb it
        # the ONE scale and each bar's ratio (never drawn)
        if not parts:
            scale = costs
            part_ratios: List[float] = []
        elif note_t is not None:
            scale = cos + opex                   # = costs + the other income (the hatched end)
            part_ratios = [_ratio(cos / scale), _ratio(opex / scale)]
        elif len(parts) == 3:
            scale = costs                        # = cost of sales + operating expenses + the other cost
            part_ratios = [_ratio(v / scale) for _id, v, _f in parts]
        else:
            scale = costs                        # operating expenses' bar absorbs an omitted sliver of other
            part_ratios = [_ratio(cos / scale), _ratio((costs - cos) / scale)]
        terms = [(1, f) for _id, _v, f in parts] + ([(-1, note_t)] if note_t is not None else [])
        adds_up = _lands([(1, costs_t), (-1, rev_t)], net_t) and (not parts or _lands(terms, costs_t))
        block: Dict[str, Any] = {
            "revenue": {"label": _t("mm.flow.revenue"), "value": rev_t[0], "ratio": _ratio(rev / scale)},
            "net": {"label": _t("mm.flow.net.loss"), "value": net_t[0]},
            "total": {"label": _t("mm.cost.total"), "value": costs_t[0], "ratio": _ratio(costs / scale)},
            "parts": [{"label": _t(pid), "value": f[0], "ratio": r} for (pid, _v, f), r in zip(parts, part_ratios)],
        }
        if note_t is not None:
            block["note"] = _t("mm.cost.note", other_s=note_t[0])
        block["callout"] = [_t("mm.cost.callout.lead", c100=c100), _t("mm.cost.callout.net", per100=per100)]
        items = "; ".join(_t("mm.alt.item", label=p["label"], value=p["value"]) for p in block["parts"])
        alt = (_t("mm.alt.costs", rev_s=rev_t[0], costs_s=costs_t[0], items=items) if items
               else _t("mm.alt.costs.bare", rev_s=rev_t[0], costs_s=costs_t[0]))
        if note_t is not None:
            alt += f" {block['note']}."
        alt += " " + _t("mm.alt.costs.net", ni_s=net_t[0])
        plan = _Costs(block=block, alt=alt, places=places, adds_up=adds_up)
        if adds_up:
            return plan
        logger.info("news template money_map: the costs card's figures do not add up at %d decimal(s) (%s − %s vs %s) — "
                    "%s", places, costs_t[0], rev_t[0], net_t[0],
                    "two decimals" if places < MONEY_MAP_COSTS_PLACES[-1] else "drawn at two decimals anyway")
    if plan is None:                             # MONEY_MAP_COSTS_PLACES is never empty
        raise ValueError("no costs-card precision to try")
    return plan


def _money_map_parts(rec: rules.MoneyMap, run_date: date) -> _Parts:
    if rec.period_end > run_date or (run_date - rec.period_end).days > MONEY_MAP_MAX_AGE_DAYS:
        raise NewsTemplateRefused("stale_source", "the fiscal year is not the latest reported")
    co, sym = rec.company.name, rec.company.symbol
    for value in [co] + [s.name for s in rec.segments]:
        problem = _slot_problem(value)
        if problem:
            raise NewsTemplateRefused("slot_rejected", f"name: {problem}")
    # the review of 2026-10-16: no company-name word the cover's headline cannot draw whole (the record refuses
    # it too, `company_news_rules.COMPANY_NAME_WORD_MAX_CHARS`; this stands behind it, as the other series' do)
    _name_slot(co, "company name")
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
    # news-v19 (Batch 1): "by region" only when EVERY named part is a region by its exact name — else the
    # neutral wording, true of product lines and markets alike (the rest bar is never a named part). The review
    # of 2026-10-16 (MM-6): and only while the rest is small — Coca-Cola's live map held $10.4B (22%)
    # "Unallocated", mostly its Bottling Investments and Global Ventures businesses, which "by region" passed
    # off as unassigned regions
    by_region = all(rules.is_region_name(s.name) for s in segs) and other <= MONEY_MAP_REGION_MAX_OTHER_SHARE * rev

    def said(name: str, *, opens: bool = False) -> str:
        """A part's name in RUNNING TEXT: "the United States" for a `REGION_ARTICLE` name ("The …" when it
        opens the sentence), else the name as drawn. Never on a bar label, a card or the alt text."""
        if not rules.takes_region_article(name):
            return name
        return _t("mm.region.the.cap" if opens else "mm.region.the", name=name)

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
    l2 = [_t("mm.l2.two", s1=said(segs[0].name, opens=True), sh1=sh[0], s2=said(segs[1].name), sh2=sh[1],
             basis=basis),
          _t("mm.l2.one", s1=said(segs[0].name, opens=True), sh1=sh[0], basis=basis)]
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
        "header": {"logo": sym, "name": co, "chip": sym}, "title": title, "subtitle": _t("mm.img.subtitle", fy=fy),
        "segments": segments, "flow": flow, "callout": callout,
    }
    # news-v23 (2026-11-20): the cover reads like the picture — its two-tone headline ("HOW COSTCO" / "MAKES MONEY"),
    # the plate's TICKER chip, then the "per $100" figure (GREEN a profit, RED a loss: the worker's
    # `cards.money_map_net_tone`) with its label under it inside the card. The fiscal year rides the cover's short
    # footer ("… · fiscal 2025"); a worker without the label is sent neither the label nor the figure
    # (`script_service._without_figure_label`)
    opening = {"kicker": _t("mm.kicker"), "logos": [sym], "chip": sym, "figure": per100,
               "figure_label": _t("mm.open.profit" if profit else "mm.open.loss"), "headline": title}
    # Owner decision 2026-11-20 ("for companies who are loss, it's a bit hard to understand … a cost or loss will have a
    # longer red bar than the revenue", and "A: costs vs revenue (Recommended)"): a LOSS year's picture draws the COSTS
    # card in place of the flow's gross / operating / net steps and its one-line callout, and its video cover is that
    # card under the headline and sub line (money_map_loss_A_rivian_cover.jpg). The flow, the callout, the figure and its
    # label stay in the stored output — what a worker without WORKER_CAPABILITY_LOOK_V23 is sent (`script_service`
    # strips `costs` and the cover's sub line), and the cover a worker falls back to when the costs cover cannot fit
    # (`cards._cover_or_plain`). A PROFIT year is untouched, key for key.
    costs_card: Optional[_Costs] = None
    if not profit:
        costs_card = _money_map_costs(rev, gp, op, ni)
        if costs_card.places > MONEY_MAP_COSTS_PLACES[0]:
            # review of 2026-11-26: a year whose costs card needed two decimals drew revenue twice on ONE picture —
            # "$5.8B" on the top card (`flow[0]`, the worker's revenue total) and "$5.84B" on the costs card. The top
            # card now prints the costs card's revenue figure (`spec["flow"]` is this very list); a worker without
            # look_v23 draws the flow steps under it, every figure still the true value rounded
            flow[0]["value"] = costs_card.block["revenue"]["value"]
        spec["costs"] = costs_card.block
        opening["subtitle"] = spec["subtitle"]
        opening["costs"] = copy.deepcopy(costs_card.block)
    # captions
    seg_items = [_t("mm.item", name=said(s.name), value=money_words(s.value_usd)) for s in segs]
    if other > 0:
        seg_items.append(_t("mm.item.other", value=money_words(float(other))))
    seg_para = _t("mm.p.segments.region" if by_region else "mm.p.segments", fy=fy, items="; ".join(seg_items))
    if elim < 0:
        seg_para += " " + _t("mm.p.elim", elim=money_words(float(abs(elim))))
    paras = [seg_para]
    if costs != "none":
        paras.append(_t(f"mm.p.costs.{costs}", **gp_fill, **op_fill))
    paras.append(_t("mm.p.net.profit" if profit else "mm.p.net.loss", ni=ni_w, per100=per100))
    # news-v19 (Batch 1): where the figures come from — never "as reported" — and, whenever a gross line is
    # shown, what gross profit is (FMP's own revenue − cost of revenue where the company reports no subtotal)
    source_para = _t("mm.p.source", fy=fy)
    if gp is not None:
        source_para += " " + _t("mm.p.gross" if gp >= 0 else "mm.p.gross.loss")
    paras.append(source_para)
    heads = [_t("mm.ch.long.profit" if profit else "mm.ch.long.loss", co=co, rev=rev_w, fy=fy, per100=per100),
             _t("mm.ch.short", co=co, fy=fy)]
    # The alt text is plain text: a dollar figure keeps its ASCII "-" ("Intersegment eliminations -$4.3B"), as the
    # earnings alt text does ("EPS -$0.05"). Only the DRAWN value carries U+2212 (`money_image`; final review
    # 2026-11-20: the round-2 minus leaked into the alt text through the bars' shared value).
    alt = [_t("mm.alt.segments.region" if by_region else "mm.alt.segments",
              items="; ".join(_t("mm.alt.item", label=b["label"], value=_alt_money(b["value"])) for b in segments)),
           _t("mm.alt.flow", items="; ".join(_t("mm.alt.item", label=b["label"], value=_alt_money(b["value"]))
                                             for b in flow)),
           _t("mm.alt.callout", callout=callout)]
    alt_pre_v23: Optional[List[str]] = None
    if costs_card is not None:
        # owner decision 2026-11-20: the loss year's alt text says what ITS picture draws — revenue beside the costs
        # bar, its parts (or the other income's note), the net loss past revenue, and the costs callout. Review of
        # 2026-11-26: a worker without look_v23 draws the flow steps and their callout instead (`script_service`
        # strips the costs card) — the alt text of THAT picture is kept beside it (`image_post_pre_v23`), and the
        # publisher sends the one describing the picture the worker registered (`picture_alt`)
        alt_pre_v23 = list(alt)
        alt[1:] = [costs_card.alt, _t("mm.alt.callout", callout=" ".join(costs_card.block["callout"]))]
    as_of = _t("mm.asof", fy=fy, period_img=date_image(rec.period_end))
    return _Parts(hook=hook, lines=[l1, l2, l3, l4],
                  cards=[c1, (_t("mm.c2.title"), c2_body[0]), c3, c4],
                  opening=opening, image_spec=spec, alt_title=title, alt_paragraphs=alt,
                  source=rules.source_label(rec), as_of=as_of, image_headlines=heads, image_paragraphs=paras,
                  video_headlines=heads, video_paragraphs=paras,
                  youtube_titles=[_t("mm.yt", co=co, fy=fy), _t("mm.yt.short", co=co)],
                  persons=[], names={sym: co}, footer_when=_t("foot.fiscal", fy=fy),
                  alt_paragraphs_pre_v23=alt_pre_v23)


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


def _congress_checked(rec: rules.CongressMonth, run_date: date) -> date:
    """The record-wide checks of a Congress Count (its month and its read), returning the month's first day."""
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
    return start


def _congress_row(count: rules.CongressCount) -> None:
    """One company of a Congress Count — the per-company gates (`drop_refused_rows` drops a company that fails one; a
    record that reaches `compose` with one is refused whole: forged)."""
    n = count.members
    # ≥ 2 DISTINCT members: "1 member of Congress" points at one findable person (rules §1)
    if not isinstance(n, int) or isinstance(n, bool) or n < rules.CONGRESS_MIN_MEMBERS or n > 535:
        raise NewsTemplateRefused("too_few_rows", "a count of fewer than two members is never posted")
    _name_slot(count.company.name, "company name")


def _congress_parts(rec: rules.CongressMonth, run_date: date) -> _Parts:
    """ONE Congress Count post of 2-3 companies (news-v23, the restyle — owner 2026-11-19: "for congress disclosed, i
    want to have 2-3 companies at once"; congress_multi3.jpg + theme_congress_defense.jpg): the most members first.

    * the PICTURE (`rows`): the kicker, the title "Members of Congress disclosed purchases", the subtitle "{month} ·
      disclosures as of {day}", one row per company — its logo, ticker chip, name, "members disclosed purchases of
      {co} stock" and its count (cells: name, line, count — the worker draws the count GREEN in a tile on the left and
      "purchases" on a solid GREEN pill) — and the note "Each member counted once per company";
    * the COVER: the same title and rows on the opening card's `list` (cover_13f_berkshire.jpg's idea);
    * the narration: the lead company's count (the hook), the other companies' counts, the period, "counted once per
      company" with the spouse hedge, the reporting delay — never a member, a party, a state, a chamber;
    * the captions: every count in the long first line (else the companies' names), a paragraph per company, the
      period, "counted once per company", the report hedges.
    Every count is ≥ 2 DISTINCT members; "disclosed purchases", never "bought"."""
    start = _congress_checked(rec, run_date)
    for count in rec.counts:
        _congress_row(count)
    counts = list(rec.counts)
    n_co = len(counts)
    fetched = rec.fetched_on
    month = dict(month_long=month_words(start), month_name=month_name(start), month_upper=month_name(start).upper(),
                 month_short=month_image(start), as_of_img=date_image(fetched), as_of_sp=date_spoken(fetched, run_date),
                 as_of_cap=date_caption(fetched, run_date))
    cos = [c.company.name for c in counts]
    syms = [c.company.symbol for c in counts]
    ns = [c.members for c in counts]
    sps = [spoken_company(co) for co in cos]
    kicker = _t("cg.kicker", **month)
    title = _t("cg.m.title")
    rows = [{"logo": sym, **_row_chip(sym), "cells": [co, _t("cg.m.row", co=co), str(n)]}
            for co, sym, n in zip(cos, syms, ns)]
    spec: Dict[str, Any] = {
        "layout": "rows", "version": onscreen.SPEC_VERSION, "kicker": kicker, "title": title,
        "subtitle": _t("cg.m.sub", **month), "sections": [{"rows": rows}], "notes": [_t("cg.m.once")],
    }
    opening: Dict[str, Any] = {"kicker": kicker, "logos": [syms[0]], "headline": title,
                               "list": {"rows": rows[:onscreen.MAX_OPENING_ROWS]}}

    # narration: the hook is the LEAD company's count (`_validate_congress_wording`: "{n} members of Congress
    # disclosed …", n ≥ 2); line 1 the other companies' counts; then the period, counted once per company with the
    # spouse hedge, and the reporting delay
    lead = dict(n=ns[0], co_sp=sps[0], **month)
    hook = [_t("cg.hook.year", **lead), _t("cg.hook", **lead), _t("cg.hook.min", **lead)]
    if n_co == 2:
        l1 = [_t("cg.m.l.other.one", n=ns[1], co_sp=sps[1])]
        c1 = (_t("cg.m.c.other.title"), _t("cg.m.c.other.body.one", co=cos[1], n=ns[1]))
    else:
        pair = dict(n=ns[1], co_sp=sps[1], n2=ns[2], co2_sp=sps[2])
        l1 = [_t("cg.m.l.other.two", **pair), _t("cg.m.l.other.two.short", **pair)]
        c1 = (_t("cg.m.c.other.title"), _t("cg.m.c.other.body.two", co=cos[1], n=ns[1], co2=cos[2], n2=ns[2]))
    l2 = [_t("cg.m.l.count", **month), _t("cg.m.l.count.short", **month)]
    c2 = (_t("cg.c1.title"), _t("cg.c1.body", **month))
    l3 = [_t("cg.m.l.once")]
    c3 = (_t("cg.m.c.once.title"), _t("cg.m.c.once.body"))
    l4 = [_t("cg.l4"), _t("cg.m.l4.short")]
    c4 = (_t("cg.c4.title"), _t("cg.c4.body"))

    # captions: every count in the first line where it fits (else the names), a paragraph per company (left out under
    # the long line, which states every count), then the period, once per company and the report hedges
    names = _join_list(cos, n_co, n_co)
    long_head = (_t("cg.m.h.2", n=ns[0], co=cos[0], n2=ns[1], co2=cos[1], **month) if n_co == 2 else
                 _t("cg.m.h.3", n=ns[0], co=cos[0], n2=ns[1], co2=cos[1], n3=ns[2], co3=cos[2], **month))
    lead_heads = [_t("cg.m.h.lead", n=ns[0], co=cos[0], **month), _t("cg.m.h.lead.min", n=ns[0], co=cos[0], **month)]
    heads = [long_head, _t("cg.m.h.names", names=names, **month), _t("cg.m.h.min", names=names, **month),
             *lead_heads]
    row_paras = [_t("cg.m.p.row", co=co, sym=sym, n=n, **month) for co, sym, n in zip(cos, syms, ns)]
    paras = row_paras + [_t("cg.m.p.count", **month), _t("cg.m.p.once"), _t("cg.l3"), _t("cg.l4")]
    stated = {long_head: frozenset(row_paras), **{h: frozenset(row_paras[:1]) for h in lead_heads}}
    yt = [_t("cg.m.yt", names=names, **month), _t("cg.m.yt.short", names=names, **month),
          _t("cg.m.yt.lead", n=ns[0], co=cos[0], **month), _t("cg.m.yt.min", **month)]
    alt = [_t("cg.m.alt", n=n_co, **month),
           _t("cg.m.alt.rows", items="; ".join(_t("cg.m.alt.row", co=co, n=n) for co, n in zip(cos, ns)))]
    return _Parts(hook=hook, lines=[l1, l2, l3, l4], cards=[c1, c2, c3, c4], opening=opening, image_spec=spec,
                  alt_title=_alt_title([title]), alt_paragraphs=alt, source=rules.source_label(rec),
                  as_of=_t("cg.asof", **month), image_headlines=heads, image_paragraphs=paras,
                  video_headlines=heads, video_paragraphs=paras, youtube_titles=yt, persons=[],
                  names=dict(zip(syms, cos)), stated_by_headline=stated, paragraph_limit=len(paras),
                  footer_when=_t("foot.month", **month))


# ── Company Stakes (2b-2) ─────────────────────────────────────────────────────

#: The bases a posted stake may carry (`schemas.trillion_club.VALUE_BASES`), each with its own verb.
STAKE_BASES: Tuple[str, ...] = ("invested", "committed_up_to", "carrying_value", "fair_value")


def _names_a_person(text: str) -> bool:
    """A capitalised given name followed by a capitalised word ("Jensen Huang said …"): fail closed
    — the line is treated as naming a person."""
    toks = re.findall(r"[A-Za-z][A-Za-z'-]*", text)
    known = given_names()
    return any(t[0].isupper() and t.lower() in known and nxt[0].isupper() for t, nxt in zip(toks, toks[1:]))


def _stake_person_problem(rec: rules.CompanyStake, source: str) -> Optional[str]:
    """Batch 1 (2026-10-15, plan item 4): which drawn stake text names a person, or None.

    The source title (the image's source line, card 4, every caption) and the local listing (card 3)
    are drawn on every post, and an UNLISTED investee's name heads it — the cover, the hook, the
    YouTube title. Under the 2026-10-09 rules a person is named only in written body text, and a stake
    post has none, so a hand-kept "Jensen Huang keynote (Mar 18, 2026)" or "Warren Buffett's 2025
    shareholder letter" refuses the record. Three checks, fail closed: `_names_a_person` (a given name
    before a capitalised word), the compliance scan's ``person_named`` (the person lexicons, a lone
    surname such as "Huang keynote") and — the review of 2026-10-16, ST-1 — the stake-source surnames
    (`company_news_rules.stake_person_surname_hits`: "Dario Amodei essay", "Altman blog", "Abel letter",
    which the lesson lexicons missed). The record's own COMPANIES are masked first — the investor, and a
    listed investee under both its names — so a company named for a person stays a company ("Eli Lilly
    2025 Form 10-K"; a listed "Charles Schwab", whose name is never checked: its profile verified it).
    An unlisted investee's own name is never masked: it is the text checked."""
    companies = [rec.investor.name]
    if rec.investee is not None:
        companies += [rec.investee.name, rec.investee_name]
    rx = _mask_re(companies)
    fields: List[Tuple[str, Optional[str]]] = [("source title", source), ("local listing", rec.local_listing)]
    if rec.investee is None:
        fields.append(("investee name", rec.investee_name))
    for what, value in fields:
        if not value:
            continue
        masked = _masked(value, rx)
        if (_names_a_person(masked) or rules.stake_person_surname_hits(masked)
                or any(v.code == "person_named" for v in scan_text(f"stake.{what.replace(' ', '_')}", masked))):
            return what
    return None


def _stake_background(text: Any, names: Sequence[str]) -> Optional[str]:
    """The stake's background line when every rule passes, else None (it is dropped, never fixed:
    hand-written free text with unnormalised numbers — A3: only ever a long-caption paragraph, and since
    news-v19 only while STAKE_BACKGROUND_IN_CAPTIONS is on — off by default)."""
    if not isinstance(text, str) or not text or len(text) > STAKE_BACKGROUND_MAX_CHARS or not text.isascii():
        return None
    if _slot_problem(text) or not text[0].isupper() or not text.endswith("."):
        return None
    if _PER_SHARE_RE.search(text) or not _percent_ok(text) or rules.congress_name_hits(text):
        return None
    folded = fold(_masked(text, _mask_re(names)))
    if any(rx.search(folded) for rx in (copy_rules.BANNED_COPY, copy_rules.FORECAST_COPY, NEWS_BANNED_RE,
                                         STAKES_BANNED_RE, STAKE_CLAIM_RE)):
        return None
    if _names_a_person(text) or scan_text("stake.background", text):
        return None
    return text


#: news-v23 (company stakes, owner 2026-11-19; stakes_nvidia.jpg): the picture draws at most this many stake rows —
#: every stake of a post (`rules.STAKES_POST_STAKES`, pinned equal) — and the video cover STAKES_COVER_ROWS: "+N more
#: stakes in the caption" for the rest, which every caption that rides the video lists (`_Parts.field_required`),
#: else that outlet is dropped. Review 2026-11-20: three on the cover, as the 13F cover (`rules.THIRTEEN_F_COVER_ROWS`:
#: four rows above the burned-caption band squeezed each card to ~120 px with the chip beside the name).
STAKES_PICTURE_ROWS = 4
STAKES_COVER_ROWS = min(3, STAKES_PICTURE_ROWS, onscreen.MAX_OPENING_ROWS)
#: A source title's trailing parenthetical that only DATES the document — left off the picture's as-of line when its
#: date is the row's own as-of date (`_stake_source_shown`; stakes_nvidia.jpg). The captions keep the title whole.
_SOURCE_DATE_TAIL_RE = re.compile(r"(.*\S)\s\((?:quarter ended |fiscal year ended )?([A-Z][a-z]{2} [0-9]{1,2}, [0-9]{4})\)")


def _stake_source_shown(source: str, as_of: date) -> str:
    """The source title as the picture's as-of line draws it: without a trailing "(<date>)" / "(quarter ended <date>)"
    / "(fiscal year ended <date>)" whose date IS the row's as-of date (`date_image`) — the line already prints it
    ("As of Dec 1, 2025 · Synopsys 8-K"); any other title whole ("Intel 8-K (Dec 29, 2025)": a filing dated after
    its as-of date). Pure."""
    m = _SOURCE_DATE_TAIL_RE.fullmatch(source)
    if m and m.group(2) == date_image(as_of):
        return m.group(1)
    return source


def _stake_row(rec: rules.CompanyStake, run_date: date) -> str:
    """One stake of a company_stakes post — the per-stake gates (`drop_refused_rows` drops a stake that fails one; a
    record that reaches `compose` with one is refused whole: forged). Returns the stake's source title. news-v23
    (owner 2026-11-19 "public only"): a stake in a company with no verified U.S. listing, or of a kind that is never
    public, is refused here too (`record_invalid`) — the adapter never offers one."""
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
    investee = rec.investee
    if not isinstance(investee, rules.CompanyRef) or rec.kind not in rules.PUBLIC_STAKE_KINDS:
        raise NewsTemplateRefused("record_invalid", "a stake in a company with no verified U.S. listing (public only)")
    inv = rec.investor.name
    if investee.symbol == rec.investor.symbol or fold(inv) == fold(ee):
        raise NewsTemplateRefused("record_invalid", "a company's stake in itself")
    if rec.listed_since is not None and rec.listed_since > run_date:
        raise NewsTemplateRefused("record_invalid", "listed after the run")
    _name_slot(ee, "investee name", PAIR_NAME_WORD_MAX_CHARS)
    _name_slot(investee.name, "investee company name")
    if len(ee) > STAKE_NAME_MAX_CHARS:
        raise NewsTemplateRefused("slot_rejected", f"investee name over {STAKE_NAME_MAX_CHARS} characters")
    source = rec.source_title
    for value, what, cap in ((source, "source title", STAKE_SOURCE_MAX_CHARS),
                             (rec.local_listing, "local listing", rules.COMPANY_NAME_CHARS[1])):
        if value is None:
            continue
        problem = _slot_problem(value)
        if problem or len(value) > cap or _longest_word(value) > NAME_WORD_MAX_CHARS:
            raise NewsTemplateRefused("slot_rejected", f"{what}: {problem or 'too long to draw whole'}")
    # Batch 1 (2026-10-15): no drawn stake text names a person (the detail names the field, never the text)
    named = _stake_person_problem(rec, source)
    if named is not None:
        raise NewsTemplateRefused("slot_rejected", f"{named}: names a person")
    return source


def _stakes_checked(rec: rules.CompanyStakes) -> None:
    """The record-wide checks of a company_stakes post: its investor's name is drawn whole (the title), and it holds no
    more stakes than its picture draws (review 2026-11-20: `rules.STAKES_POST_STAKES`, the record's own bound — a
    forged record past it is refused, never a picture that leaves a stake out)."""
    _name_slot(rec.investor.name, "investor name", PAIR_NAME_WORD_MAX_CHARS)
    if not 1 <= len(rec.stakes) <= min(rules.STAKES_POST_STAKES[1], STAKES_PICTURE_ROWS):
        raise NewsTemplateRefused("record_invalid", f"a company_stakes post holds 1-{STAKES_PICTURE_ROWS} stakes")


def _stake_heads(row: rules.CompanyStake, *, spoken: bool, image: bool = False) -> List[str]:
    """A stake's own sentence(s) — `st.h.*`, the stake's verb and basis (preferred first): spoken (the narration: the
    names as narration reads them, the figure in words), else display (captions: words; `image`: the image figure)."""
    inv = spoken_company(row.investor.name) if spoken else row.investor.name
    ee = spoken_company(row.investee_name) if spoken else row.investee_name
    article = rules.takes_name_article(row.investee_name)
    fills = ({"ee": ee, "ee_np": ee, "ee_np_cap": ee} if not article else
             {"ee": ee, "ee_np": _t("st.ee.the", ee=ee), "ee_np_cap": _t("st.ee.the.cap", ee=ee)})
    basis = row.value_basis
    poss = "of" if possessive(row.investor.name) is None else "poss"
    if basis == "carrying_value":
        ids = [f"st.h.carrying_value.{poss}", f"st.h.carrying_value.short.{poss}"]
    elif basis == "fair_value":
        ids = [f"st.h.fair_value.{poss}"]
    else:
        ids = [f"st.h.{basis}"]
    amt = money_image(row.value_usd) if image else money_words(row.value_usd)
    return [_t(i, inv=inv, inv_s=possessive(inv), amt=amt, **fills) for i in ids]


def _stake_other(row: rules.CompanyStake) -> str:
    """A stake named after the post's lead in the narration (`st.m.o.*`: "$2 billion invested in Synopsys")."""
    ee = spoken_company(row.investee_name)
    ee_np = _t("st.ee.the", ee=ee) if rules.takes_name_article(row.investee_name) else ee
    return _t(f"st.m.o.{row.value_basis}", amt=money_words(row.value_usd), ee=ee, ee_np=ee_np)


def _stakes_parts(rec: rules.CompanyStakes, run_date: date) -> _Parts:
    """ONE investor's stakes in PUBLIC companies (news-v23, the restyle — owner 2026-11-19: "filter for companies that
    public only. no private" + "add 1-3 more companies for the NVIDIA (or the other trillion companies)";
    stakes_nvidia.jpg / stakes_nvidia3.jpg), the largest figure first (`rules.stake_rank_key`). A disclosed figure
    with its own basis and as-of date, never a price, never a value today, never a verdict.

    * the PICTURE (`rows`): the kicker "COMPANY STAKES", the title "{inv_s} stakes in public companies" (white, then
      "in public companies" gold), the INVESTOR's plate (its logo and ticker chip), at most STAKES_PICTURE_ROWS rows —
      the investee's logo, ticker chip and name, the basis word (`label`, "invested") over the amount, and the line
      "As of {date} · {source}" (`_stake_source_shown`) — and "+N more stakes in the caption" for the rest;
    * the COVER (the opening card's `list`, cover_13f_berkshire.jpg's idea): the same title, plate and first
      STAKES_COVER_ROWS rows;
    * the narration: one stake — its sentence, its date, what its figure is, its listing; two or more — the count, the
      lead stake with its date, the next one or two, what each figure is, the listing;
    * the captions: one stake — its sentence with its date (and source), what its figure is, its listing; two or
      more — the first line, a paragraph per stake (its sentence, its date, its WHOLE source title), what each figure
      is, the listing. Every caption that rides the picture lists the stakes the picture leaves out, and every one that
      rides the video those the cover leaves out — an outlet that cannot is dropped (`over_budget`).
    No private, unlisted or not-trading company appears anywhere (the record cannot carry one)."""
    _stakes_checked(rec)
    rows_ = list(rec.stakes)
    sources = [_stake_row(r, run_date) for r in rows_]
    n = len(rows_)
    inv, inv_sym = rec.investor.name, rec.investor.symbol
    inv_s = possessive(inv)
    inv_sp = spoken_company(inv)
    inv_sp_s = possessive(inv_sp)
    tag = "of" if inv_s is None else "poss"
    title = _t(f"st.m.title.{tag}", inv=inv, inv_s=inv_s)

    # the rows, in rank order: the picture's and the cover's row, the caption paragraph, the alt text's item
    rows: List[Dict[str, Any]] = []
    row_paras: List[str] = []
    short_paras: List[str] = []
    alt_rows: List[str] = []
    names: Dict[str, str] = {inv_sym: inv}
    for r, source in zip(rows_, sources):
        sym = r.investee.symbol
        amt_s = money_image(r.value_usd)
        label = _t(f"st.label.{r.value_basis}")
        shown_source = _stake_source_shown(source, r.as_of)
        line = (_t("st.m.row.asof", as_of_img=date_image(r.as_of), source=shown_source)
                if len(shown_source) <= STAKE_ROW_SOURCE_MAX_CHARS else _t("st.asof", as_of_img=date_image(r.as_of)))
        rows.append({"logo": sym, **_row_chip(sym), "cells": [r.investee_name, line, amt_s], "label": label})
        row_paras.append(_t("st.m.p.row", h=_stake_heads(r, spoken=False)[0], as_of_cap=date_caption(r.as_of, run_date),
                            source=source))
        short_paras.append(_t(f"st.m.p.short.{r.value_basis}", ee=r.investee_name, amt=money_words(r.value_usd),
                              as_of_cap=date_caption(r.as_of, run_date)))
        alt_rows.append(_t("st.m.alt.row", ee=r.investee_name, amt_s=amt_s, label=label))
        names[sym] = r.investee.name

    # the picture: the first STAKES_PICTURE_ROWS rows, "+N more stakes in the caption" for the rest
    def more_line(k: int) -> str:
        return _t("st.m.more.one") if k == 1 else _t("st.m.more.many", k=k)

    shown = rows[:STAKES_PICTURE_ROWS]
    more = n - len(shown)
    section: Dict[str, Any] = {"rows": shown}
    if more > 0:
        section["more"] = more_line(more)
    spec: Dict[str, Any] = {"layout": "rows", "version": onscreen.SPEC_VERSION, "kicker": _t("st.kicker"),
                            "header": {"logo": inv_sym, "name": inv, "chip": inv_sym}, "title": title,
                            "sections": [section]}
    # the cover: the same title, plate and list — its first STAKES_COVER_ROWS rows
    cover_rows = rows[:STAKES_COVER_ROWS]
    cover_more = n - len(cover_rows)
    cover_list: Dict[str, Any] = {"rows": cover_rows}
    if cover_more > 0:
        cover_list["more"] = more_line(cover_more)
    opening: Dict[str, Any] = {"kicker": _t("st.kicker"), "logos": [inv_sym], "chip": inv_sym, "headline": title,
                               "list": cover_list}

    lead = rows_[0]
    bases = list(dict.fromkeys(r.value_basis for r in rows_))
    lead_as_of_img = date_image(lead.as_of)
    if n == 1:
        # ONE stake: its own sentence (the hook), its date, what its figure is, its listing, where such stakes come from
        basis = lead.value_basis
        ee = lead.investee_name
        article = rules.takes_name_article(ee)
        ee_said = ({"ee": ee, "ee_np": ee, "ee_np_cap": ee} if not article else
                   {"ee": ee, "ee_np": _t("st.ee.the", ee=ee), "ee_np_cap": _t("st.ee.the.cap", ee=ee)})
        ee_sp = spoken_company(ee)
        ee_spoken = ({"ee": ee_sp, "ee_np": ee_sp, "ee_np_cap": ee_sp} if not article else
                     {"ee": ee_sp, "ee_np": _t("st.ee.the", ee=ee_sp), "ee_np_cap": _t("st.ee.the.cap", ee=ee_sp)})
        if lead.kind == "commitment":
            l3_key = "commitment"
        elif lead.listed_since is not None:
            l3_key = "listed"
        else:
            l3_key = "us"
        month_fill = ({"month_sp": month_words(lead.listed_since), "month_img": month_image(lead.listed_since)}
                      if lead.listed_since is not None else {})
        h_sp = _stake_heads(lead, spoken=True)
        h_cap = _stake_heads(lead, spoken=False)
        h_img = _stake_heads(lead, spoken=False, image=True)
        hook = [_t("st.hook", h=h) for h in h_sp]
        lines = [[_t("st.l1", as_of_sp=date_spoken(lead.as_of, run_date))], [_t(f"st.l2.{basis}")],
                 [_t(f"st.l3.{l3_key}", **ee_spoken, **month_fill)], [_t("st.l4")]]
        cards = [(_t("st.c1.title"), _t("st.c1.body", as_of_img=lead_as_of_img)),
                 (_t(f"st.c2.{basis}.title"), _t(f"st.c2.{basis}.body")),
                 (_t(f"st.c3.{l3_key}.title"), _t(f"st.c3.{l3_key}.body", **month_fill)),
                 (_t("st.c4.title"), _t("st.c4.body", source=sources[0]))]
        as_of_cap = date_caption(lead.as_of, run_date)
        long_heads = [_t("st.ch.long", h=h, as_of_cap=as_of_cap, source=sources[0]) for h in h_cap]
        heads = long_heads + [_t("st.ch.short", h=h, as_of_cap=as_of_cap) for h in h_cap]
        # news-v19 (Batch 1, plan item 5): the catalogue background stays out of the captions unless
        # STAKE_BACKGROUND_IN_CAPTIONS — see the switch for why; the rules below still decide when it is on
        background = (_stake_background(lead.background, [inv, ee, sources[0], lead.investee.name])
                      if STAKE_BACKGROUND_IN_CAPTIONS else None)
        source_para = _t("st.p.source", source=sources[0], as_of_cap=as_of_cap)
        paras = ([background] if background else []) + [_t(f"st.l2.{basis}"),
                                                        _t(f"st.l3.{l3_key}", **ee_said, **month_fill), source_para]
        # review round 11: a ".long" headline cites the source and its as-of date, so `st.p.source` is left
        # out under it; a ".short" headline does not, and keeps it
        stated = {h: frozenset({source_para}) for h in long_heads}
        value_head = _t(f"st.open.value.{tag}", inv=inv, inv_s=inv_s, **ee_said)
        yt = h_img + [value_head]
        return _Parts(hook=hook, lines=lines, cards=cards, opening=opening, image_spec=spec,
                      alt_title=_alt_title([title]),
                      alt_paragraphs=[_t("st.m.alt.one", inv=inv), _t("st.m.alt.rows", items=alt_rows[0])],
                      source=rules.source_label(rec), as_of=_t("st.asof", as_of_img=lead_as_of_img),
                      image_headlines=heads, image_paragraphs=paras, video_headlines=heads, video_paragraphs=paras,
                      youtube_titles=yt, persons=[], names=names, stated_by_headline=stated)

    # TWO OR MORE: the count (the hook), the lead stake with its date, the next one or two, what each figure is, the
    # listing — never every row (the cover lists them)
    spoken = dict(n=n, inv=inv_sp, inv_s=inv_sp_s)
    hook = [_t(f"st.m.hook.{tag}", **spoken), _t(f"st.m.hook.{tag}.short", **spoken)]
    l1 = [_t("st.m.l.lead", h=h, as_of_sp=date_spoken(lead.as_of, run_date)) for h in _stake_heads(lead, spoken=True)]
    others = [_stake_other(r) for r in rows_[1:3]]
    l2 = ([_t("st.m.l.also.two", a=others[0], b=others[1])] if len(others) == 2 else []) + [
        _t("st.m.l.also.one", a=others[0])]
    basis_key = bases[0] if len(bases) == 1 else "mixed"
    l3 = [_t(f"st.m.l.basis.{basis_key}")]
    l4 = [_t("st.m.l.public"), _t("st.l4")]
    lead_img = rows[0]
    c1 = (lead.investee_name, _t("st.m.c.row", basis_title=_t(f"st.c2.{lead.value_basis}.title"),
                                 amt_s=lead_img["cells"][2], as_of_img=lead_as_of_img))
    c2 = (_t("st.m.c.also.title"), "; ".join(_t("st.m.c.item", ee=r["cells"][0], amt_s=r["cells"][2],
                                                  label=r["label"]) for r in rows[1:3]))
    c3 = ((_t(f"st.c2.{basis_key}.title"), _t(f"st.c2.{basis_key}.body")) if basis_key != "mixed" else
          (_t("st.m.c.basis.mixed.title"), _t("st.m.c.basis.mixed.body")))
    c4 = (_t("st.m.c.public.title"), _t("st.m.c.public.body"))

    # captions: the first line, a paragraph per stake in rank order, what each figure is, the listing. What each
    # field MUST carry (`_Parts.field_required`, the 13F list's rule): the picture's "+N more" promises the stakes it
    # leaves out on every caption that can ride the picture (x, bluesky, threads, facebook, linkedin); the cover's
    # promises those it leaves out on every caption that can ride the video (tiktok, instagram, the YouTube
    # description — facebook / linkedin / threads videos are re-checked at freeze, `extra_video_problems`)
    heads = [_t(f"st.m.h.{tag}", n=n, inv=inv, inv_s=inv_s), _t(f"st.m.h.{tag}.min", n=n, inv=inv, inv_s=inv_s)]
    tail = [_t(f"st.m.l.basis.{basis_key}"), _t("st.m.l.public"), _t("st.l4")]
    paras = row_paras + tail
    yt = [_t(f"st.m.yt.{tag}", n=n, inv=inv, inv_s=inv_s), _t("st.m.yt.min")]
    picture_rest, cover_rest = row_paras[len(shown):], row_paras[len(cover_rows):]
    # the short captions carry the SHORT form of each stake (X, Bluesky, Threads: a "+N more" promise must fit them)
    short_picture_rest, short_cover_rest = short_paras[len(shown):], short_paras[len(cover_rows):]
    text = short_picture_rest if short_picture_rest else short_paras
    field_paragraphs: Dict[str, List[str]] = {"x": text, "bluesky": text,
                                              "threads": short_cover_rest if short_cover_rest else short_paras,
                                              "tiktok": cover_rest if cover_rest else row_paras[:1]}
    field_required: Dict[str, int] = {f: n for f in ("facebook", "linkedin", "instagram", "youtube_description")}
    field_required.update({f: len(picture_rest) for f in ("x", "bluesky")})
    field_required["threads"] = len(cover_rest)
    field_required["tiktok"] = len(cover_rest)
    field_fallbacks: Dict[str, List[Tuple[List[str], int]]] = {}
    for f, rests in (("facebook", (cover_rest, picture_rest)), ("linkedin", (cover_rest, picture_rest)),
                     ("instagram", (cover_rest,)), ("youtube_description", (cover_rest,))):
        for r in rests:
            plan = (list(r) + tail, len(r))
            if len(r) < n and plan not in field_fallbacks.setdefault(f, []):
                field_fallbacks[f].append(plan)
    if short_cover_rest and short_picture_rest != short_cover_rest:
        field_fallbacks["threads"] = [(list(short_picture_rest), len(short_picture_rest))]
    alt = [_t("st.m.alt.many", n=n, inv=inv), _t("st.m.alt.rows", items="; ".join(alt_rows[:len(shown)]))]
    if more > 0:
        alt.append(_t("st.m.alt.more.one") if more == 1 else _t("st.m.alt.more.many", k=more))
    dates = sorted({r.as_of for r in rows_})
    as_of = (_t("st.asof", as_of_img=date_image(dates[0])) if len(dates) == 1 else
             _t("st.m.asof.span", first=date_image(dates[0]), last=date_image(dates[-1])))
    return _Parts(hook=hook, lines=[l1, l2, l3, l4], cards=[c1, c2, c3, c4], opening=opening, image_spec=spec,
                  alt_title=_alt_title([title]), alt_paragraphs=alt, source=rules.source_label(rec), as_of=as_of,
                  image_headlines=heads, image_paragraphs=paras, video_headlines=heads, video_paragraphs=paras,
                  youtube_titles=yt, persons=[], names=names, paragraph_limit=len(paras),
                  field_paragraphs=field_paragraphs, field_required=field_required, field_fallbacks=field_fallbacks,
                  video_promise=tuple(cover_rest), video_promise_short=tuple(short_cover_rest),
                  text_paragraphs=list(short_paras))


# ── Earnings vs Estimates (2b-3) ──────────────────────────────────────────────


#: news-v23 (the restyle, owner 2026-11-19): the surprise cell draws "—" (`er.img.sur.none`), never a %, when the EPS
#: estimate is smaller than this in size — a % of a near-zero estimate is no meaningful figure (|EPS est| < $0.05). Today
#: the adapter's `eps_estimate_too_small` gate (|estimate| < `company_news_rules.EPS_MIN_ABS_ESTIMATE`, $0.10) refuses
#: such a company first, so the "—" is defence in depth; a revenue estimate that is not a positive figure draws "—" too.
EARNINGS_SURPRISE_MIN_ABS_EPS_ESTIMATE = Decimal("0.05")
#: The surprise is computed at this precision (exact for every finite figure the record admits).
_SURPRISE_PRECISION = 60


def surprise_cell(actual: float, estimate: float, *, eps: bool) -> str:
    """The table's surprise cell (news-v23, owner 2026-11-19 "add +X% surprised (in green) or -X% surprised (in red)"):
    (reported − estimate) / |estimate| × 100 on the figures as the feed wrote them (`_dec`), one decimal, half-up —
    "+8.0%", "−11.4%" (U+2212), "0.0%" when it rounds to zero, and "—" when no % is meaningful: an EPS estimate under
    EARNINGS_SURPRISE_MIN_ABS_EPS_ESTIMATE in size (`eps=True`), a revenue estimate that is not positive. Never a
    word: the worker colours a "+" cell GREEN and a "−" cell RED (`look.signed_tone`). Pure."""
    e, a = _dec(estimate), _dec(actual)
    if (eps and abs(e) < EARNINGS_SURPRISE_MIN_ABS_EPS_ESTIMATE) or (not eps and e <= 0) or e == 0:
        return _t("er.img.sur.none")
    with localcontext() as ctx:
        ctx.prec = _SURPRISE_PRECISION
        p = _q((a - e) / abs(e) * 100, 1)
    if p == 0:
        return _t("er.img.sur.zero")
    return _t("er.img.sur.plus" if p > 0 else "er.img.sur.minus", p=f"{abs(p):,.1f}")


def _eps_cell(value: float) -> str:
    """A per-share figure as the TABLE draws it: `eps_image` with its sign the real minus (U+2212)."""
    shown = eps_image(value)
    return _t("er.img.neg", x=shown[1:]) if shown.startswith("-") else shown


def _revenue_figures(actual: float, estimate: float) -> Dict[str, str]:
    """{ra, re, ra_s, re_s} of a report's revenue pair — "Revenue always" (owner 2026-11-19): the shared precision
    `revenue_pair` picks (its shown gap within REVENUE_GAP_TOLERANCE of the true one), else — the gap too small for any
    precision up to REVENUE_MAX_PLACES to show truly — both figures at REVENUE_MAX_PLACES (one unit, one precision:
    never each rounded on its own; the surprise cell then reads about 0.0%, computed from the figures as filed).
    `implausible_figures` when the pair cannot be written at all (a figure that is not positive, or a larger figure
    under $1 million: the pair's smallest unit)."""
    pair = revenue_pair(actual, estimate)
    if pair is not None:
        return pair
    a, e = _dec(actual), _dec(estimate)
    big = max(a, e)
    if a <= 0 or e <= 0 or big < _PAIR_UNITS[0][0]:
        raise NewsTemplateRefused("implausible_figures", "a revenue pair the post cannot write")
    i = max(j for j, (base, _s, _w) in enumerate(_PAIR_UNITS) if big >= base)
    sa, se = _q(a / _PAIR_UNITS[i][0], REVENUE_MAX_PLACES), _q(e / _PAIR_UNITS[i][0], REVENUE_MAX_PLACES)
    if max(sa, se) >= 1000 and i + 1 < len(_PAIR_UNITS):
        i += 1
        sa, se = _q(a / _PAIR_UNITS[i][0], REVENUE_MAX_PLACES), _q(e / _PAIR_UNITS[i][0], REVENUE_MAX_PLACES)
    _base, suffix, word = _PAIR_UNITS[i]
    fa, fe = _pair_figures(sa, se, REVENUE_MAX_PLACES)
    logger.info("news template: earnings revenue written at %d places — no precision shows its gap within %s of the "
                "true one (the surprise reads it)", REVENUE_MAX_PLACES, REVENUE_GAP_TOLERANCE)
    return {"ra": f"${fa} {word}", "re": f"${fe} {word}", "ra_s": f"${fa}{suffix}", "re_s": f"${fe}{suffix}"}


def _earnings_row(rec: rules.EarningsReport, run_date: date) -> Dict[str, Any]:
    """One company of an earnings post — every per-company gate (`drop_refused_rows` drops a company that fails one; a
    record that reaches `compose` with one is refused whole: forged) — and its strings."""
    if not 1 <= (run_date - rec.report_date).days <= rules.earnings_window_days(run_date):
        raise NewsTemplateRefused("stale_source", "the results were not reported in the week before the run")
    if rec.period_end is not None and (rec.period_end > rec.report_date or (
            rec.report_date - rec.period_end).days > EARNINGS_MAX_REPORT_LAG_DAYS):
        raise NewsTemplateRefused("implausible_figures", "the quarter's end is not before its report")
    co, sym = rec.company.name, rec.company.symbol
    _name_slot(co, "company name")
    a, e = rec.eps_actual, rec.eps_estimate
    if abs(e) < rules.EPS_MIN_ABS_ESTIMATE:
        raise NewsTemplateRefused("implausible_figures", "an estimate under ten cents")
    # the owner-delegated EPS ceiling (2026-10-19), as the adapter and the record apply it: defence in depth
    if abs(a) > rules.EPS_MAX_ABS or abs(e) > rules.EPS_MAX_ABS:
        raise NewsTemplateRefused("implausible_figures", "a per-share figure above $1,000,000")
    if a != 0 and _q(abs(_dec(a)), 2) == 0:
        raise NewsTemplateRefused("implausible_figures", "a non-zero EPS that would read $0.00")
    if eps_digit_shift_suspect(a, e):
        raise NewsTemplateRefused("implausible_figures", "a dropped or added digit")
    if abs(a - e) > EARNINGS_MAX_GAP_RATIO * abs(e):
        raise NewsTemplateRefused("implausible_figures", "an EPS gap ten times the estimate")
    # Review of 2026-10-16 (ER-1): figures that PRINT as one say nothing — refused (the adapter's `clear_surprise`
    # judges the printed figures too; this is defence in depth for a record built any other way)
    if eps_image(a) == eps_image(e):
        raise NewsTemplateRefused("implausible_figures", "the EPS and its estimate print as the same figure")
    # news-v19: no revenue pair, no post — the pair is the only sign the calendar row is the quarter the estimate is
    # for. news-v23 ("Revenue always", owner 2026-11-19): the pair is also WRITTEN, for every company
    ra, rv = rec.revenue_actual, rec.revenue_estimate
    if ra is None or rv is None:
        raise NewsTemplateRefused("implausible_figures", "no revenue pair: the report's quarter is unchecked")
    lo, hi = rules.REVENUE_RATIO_BAND
    if not (ra > 0 and rv > 0 and lo <= ra / rv <= hi):
        raise NewsTemplateRefused("implausible_figures", "revenue outside the plausible band")
    rev = _revenue_figures(ra, rv)

    def spoken(v: float) -> str:
        # the sign as a word: narration reads magnitudes, and "lost" / "earned" would state a profit or
        # loss the calendar's per-share figure (on the analysts' basis) does not establish
        return _t("er.neg", x=eps_words(v)) if v < 0 else eps_words(v)

    return dict(co=co, sym=sym, co_sp=spoken_company(co), eps=spoken(a), est=spoken(e), eps_s=eps_image(a),
                est_s=eps_image(e), eps_t=_eps_cell(a), est_t=_eps_cell(e), sur_eps=surprise_cell(a, e, eps=True),
                sur_rev=surprise_cell(ra, rv, eps=False), **rev)


def _earnings_parts(rec: rules.EarningsDay, run_date: date) -> _Parts:
    """ONE earnings post of 2-4 companies (news-v23, the restyle — owner 2026-11-19: "add Revenue reported and estimate
    too" + "combine 2 to 4 companies at once" + "add +X% surprised (in green) or -X% surprised (in red)" + column
    order "Estimate, Reported, Surprise"; earnings_multi4.jpg / earnings_multi2.jpg), the largest company first.

    * the PICTURE (`rows`): the kicker, the title "Reported vs analyst estimates", the subtitle "Reported {day or
      window}", the column labels "Estimate" | "Reported" | "Surprise", one row per company — its logo, ticker chip and
      name, and a `table` of two lines: "EPS" and "Revenue", each its estimate, its reported figure and its surprise
      ("+8.0%" GREEN / "−11.4%" RED, `surprise_cell`) — and the basis note;
    * the COVER: the same title and table on the opening card's `list` (every company: at most four);
    * the narration: a line per company — the first NEWS_SCRIPT_LINES − 1 (EARNINGS_NARRATED_COMPANIES: a fourth
      company is on the cover and in every caption) — its EPS and revenue against their estimates (a shorter form
      when the words run over), then ALWAYS the basis note (`er.m.basis`, with its "About the figures" card: every
      earnings video carries the caveat — review 2026-11-20), then the estimate's definition while a line is left;
      the hook counts the companies and never says the figures are on the analysts' basis (the revenue estimate can
      be on another — GE). No person, never "beat" / "miss" / "expected" / "surprise";
    * the captions: the companies' names in the first line (the basis note with it where it fits), a paragraph per
      company (its EPS and revenue against their estimates — every long caption carries them all), the report day,
      the basis note, the estimate's definition. No % in a caption.
    "Revenue always" (owner 2026-11-19, ACCEPTING the GE-type risk — `EARNINGS_REVENUE_IN_POSTS`)."""
    rows_ = [_earnings_row(r, run_date) for r in rec.reports]
    n = len(rows_)
    days = [r.report_date for r in rec.reports]
    first, last = min(days), max(days)
    report_img = window_image(first, last)
    kicker = _t("er.kicker")
    title = _t("er.img.title")
    columns = [_t("er.img.col.estimate"), _t("er.img.col.reported"), _t("er.img.col.surprise")]
    eps_label, rev_label = _t("er.img.eps"), _t("er.img.revenue")
    rows = [{"logo": r["sym"], **_row_chip(r["sym"]), "cells": [r["co"]],
             "table": [{"cells": [eps_label, r["est_t"], r["eps_t"], r["sur_eps"]]},
                       {"cells": [rev_label, r["re_s"], r["ra_s"], r["sur_rev"]]}]} for r in rows_]
    spec: Dict[str, Any] = {
        "layout": "rows", "version": onscreen.SPEC_VERSION, "kicker": kicker, "title": title,
        "subtitle": _t("er.img.sub.report", report_img=report_img),
        "sections": [{"columns": columns, "rows": rows}], "notes": [_t("er.img.note")],
    }
    opening: Dict[str, Any] = {"kicker": kicker, "logos": [rows_[0]["sym"]], "headline": title,
                               "list": {"columns": columns, "rows": rows[:onscreen.MAX_OPENING_ROWS]}}

    # narration and cards: a line per company — the first EARNINGS_NARRATED_COMPANIES — then ALWAYS the basis note
    # (review 2026-11-20: a four-company video used to fill every line with companies and carry no caveat on any frame
    # or in its narration), then the estimate's definition while a line is left
    hook = [_t("er.m.hook", n=n)]
    lines: List[List[str]] = []
    cards: List[Tuple[str, str]] = []
    for r in rows_[:EARNINGS_NARRATED_COMPANIES]:
        fill = {k: r[k] for k in ("co_sp", "eps", "est", "ra", "re")}
        lines.append(list(dict.fromkeys([_t("er.m.l", **fill), _t("er.m.l.short", **fill), _t("er.m.l.eps", **fill)])))
        # a DRAWN card: its per-share figures carry the table's U+2212 minus (`_eps_cell`), as every drawn negative
        # figure does — the captions and the alt text keep "-$0.05" (visual-fidelity round 2026-11-20)
        figs = {"eps_s": r["eps_t"], "est_s": r["est_t"], "ra_s": r["ra_s"], "re_s": r["re_s"]}
        if _longest_word(r["co"]) <= CARD_TITLE_WORD_MAX_CHARS:
            cards.append((_t("er.m.c.title", co=r["co"]), _t("er.m.c.body", **figs)))
        else:
            cards.append((_t("er.m.c.title.generic"), _t("er.m.c.body.named", co=r["co"], **figs)))
    for line, card in (([_t("er.m.basis")], (_t("er.c3.title"), _t("er.c3.body"))),
                       ([_t("er.estimate")], (_t("er.c.estimate.title"), _t("er.c.estimate.body")))):
        if len(lines) < NEWS_SCRIPT_LINES:
            lines.append(line)
            cards.append(card)

    # captions: the names (with the basis note where it fits — X / Bluesky / Threads carry the first line alone), a
    # paragraph per company, then when they reported, the basis note and the estimate's definition
    names = _join_list([r["co"] for r in rows_], n, n)
    heads = [_t("er.m.h.names.basis", names=names), _t("er.m.h.basis", n=n), _t("er.m.h.names", names=names),
             _t("er.m.h", n=n)]
    row_paras = [_t("er.m.p", **{k: r[k] for k in ("co", "sym", "eps_s", "est_s", "ra", "re")}) for r in rows_]
    when = (_t("er.m.p.day", day_cap=date_caption(first, run_date)) if first == last else
            _t("er.m.p.window", first_cap=date_caption(first, run_date), last_cap=date_caption(last, run_date)))
    basis_para = _t("er.m.basis")
    paras = row_paras + [when, basis_para, _t("er.estimate")]
    stated = {h: frozenset({basis_para}) for h in heads[:2]}
    yt = [_t("er.m.yt.names", names=names), _t("er.m.yt", n=n)]
    alt = [_t("er.m.alt", n=n),
           _t("er.m.alt.rows", items="; ".join(_t("er.m.alt.row", **{k: r[k] for k in ("co", "eps_s", "est_s",
                                                                                    "ra_s", "re_s")})
                                               for r in rows_))]
    return _Parts(hook=hook, lines=lines, cards=cards, opening=opening, image_spec=spec,
                  alt_title=_alt_title([title]), alt_paragraphs=alt, source=rules.source_label(rec),
                  as_of=_t("er.asof", report_img=report_img), image_headlines=heads, image_paragraphs=paras,
                  video_headlines=heads, video_paragraphs=paras, youtube_titles=yt, persons=[],
                  names={r["sym"]: r["co"] for r in rows_}, stated_by_headline=stated,
                  required_fields=EARNINGS_LIST_FIELDS, required_paragraphs=n, paragraph_limit=len(paras))


#: The long captions that list EVERY company of an earnings post (news-v23): their first paragraphs are the companies,
#: and an outlet whose budget cannot hold them is dropped (`over_budget`), never posted without them.
EARNINGS_LIST_FIELDS: Tuple[str, ...] = ("facebook", "linkedin", "instagram", "youtube_description")

#: The companies an earnings video NARRATES (review 2026-11-20): every line but one, which is ALWAYS the basis note
#: (`er.m.basis`) — so a four-company video still says, in its narration and on its "About the figures" card, that an
#: estimate's basis can differ. The fourth company is on the cover's list and in every caption.
EARNINGS_NARRATED_COMPANIES = NEWS_SCRIPT_LINES - 1


def drop_refused_rows(record: Any, run_date: date) -> Any:
    """news-v23 (the restyle, owner 2026-11-19: "a refused company is dropped from the post, not the post"): a
    several-company record — an `EarningsDay`, a `CongressMonth`, a `CompanyStakes` (news-v23: one investor's stakes),
    and since the owner decisions of 2026-11-20 the two Form 4 lists (`InsiderBuysWeek`, `InsiderSpotlight`) — without the companies its template refuses one by one (`_earnings_row` / `_congress_row` / `_stake_row`: the
    per-company gates, each a WARNING naming the company — a stake by its id and symbol — and the gate),
    rebuilt from the rest in order — so its ledger key names exactly the companies the post draws. The record as it is
    when every company passes, and every other record as it is. `NewsTemplateRefused` ("too_few_rows") when fewer than
    the post's minimum are left — for a post of one row or more (company stakes) left with none, the first row's own
    refusal; a record-wide refusal (a Congress month that is not the run's) passes through. Run
    by script_service BEFORE the fact sheet and the ledger key are made. Pure but for logging."""
    if isinstance(record, rules.EarningsDay):
        rows, check, field_, lo = record.reports, (lambda r: _earnings_row(r, run_date)), "reports", \
            rules.EARNINGS_DAY_COMPANIES[0]
    elif isinstance(record, rules.CongressMonth):
        _congress_checked(record, run_date)
        rows, check, field_, lo = record.counts, _congress_row, "counts", rules.CONGRESS_MONTH_COMPANIES[0]
    elif isinstance(record, rules.CompanyStakes):
        # news-v23: one investor's stakes in public companies — a stake its template refuses is dropped (the stake id
        # and the investee's symbol are logged, never a name the post would not draw)
        _stakes_checked(record)
        rows, check, field_, lo = (record.stakes, (lambda r: _stake_row(r, run_date)), "stakes",
                                   rules.STAKES_POST_STAKES[0])
    elif isinstance(record, rules.InsiderBuysWeek):
        # owner decisions 2026-11-20 ("3 - 5 companies at once"): a roundup row whose company name cannot be drawn whole
        # is dropped, never the week (at least INSIDER_MIN_ROWS left)
        rows, check, field_, lo = (record.rows, (lambda r: _name_slot(r.company.name, "company name")), "rows",
                                   rules.INSIDER_MIN_ROWS)
    elif isinstance(record, rules.InsiderSpotlight):
        # …and an insider list's company whose name, filed cost or share count its row cannot print (`_isp_row_check`)
        rows, check, field_, lo = record.companies, _isp_row_check, "companies", rules.INSIDER_MIN_ROWS
    else:
        return record
    kept = []
    first: Optional[NewsTemplateRefused] = None
    for row in rows:
        try:
            check(row)
        except NewsTemplateRefused as e:
            which = (f"stake {row.stake_id} ({getattr(row.investee, 'symbol', None)})"
                     if isinstance(row, rules.CompanyStake) else f"{row.company.symbol} ({row.company.name})")
            logger.warning("news template %s: %s DROPPED from the post — %s (%s); the others stay",
                           record.series, which, e.code, str(e.detail)[:120])
            first = first or e
            continue
        kept.append(row)
    if len(kept) == len(rows):
        return record
    if not kept and lo <= 1 and first is not None:
        # a post of ONE row or more (company stakes) whose every row its template refuses: the first row's own refusal,
        # never a bare "too few" (what refused it is what the trail and the refusal memo should say)
        raise first
    if len(kept) < lo:
        raise NewsTemplateRefused("too_few_rows", f"{len(kept)} of {len(rows)} companies left after the per-company "
                                                  f"gates (a post shows at least {lo})")
    try:
        return replace(record, **{field_: tuple(kept)})
    except ValueError as e:
        raise NewsTemplateRefused("record_invalid", f"the narrowed record: {e}") from None


# ── Theme Explainer (2b-4) → the weekly theme post (news-v23 phase 3) ─────────

#: The four long captions that list EVERY member (contract A4) — news-v23 phase 3: one paragraph per company with its
#: figure, required on each (`_Parts.field_required`), else the cover's and then the picture's left-out companies
#: (`field_fallbacks`) — never a caption that drops a company the picture or the cover promises ("+N more").
THEME_LIST_FIELDS: Tuple[str, ...] = ("facebook", "linkedin", "instagram", "youtube_description")
#: news-v23 phase 3 (the weekly theme post, owner 2026-11-19 — RESTYLE_SPEC §2 item 7): the picture's company rows —
#: angle 1's tiles (theme_ai_chips.jpg: 12 in two columns, "+N more") and angle 2's ranked rows
#: (theme_biggest_power.jpg: 10, "+N more") — and the video cover's rows (the same list's first ones, the 13F cover's
#: idea: RESTYLE_SPEC §1 "apply the same idea to every template video").
THEME_PICTURE_TILES = onscreen.MAX_TILES
THEME_RANKED_ROWS = onscreen.MAX_RANKED_ROWS
THEME_COVER_ROWS = min(4, onscreen.MAX_OPENING_ROWS)
#: The worker's TILE geometry (marketing/news_layouts.py, mirrored and pinned equal by tests/test_marketing_news_layouts):
#: the tile text column (px) and the floor size of a tile's name and line. A tile draws at most THEME_TILE_MAX_LINES
#: lines at the floor (its name's, then its line's), a name at most THEME_NAME_MAX_LINES — counted with the worker's
#: own font (`text_metrics`, an upper bound): six rows of such tiles fit the picture at the floor (a worker test lays
#: the widest out). The line takes the first form that keeps the tile inside: "<segment> · 88% of revenue" on one line,
#: "<segment>" over "88% of revenue", the segment alone — else the tile draws no line (the member stays, the caption
#: keeps its fact).
THEME_TILE_TEXT_W = 347
THEME_TILE_FLOOR_PX = 22
THEME_TILE_MAX_LINES = 3
THEME_NAME_MAX_LINES = 2
#: The worker's RANKED row (angle 2): a member's name draws on ONE line beside its chip — at the floor size, in at least
#: this column (px; the narrowest the worker's row leaves between the widest chip and the widest value, pinned by
#: tests/test_marketing_news_layouts). A top-row name that cannot is refused (`slot_rejected`), never wrapped into the
#: bar.
THEME_RANKED_NAME_W = 440
THEME_RANKED_FLOOR_PX = 22
#: Long FILED segment names → the short label a tile draws (owner 2026-11-19: "Electronic Design Automation Software" →
#: "EDA Software"). Code-owned, each a faithful abbreviation of the company's own line — the caption, the narration and
#: the alt text keep the full name. Exact match on the record's segment display name. Add a row only for a real filed
#: name (tests/fixtures, the seed, a live preview) — never to make a line fit by dropping its meaning.
THEME_SEGMENT_SHORT_LABELS: Dict[str, str] = {
    "Electronic Design Automation Software": "EDA Software",          # Synopsys (the AI chips sample, the owner's ask)
    # Intel's own segment name as FMP files it (tests/test_revenue_breakdown_reconciliation.py's INTC rows) — the
    # approved theme_ai_chips_more.jpg tile reads "Client Computing, FY2022" (phase 4, 2026-11-20)
    "Client Computing Group": "Client Computing",
    "Server Products And Cloud Services": "Server & Cloud Services",  # Microsoft's product line (the Money Map fixture)
    "Server Products and Cloud Services": "Server & Cloud Services",
    "International Beverages Franchise": "Intl. Beverages Franchise",  # PepsiCo (SEGMENT_SYMBOL_OVERRIDES' name)
    "International Beverage Franchise": "Intl. Beverage Franchise",
    "PepsiCo Beverages North America": "Beverages North America",     # PepsiCo's own segments (the fixtures)
    "PepsiCo Foods North America": "Foods North America",
    "Europe, Middle East and Africa": "EMEA",                          # a region, by its own acronym
    "Europe, Middle East & Africa": "EMEA",
    # 2026-11-20 (the final build's KLA tile drew its segment without its share): KLA's own segment (the Silicon Rush
    # fixture, tests/test_marketing_company_news_adapter.py `_sr_breakdowns`) — "Process Control" is the business's
    # name inside its 10-K segment; Coherent's segment there, "and" written "&" (as Microsoft's above). Every key is a
    # name a theme member can carry (≤ THEME_MEMBER_SEGMENT_MAX_CHARS, already in `segment_title_case`'s form): a
    # longer one never reaches a tile (`ThemeMember.top_segment` refuses it) — McDonald's 58-character "International
    # Developmental Licensed Markets and Corporate" was listed here and removed (final review, 2026-11-20)
    "Semiconductor Process Control": "Process Control",
    "Datacenter and Communications": "Datacenter & Communications",
}
#: The small words a theme tile's segment label keeps in lower case unless it is the label's first word (title case,
#: `segment_title_case`).
SEGMENT_SMALL_WORDS: FrozenSet[str] = frozenset({"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or",
                                                 "the", "to"})


def segment_title_case(name: str) -> str:
    """A FILED segment name in title case, for the theme post (2026-11-20: the feed's own case drew "Data center" beside
    "Data Center" on one picture). Only a word written ENTIRELY in lower case changes — its first letter (of each
    hyphen-joined part) capitalised, "data" → "Data", "third-party" → "Third-Party" — and not a small word
    (SEGMENT_SMALL_WORDS) after the label's first word; an all-caps acronym ("QCT"), a mixed-case brand ("iPhone") and
    a capitalised word are kept as filed. Whitespace runs read as one space (the slot's own form). Pure."""
    out: List[str] = []
    for i, word in enumerate(name.split()):
        if not word.islower() or (i and word in SEGMENT_SMALL_WORDS):
            out.append(word)
            continue
        parts = []
        for part in word.split("-"):
            k = next((j for j, ch in enumerate(part) if ch.isalpha()), None)
            parts.append(part if k is None or not part.islower() else part[:k] + part[k].upper() + part[k + 1:])
        out.append("-".join(parts))
    return " ".join(out)
#: The picture's quiet decoration per theme (a text-free gold drawing in the top-right glow — theme_family.py: the
#: microchip of theme_ai_chips.jpg, the bolt of theme_biggest_power.jpg, the wafer of theme_growth_profit_silicon.jpg,
#: the shield of theme_insiders_cyber.jpg, the radar of theme_congress_defense.jpg), by slug. A theme not listed draws
#: none (the owner has approved no drawing for it yet). Tokens: template_onscreen.THEME_ICONS (the worker draws them).
THEME_ICON_OF_SLUG: Dict[str, str] = {
    "ai-chips": "chip",               # the approved AI CHIPS sample (theme_ai_chips.jpg)
    "silicon-rush": "wafer",
    "powering-machine": "bolt",
    "cyber-wars": "shield",
    "modern-battlefield": "radar",
}
#: The theme post's family source per picture layout (RESTYLE_SPEC §1: "Stakes / themes: … company filings (biggest:
#: … company filings and market data)"). The next angles add their layout here with their own family's source.
THEME_FOOTER_SOURCE_OF_LAYOUT: Dict[str, str] = {
    "grid": post_copy.FOOTER_SOURCE_FILINGS,
    "ranked": post_copy.FOOTER_SOURCE_FILINGS_MARKET,
    # news-v23 phase 3 (2026-11-20): the last three angles' families (RESTYLE_SPEC §1) — each with its family's date
    "figures": post_copy.FOOTER_SOURCE_STATEMENTS,
    "ledger": post_copy.FOOTER_SOURCE_FORM4,
    "tally": post_copy.FOOTER_SOURCE_CONGRESS,
}
#: The theme post's verdict words, scanned on every public string of a theme post with the names masked (MAR: a theme
#: is members and facts — "largest" is the size fact; never a ranking verdict about a company).
THEME_BANNED_RE = re.compile(
    r"\b(?:biggest|leaders?|leading|dominat\w*|winners?|losers?|best|worst|top|cheap\w*|expensive|"
    r"worth|valuation|overvalued|undervalued)\b",
    re.IGNORECASE,
)


def market_value_image(value: float) -> str:
    """A market value on the picture (theme_biggest_power.jpg: "$171B", "$12B"): trillions with one decimal ("$4.3T"),
    from $10B whole billions, from $1B one decimal ("$9.8B"), below whole millions — rounded half-up, rolled up
    ("$999.95B" → "$1T"). Always rounded: every other surface says "about"."""
    a = abs(_dec(value))
    if a >= Decimal(10) ** 12 or _q(a / Decimal(10) ** 9, 0) >= 1000:
        return f"${_one_decimal(a / Decimal(10) ** 12)}T"
    if a >= Decimal(10) ** 10:
        return f"${_q(a / Decimal(10) ** 9, 0):,}B"
    if a >= Decimal(10) ** 9 or _q(a / Decimal(10) ** 6, 0) >= 1000:
        return f"${_one_decimal(a / Decimal(10) ** 9)}B"
    return f"${_q(a / Decimal(10) ** 6, 0):,}M"


def market_value_words(value: float) -> str:
    """The same figure in words ("$171 billion", "$4.3 trillion", "$9.8 billion", "$980 million") — narration and
    captions, always after "about"."""
    shown = market_value_image(value)
    return f"{shown[:-1]} {dict(T='trillion', B='billion', M='million')[shown[-1]]}"


@dataclass(frozen=True)
class _ThemeBase:
    """What every angle reads of a theme record, checked once (`_theme_parts`)."""

    rec: rules.ThemeExplainer
    theme: str
    theme_sp: str
    n: int
    size: Optional[int]
    part: bool

    @property
    def fill(self) -> Dict[str, Any]:
        return {"theme": self.theme, "n": self.n, "m": self.size}

    @property
    def count(self) -> str:
        return _t("th.img.count.part" if self.part else "th.img.count", n=self.n, m=self.size)

    @property
    def icon(self) -> Dict[str, str]:
        icon = THEME_ICON_OF_SLUG.get(self.rec.slug)
        return {"icon": icon} if icon in onscreen.THEME_ICONS else {}


def _theme_members_as_of(rec: rules.ThemeExplainer) -> str:
    """The alt text's "Members as of <day>" (`th.asof`) — or "" (the source line alone, `alt.source.bare`) when the
    theme row has no `tickers_as_of` of its own (`tickers_as_of_from_row`: owner decision 2026-11-20, "Trust the app's
    lists" — the record's day is then the run day, not the list's own date, so it never dates the list in public)."""
    if rec.tickers_as_of_from_row:
        return ""
    return _t("th.asof", tickers_img=date_image(rec.tickers_as_of))


def _theme_parts(rec: rules.ThemeExplainer, run_date: date) -> _Parts:
    """The weekly theme post (news-v23 phase 3): the checks every angle shares — the member list's age, the title
    (Caydex's own words: the news ban and the verdict words, unmasked), the members and the theme's count — then the
    record's ANGLE builder (`_THEME_ANGLE_BUILDERS`; an angle without one is refused `record_invalid`)."""
    if rec.tickers_as_of > run_date or (run_date - rec.tickers_as_of).days > THEME_MAX_AGE_DAYS:
        raise NewsTemplateRefused("stale_source", "the theme's member list is too old")
    if rec.posted_on is not None and rec.posted_on != run_date:
        raise NewsTemplateRefused("record_invalid", "the record was built for another run date")
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
    least = rules.THEME_MEMBERS[0] if rec.angle == "make_money" else rules.THEME_ANGLE_MIN_COMPANIES
    if n < least or len({m.company.symbol for m in members}) != n:
        raise NewsTemplateRefused("record_invalid", f"a theme post of fewer than {least} distinct members")
    # The theme's own count before the adapter's gates (`ThemeExplainer.theme_size`, the shared
    # record contract; None = not recorded). Larger than the members → "{n} of its {m}" wherever the
    # count is given; equal → the list is the whole theme and may say so; None → never a size claim.
    size = rec.theme_size
    if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < n):
        raise NewsTemplateRefused("record_invalid", "the theme's size is not a count of at least its members")
    for m in members:
        _name_slot(m.company.name, "member name")
    builder = _THEME_ANGLE_BUILDERS.get(rec.angle)
    if builder is None:
        raise NewsTemplateRefused("record_invalid", f"no template for the theme angle {rec.angle!r} yet")
    return builder(_ThemeBase(rec=rec, theme=theme, theme_sp=spoken_company(theme), n=n, size=size,
                              part=size is not None and size > n), run_date)


def _theme_row_plan(rows: Sequence[str], n: int, shown: int, cover: int, tail: Sequence[str],
                    short_rows: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """The caption plan of a theme's company paragraphs (`rows`: one per company, in the picture's order — its drawn
    companies first — `shown` of them on the picture, `cover` on the video cover), the 13F list's and company stakes':
    every long caption lists them all, else the cover's left-out ones, else the picture's; a caption that rides the
    picture (X, Bluesky, Threads) lists the picture's left-out ones, one that rides the video (TikTok, Threads' extra
    video) the cover's — the "+N more" promises; a TEXT X / Bluesky post lists them from the top (`text_paragraphs`).
    `short_rows` (the same companies, a few words each — the market-value angle's "Oklo: $9.8B"): what the SHORT
    captions (X, Bluesky, Threads) carry instead, each keeping the same promise (`video_promise_short`)."""
    shorts = list(short_rows) if short_rows is not None else list(rows)
    if len(shorts) != len(rows):
        raise NewsTemplateRefused("record_invalid", "a short caption row for every company row")
    picture_rest, cover_rest = list(rows[shown:]), list(rows[cover:])
    short_picture_rest, short_cover_rest = shorts[shown:], shorts[cover:]
    text = short_picture_rest if short_picture_rest else shorts
    tiktok_rest = short_cover_rest if short_rows is not None else cover_rest
    field_paragraphs: Dict[str, List[str]] = {"x": text, "bluesky": text,
                                              "threads": short_cover_rest if short_cover_rest else shorts,
                                              "tiktok": tiktok_rest if tiktok_rest else list(rows[:1])}
    field_required: Dict[str, int] = {f: n for f in THEME_LIST_FIELDS}
    field_required.update({f: len(picture_rest) for f in ("x", "bluesky")})
    field_required["threads"] = len(cover_rest)
    field_required["tiktok"] = len(cover_rest)
    field_fallbacks: Dict[str, List[Tuple[List[str], int]]] = {}
    for f, rests in (("facebook", (cover_rest, picture_rest)), ("linkedin", (cover_rest, picture_rest)),
                     ("instagram", (cover_rest,)), ("youtube_description", (cover_rest,))):
        # a long caption that cannot hold every company's full paragraph lists them ALL in their short form first (a
        # theme holds up to 24 companies), then the cover's left-out ones, then the picture's
        plans = ([(list(shorts) + list(tail), n)] if short_rows is not None else []) + [
            (list(r) + list(tail), len(r)) for r in rests if len(r) < n]
        for plan in plans:
            if plan not in field_fallbacks.setdefault(f, []):
                field_fallbacks[f].append(plan)
    if short_cover_rest and short_picture_rest != short_cover_rest:
        field_fallbacks["threads"] = [(list(short_picture_rest), len(short_picture_rest))]
    paras = list(rows) + list(tail)
    return dict(image_paragraphs=paras, video_paragraphs=paras, paragraph_limit=len(paras),
                field_paragraphs=field_paragraphs, field_required=field_required, field_fallbacks=field_fallbacks,
                video_promise=tuple(cover_rest),
                video_promise_short=tuple(short_cover_rest) if short_rows is not None else (),
                text_paragraphs=list(shorts))


def _theme_cover(b: _ThemeBase, rows: Sequence[Dict[str, Any]], heading: str, n: int) -> Dict[str, Any]:
    """The video's opening card: the theme's title and its list's first THEME_COVER_ROWS rows (the 13F cover's idea),
    "+N more" for the rest; `figure` (the company count) for a worker that draws today's opening card (no list)."""
    cover_rows = list(rows[:THEME_COVER_ROWS])
    cover: Dict[str, Any] = {"heading": heading, "rows": cover_rows}
    if n > len(cover_rows):
        cover["more"] = _t("th.img.more", k=n - len(cover_rows))
    return {"kicker": _t("th.kicker"), "logos": [cover_rows[0]["logo"]], "figure": str(n), "headline": b.theme,
            "list": cover}


def _tile_name_lines(name: str) -> int:
    return text_metrics.line_count(name, THEME_TILE_FLOOR_PX, THEME_TILE_TEXT_W)


def _tile_line(name_lines: int, label: str, share: Optional[str]) -> Optional[str]:
    """A tile's line (the first form that keeps the tile within THEME_TILE_MAX_LINES at the floor): "<label> · 88% of
    revenue" — one line, or "<label>" over "88% of revenue" — else "<label>" alone, else None."""
    px, w = THEME_TILE_FLOOR_PX, THEME_TILE_TEXT_W
    label_lines = text_metrics.line_count(label, px, w)
    if share is not None:
        one = _t("th.img.line.share", seg=label, share=share)
        lines = 1 if text_metrics.line_count(one, px, w) == 1 else label_lines + 1
        if name_lines + lines <= THEME_TILE_MAX_LINES:
            return one
    if name_lines + label_lines <= THEME_TILE_MAX_LINES:
        return _t("th.img.line", seg=label)
    return None


def _theme_make_money(b: _ThemeBase, run_date: date) -> _Parts:
    """Angle 1, "how they make money" (theme_ai_chips.jpg; owner 2026-11-19: "AI CHIPS, what are NVDA 88% or Broadcom
    58% mean? we need add something to lets users know what it means" and "for AI CHIPS, i don't see ARM, Intel and
    SNPS? add for me").

    * the PICTURE (`grid`): the title, the sub line "{count} · each company's largest business / and its share of total
      revenue · fiscal {years}", THEME_PICTURE_TILES tiles — the logo, the ticker chip, the name and "<segment> · 88% of
      revenue" — the members WITH a fact first, in the record's order (the adapter's: largest market value first —
      phase 4, 2026-11-20), "+N more" for the rest. A long filed name draws its short label
      (THEME_SEGMENT_SHORT_LABELS); a fact older than the sub line's fiscal years — or walked back past a gross year
      (`ThemeMember.latest_fiscal_year`) — draws its own ", FY2022" (no older than
      `rules.THEME_TAGGED_FACT_MAX_AGE_YEARS`); a tile's line takes the first form that fits (`_tile_line`);
    * the COVER: the title and the list's first rows, "+N more";
    * the narration and cards: each fact's own fiscal year, two to a line when they share one (news-v16), the explainer
      and the grouping line;
    * the captions: one paragraph per company with its figure (`_theme_row_plan`)."""
    members = list(b.rec.members)
    n, fill = b.n, b.fill
    # A member's segment fact: shown and narrated only when it passes the slot rules and is not too old — otherwise
    # the member stays, without the fact (never guessed). The sub line's years: the newest fact year and the one
    # before (THEME_FACT_SPREAD_YEARS), each still possibly that company's latest year at the run (news-v16:
    # THEME_FACT_LATE_MONTH); an older fact (down to THEME_TAGGED_FACT_MAX_AGE_YEARS) is TAGGED with its own year.
    oldest_untagged = run_date.year - (THEME_FACT_MAX_YEARS if run_date.month < THEME_FACT_LATE_MONTH
                                       else min(THEME_FACT_MAX_YEARS, 1))
    oldest = run_date.year - rules.THEME_TAGGED_FACT_MAX_AGE_YEARS
    raw: List[Tuple[rules.ThemeMember, str, Optional[str]]] = []
    for m in members:
        seg, fy = m.top_segment, m.fiscal_year
        if seg is None or fy is None or not fy.isascii() or not fy.isdigit():
            continue
        if not oldest <= int(fy) <= run_date.year:
            continue
        if _slot_problem(seg) or len(seg) > THEME_MEMBER_SEGMENT_MAX_CHARS:
            continue
        # 2026-11-20: ONE case on the picture, the cover, the narration and the captions ("Data Center", never the
        # feed's "Data center" beside another company's "Data Center") — the tile's short label is looked up on it
        raw.append((m, segment_title_case(seg), pct(m.top_segment_share) if m.top_segment_share is not None else None))
    # phase 4 (2026-11-20): a WALKED-BACK fact (`latest_fiscal_year` set: the company's newest year was gross) is never
    # its company's latest year — it never sets the sub line's years and is always tagged with its own (`tagged`)
    recent = [int(f[0].fiscal_year) for f in raw
              if int(f[0].fiscal_year) >= oldest_untagged and f[0].latest_fiscal_year is None]
    if not recent:
        raise NewsTemplateRefused("too_few_rows", "no member's fact is recent enough for the sub line's fiscal year")
    newest = max(recent)
    sub_floor = max(oldest_untagged, newest - THEME_FACT_SPREAD_YEARS)
    # a walked-back fact is kept whatever the current facts' newest year (it is tagged, never part of the sub line)
    facts = [f for f in raw if int(f[0].fiscal_year) <= newest or f[0].latest_fiscal_year is not None]
    if len(facts) < THEME_MIN_FACTS:
        raise NewsTemplateRefused("too_few_rows", f"fewer than {THEME_MIN_FACTS} members with a segment fact")
    fact_of = {f[0].company.symbol: f for f in facts}

    def tagged(f: Tuple[rules.ThemeMember, str, Optional[str]]) -> bool:
        return int(f[0].fiscal_year) < sub_floor or f[0].latest_fiscal_year is not None

    # the picture: the members whose name a tile can draw (THEME_NAME_MAX_LINES at the floor), those with a fact first,
    # each group in the RECORD's order — the adapter's: the members with a fact by market value, the largest first
    # (phase 4, 2026-11-20: the owner's Arm / Intel / Synopsys ask), then the rest in the theme's own order
    drawable = [m for m in members if _tile_name_lines(m.company.name) <= THEME_NAME_MAX_LINES]
    if len(drawable) < onscreen.MIN_TILES:
        raise NewsTemplateRefused("slot_rejected", f"fewer than {onscreen.MIN_TILES} member names fit a grid tile")
    order = ([m for m in drawable if m.company.symbol in fact_of]
             + [m for m in drawable if m.company.symbol not in fact_of])
    tiles: List[Dict[str, Any]] = []
    tile_lines: Dict[str, str] = {}
    for m in order[:THEME_PICTURE_TILES]:
        sym, name = m.company.symbol, m.company.name
        tile: Dict[str, Any] = {"logo": sym, **_row_chip(sym), "name": name}
        fact = fact_of.get(sym)
        if fact is not None:
            label = THEME_SEGMENT_SHORT_LABELS.get(fact[1], fact[1])
            if tagged(fact):
                label = _t("th.img.seg.fy", seg=label, fy=fact[0].fiscal_year)
            line = _tile_line(_tile_name_lines(name), label, fact[2])
            if line is not None:
                tile["line"] = tile_lines[sym] = line
        tiles.append(tile)
    shown_syms = [t["logo"] for t in tiles]
    years = sorted({fact_of[s][0].fiscal_year for s in tile_lines if not tagged(fact_of[s])}, key=int)
    if not years:
        raise NewsTemplateRefused("too_few_rows", "no tile draws a fact of the sub line's fiscal years")
    fys = years[0] if len(years) == 1 else _t("th.fys.2", a=years[0], b=years[1])
    lead, tail = _t("th.mm.sub.lead", count=b.count), _t("th.mm.sub.tail", fys=fys)
    subtitle = f"{lead} {tail}"
    spec: Dict[str, Any] = {"layout": "grid", "version": onscreen.SPEC_VERSION, "kicker": _t("th.kicker"),
                            "title": b.theme, "subtitle": subtitle, "sub_break": len(lead.split()), "tiles": tiles,
                            **b.icon}
    if n > len(tiles):      # every other LISTED member (the long captions list them); never the gated ones
        spec["more"] = _t("th.img.more", k=n - len(tiles))
    cover_rows = [{"logo": t["logo"], **_row_chip(t["logo"]),
                   "cells": [t["name"]] + ([t["line"]] if "line" in t else [])} for t in tiles]
    opening = _theme_cover(b, cover_rows, subtitle, n)

    # narration: the facts in theme order, two to a line when they share a fiscal year and the pair fits, at most three
    # lines and THEME_NARRATED_MAX facts; a fact whose own line cannot fit is not narrated
    def sp(fact: Tuple[rules.ThemeMember, str, Optional[str]]) -> Tuple[str, Optional[str], str]:
        name = spoken_company(fact[0].company.name)
        line = spoken_company(fact[1])
        # news-v19 (the review of 2026-10-16, TH-1): "… was the United States", as Money Map says it (a line is
        # never the sentence's first word here, so never "The")
        if rules.takes_region_article(fact[1]):
            line = _t("th.region.the", name=line)
        return name, possessive(name), line

    def line_of(group: Sequence[Tuple[rules.ThemeMember, str, Optional[str]]]) -> str:
        m1, m1_s, g1 = sp(group[0])
        if len(group) == 1:
            # news-v12: the fact's own fiscal year (a fact may be older: never "latest")
            return _t(f"th.one.{'poss' if m1_s else 'of'}", m1=m1, m1_s=m1_s, g1=g1, fy=group[0][0].fiscal_year)
        # news-v16: a pair says its fiscal year too — and only facts of ONE fiscal year share a line
        if group[0][0].fiscal_year != group[1][0].fiscal_year:
            raise NewsTemplateRefused("record_invalid", "a narration pair of two fiscal years")
        m2, m2_s, g2 = sp(group[1])
        return _t(f"th.pair.{'poss' if m1_s else 'of'}.{'poss' if m2_s else 'of'}",
                  m1=m1, m1_s=m1_s, g1=g1, m2=m2, m2_s=m2_s, g2=g2, fy=group[0][0].fiscal_year)

    def fits(text: str) -> bool:
        return LINE_WORDS[0] <= _words(text) <= LINE_WORDS[1]

    # the narration follows the picture's order (its tiles' facts first), so the video speaks of what the cover shows
    narrated = [fact_of[s] for s in shown_syms if s in fact_of][:THEME_NARRATED_MAX]
    groups: List[Tuple[Tuple[rules.ThemeMember, str, Optional[str]], ...]] = []
    i = 0
    while i < len(narrated) and len(groups) < 3:
        if (i + 1 < len(narrated) and narrated[i][0].fiscal_year == narrated[i + 1][0].fiscal_year
                and fits(line_of(narrated[i:i + 2]))):
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
    hook = ([_t(i, n=n, m=b.size, theme=b.theme_sp) for i in ("th.hook.part", "th.hook.part.short", "th.hook.part.min")]
            if b.part else [_t("th.hook", n=n, theme=b.theme_sp), _t("th.hook.short", n=n, theme=b.theme_sp)])
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
        a, s1, fy = group[0][0].company.name, group[0][1], group[0][0].fiscal_year
        if len(group) == 1:
            if _longest_word(a) > CARD_TITLE_WORD_MAX_CHARS:
                return _t("th.c.one.title.min"), _t("th.c.one.body.min", m1=a, g1=s1, fy=fy)
            return _t("th.c.one.title", m1=a), _t("th.c.one.body", g1=s1, fy=fy)
        bname, s2 = group[1][0].company.name, group[1][1]       # a pair shares one fiscal year (line_of)
        title = _t("th.c.pair.title", m1=a, m2=bname)
        if _words(title) > CARD_TITLE_MAX_WORDS or _longest_word(title) > CARD_TITLE_WORD_MAX_CHARS:
            return _t("th.c.pair.title.min"), _t("th.c.pair.body.min", m1=a, g1=s1, m2=bname, g2=s2, fy=fy)
        return title, _t("th.c.pair.body", g1=s1, g2=s2, fy=fy)

    lines = [[line_of(g)] for g in groups] + ([[explainer]] if len(groups) < 3 else []) + [[grouping]]
    cards = [card_of(g) for g in groups] + (
        [(_t("th.c.explainer.title"), _t("th.c.explainer.body"))] if len(groups) < 3 else []) + [
        (_t("th.c.grouping.title"), _t("th.c.grouping.body"))]

    # captions: one paragraph per company — the picture's companies first, in its order — then the explainer and the
    # grouping line; "each one's largest revenue line" only when every listed member has its fact in the post
    sfx = ".part" if b.part else ""
    each = ".each" if len(facts) == n else ""
    heads = [_t(f"th.ch.long{sfx}{each}", **fill), _t(f"th.ch.short{sfx}", **fill)] + (
        [_t(f"th.ch.min{sfx}", **fill)] if b.size is not None else [])

    def row_para(m: rules.ThemeMember) -> str:
        fact = fact_of.get(m.company.symbol)
        if fact is None:
            return _t("th.mm.p.none", co=m.company.name, theme=b.theme)
        if fact[2] is not None:
            return _t("th.mm.p.share", co=m.company.name, seg=fact[1], share=fact[2], fy=fact[0].fiscal_year)
        return _t("th.mm.p.seg", co=m.company.name, seg=fact[1], fy=fact[0].fiscal_year)

    def short_para(m: rules.ThemeMember) -> str:
        fact = fact_of.get(m.company.symbol)
        if fact is None:
            return _t("th.mm.s.none", co=m.company.name)
        label = THEME_SEGMENT_SHORT_LABELS.get(fact[1], fact[1])
        if fact[2] is not None:
            return _t("th.mm.s.share", co=m.company.name, seg=label, share=fact[2], fy=fact[0].fiscal_year)
        return _t("th.mm.s.seg", co=m.company.name, seg=label, fy=fact[0].fiscal_year)

    by_sym = {m.company.symbol: m for m in members}
    in_order = [by_sym[s] for s in shown_syms] + [m for m in members if m.company.symbol not in shown_syms]
    plan = _theme_row_plan([row_para(m) for m in in_order], n, len(tiles), len(opening["list"]["rows"]),
                           [explainer, grouping], short_rows=[short_para(m) for m in in_order])
    shown = "; ".join(t["name"] for t in tiles)
    if b.size is None:
        alt_body = _t("th.alt.body.some", k=len(tiles), theme=b.theme)
    elif b.size > len(tiles):     # "{k} of the {m}": the whole theme's count (≥ the listed members)
        alt_body = _t("th.alt.body", k=len(tiles), n=b.size, theme=b.theme)
    else:
        alt_body = _t("th.alt.body.all", n=n, theme=b.theme)
    # "its share of revenue" only for the tiles that draw one; "each one's" only when every tile does
    with_share = sum(1 for s in tile_lines if fact_of[s][2] is not None and " · " in tile_lines[s])
    if with_share:
        alt_body += " " + (_t("th.alt.segs.each", fys=fys) if with_share == len(tiles)
                           else _t("th.alt.segs", j=with_share, fys=fys))
    if any(tagged(fact_of[s]) for s in tile_lines):
        alt_body += " " + _t("th.alt.segs.tagged")
    yt = ([_t("th.yt.part", **fill), _t("th.yt.part.short", **fill)] if b.part
          else [_t("th.yt", **fill)] + ([_t("th.yt.short", **fill)] if b.size is not None else []))
    return _Parts(hook=hook, lines=lines, cards=cards, opening=opening, image_spec=spec,
                  alt_title=_alt_title([b.theme, _t("th.alt.title.min")]),
                  alt_paragraphs=[alt_body, _t("th.alt.names", names=shown)], source=rules.source_label(b.rec),
                  as_of=_theme_members_as_of(b.rec),
                  image_headlines=heads, video_headlines=heads, youtube_titles=yt, persons=[],
                  names={t["logo"]: t["name"] for t in tiles}, **plan)


def _theme_biggest(b: _ThemeBase, run_date: date) -> _Parts:
    """Angle 2, "who's biggest" by market value (theme_biggest_power.jpg): the members ranked by their DATED market value
    (owner 2026-09-28: market cap is allowed on a public post — never a price, a % move or a verdict).

    * the PICTURE (`ranked`): the title, "{count} · market value as of {date}", up to THEME_RANKED_ROWS rows — the rank,
      the logo, the ticker chip, the name (ONE line: a top-row name the row cannot hold is refused), a bar in proportion
      to the largest and the value ("$171B") — "+N more" for the rest; the footer "… company filings and market data";
    * the COVER: the title and the list's first rows (rank, logo, chip, name, value), "+N more";
    * the narration: the largest and the next two (each "about": the values are rounded), what market value is, the
      grouping line;
    * the captions: one paragraph per company with its value, the as-of date (`_theme_row_plan`)."""
    rec, members, n, fill = b.rec, list(b.rec.members), b.n, b.fill
    as_of = rec.values_as_of
    values = [m.market_value_usd for m in members]
    if as_of is None or any(v is None or not v > 0 for v in values) or not 0 <= (run_date - as_of).days <= \
            rules.THEME_MARKET_VALUE_MAX_AGE_DAYS:
        raise NewsTemplateRefused("record_invalid", "a market-value post without dated values of the week")
    if [m.company.symbol for m in members] != [m.company.symbol for m in sorted(
            members, key=lambda m: (-float(m.market_value_usd or 0.0), m.company.symbol))]:
        raise NewsTemplateRefused("record_invalid", "the members are not ranked by market value")
    shown = members[:THEME_RANKED_ROWS]
    for m in shown:
        if text_metrics.line_count(m.company.name, THEME_RANKED_FLOOR_PX, THEME_RANKED_NAME_W) != 1:
            raise NewsTemplateRefused("slot_rejected", "a ranked company's name does not fit its row")
    top = float(shown[0].market_value_usd or 0.0)
    rows = [{"rank": str(i + 1), "logo": m.company.symbol, **_row_chip(m.company.symbol), "name": m.company.name,
             "value": market_value_image(float(m.market_value_usd or 0.0)),
             "ratio": _ratio(_dec(float(m.market_value_usd or 0.0)) / _dec(top))} for i, m in enumerate(shown)]
    subtitle = _t("th.big.sub", count=b.count, date_img=date_image(as_of))
    spec: Dict[str, Any] = {"layout": "ranked", "version": onscreen.SPEC_VERSION, "kicker": _t("th.kicker"),
                            "title": b.theme, "subtitle": subtitle, "rows": rows, **b.icon}
    if n > len(rows):
        spec["more"] = _t("th.img.more", k=n - len(rows))
    cover_rows = [{"logo": r["logo"], **({"chip": r["chip"]} if "chip" in r else {}), "label": r["rank"],
                   "cells": [r["name"], r["value"]]} for r in rows]
    opening = _theme_cover(b, cover_rows, subtitle, n)

    sp = [spoken_company(m.company.name) for m in members[:3]]
    vw = [market_value_words(float(m.market_value_usd or 0.0)) for m in members[:3]]
    vi = [market_value_image(float(m.market_value_usd or 0.0)) for m in members[:3]]
    hook = ([_t("th.big.hook.part", **{**fill, "theme": b.theme_sp})] if b.part
            else [_t("th.big.hook", **{**fill, "theme": b.theme_sp})]) + [_t("th.big.hook.min", theme=b.theme_sp)]
    l1 = [_t("th.big.l1", co1=sp[0], v1=vw[0], day_sp=date_spoken(as_of, run_date)),
          _t("th.big.l1.short", co1=sp[0], v1=vw[0])]
    l2 = [_t("th.big.l2", co2=sp[1], v2=vw[1], co3=sp[2], v3=vw[2]), _t("th.big.l2.short", co2=sp[1], v2=vw[1])]
    l3, l4 = [_t("th.big.l3")], [_t("th.grouping")]
    c1 = (_t("th.big.c1.title"), _t("th.big.c1.body", co1=members[0].company.name, v1_img=vi[0],
                                    date_img=date_image(as_of)))
    c2 = (_t("th.big.c2.title"), _t("th.big.c2.body", co2=members[1].company.name, v2_img=vi[1],
                                    co3=members[2].company.name, v3_img=vi[2]))
    c3 = (_t("th.big.c3.title"), _t("th.big.c3.body"))
    c4 = (_t("th.c.grouping.title"), _t("th.c.grouping.body"))
    sfx = ".part" if b.part else ""
    date_cap = date_caption(as_of, run_date)
    heads = [_t(f"th.big.ch.long{sfx}", **fill, date_cap=date_cap), _t(f"th.big.ch.short{sfx}", **fill)]
    row_paras = [_t("th.big.p.row", co=m.company.name, v=market_value_words(float(m.market_value_usd or 0.0)))
                 for m in members]
    short_paras = [_t("th.big.p.short", co=m.company.name, v=market_value_image(float(m.market_value_usd or 0.0)))
                   for m in members]
    plan = _theme_row_plan(row_paras, n, len(rows), len(cover_rows[:THEME_COVER_ROWS]),
                           [_t("th.big.p.explainer", date_cap=date_cap), _t("th.grouping")], short_rows=short_paras)
    if b.size is None:
        alt_body = _t("th.big.alt.body.some", k=len(rows), theme=b.theme, date_img=date_image(as_of))
    elif b.size > len(rows):
        alt_body = _t("th.big.alt.body", k=len(rows), n=b.size, theme=b.theme, date_img=date_image(as_of))
    else:
        alt_body = _t("th.big.alt.body.all", n=n, theme=b.theme, date_img=date_image(as_of))
    yt = [_t(f"th.big.yt{sfx}", **fill)]
    return _Parts(hook=hook, lines=[l1, l2, l3, l4], cards=[c1, c2, c3, c4], opening=opening, image_spec=spec,
                  alt_title=_alt_title([b.theme, _t("th.alt.title.min")]),
                  alt_paragraphs=[alt_body, _t("th.alt.names", names="; ".join(r["name"] for r in rows))],
                  source=rules.source_label(b.rec), as_of=_theme_members_as_of(rec),
                  image_headlines=heads, video_headlines=heads, youtube_titles=yt, persons=[],
                  names={r["logo"]: r["name"] for r in rows}, footer_source=post_copy.FOOTER_SOURCE_FILINGS_MARKET,
                  **plan)


# ── the theme post's last three angles (news-v23 phase 3, 2026-11-20) ─────────────────────────────────────────────

#: The picture's rows of each table angle (template_onscreen.MAX_THEME_TABLE_ROWS: the growth + profit and insider mocks
#: draw 10, the Congress mock 5 — up to 6), "+N more" for the rest; the cover the first THEME_COVER_ROWS.
THEME_FIGURES_ROWS = onscreen.MAX_THEME_TABLE_ROWS["figures"]
THEME_LEDGER_ROWS = onscreen.MAX_THEME_TABLE_ROWS["ledger"]
THEME_TALLY_ROWS = onscreen.MAX_THEME_TABLE_ROWS["tally"]
#: The table angles' video cover: the picture's first THEME_TABLE_COVER_ROWS rows (template_onscreen.MAX_OPENING_TABLE_ROWS).
THEME_TABLE_COVER_ROWS = onscreen.MAX_OPENING_TABLE_ROWS
#: The picture layout each angle draws (`_validate`: a theme post's layout is its record's angle's).
THEME_LAYOUT_OF_ANGLE: Dict[str, str] = {"make_money": "grid", "biggest": "ranked", "growth_profit": "figures",
                                         "insiders": "ledger", "congress": "tally"}


def growth_image(growth: float) -> str:
    """Revenue growth on the picture (theme_growth_profit_silicon.jpg): a whole percent with its sign — "+114%",
    "−5%" (U+2212), and "0%" when it rounds to zero — half-up."""
    pct_ = _q(abs(_dec(growth)) * 100, 0)
    if pct_ == 0:
        return "0%"
    return f"{'+' if growth > 0 else onscreen.MINUS}{pct_:,}%"


def growth_words(growth: float) -> str:
    """The same growth in words: "114% higher than the year before", "5% lower …", "about the same as the year
    before" (rounds to zero) — the only "%" a growth post writes outside the picture (`_GROWTH_PHRASE_RE`)."""
    pct_ = _q(abs(_dec(growth)) * 100, 0)
    if pct_ == 0:
        return _t("th.gp.flat")
    return _t("th.gp.up" if growth > 0 else "th.gp.down", g=f"{pct_:,}")


def _per_100(value: float) -> str:
    """|profit per $100| as dollars: whole dollars from $1 ("$56", "$1,000"), cents below ("$0.40"). A non-zero figure
    that would print as "$0.00" is refused (`implausible_figures`): never a zero that is not one."""
    a = abs(_dec(value))
    if a == 0:
        return "$0"
    whole = _q(a, 0)
    if whole >= 1:
        return f"${whole:,}"
    cents = _q(a, 2)
    if cents == 0:
        raise NewsTemplateRefused("implausible_figures", "a profit per $100 under a cent")
    return f"${cents}"


def profit_image(value: float) -> str:
    """Net income per $100 of revenue on the picture: "$56", "−$17" (a loss, U+2212), "$0.40", "$0"."""
    shown = _per_100(value)
    return f"{onscreen.MINUS}{shown}" if value < 0 and shown != "$0" else shown


def profit_words(value: float, *, short: bool = False) -> str:
    """The same in words: "$56 of profit per $100 of revenue" / "a loss of $17 per $100 of revenue" / "no profit and no
    loss" (short: "$56 profit per $100" / "$17 loss per $100")."""
    if value == 0:
        return _t("th.gp.zero")
    shown = _per_100(value)
    if short:
        return _t("th.gp.keep.short" if value > 0 else "th.gp.loss.short", p=shown)
    return _t("th.gp.keep" if value > 0 else "th.gp.loss", p=shown)


def _table_count(b: _ThemeBase, n: int) -> str:
    """The sub line's count of `n` companies — "{n} of its {m} companies" whenever the theme holds more."""
    part = b.size is not None and b.size > n
    return _t("th.img.count.part" if part else "th.img.count", n=n, m=b.size)


def _table_names_fit(angle: str, names: Sequence[str]) -> None:
    """Every company of a theme table draws whole in the worker's name column (`rules.theme_table_name_fits`: at most
    two lines of the floor) — the adapter never lists one that cannot; a record that does is refused (`slot_rejected`),
    never clipped or squeezed under the floor."""
    for name in names:
        if not rules.theme_table_name_fits(angle, name):
            raise NewsTemplateRefused("slot_rejected", "a theme table's company name does not fit its column")


def _theme_table_cover(b: _ThemeBase, spec: Mapping[str, Any], n: int, more_id: str = "th.img.more"
                       ) -> Dict[str, Any]:
    """The video's opening card of a theme TABLE (the 13F cover's idea, RESTYLE_SPEC §1): the title and the picture's
    first THEME_TABLE_COVER_ROWS rows in the picture's own layout (`list.layout`), under its sub line, its column heads (or
    unit), "+N more" for the rest, and its note — the same rows, figures and words as the picture."""
    rows = list(spec["rows"][:THEME_TABLE_COVER_ROWS])
    lst: Dict[str, Any] = {"layout": spec["layout"], "heading": spec["subtitle"], "rows": rows}
    for k in ("heads", "unit"):
        if k in spec:
            lst[k] = spec[k]
    if n > len(rows):
        lst["more"] = _t(more_id, k=n - len(rows))
    if "note" in spec:
        lst["note"] = spec["note"]
    return {"kicker": spec["kicker"], "logos": [rows[0]["logo"]], "figure": str(n), "headline": b.theme, "list": lst}


def _theme_alt(b: _ThemeBase, k: int, n: int, ids: Tuple[str, str, str], **fill: Any) -> str:
    """The alt text's first sentence: "{k} of the {n} companies" / "the {n} companies" / "{k} companies"."""
    if b.size is None:
        return _t(ids[2], k=k, theme=b.theme, **fill)
    if b.size > k:
        return _t(ids[0], k=k, n=b.size, theme=b.theme, **fill)
    return _t(ids[1], n=n, theme=b.theme, **fill)


def _theme_growth_profit(b: _ThemeBase, run_date: date) -> _Parts:
    """Angle 3, "growth + profit" (theme_growth_profit_silicon.jpg): each member's latest fiscal year — its revenue
    growth over the year before and its net income per $100 of revenue — in the theme's order (no ranking word).

    * the PICTURE (`figures`): the title, "{count} · revenue growth and profit · latest fiscal year", the column heads
      "Revenue growth" and "Profit per $100" / "of revenue", up to THEME_FIGURES_ROWS rows — the logo, the ticker chip,
      the name, a bar in proportion to the largest growth shown, the growth ("+114%", GREEN; "−5%", RED) and the profit
      per $100 ("$56" GREEN, "−$17" RED) — "+N more", the note "Profit = net income; growth = revenue vs the year
      before", the footer "… company financial statements · fiscal {years}" (the rows' years);
    * the COVER: the same table's first rows;
    * the narration: the first two companies' figures, what the figures are, the grouping line;
    * the captions: one paragraph per company (`_theme_row_plan`), its fiscal year with it."""
    members = list(b.rec.members)
    n, fill = b.n, b.fill
    facts: List[Tuple[rules.ThemeMember, float, float]] = []
    for m in members:
        g, p = rules.theme_growth(m), rules.theme_profit_per_100(m)
        fy = m.fiscal_year
        if g is None or p is None or fy is None or not fy.isascii() or not fy.isdigit():
            raise NewsTemplateRefused("record_invalid", "a growth + profit member without its figures")
        if not run_date.year - 3 <= int(fy) <= run_date.year + 1:
            raise NewsTemplateRefused("stale_source", "a member's latest fiscal year is not near the run")
        facts.append((m, g, p))
    shown = facts[:THEME_FIGURES_ROWS]
    _table_names_fit("growth_profit", [m.company.name for m, _g, _p in facts])
    top = max(abs(_dec(g)) for _m, g, _p in shown)
    rows = [{"logo": m.company.symbol, **_row_chip(m.company.symbol), "name": m.company.name,
             "growth": growth_image(g), "ratio": _ratio(abs(_dec(g)) / top) if top > 0 else 0.0,
             "profit": profit_image(p)} for m, g, p in shown]
    years = sorted({m.fiscal_year for m, _g, _p in shown if m.fiscal_year}, key=int)
    fys = years[0] if len(years) == 1 else (_t("th.fys.2", a=years[0], b=years[1]) if len(years) == 2
                                            else _t("th.fys.span", a=years[0], b=years[-1]))
    spec: Dict[str, Any] = {"layout": "figures", "version": onscreen.SPEC_VERSION, "kicker": _t("th.kicker"),
                            "title": b.theme, "subtitle": _t("th.gp.sub", count=b.count),
                            "heads": [[_t("th.gp.head.growth")], [_t("th.gp.head.profit.1"), _t("th.gp.head.profit.2")]],
                            "rows": rows, "note": _t("th.gp.note"), **b.icon}
    if n > len(rows):
        spec["more"] = _t("th.img.more", k=n - len(rows))
    opening = _theme_table_cover(b, spec, n)

    def says(m: rules.ThemeMember, g: float, p: float) -> Dict[str, Any]:
        return dict(co=m.company.name, co_sp=spoken_company(m.company.name), fy=m.fiscal_year,
                    growth=growth_words(g), profit=profit_words(p), profit_short=profit_words(p, short=True))

    s1, s2 = says(*facts[0]), says(*facts[1])
    hook = ([_t("th.gp.hook.part", n=n, m=b.size, theme=b.theme_sp)] if b.part
            else [_t("th.gp.hook", n=n, theme=b.theme_sp)]) + [_t("th.gp.hook.min", theme=b.theme_sp)]

    def line(s: Dict[str, Any]) -> List[str]:
        return [_t("th.gp.l", co=s["co_sp"], fy=s["fy"], growth=s["growth"], profit=s["profit"]),
                _t("th.gp.l.short", co=s["co_sp"], growth=s["growth"], profit=s["profit"]),
                _t("th.gp.l.min", co=s["co_sp"], fy=s["fy"], growth=s["growth"])]

    def card(s: Dict[str, Any]) -> Tuple[str, str]:
        if _longest_word(s["co"]) > CARD_TITLE_WORD_MAX_CHARS or _words(s["co"]) > CARD_TITLE_MAX_WORDS:
            return _t("th.gp.c.title.min"), _t("th.gp.c.body.min", co=s["co"], fy=s["fy"], growth=s["growth"],
                                               profit=s["profit"])
        return s["co"], _t("th.gp.c.body", fy=s["fy"], growth=s["growth"], profit=s["profit"])

    lines = [line(s1), line(s2), [_t("th.gp.l3"), _t("th.gp.l3.short")], [_t("th.grouping")]]
    cards = [card(s1), card(s2), (_t("th.gp.c3.title"), _t("th.gp.c3.body")),
             (_t("th.c.grouping.title"), _t("th.c.grouping.body"))]
    sfx = ".part" if b.part else ""
    heads = [_t(f"th.gp.ch.long{sfx}", **fill), _t(f"th.gp.ch.short{sfx}", **fill)]
    row_paras = [_t("th.gp.p.row", co=m.company.name, fy=m.fiscal_year, growth=growth_words(g), profit=profit_words(p))
                 for m, g, p in facts]
    short_paras = [_t("th.gp.p.short", co=m.company.name, g_img=growth_image(g), profit=profit_words(p, short=True))
                   for m, g, p in facts]
    plan = _theme_row_plan(row_paras, n, len(rows), len(opening["list"]["rows"]),
                           [_t("th.gp.p.explainer"), _t("th.grouping")], short_rows=short_paras)
    alt = _theme_alt(b, len(rows), n, ("th.gp.alt.body", "th.gp.alt.body.all", "th.gp.alt.body.some"))
    yt = [_t(f"th.gp.yt{sfx}", **fill)]
    return _Parts(hook=hook, lines=lines, cards=cards, opening=opening, image_spec=spec,
                  alt_title=_alt_title([b.theme, _t("th.alt.title.min")]),
                  alt_paragraphs=[alt, _t("th.alt.names", names="; ".join(r["name"] for r in rows))],
                  source=rules.source_label(b.rec), as_of=_theme_members_as_of(b.rec),
                  image_headlines=heads, video_headlines=heads, youtube_titles=yt, persons=[],
                  names={r["logo"]: r["name"] for r in rows}, footer_source=post_copy.FOOTER_SOURCE_STATEMENTS,
                  footer_when=_t("foot.fiscal", fy=fys), **plan)


def _theme_insiders(b: _ThemeBase, run_date: date) -> _Parts:
    """Angle 4, the "insider scorecard" (theme_insiders_cyber.jpg; owner 2026-11-19): each listed member's Form 4
    purchases (code P) and sales, in dollars as filed, filed in the record's 90-day window — class C wording, company
    totals only (never a person, a role or anyone's name).

    * the PICTURE (`ledger`): the title, "{companies checked} · insider filings · last 90 days", the column heads
      "Disclosed purchases" (a solid GREEN pill) and "Disclosed sales" (RED), up to THEME_LEDGER_ROWS rows — the logo,
      the ticker chip, the name, the purchases (GREEN) and the sales (RED), "—" for none — "+N more with filings", the
      note "Purchases and sales only · sales can be pre-planned (10b5-1)" (never "open-market": code P / S is "open
      market or private", compliance F2), the footer "… SEC Form 4 · filed {window}";
    * the COVER: the same table's first rows;
    * the narration: how many disclosed purchases and sales (the largest of each), the 10b5-1 hedge, the grouping;
    * the captions: one paragraph per listed company, how the totals are made, the hedges, the companies checked with
      none, the companies left unchecked."""
    rec = b.rec
    members = list(rec.members)
    n = b.n
    ws, we, checked = rec.window_start, rec.window_end, rec.companies_checked
    if ws is None or we is None or not isinstance(checked, int) or we != run_date - timedelta(days=1) or \
            (we - ws).days != rules.THEME_INSIDER_WINDOW_DAYS - 1 or checked < n:
        raise NewsTemplateRefused("record_invalid", "an insider scorecard without its window or its checked count")
    if members != sorted(members, key=rules.theme_insider_order_key):
        raise NewsTemplateRefused("record_invalid", "the scorecard's rows are not in its order")
    shown = members[:THEME_LEDGER_ROWS]
    _table_names_fit("insiders", [m.company.name for m in members])
    dash = onscreen.EMPTY_CELL

    def amt(v: Optional[float]) -> str:
        if v and v < 1:
            raise NewsTemplateRefused("implausible_figures", "an insider total under $1 would print as $0")
        return money_image(float(v)) if v else dash

    rows = [{"logo": m.company.symbol, **_row_chip(m.company.symbol), "name": m.company.name,
             "buys": amt(m.purchases_usd), "sells": amt(m.sales_usd)} for m in shown]
    head1 = _t("th.in.head.1")
    count = _table_count(b, checked)
    spec: Dict[str, Any] = {"layout": "ledger", "version": onscreen.SPEC_VERSION, "kicker": _t("th.kicker"),
                            "title": b.theme, "subtitle": _t("th.in.sub", count=count),
                            "heads": [[head1, _t("th.in.head.buy")], [head1, _t("th.in.head.sell")]],
                            "rows": rows, "note": _t("th.in.note"), **b.icon}
    if n > len(rows):
        spec["more"] = _t("th.in.more", k=n - len(rows))
    opening = _theme_table_cover(b, spec, n, "th.in.more")
    window_cap = window_caption(ws, we, run_date)

    def leaders(key: str) -> List[rules.ThemeMember]:
        return sorted((m for m in members if (getattr(m, key) or 0) > 0),
                      key=lambda m: (-float(getattr(m, key) or 0.0), m.company.symbol))

    buyers, sellers = leaders("purchases_usd"), leaders("sales_usd")

    def summary(prefix: str, group: List[rules.ThemeMember], key: str) -> List[str]:
        if not group:
            return [_t(f"{prefix}.none", c=checked)]
        co = spoken_company(group[0].company.name)
        words = money_words(float(getattr(group[0], key) or 0.0))
        if len(group) == 1:
            return [_t(f"{prefix}.one", c=checked, co=co, amt=words), _t(f"{prefix}.one.short", co=co)]
        return [_t(prefix, k=len(group), c=checked, co=co, amt=words), _t(f"{prefix}.short", k=len(group), c=checked)]

    def card_body(group: List[rules.ThemeMember], key: str) -> str:
        if not group:
            return _t("th.in.c.none")
        a = group[0]
        if len(group) == 1:
            return _t("th.in.c.one", co=a.company.name, amt=money_image(float(getattr(a, key) or 0.0)))
        c = group[1]
        return _t("th.in.c.two", co=a.company.name, amt=money_image(float(getattr(a, key) or 0.0)),
                  co2=c.company.name, amt2=money_image(float(getattr(c, key) or 0.0)))

    hook = [_t("th.in.hook", theme=b.theme_sp), _t("th.in.hook.min", theme=b.theme_sp)]
    lines = [summary("th.in.l1", buyers, "purchases_usd"), summary("th.in.l2", sellers, "sales_usd"),
             [_t("th.in.l3")], [_t("th.grouping")]]
    cards = [(_t("th.in.c1.title"), card_body(buyers, "purchases_usd")),
             (_t("th.in.c2.title"), card_body(sellers, "sales_usd")),
             (_t("th.in.c3.title"), _t("th.in.c3.body")), (_t("th.c.grouping.title"), _t("th.c.grouping.body"))]
    heads = [_t("th.in.ch.long", theme=b.theme, window_cap=window_cap), _t("th.in.ch.short", theme=b.theme)]

    def para(m: rules.ThemeMember, short: bool) -> str:
        buys, sells = float(m.purchases_usd or 0.0), float(m.sales_usd or 0.0)
        form = (money_image if short else money_words)
        kind = "both" if buys > 0 and sells > 0 else ("buy" if buys > 0 else "sell")
        return _t(f"th.in.{'s' if short else 'p'}.{kind}", co=m.company.name, b=form(buys) if buys else "",
                  s=form(sells) if sells else "")

    tail = [_t("th.in.p.how", window_cap=window_cap), _t("th.in.p.plans")]
    quiet = checked - n
    if quiet == 1:
        tail.append(_t("th.in.p.quiet.one"))
    elif quiet > 1:
        tail.append(_t("th.in.p.quiet", k=quiet))
    if b.size is not None and b.size > checked:
        tail.append(_t("th.in.p.unchecked", u=b.size - checked, m=b.size))
    tail.append(_t("th.grouping"))
    plan = _theme_row_plan([para(m, False) for m in members], n, len(rows), len(opening["list"]["rows"]), tail,
                           short_rows=[para(m, True) for m in members])
    return _Parts(hook=hook, lines=lines, cards=cards, opening=opening, image_spec=spec,
                  alt_title=_alt_title([b.theme, _t("th.alt.title.min")]),
                  alt_paragraphs=[_t("th.in.alt.body", k=len(rows), theme=b.theme),
                                  _t("th.alt.names", names="; ".join(r["name"] for r in rows))],
                  source=rules.source_label(rec), as_of=_theme_members_as_of(rec),
                  image_headlines=heads, video_headlines=heads, youtube_titles=[_t("th.in.yt", theme=b.theme)],
                  persons=[], names={r["logo"]: r["name"] for r in rows}, footer_source=post_copy.FOOTER_SOURCE_FORM4,
                  footer_when=_t("foot.filed", date_img=window_image(ws, we)), **plan)


def _theme_congress(b: _ThemeBase, run_date: date) -> _Parts:
    """Angle 5, "Congress" (theme_congress_defense.jpg; owner 2026-11-19): the theme's companies whose stock at least
    two DISTINCT members of Congress disclosed purchases of in the record's disclosure month, the most members first —
    the Congress Count's rules and words (never a member's name, party, state, chamber; never "bought"; no dollars).

    * the PICTURE (`tally`): the title, "Members of Congress who disclosed purchases" ("purchases" on a solid GREEN
      pill), up to THEME_TALLY_ROWS rows — the logo, the ticker chip over the name, the count (GREEN) over "members" —
      "+N more", the note "{Month YYYY} · each member counted once per company", the footer "… congressional
      disclosures · {Month YYYY}";
    * the COVER: the same rows;
    * the narration: the lead company's count, the next two, "counted once" with the spouse hedge, the reporting delay;
    * the captions: one paragraph per company, the floor, the period, once per company, the report hedges."""
    rec = b.rec
    members = list(rec.members)
    n = b.n
    if not isinstance(rec.month, str) or rec.month is None:
        raise NewsTemplateRefused("record_invalid", "a Congress post without its month")
    start = date(int(rec.month[:4]), int(rec.month[5:7]), 1)
    if rules.month_end_of(rec.month) >= run_date:
        raise NewsTemplateRefused("record_invalid", "the disclosure month has not ended")
    for m in members:
        c = m.congress_members
        if not isinstance(c, int) or isinstance(c, bool) or not rules.CONGRESS_MIN_MEMBERS <= c <= 535:
            raise NewsTemplateRefused("too_few_rows", "a count of fewer than two members is never posted")
    if members != sorted(members, key=rules.theme_congress_order_key):
        raise NewsTemplateRefused("record_invalid", "the Congress rows are not the most members first")
    shown = members[:THEME_TALLY_ROWS]
    _table_names_fit("congress", [m.company.name for m in members])
    month = dict(month_long=month_words(start), month_name=month_name(start), as_of_cap=date_caption(run_date, run_date))
    rows = [{"logo": m.company.symbol, **_row_chip(m.company.symbol), "name": m.company.name,
             "count": str(m.congress_members)} for m in shown]
    spec: Dict[str, Any] = {"layout": "tally", "version": onscreen.SPEC_VERSION, "kicker": _t("th.kicker"),
                            "title": b.theme, "subtitle": _t("th.cg.sub"), "unit": _t("th.cg.unit"), "rows": rows,
                            "note": _t("th.cg.note", **month), **b.icon}
    if n > len(rows):
        spec["more"] = _t("th.img.more", k=n - len(rows))
    opening = _theme_table_cover(b, spec, n)
    cos = [m.company.name for m in members]
    sps = [spoken_company(co) for co in cos]
    ns = [int(m.congress_members or 0) for m in members]
    hook = [_t("th.cg.hook", theme=b.theme_sp, **month), _t("th.cg.hook.min", theme=b.theme_sp)]
    l1 = [_t("th.cg.l1", n=ns[0], co_sp=sps[0], **month), _t("cg.hook", n=ns[0], co_sp=sps[0], **month)]
    pair = dict(n=ns[1], co_sp=sps[1], n2=ns[2], co2_sp=sps[2])
    l2 = [_t("cg.m.l.other.two", **pair), _t("cg.m.l.other.two.short", **pair)]
    lines = [l1, l2, [_t("cg.m.l.once")], [_t("cg.l4"), _t("cg.m.l4.short")]]
    if _longest_word(cos[0]) > CARD_TITLE_WORD_MAX_CHARS or _words(cos[0]) > CARD_TITLE_MAX_WORDS:
        c1 = (_t("th.cg.c1.title.min"), _t("th.cg.c1.body.min", co=cos[0], n=ns[0], **month))
    else:
        c1 = (_t("th.cg.c1.title", co=cos[0]), _t("th.cg.c1.body", n=ns[0], **month))
    cards = [c1, (_t("cg.m.c.other.title"), _t("cg.m.c.other.body.two", co=cos[1], n=ns[1], co2=cos[2], n2=ns[2])),
             (_t("cg.m.c.once.title"), _t("cg.m.c.once.body")), (_t("cg.c4.title"), _t("cg.c4.body"))]
    heads = [_t("th.cg.ch.long", theme=b.theme, **month), _t("th.cg.ch.short", theme=b.theme, **month)]
    row_paras = [_t("cg.m.p.row", co=m.company.name, sym=m.company.symbol, n=m.congress_members, **month)
                 for m in members]
    short_paras = [_t("th.cg.s", co=m.company.name, n=m.congress_members) for m in members]
    tail = [_t("th.cg.p.floor"), _t("cg.m.p.count", **month), _t("cg.m.p.once"), _t("cg.l3"), _t("cg.l4"),
            _t("th.grouping")]
    plan = _theme_row_plan(row_paras, n, len(rows), len(opening["list"]["rows"]), tail, short_rows=short_paras)
    alt = [_t("th.cg.alt.body", k=len(rows), theme=b.theme, **month),
           _t("cg.m.alt.rows", items="; ".join(_t("cg.m.alt.row", co=r["name"], n=r["count"]) for r in rows))]
    return _Parts(hook=hook, lines=lines, cards=cards, opening=opening, image_spec=spec,
                  alt_title=_alt_title([b.theme, _t("th.alt.title.min")]), alt_paragraphs=alt,
                  source=rules.source_label(rec), as_of=_theme_members_as_of(rec),
                  image_headlines=heads, video_headlines=heads, youtube_titles=[_t("th.cg.yt", theme=b.theme, **month)],
                  persons=[], names={r["logo"]: r["name"] for r in rows}, footer_source=post_copy.FOOTER_SOURCE_CONGRESS,
                  footer_when=_t("foot.month", **month), **plan)


#: The angle builders (news-v23 phase 3), pinned == `rules.THEME_BUILT_ANGLES` and the adapter's collectors — each a
#: `(base, run_date) -> _Parts` function.
_THEME_ANGLE_BUILDERS: Dict[str, Callable[[_ThemeBase, date], _Parts]] = {
    "make_money": _theme_make_money,
    "biggest": _theme_biggest,
    "growth_profit": _theme_growth_profit,
    "insiders": _theme_insiders,
    "congress": _theme_congress,
}


_BUILDERS: Dict[str, Callable[..., _Parts]] = {
    "ceo_buys": _insider_parts, "insider_buys": _insider_parts,
    "thirteen_f": _thirteen_f_parts, "congress_count": _congress_parts, "company_stakes": _stakes_parts,
    "earnings": _earnings_parts, "money_map": _money_map_parts, "theme_explainer": _theme_parts,
    # news-v22 (2026-11-15): the spotlight lane's single-company series
    "insider_spotlight": _insider_spotlight_parts, "thirteen_f_spotlight": _thirteen_f_spotlight_parts,
}
#: The builders that may name a PERSON (a Form 4 filer): they take the `suppressed` set `compose` grows when a
#: rendered name collides with restricted text (rendered role-only).
_PERSON_BUILDERS: Tuple[Callable[..., _Parts], ...] = (_insider_parts, _insider_spotlight_parts)
#: The series whose records carry Form 4 people (the persons, placement and members-elect checks read them).
FORM4_PERSON_SERIES: FrozenSet[str] = frozenset({"ceo_buys", "insider_buys", "insider_spotlight"})


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


def _breaking_fresh(record: Any, run_date: date) -> bool:
    """May this record's caption say BREAKING (news-v22)? Only an insider spotlight whose LEAD's last Form 4 filing
    falls at most BREAKING_MAX_BUSINESS_DAYS federal business days before the run date — (source, run_date] counted on
    the SEC's federal calendar, weekends and federal holidays excluded (`_business_days_after`) — and whose lead's
    trades are not late (`_late_trades`: a late filing states its trade dates, never news); or an earnings report whose
    report date falls at most BREAKING_MAX_BUSINESS_DAYS such days before the run date counted on the EXCHANGE's
    calendar (C0, 2026-11-16: `_earnings_days_after` — a report is market news, and a federal holiday the exchanges
    trade through, such as Veterans Day, still ages it a session; never looser than the federal count). Tue run + Mon
    filing → yes; Thu run + Tue filing → no; Sat run + Fri or Thu filing → yes; Fri Nov 27, 2026 + Wed Nov 25 → yes
    (Thanksgiving closes both calendars); earnings Thu Nov 12, 2026 + Tue Nov 10 → no (Veterans Day is a trading day).
    news-v23: an earnings post of several companies (`EarningsDay`) is fresh only when EVERY company's report is — one
    older report among them and the post is no BREAKING news (the stricter reading).
    Any other record (a weekly roundup, a 13F, a Money Map…) is never fresh. Pure; never raises on a valid record."""
    n = BREAKING_MAX_BUSINESS_DAYS
    if isinstance(record, rules.InsiderSpotlight):
        # owner decisions 2026-11-20: a LIST of 3-5 companies is fresh only when EVERY person's last filing is and no
        # one's trades are late (the earnings list's stricter reading)
        people = [p for c in record.companies for p in c.people]
        return all(p.filing_dates[-1] < run_date
                   and _business_days_after(p.filing_dates[-1], run_date, stop_above=n) <= n
                   and not _late_trades(p) for p in people)
    if isinstance(record, rules.EarningsDay):
        return all(r.report_date < run_date and _earnings_days_after(r.report_date, run_date, stop_above=n) <= n
                   for r in record.reports)
    return False


def _chosen_prefix(spec: SeriesSpec, record: Any, run_date: date, on: bool) -> Optional[str]:
    """The first-line prefix this composition writes, or None: none while the switch is off or for a series that
    lists none; else the series' first id that applies — a BREAKING id only while `_breaking_fresh` (an insider
    spotlight then falls to its alert; earnings to no prefix at all)."""
    if not on:
        return None
    for pid in PREFIX_IDS_BY_SERIES.get(spec.series, ()):
        if pid in BREAKING_PREFIX_IDS and not _breaking_fresh(record, run_date):
            continue
        return FIRST_LINE_PREFIXES[pid]
    return None


#: The caption fields that carry a headline and, only for the 13F list, its rows (`_Parts.field_paragraphs`).
_TEXT_FIELDS: FrozenSet[str] = frozenset({"x", "bluesky", "threads"})
#: The platforms whose post may carry the day's PICTURE (`schemas.POST_FORMATS_BY_PLATFORM`'s "image" format, pinned
#: equal by a test) — `compose`'s `picture_outlets` default (every one: what a caller that does not say composes).
PICTURE_OUTLETS: Tuple[str, ...] = ("bluesky", "facebook", "linkedin", "threads", "x")
#: Review 2026-11-20: the short outlets whose caption follows its post's FORMAT — a TEXT post lists the rows from the
#: top (`_Parts.text_paragraphs`); a pictured one keeps the picture's "+N more" promise, else it is written as a text
#: post and frozen as TEXT (`text_outlets` → script_service.freeze_post_formats, ``caption_list_long``) — never
#: dropped. X is TEXT unless MARKETING_X_IMAGES; Bluesky's 300 characters hold the headline and about one row.
TEXT_PLAN_OUTLETS: Tuple[str, ...] = ("x", "bluesky")


def _caption_bodies(parts: _Parts, spec: SeriesSpec, run_date: date, store_state: str, allow_x_url: bool,
                    prefix: Optional[str] = None, pictured: FrozenSet[str] = frozenset(PICTURE_OUTLETS),
                    ) -> Tuple[Dict[str, str], Dict[str, List[Dict[str, str]]], List[str]]:
    """A9: the body of every caption field, or the outlet dropped (`over_budget`). With a first-line `prefix` (news-v22)
    every field but the YouTube title tries it CONTENT FIRST: per headline (long → short → min), the prefixed form
    (the prefix, a space, the headline's `after_prefix` twin), then the plain headline — so a tight outlet (X,
    Bluesky) drops the prefix before it drops a longer headline or a paragraph, and Threads drops it before its
    composed caption passes THREADS_PICTURE_CAPTION_MAX_BYTES (the picture). Without a prefix: exactly as before.

    Review 2026-11-20 (`pictured`: the platforms whose post carries the picture — `compose`'s `picture_outlets`), for
    a list series (`_Parts.text_paragraphs`): X or Bluesky NOT pictured writes the rows from the top, none required
    (a text post owes the picture nothing; it used to list only the rows the picture leaves out — the smallest); a
    pictured one keeps the picture's promise, else it is written the same text way and returned in the third value
    (`text_outlets`: the post is frozen as TEXT, never dropped). Threads not pictured tries every row first."""
    def budget(f: str) -> int:
        return post_copy.body_budget(f, spec.category, run_date, allow_x_url=allow_x_url,
                                     store_state=store_state, authorship=AUTHORSHIP)

    def firsts(f: str, h: str) -> List[str]:
        if prefix is None or f == "youtube_title":
            return [h]
        return [f"{prefix} {parts.after_prefix.get(h, h)}", h]

    def costs_the_picture(f: str, body: str, *, rows: bool = False) -> bool:
        # Threads: a prefixed caption over the picture post's byte cap would freeze Threads as TEXT — the prefix
        # gives way first (the plain headline is tried next). A body carrying the 13F spotlight list's rows
        # (`rows`) gives way to fewer rows the same way, and also when the EXTRA video post's caption — the same
        # caption with the longer narrated disclaimer (`post_copy.extra_video_caption`) — would pass Threads' limit
        if f != "threads":
            return False
        composed = post_copy.compose(f, {f: body}, category=spec.category, run_date=run_date,
                                     allow_x_url=allow_x_url, store_state=store_state, authorship=AUTHORSHIP)
        if len(composed.caption.encode("utf-8")) > THREADS_PICTURE_CAPTION_MAX_BYTES:
            return True
        if rows:
            try:
                post_copy.extra_video_caption(f, composed.caption, run_date, AUTHORSHIP)
            except ValueError:
                return True
        return False

    def fit(f: str, heads: Sequence[str], paras: Sequence[str], limit: int,
            need: Optional[int] = None) -> Optional[str]:
        b = budget(f)
        if need is None:
            need = parts.field_required.get(f, parts.required_paragraphs if f in parts.required_fields else 0)
        # Each headline's OWN paragraphs: the ones it does not already state, then the outlet's limit
        # (review round 10 — assembled after the headline is picked, so a paragraph never repeats it).
        own = [[p for p in paras if p not in parts.stated_by_headline.get(h, frozenset())][:limit]
               for h in heads]
        # Long → short headline, then one trailing paragraph fewer: `cut` counts the paragraphs
        # dropped from each headline's own list (with no stated paragraphs, every list is the same
        # and this is the plain "most paragraphs first, then the longest headline" order). A prefix is
        # tried before each headline's plain form, never before a longer headline or a paragraph.
        for cut in range(max((len(o) for o in own), default=0) + 1):
            for h, mine in zip(heads, own):
                kept = len(mine) - cut
                if kept < need or kept < 0:
                    continue
                for first in firsts(f, h):
                    body = "\n\n".join([first, *mine[:kept]])
                    if post_copy.measured_length(f, body) > b:
                        continue
                    # a prefixed body, or one carrying text paragraphs (the 13F spotlight list's rows on Threads): never
                    # past the picture's byte cap — the plain headline alone is the form every other series knew fits
                    rows = kept > 0 and f in _TEXT_FIELDS
                    if (first != h or rows) and costs_the_picture(f, body, rows=rows):
                        continue
                    return body
        return None

    wide = 4 if parts.paragraph_limit is None else parts.paragraph_limit
    plan: Dict[str, Tuple[Sequence[str], Sequence[str], int]] = {
        "x": (parts.image_headlines, (), 0), "bluesky": (parts.image_headlines, (), 0),
        "threads": (parts.image_headlines, (), 0),
        "facebook": (parts.image_headlines, parts.image_paragraphs, wide),
        "linkedin": (parts.image_headlines, parts.image_paragraphs, wide),
        "tiktok": (parts.video_headlines, parts.video_paragraphs, 1),
        "instagram": (parts.video_headlines, parts.video_paragraphs, wide),
        "youtube_description": (parts.video_headlines, parts.video_paragraphs, wide),
        "youtube_title": (parts.youtube_titles, (), 0),
    }
    for f, own in parts.field_paragraphs.items():
        if f in plan and f != "youtube_title":
            plan[f] = (plan[f][0], list(own), len(own))
    bodies: Dict[str, str] = {}
    dropped: Dict[str, List[Dict[str, str]]] = {}
    text_outlets: List[str] = []
    text = list(parts.text_paragraphs)
    for f in post_copy.CAPTION_FIELDS:
        heads, paras, limit = plan[f]
        body: Optional[str] = None
        if text and f in TEXT_PLAN_OUTLETS and f not in pictured:
            # a TEXT post: the rows from the top, none required — it carries no picture whose promise it must keep
            body = fit(f, heads, text, len(text), need=0)
        else:
            if text and f == "threads" and f not in pictured:
                # a TEXT Threads post: every row while they all fit (a superset of the cover's promise, which its
                # caption also keeps as the extra video's); else its own plan below
                body = fit(f, heads, text, len(text), need=len(text))
            if body is None:
                body = fit(f, heads, paras, limit)
            for fewer, keep in parts.field_fallbacks.get(f, []) if f != "youtube_title" else []:
                if body is not None:
                    break
                body = fit(f, heads, fewer, len(fewer), need=keep)
            if body is None and text and f in TEXT_PLAN_OUTLETS:
                # pictured, but no body keeps the picture's "+N more": written as a TEXT post (frozen as text), never
                # dropped — and never beside a picture whose promise it does not keep
                body = fit(f, heads, text, len(text), need=0)
                if body is not None:
                    text_outlets.append(f)
        platform = "youtube" if f.startswith("youtube") else f
        if body is None:
            dropped.setdefault(platform, []).append(
                {"field": f, "code": "over_budget", "detail": f"no variant fits {budget(f)} characters"})
        else:
            bodies[f] = body
    return bodies, dropped, sorted(text_outlets)


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
    elif layout in ("spotlight", "bars", "feature"):    # `feature` (2026-11-15): its header logo, in the art band
        add(spec_image["header"].get("logo"))
    elif layout == "pair":
        add(spec_image["left"].get("logo"))
        add(spec_image["right"].get("logo"))
    elif layout == "grid":
        for tile in spec_image.get("tiles") or ():
            add(tile.get("logo"))
    elif layout in ("ranked",) + onscreen.THEME_TABLE_LAYOUTS:     # news-v23 phase 3: a theme's company rows
        for row in spec_image.get("rows") or ():
            add(row.get("logo"))
    for k in opening.get("logos") or ():
        add(k)
    cover = opening.get("list")
    if isinstance(cover, dict):
        # news-v23: the 13F list cover's rows (the picture's first rows — already referenced; kept for any other)
        for row in cover.get("rows") or ():
            if isinstance(row, dict):
                add(row.get("logo"))
    missing = [k for k in keys if k not in names]
    if missing:
        raise NewsTemplateRefused("image_spec_invalid", "a referenced logo has no company name")
    return [{"key": k, "name": names[k]} for k in keys]


#: news-v23 (restyle 2026-11-19, RESTYLE_SPEC §1): each series' family source on the picture's short footer
#: (`post_copy.image_footer`). Pinned in tests/test_marketing_news_templates.py; a series without a row cannot compose.
FOOTER_SOURCE_OF_SERIES: Dict[str, str] = {
    "ceo_buys": post_copy.FOOTER_SOURCE_FORM4,
    "insider_buys": post_copy.FOOTER_SOURCE_FORM4,
    "insider_spotlight": post_copy.FOOTER_SOURCE_FORM4,
    "thirteen_f": post_copy.FOOTER_SOURCE_13F,
    "thirteen_f_spotlight": post_copy.FOOTER_SOURCE_13F,
    "money_map": post_copy.FOOTER_SOURCE_STATEMENTS,
    "earnings": post_copy.FOOTER_SOURCE_EARNINGS,
    "congress_count": post_copy.FOOTER_SOURCE_CONGRESS,
    "company_stakes": post_copy.FOOTER_SOURCE_FILINGS,
    "theme_explainer": post_copy.FOOTER_SOURCE_FILINGS,
}


def _compose_once(record: Any, spec: SeriesSpec, run_date: date, store_state: str, allow_x_url: bool,
                  suppressed: FrozenSet[str], *, prefix_on: bool = False, prefix: Optional[str] = None,
                  pictured: Tuple[str, ...] = PICTURE_OUTLETS) -> Dict[str, Any]:
    builder = _BUILDERS[spec.series]
    parts = (builder(record, run_date, suppressed) if builder in _PERSON_BUILDERS else builder(record, run_date))
    hook, lines = _pick_script(parts.hook, parts.lines)
    footer = post_copy.image_footer(run_date, AUTHORSHIP,
                                    source=parts.footer_source or FOOTER_SOURCE_OF_SERIES[spec.series],
                                    when=parts.footer_when)
    image_spec = dict(parts.image_spec)
    image_spec["footer"] = footer
    logo_refs = _logo_refs(image_spec, parts.opening, parts.names)
    bodies, dropped, text_outlets = _caption_bodies(parts, spec, run_date, store_state, allow_x_url, prefix,
                                                    frozenset(pictured))
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
    source_line = (_t("alt.source", source=parts.source, as_of=parts.as_of) if parts.as_of
                   else _t("alt.source.bare", source=parts.source))
    alt = [*parts.alt_paragraphs[:3], source_line]
    pre_v23 = ({"image_post_pre_v23": {"title": parts.alt_title,
                                       "paragraphs": [*parts.alt_paragraphs_pre_v23[:3], source_line]}}
               if parts.alt_paragraphs_pre_v23 else {})
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
        # review of 2026-11-26: the alt text of an OLDER worker's picture, only where it differs (`picture_alt`)
        **pre_v23,
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
        # news-v22: the switch as this build read it (an INPUT, like allow_x_url: `revalidate` re-composes from it)
        # and the prefix this composition offered its captions (None: off, or none for this series / record)
        "first_line_prefix_on": prefix_on,
        "first_line_prefix": prefix,
        # review 2026-11-20: the platforms whose post carries the picture, as this build read its switches (an INPUT,
        # like allow_x_url: `revalidate` re-composes from it), and those of them whose caption could not keep the
        # picture's "+N more" promise — written as text posts, frozen as TEXT (script_service.freeze_post_formats)
        "picture_outlets": list(pictured),
        "text_outlets": text_outlets,
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


def normalize_picture_outlets(picture_outlets: Any) -> Tuple[str, ...]:
    """`compose`'s `picture_outlets` as stored: None → every PICTURE_OUTLETS platform; else the given platforms, sorted
    and deduplicated. A string (one name, not a collection), a non-string member or a platform outside
    PICTURE_OUTLETS is a caller bug: TypeError / ValueError."""
    if picture_outlets is None:
        return PICTURE_OUTLETS
    if isinstance(picture_outlets, (str, bytes)) or not isinstance(picture_outlets, (list, tuple, set, frozenset)):
        raise TypeError("picture_outlets must be a collection of platform names")
    if not all(isinstance(p, str) for p in picture_outlets):
        raise TypeError("picture_outlets holds a non-string")
    unknown = sorted(set(picture_outlets) - set(PICTURE_OUTLETS))
    if unknown:
        raise ValueError(f"picture_outlets names {unknown[:3]} — not a picture platform ({PICTURE_OUTLETS})")
    return tuple(sorted(set(picture_outlets)))


def compose(record: Any, *, run_date: date, store_state: str, allow_x_url: bool,
            first_line_prefix: bool = False, picture_outlets: Optional[Any] = None) -> Dict[str, Any]:
    """The accepted-output dict of one record (script_service adds `logos` and the frozen formats).
    Deterministic and pure. Raises `NewsTemplateRefused(code)` when the record cannot be rendered
    within every rule; a `ValueError` / `TypeError` for a caller bug (a datetime, a non-bool
    `allow_x_url` or `first_line_prefix`, a malformed `picture_outlets`). `first_line_prefix` (news-v22):
    MARKETING_FIRST_LINE_PREFIX as the build read it — False (the default) writes every caption exactly as before; True
    lets the series' first-line prefix (`_chosen_prefix`) open the captions where it fits (`_caption_bodies`).
    `picture_outlets` (review 2026-11-20): the platforms whose post will carry the picture, as the build read
    MARKETING_IMAGE_POSTS / MARKETING_X_IMAGES / MARKETING_IMAGE_PLATFORMS (None: every one) — frozen into the output;
    a list series' X / Bluesky caption follows it (`_caption_bodies`)."""
    if not isinstance(run_date, date) or isinstance(run_date, datetime):
        raise TypeError("run_date must be a date")
    if not isinstance(allow_x_url, bool):
        raise TypeError("allow_x_url must be a bool")
    if not isinstance(first_line_prefix, bool):
        raise TypeError("first_line_prefix must be a bool")
    pictured = normalize_picture_outlets(picture_outlets)
    store_state = post_copy.normalize_store_state(store_state)
    if not isinstance(record, rules.RECORD_TYPES):
        raise NewsTemplateRefused("record_invalid", f"not a news record ({type(record).__name__})")
    spec = SERIES_SPECS.get(getattr(record, "series", None))
    if spec is None:
        raise NewsTemplateRefused("record_invalid", f"series {getattr(record, 'series', None)!r} is not shipped")
    suppressed: FrozenSet[str] = frozenset()
    prefix = _chosen_prefix(spec, record, run_date, first_line_prefix)
    pfx = dict(prefix_on=first_line_prefix, prefix=prefix, pictured=pictured)
    out = _compose_once(record, spec, run_date, store_state, allow_x_url, suppressed, **pfx)
    # A person whose name collides with restricted text (a surname that is also a template word)
    # is rendered role-only — the one placement fix that never guesses. Bounded by the people a record can carry
    # (a roundup's rows; a spotlight's lead and its others).
    for _ in range(rules.INSIDER_MAX_ROWS * (1 + rules.SPOTLIGHT_MAX_OTHERS)):
        leaking = _leaking_persons(out, record)
        if not leaking or leaking <= suppressed:
            break
        suppressed = suppressed | leaking
        out = _compose_once(record, spec, run_date, store_state, allow_x_url, suppressed, **pfx)
    violations = validate_package(out, record=record)
    if violations:
        first = violations[0]
        code = _REFUSAL_OF.get(first["code"], "script_shape")
        logger.info("news template REFUSED series=%s source_ref=%s code=%s violations=%s",
                    spec.series, out.get("source_ref"), code, sorted({v["code"] for v in violations}))
        raise NewsTemplateRefused(code, f"{first['field']}: {first['code']} ({first['detail'][:120]})")
    return out


# ── the alt text of the picture the worker drew (review of 2026-11-26) ────────

#: Every stored alt text a template output may carry: the picture's (`image_post`) and, only where an older worker's
#: picture differs in what the alt text says, that picture's (`image_post_pre_v23`). Every scan of the alt text reads
#: both (the person, cost-placement and public-string scans, `validate_package`, `revalidate`).
ALT_TEXT_KEYS: Tuple[str, ...] = ("image_post", "image_post_pre_v23")
#: The series that may carry `image_post_pre_v23`: a Money Map loss year (an older worker draws the flow steps, not the
#: costs card) and the 13F list (an older worker draws at most `template_onscreen.PRE_V23_MAX_SECTION_ROWS` rows and no
#: "+N more" line). Every other series' older picture is described by its one alt text, or never reaches such a worker
#: at build time (`script_service.LOOK_V23_SERIES`) — and when one re-claims a run a newer worker built, its walker
#: refuses a stakes or earnings picture outright (`template_onscreen.PRE_V23_ROW_DROPS` keeps the basis label and the
#: table), so no older picture of those exists to describe.
PRE_V23_ALT_SERIES: FrozenSet[str] = frozenset({"money_map", "thirteen_f", "thirteen_f_spotlight"})


def picture_alt(output: Mapping[str, Any], drawn: Any) -> Any:
    """The alt text (an `image_post`-shaped dict) that describes the picture a worker REGISTERED, given the on-screen
    strings it declared (`drawn`: the post image's `metadata.onscreen_text`, which the server checked against the
    stored spec). The stored `image_post` — what a look_v23 worker's picture shows — unless the output also carries
    `image_post_pre_v23` and the picture draws NONE of the strings only the look_v23 picture draws
    (`template_onscreen.v23_only_strings`: the costs card, the 13F list's sixth row and "+N more" line, the ticker
    chips): then that picture is an older worker's (a worker rolled back, or an older one re-claiming the built run —
    `script_service._without_look_chips`), and its own alt text is returned. Unreadable `drawn` or marks: the stored
    `image_post` (today's behaviour), never a guess. Pure."""
    image_post = output.get("image_post") if isinstance(output, Mapping) else None
    pre = output.get("image_post_pre_v23") if isinstance(output, Mapping) else None
    if not isinstance(pre, dict) or not isinstance(drawn, (list, tuple)) or not all(isinstance(t, str) for t in drawn):
        return image_post
    marks = onscreen.v23_only_strings(output.get("image_spec"), output.get("logos"))
    if not marks or marks & set(drawn):
        return image_post
    return pre


# ── validation (compose's own check; revalidate's last step) ──────────────────

_REQUIRED: Dict[str, type] = {
    "hook": str, "video_script": list, "cards": list, "opening_card": dict, "carousel_slides": list,
    "captions": dict, "posts": dict, "dropped_outlets": dict, "disclaimer_card": str, "image_post": dict,
    "image_footer": str, "image_spec": dict, "video_layout": str, "authorship": str, "content_class": str,
    "series": str, "template_version": str, "source_ref": str, "run_date": str, "store_state": str,
    "allow_x_url": bool, "persons": list, "logo_refs": list,
    # news-v22: the frozen switch (`first_line_prefix`, str or None, is checked by `_validate_prefix`)
    "first_line_prefix_on": bool,
    # review 2026-11-20: the frozen picture platforms and the pictured ones written as text posts
    "picture_outlets": list, "text_outlets": list,
}
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
    elif isinstance(record, rules.InsiderSpotlight):          # 2026-11-20: the insider list — its companies, people
        out = [c.company.name for c in record.companies]
        if not companies_only:
            out += [p.person_name for c in record.companies for p in c.people if p.person_name]
    elif isinstance(record, rules.ThirteenFFiling):
        out = [record.filer_name] + [m.company.name for m in record.moves]
    elif isinstance(record, rules.ThirteenFSpotlight):        # news-v22: the filing entity and its holdings
        out = [record.filer_name] + [m.company.name for m in record.moves]
    elif isinstance(record, rules.MoneyMap):
        out = [record.company.name] + ([] if companies_only else [s.name for s in record.segments])
    elif isinstance(record, rules.CongressMonth):              # news-v23: 2-3 companies
        out = [c.company.name for c in record.counts]
    elif isinstance(record, rules.EarningsDay):                # news-v23: 2-4 companies
        out = [r.company.name for r in record.reports]
    elif isinstance(record, rules.CompanyStakes):              # news-v23: one investor's stakes in public companies
        out = [record.investor.name]
        for st in record.stakes:
            out += [st.investee_name] + ([st.investee.name] if st.investee else [])
        if not companies_only:      # each stake's own slot values (§4.5), never the free-text background
            for st in record.stakes:
                out += [st.source_title] + ([st.local_listing] if st.local_listing else [])
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
    for key in ALT_TEXT_KEYS:               # review of 2026-11-26: the older worker's alt text too
        ip = output.get(key) if isinstance(output.get(key), dict) else {}
        out += _drawn_strings(ip, key)
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


def _withheld_name_hits(output: Mapping[str, Any], record: Any) -> List[Tuple[str, str]]:
    """(field, person) for every Form 4 person of ``record`` whose full name or "first last" appears in
    ANY public string (news-v19: inside the members-elect window no one is named anywhere — the record may
    still carry a name, e.g. one built before the window; the output must not). Never a bare surname: the
    templates only ever write a whole rendered name, and a surname alone ("Price", "Cash") would refuse an
    honest post."""
    if isinstance(record, rules.InsiderBuysWeek):
        people: Tuple[rules.InsiderPurchase, ...] = record.rows
    elif isinstance(record, rules.InsiderSpotlight):          # 2026-11-20: every person of every row
        people = tuple(p for c in record.companies for p in c.people)
    else:
        return []
    patterns: List[Tuple[str, List["re.Pattern[str]"]]] = []
    for name in dict.fromkeys(r.person_name for r in people if r.person_name):
        toks = fold(name).split()
        if len(toks) < 2:
            continue
        forms = dict.fromkeys((" ".join(toks), f"{toks[0]} {toks[-1]}"))
        patterns.append((name, [re.compile(r"(?<![a-z0-9])" + re.escape(f) + r"(?![a-z0-9])") for f in forms]))
    hits: List[Tuple[str, str]] = []
    if not patterns:
        return hits
    for f, text, _is_body in _public_strings(output):
        if not isinstance(text, str) or not text or len(text) > MAX_STRING_CHARS:
            continue
        folded = fold(text)
        hits += [(f, name) for name, pats in patterns if any(p.search(folded) for p in pats)]
    return hits


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
    for key in ALT_TEXT_KEYS:               # review of 2026-11-26: the older worker's alt text too
        ip = output.get(key) if isinstance(output.get(key), dict) else {}
        out += [(f, s, True) for f, s in _drawn_strings(ip, key)]
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
    # review 2026-11-20: the frozen picture platforms (sorted, unique, known) and the pictured short outlets written as
    # text posts — each pictured, each a TEXT_PLAN_OUTLETS one, each with a caption
    pictured = output["picture_outlets"]
    if (not all(isinstance(p, str) for p in pictured) or pictured != sorted(set(pictured))
            or not set(pictured) <= set(PICTURE_OUTLETS)):
        v("picture_outlets", "provenance", "not sorted, unique picture platforms")
    texted = output["text_outlets"]
    if (not all(isinstance(p, str) for p in texted) or texted != sorted(set(texted))
            or not set(texted) <= set(pictured) & set(TEXT_PLAN_OUTLETS)
            or not all(p in output["captions"] for p in texted)):
        v("text_outlets", "provenance", "a text-post outlet that is not a pictured short outlet with a caption")
    if not output["source_ref"].startswith(f"{rules.LEDGER_PREFIX}{spec.series}:"):
        v("source_ref", "provenance", "not this series' ledger key")
    if record is not None:
        if getattr(record, "series", None) != spec.series:
            v("series", "provenance", "not the record's series")
        elif output["source_ref"] != rules.ledger_key(record):
            v("source_ref", "provenance", "not the record's ledger key")

    _validate_script(output, v)
    persons = _validate_persons(output, v)
    # news-v19 (owner decision 4, 2026-10-15): the members-elect window names NO ONE — not through
    # `persons`, and no Form 4 person of the record written anywhere public (a code path that bypassed
    # `_insider_parts`' `person()` would put the name in a caption the placement rule allows). The same
    # run check as the composer (`members_elect_for_run`, review of 2026-10-16): compose and re-check agree.
    if rules.members_elect_for_run(run_date):
        if output["persons"]:
            v("persons", "placement", "no person is named in the members-elect window")
        for f, _person in _withheld_name_hits(output, record):
            v(f, "placement", "a person named in the members-elect window")
    keys = _validate_logos(output, v)
    # opening card, image, alt text, disclaimer, footer
    problem = onscreen.validate_opening_card(output["opening_card"], keys)
    if problem:
        v("opening_card", "opening_card_invalid", problem)
    # a Form 4 roundup of ONE row draws the `spotlight`; both 13F series (news-v23) the `rows` list — never a
    # single-holding `feature` picture
    # news-v23 phase 3: a theme post draws its ANGLE's layout (THEME_FOOTER_SOURCE_OF_LAYOUT's keys)
    expected_layouts = (("rows", "spotlight") if spec.series in ("ceo_buys", "insider_buys")
                        else tuple(THEME_FOOTER_SOURCE_OF_LAYOUT) if spec.series == "theme_explainer"
                        else (spec.layout,))
    if isinstance(record, rules.ThemeExplainer) and record.angle in THEME_LAYOUT_OF_ANGLE:
        # news-v23 phase 3 (2026-11-20): with the record known, its ANGLE's own layout — never another angle's picture
        expected_layouts = (THEME_LAYOUT_OF_ANGLE[record.angle],)
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
    # review of 2026-11-26: the older worker's alt text, when present, under the same rules (and only where its picture
    # differs: a Money Map loss year, a 13F list past PRE_V23_MAX_SECTION_ROWS rows)
    if "image_post_pre_v23" in output:
        pre = output["image_post_pre_v23"]
        problem = image_post_problem(pre) if isinstance(pre, dict) else "not an object"
        if problem:
            v("image_post_pre_v23", "image_post_invalid", problem)
        elif set(pre) != {"title", "paragraphs"}:
            v("image_post_pre_v23", "image_post_invalid", "unknown keys")
        elif pre["title"] != output["image_post"].get("title"):
            v("image_post_pre_v23", "image_post_invalid", "not the alt text's own title")
        elif spec.series not in PRE_V23_ALT_SERIES:
            v("image_post_pre_v23", "image_post_invalid", "a series whose older picture is described by its alt text")
    if output["disclaimer_card"] != post_copy.disclaimer_card(run_date, AUTHORSHIP):
        v("disclaimer_card", "disclaimer_invalid", "not the template disclaimer card")
    # news-v23: the series' short footer (its family source, its date in the family's shape) — never the long one of
    # news-v22, never another family's (`post_copy.image_footer_problem`, bounded before any parse)
    # news-v23 phase 3: a theme post's source is its picture layout's (THEME_FOOTER_SOURCE_OF_LAYOUT: the "biggest"
    # angle's market values add "and market data") — never another family's
    source = (THEME_FOOTER_SOURCE_OF_LAYOUT.get(output["image_spec"].get("layout"), "")
              if spec.series == "theme_explainer" else FOOTER_SOURCE_OF_SERIES.get(spec.series, ""))
    problem = post_copy.image_footer_problem(output["image_footer"], AUTHORSHIP, source=source)
    if problem:
        v("image_footer", "disclaimer_invalid", f"not this series' template image footer ({problem})")
    _validate_captions(output, spec, run_date, v)
    _validate_prefix(output, spec, record, run_date, v)
    _validate_words(output, spec, record, persons, v)
    for f, person in _placement_hits(output, record):
        v(f, "placement", "a person in a restricted field")
    if spec.series == "insider_spotlight":
        _validate_cost_placement(output, record, v)


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
    if output["series"] not in FORM4_PERSON_SERIES and output["persons"]:
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


#: news-v22: the ONLY strings a first-line prefix may open — every caption body but the YouTube title, and every
#: composed caption (which starts with its body; an EXTRA video post's too). Never `posts.youtube.title`.
_PREFIX_FIELDS: FrozenSet[str] = frozenset(
    {f"captions.{f}" for f in post_copy.CAPTION_FIELDS if f != "youtube_title"}
    | {f"posts.{p}.caption" for p in post_copy.PLATFORMS})


def _declared_prefix(output: Mapping[str, Any], spec: SeriesSpec) -> Optional[str]:
    """The first-line prefix the word scan exempts at position 0 (`_validate_words`): the output's declared
    `first_line_prefix` — only while its frozen switch is exactly True and the text is one this series may carry
    (`PREFIX_IDS_BY_SERIES`); else None, and the whole string is scanned (an emoji, "BREAKING" or "alert" is refused).
    Whether a BREAKING prefix is fresh is `_validate_prefix`'s question."""
    if output.get("first_line_prefix_on") is not True:
        return None
    declared = output.get("first_line_prefix")
    allowed = {FIRST_LINE_PREFIXES[i] for i in PREFIX_IDS_BY_SERIES.get(spec.series, ())}
    return declared if isinstance(declared, str) and declared in allowed else None


def _validate_prefix(output: Mapping[str, Any], spec: SeriesSpec, record: Any, run_date: date,
                     v: Callable[..., None]) -> None:
    """news-v22: the declared first-line prefix is None, or — with the frozen switch on — one of THIS series' closed
    prefixes (`PREFIX_IDS_BY_SERIES`); a BREAKING one only with the record and only while `_breaking_fresh` (without
    the record it is refused: stricter, never looser). A violation is `structure` (`slot_rejected` at compose).
    Where the prefix may stand is the word scan's rule (`_validate_words`)."""
    if "first_line_prefix" not in output:
        v("first_line_prefix", "schema", "missing")
        return
    declared = output["first_line_prefix"]
    if declared is None:
        return
    if not isinstance(declared, str):
        v("first_line_prefix", "schema", "not a string or null")
        return
    if output["first_line_prefix_on"] is not True:
        v("first_line_prefix", "structure", "a first-line prefix while the switch was off")
        return
    ids = [i for i in PREFIX_IDS_BY_SERIES.get(spec.series, ()) if FIRST_LINE_PREFIXES[i] == declared]
    if not ids:
        v("first_line_prefix", "structure", "not one of this series' first-line prefixes")
        return
    if all(i in BREAKING_PREFIX_IDS for i in ids):
        if record is None:
            v("first_line_prefix", "structure", "BREAKING needs its record: its freshness cannot be shown")
        elif not _breaking_fresh(record, run_date):
            v("first_line_prefix", "structure",
              f"BREAKING on a source more than {BREAKING_MAX_BUSINESS_DAYS} business day before the run (federal for "
              "a Form 4, the exchange's for earnings), or a late filing")


def _validate_words(output: Mapping[str, Any], spec: SeriesSpec, record: Any, persons: Sequence[str],
                    v: Callable[..., None]) -> None:
    names = [r["name"] for r in output["logo_refs"] if isinstance(r, dict) and isinstance(r.get("name"), str)]
    names += list(persons)
    if record is not None:
        names += _record_names(record)
    names += [spoken_company(n) for n in names]
    mask = _mask_re(names)
    # news-v23 phase 3: a theme post of FILINGS (its `ledger` / `tally` picture: the insider scorecard, Congress) speaks
    # the class-C verb table too; its `tally` the Congress Count's own word rules; its `figures` ("growth + profit")
    # writes the one growth phrase
    layout = output["image_spec"].get("layout") if isinstance(output.get("image_spec"), dict) else None
    theme_table = layout if spec.series == "theme_explainer" else None
    class_c = spec.content_class == "C" or theme_table in ("ledger", "tally")
    # news-v22: the ONE positional exemption — the declared prefix and its space at position 0 of a `_PREFIX_FIELDS`
    # string are not scanned; everything after them, and every other string, is scanned whole (marketing.md §7: an
    # exemption is positional, with a must-reject twin — tests/test_marketing_news_templates.py)
    prefix = _declared_prefix(output, spec)
    head = f"{prefix} " if prefix is not None else None
    for f, whole, is_body in _public_strings(output):
        if not whole:
            v(f, "schema", "an empty string")
            continue
        if len(whole) > MAX_STRING_CHARS:
            v(f, "schema", f"{len(whole)} characters (max {MAX_STRING_CHARS})")
            continue
        if clean(whole) != whole:
            v(f, "structure", "not clean text")
        text = whole[len(head):] if head is not None and f in _PREFIX_FIELDS and whole.startswith(head) else whole
        if any(_is_emoji(ch) for ch in text):
            v(f, "structure", "an emoji")
        if "!" in text:
            v(f, "structure", "an exclamation mark")
        if _CASHTAG_RE.search(text):
            v(f, "structure", "a cashtag")
        # a grid tile's share cell ("Data Center · 88%") is the one "%" that stands alone (§4.5) — and, news-v23, an
        # earnings table's surprise cell, at its own path and in its own shapes only (`_SURPRISE_CELL_FIELD_RE`)
        pct_text = _GROWTH_PHRASE_RE.sub("revenue", text) if theme_table == "figures" else text
        if (not _percent_ok(pct_text) and not (_TILE_LINE_FIELD_RE.fullmatch(f) and _SHARE_CELL_RE.fullmatch(text))
                and not (spec.series == "earnings" and _SURPRISE_CELL_FIELD_RE.fullmatch(f)
                         and _SURPRISE_CELL_RE.fullmatch(text))
                and not (theme_table == "figures" and _GROWTH_CELL_FIELD_RE.fullmatch(f)
                         and onscreen.GROWTH_FIGURE_RE.fullmatch(text))):
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
        if theme_table == "tally":
            # the Congress Count's own word rules on the theme's Congress post (rules §1): nothing that narrows a count
            # to a member, "disclosed purchases" only, never a dollar figure
            _validate_series_words("congress_count", f, text, folded, v)
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
        # news-v23: the table's column LABEL "Surprise" at its own path, exactly (`_SURPRISE_COLUMN_FIELD_RE`) — the
        # one string the word may stand in; every other string is scanned whole
        if _SURPRISE_COLUMN_FIELD_RE.fullmatch(f) and text == LEXICON["er.img.col.surprise"]:
            return
        m = EARNINGS_BANNED_RE.search(folded)
        if m:
            v(f, "series_word", f"a verdict beside the figures: {m.group(0)[:30]}")
    elif series == "theme_explainer":
        # news-v23 phase 3: a theme is its members and their facts — never a ranking verdict ("biggest", "leader",
        # "top", a worth or a valuation word), the market-value angle included
        m = THEME_BANNED_RE.search(folded)
        if m:
            v(f, "series_word", f"a verdict about a theme's companies: {m.group(0)[:30]}")
    elif series == "company_stakes":
        m = STAKES_BANNED_RE.search(folded)
        if m:
            v(f, "series_word", f"not the stake's own verb: {m.group(0)[:30]}")
        m = STAKE_CLAIM_RE.search(folded)
        if m:
            v(f, "series_word", f"a worth or rank claim about a company: {m.group(0)[:30]}")


#: news-v22 (owner decision "Captions + picture"): the words that carry the filing's cost per share — "… at $142.10 a
#: share, as filed" in a caption, "Cost a share, as filed" on the picture — never stand in the video or the alt text.
_COST_PHRASE_RE = re.compile(r"\bas filed\b|\ba share\b", re.IGNORECASE)
#: The insider list's picture rows' detail lines (owner decisions 2026-11-20): the one place on the PICTURE (with a
#: stored `feature` picture's facts) where the filed cost per share may stand — never on the cover's rows.
_DETAIL_FIELD_RE = re.compile(r"image_spec\.sections\[0\]\.rows\[[0-9]\]\.detail")


def _validate_cost_placement(output: Mapping[str, Any], record: Any, v: Callable[..., None]) -> None:
    """The insider spotlight's filed cost per share — its words, and (with the record) every person's figure as
    written — appear in no narrated, burned or drawn VIDEO string (the hook, a narration line, a card, the cover), in
    no alt-text string, in no YouTube title and nowhere on the picture but its FACT rows (the kicker, the header's name
    and chip, the figure, its label, the headline and the footer are checked): captions and the picture's facts only
    (owner decision 5, 2026-11-15; the picture half added by the review of 2026-11-16). Defence in depth behind the
    builder (which never writes them there) and behind `revalidate`'s field comparison; a hit is `placement`. Each
    figure is matched as filed AND rounded to cents (a builder that rounded a filed $142.1234 is caught too)."""
    costs: set = set()
    if isinstance(record, rules.InsiderSpotlight):
        for person_row in (p for c in record.companies for p in c.people):
            for x in (person_row.cost_per_share_min_usd, person_row.cost_per_share_max_usd):
                if isinstance(x, (int, float)) and not isinstance(x, bool) and 0 < x < float("inf"):
                    costs.add(cost_image(x))
                    costs.add(f"${_q(abs(_dec(x)), 2):,.2f}")
    places: List[Tuple[str, Any]] = [("hook", output.get("hook"))]
    places += [(f"video_script[{i}]", s) for i, s in enumerate(output.get("video_script") or ())]
    for i, c in enumerate(output.get("cards") or ()):
        if isinstance(c, Mapping):
            places += [(f"cards[{i}].{k}", c.get(k)) for k in ("title", "body")]
    places += _drawn_strings(output.get("opening_card") or {}, "opening_card")
    for key in ALT_TEXT_KEYS:               # review of 2026-11-26: the older worker's alt text too
        places += _drawn_strings(output.get(key) if isinstance(output.get(key), dict) else {}, key)
    # the picture: every drawn string but its fact rows (a stored `feature` picture) and — the insider list, owner
    # decisions 2026-11-20 — its rows' grey detail lines: the only places on it the cost may stand
    places += [(f, s) for f, s in _drawn_strings(output.get("image_spec") if isinstance(output.get("image_spec"), dict)
                                                 else {}, "image_spec")
               if not (f == "image_spec.facts" or f.startswith("image_spec.facts[") or _DETAIL_FIELD_RE.fullmatch(f))]
    captions = output.get("captions") if isinstance(output.get("captions"), Mapping) else {}
    places.append(("captions.youtube_title", captions.get("youtube_title")))
    posts = output.get("posts") if isinstance(output.get("posts"), Mapping) else {}
    youtube = posts.get("youtube") if isinstance(posts.get("youtube"), Mapping) else {}
    places.append(("posts.youtube.title", youtube.get("title")))
    for f, text in places:
        if not isinstance(text, str) or len(text) > MAX_STRING_CHARS:
            continue
        if _COST_PHRASE_RE.search(text) or any(c in text for c in costs):
            v(f, "placement", "the filing's cost per share outside the captions and the picture")


_CONGRESS_COUNT_RE = re.compile(r"([0-9]{1,3}) members of Congress disclosed\b")


#: news-v23: a Congress Count row's count cell — a whole number of members, never padded or signed.
_CONGRESS_ROW_COUNT_RE = re.compile(r"[1-9][0-9]{0,2}")


def _validate_congress_wording(output: Mapping[str, Any], v: Callable[..., None]) -> None:
    """A Congress Count says "disclosed" wherever it states the count — the hook, the image's title (news-v23: the
    `rows` list; its `headline` before) and the cover's headline, every caption's first line — and every count it
    states is ≥ 2 distinct members: the hook's, and (news-v23) each row's count cell on the picture and the cover."""
    captions = output["captions"] if isinstance(output.get("captions"), dict) else {}
    spec = output.get("image_spec") if isinstance(output.get("image_spec"), dict) else {}
    opening = output.get("opening_card") if isinstance(output.get("opening_card"), dict) else {}
    head_key = "title" if spec.get("layout") == "rows" else "headline"
    places = [("hook", output.get("hook")),
              (f"image_spec.{head_key}", spec.get(head_key)),
              ("opening_card.headline", opening.get("headline"))]
    places += [(f"captions.{k}[0]", b.split("\n", 1)[0]) for k, b in captions.items() if isinstance(b, str)]
    for f, text in places:
        if not isinstance(text, str) or "disclosed" not in text:
            v(f, "congress_wording", "the count is not stated as disclosed")
    m = _CONGRESS_COUNT_RE.match(output.get("hook") or "")
    if m is None or int(m.group(1)) < rules.CONGRESS_MIN_MEMBERS:
        v("hook", "congress_wording", "the hook does not state a count of at least two members")
    lst = opening.get("list") if isinstance(opening.get("list"), dict) else {}
    tables = [(f"image_spec.sections[{i}]", sec.get("rows")) for i, sec in enumerate(spec.get("sections") or ())
              if isinstance(sec, dict)] + [("opening_card.list", lst.get("rows"))]
    for path, rows in tables:
        for j, row in enumerate(rows if isinstance(rows, list) else ()):
            cells = row.get("cells") if isinstance(row, dict) else None
            count = cells[-1] if isinstance(cells, list) and cells else None
            if (not isinstance(count, str) or not _CONGRESS_ROW_COUNT_RE.fullmatch(count)
                    or int(count) < rules.CONGRESS_MIN_MEMBERS):
                v(f"{path}.rows[{j}]", "congress_wording", "a row whose count is not at least two members")


# ── revalidate (create_posts and the build's own re-check) ────────────────────

#: The fields `revalidate` re-composes and compares (everything compose writes but its inputs).
COMPARED_FIELDS: Tuple[str, ...] = (
    "hook", "video_script", "cards", "opening_card", "carousel_slides", "captions", "posts",
    "dropped_outlets", "disclaimer_card", "image_post", "image_post_pre_v23", "image_footer", "image_spec",
    "video_layout",
    "authorship", "content_class", "series", "template_version", "source_ref", "persons", "logo_refs",
    # news-v22: the prefix the composition offered (its switch `first_line_prefix_on` is an input, like allow_x_url)
    "first_line_prefix",
    # review 2026-11-20: the pictured outlets written as text posts (`picture_outlets` is an input, like allow_x_url)
    "text_outlets",
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
        # news-v22: MARKETING_FIRST_LINE_PREFIX as the build froze it — never today's switch, so flipping it between
        # the build and create_posts can neither refuse the day nor change a caption
        prefix_on = output.get("first_line_prefix_on")
        if not isinstance(prefix_on, bool):
            v("first_line_prefix_on", "provenance", "the frozen first-line prefix switch is not a bool")
            return out
        # review 2026-11-20: the picture platforms as the build froze them — never today's switches
        try:
            pictured = normalize_picture_outlets(output.get("picture_outlets"))
        except (TypeError, ValueError) as e:
            v("picture_outlets", "provenance", f"the frozen picture platforms do not read ({e})")
            return out
        if output.get("picture_outlets") is None or list(pictured) != output.get("picture_outlets"):
            v("picture_outlets", "provenance", "the frozen picture platforms are missing or not sorted and unique")
            return out
        try:
            again = compose(record, run_date=run_date, store_state=store_state, allow_x_url=allow,
                            first_line_prefix=prefix_on, picture_outlets=list(pictured))
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


def _picture_bound(record: Any, spec: SeriesSpec, run_date: date) -> bool:
    """Do the record's PICTURE caption parts speak of what only its picture shows (`_Parts.picture_bound`,
    B3-LIVE-1)? Re-derived with the series' own builder (the person suppression `compose` may apply never changes
    it). A builder that refuses raises: the caller reads that as a violation, never as a pass."""
    builder = _BUILDERS[spec.series]
    parts = builder(record, run_date, frozenset()) if builder in _PERSON_BUILDERS else builder(record, run_date)
    return bool(parts.picture_bound)


def _video_promise(record: Any, spec: SeriesSpec, run_date: date) -> Tuple[Tuple[str, ...], ...]:
    """What the record's VIDEO COVER promises (`_Parts.video_promise`: a list cover's "+N more … in the caption" — the
    rows it leaves out), re-derived with the series' own builder: per row, the caption paragraphs that keep its promise
    (the paragraph itself and — company stakes, news-v23 — its short form, `video_promise_short`); () for every other
    series. A builder that refuses raises: the caller reads that as a violation, never as a pass."""
    builder = _BUILDERS[spec.series]
    parts = builder(record, run_date, frozenset()) if builder in _PERSON_BUILDERS else builder(record, run_date)
    shorts = tuple(parts.video_promise_short)
    return tuple((p,) + ((shorts[i],) if i < len(shorts) else ()) for i, p in enumerate(parts.video_promise))


def extra_video_problems(output: Any, *, fact_sheet: Any, run_date: Any, platform: Any, caption: Any,
                         video_draws_picture: bool = False) -> List[Dict[str, str]]:
    """[] when `caption` may be `platform`'s EXTRA video post (Batch 3, 2026-10-20) of the template `output`;
    otherwise the violations. Run when the extras are frozen (`script_service.freeze_extra_formats`) and again
    by create_posts, after `revalidate` passed on the output itself. Never raises.

    The record is re-derived from the stored fact sheet (as `revalidate` does). The caption must be exactly
    the platform's own accepted caption under the narrated-video disclaimer (`post_copy.extra_video_caption`
    of `output.posts[platform]`, template authorship) — else `caption_invalid`. B3-LIVE-1 (2026-10-20): that
    caption is built from the PICTURE parts, and a Form 4 roundup's picture parts speak of the rows only its
    picture shows ("The purchases: …", "The largest of these: …") while the video is about the lead row — so a
    picture-bound record (`_picture_bound`) is `picture_caption` unless the caller says the video draws the
    picture (`video_draws_picture` exactly True: the frozen data card at the freeze, and at create_posts also the
    verified video's own word that it drew it); fail closed by default. news-v23 (the 13F list): a cover that says
    "+N more changes in the caption" (`_video_promise`) needs every holding it leaves out in this caption, else
    `cover_caption` (a Threads caption carries only the picture's). It is then checked as a public
    string of the output: a PROBE — the output with `posts` holding only this video post — goes through the
    word scan (`_validate_words`: banned / forecast / news words, structure, Congress names), the placement
    rule (`_placement_hits`: no person in its first line or its code-owned suffix — the whole caption on
    Threads) and, inside the members-elect window, the withheld-names scan (`_withheld_name_hits`); each hit is
    reported on `video_posts.<platform>…`. An unreadable output or fact sheet is a violation (logged), never a
    pass."""
    out: List[Dict[str, str]] = []

    def v(field_: str, code: str, detail: str = "") -> None:
        if field_.startswith("posts."):      # the probe's post is the EXTRA one: say so
            field_ = "video_" + field_
        out.append({"field": field_, "code": code, "detail": detail[:200]})

    try:
        if not isinstance(output, Mapping):
            v("package", "schema", "not an object")
            return out
        try:
            record = rules.record_from_fact_sheet(fact_sheet)
        except ValueError as e:
            v("fact_sheet", "facts_invalid", str(e))
            return out
        if not isinstance(run_date, date) or isinstance(run_date, datetime):
            v("run_date", "provenance", "not a date")
            return out
        spec = SERIES_SPECS.get(output.get("series"))
        if spec is None or getattr(record, "series", None) != spec.series:
            v("series", "provenance", "not the record's series")
            return out
        name = str(platform)[:20]
        posts = output.get("posts") if isinstance(output.get("posts"), Mapping) else {}
        primary = posts.get(platform) if isinstance(platform, str) else None
        accepted = primary.get("caption") if isinstance(primary, Mapping) else None
        try:
            derived = post_copy.extra_video_caption(platform, accepted, run_date, AUTHORSHIP)
        except ValueError as e:
            v(f"posts.{name}", "caption_invalid", str(e))
            return out
        if caption != derived:
            v(f"posts.{name}", "caption_invalid", "not its accepted caption under the narrated-video disclaimer")
            return out
        probe = {**output, "posts": {platform: {"platform": platform, "title": None, "caption": caption}}}
        persons = _validate_persons(probe, lambda *_a, **_k: None)
        _validate_words(probe, spec, record, persons, v)
        for f, _person in _placement_hits(probe, record):
            v(f, "placement", "a person in a restricted field")
        if rules.members_elect_for_run(run_date):
            for f, _person in _withheld_name_hits(probe, record):
                v(f, "placement", "a person named in the members-elect window")
        if video_draws_picture is not True and _picture_bound(record, spec, run_date):
            v(f"posts.{name}", "picture_caption",
              "its caption speaks of the picture's rows, and the video does not draw the picture (no data card)")
        # news-v23 (the 13F list): the video's cover says "+N more changes in the caption" — this caption must list
        # every holding the cover leaves out (a Threads caption carries only the picture's)
        missing = [forms for forms in _video_promise(record, spec, run_date) if not any(p in caption for p in forms)]
        if missing:
            v(f"posts.{name}", "cover_caption",
              f"the cover's '+N more changes in the caption' promises {len(missing)} holding(s) this caption leaves out")
    except Exception as e:  # noqa: BLE001 — a malformed stored row must read as a violation, not a 500
        logger.exception("news template: extra_video_problems failed (%s: %s)", type(e).__name__, e)
        v("package", "schema", f"unreadable ({type(e).__name__})")
    return out


def logo_refs(output: Mapping[str, Any]) -> List[Dict[str, str]]:
    """The `{key, name}` logos script_service fetches for a composed output, in draw order (image
    then opening card), at most `template_onscreen.MAX_LOGOS`. Each becomes one `output.logos`
    entry with exactly this key and name (url None when the logo cannot be had: a wordmark)."""
    refs = output.get("logo_refs") if isinstance(output, Mapping) else None
    return [dict(r) for r in refs or () if isinstance(r, dict)][:onscreen.MAX_LOGOS]


__all__ = [
    # news-v23 (restyle 2026-11-19): each series' short-footer source
    "FOOTER_SOURCE_OF_SERIES",
    "TEMPLATE_VERSION", "AUTHORSHIP", "NEWS_SCRIPT_LINES", "PICTURE_OUTLETS", "TEXT_PLAN_OUTLETS",
    "normalize_picture_outlets", "EARNINGS_NARRATED_COMPANIES", "HOOK_WORDS", "LINE_WORDS", "NARRATION_WORDS",
    "NEWS_MIN_OUTLETS", "CARD_TITLE_MAX_WORDS", "CARD_BODY_MAX_WORDS", "VIDEO_LAYOUT", "SeriesSpec",
    "SERIES_SPECS", "REFUSAL_CODES", "NewsTemplateRefused", "LEXICON", "NEWS_BANNED_RE", "SLOT_BANNED_RE",
    "OFF_TABLE_VERBS_RE", "KEEP_CAPS", "COMPARED_FIELDS", "compose", "revalidate", "validate_package",
    "ALT_TEXT_KEYS", "PRE_V23_ALT_SERIES", "picture_alt",
    "logo_refs", "F13_PICTURE_ROWS", "F13_COVER_ROWS", "money_image", "money_words", "money_cents", "SUB_CENT", "shares_image", "shares_words", "pct",
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
    # news-v12 (business days since news-v14)
    "INSIDER_LATE_BUSINESS_DAYS",
    # news-v16
    "THEME_FACT_SPREAD_YEARS", "THEME_FACT_LATE_MONTH", "STAKE_CLAIM_ROWS", "STAKE_CLAIM_RE",
    # news-v19 (Batch 1, plan item 5)
    "STAKE_BACKGROUND_IN_CAPTIONS", "OPENING_CHIP_MAX_CHARS",
    # news-v19 (the review of 2026-10-16)
    "MONEY_MAP_REGION_MAX_OTHER_SHARE",
    # Batch 3 (2026-10-20): the extra video posts' re-check
    "extra_video_problems",
    # news-v21 (main-session decision of 2026-10-20): an earnings post printed no revenue comparison; news-v23 (owner
    # 2026-11-19, "Revenue always"): every earnings post prints it
    "EARNINGS_REVENUE_IN_POSTS",
    # news-v23 (the restyle, owner 2026-11-19): one post of several companies — earnings and Congress Count
    "drop_refused_rows", "surprise_cell", "EARNINGS_SURPRISE_MIN_ABS_EPS_ESTIMATE", "EARNINGS_LIST_FIELDS",
    # news-v22 (2026-11-15): the spotlight lane's two series — the filing's own cost per share, the Form 4 people
    "cost_image", "cost_pair", "COST_MAX_PLACES", "FORM4_PERSON_SERIES",
    # news-v22 (2026-11-15): the closed first-line prefixes ("Loosen caption hype words")
    "FIRST_LINE_PREFIXES", "PREFIX_IDS_BY_SERIES", "BREAKING_PREFIX_IDS", "BREAKING_MAX_BUSINESS_DAYS",
    "THREADS_PICTURE_CAPTION_MAX_BYTES",
]
