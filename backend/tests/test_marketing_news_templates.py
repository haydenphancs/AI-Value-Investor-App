"""`news_templates` — the code-owned Company Weekly templates (contract D7, drop 2a).

Pinned here:

* the lexicon, verbatim (`tests/data/marketing_news_lexicon_v1.json`) and `TEMPLATE_VERSION`;
  every entry with neutral fills passes `BANNED_COPY` / `FORECAST_COPY` / `NEWS_BANNED_RE` and the
  verb table, and `compliance.scan_text` finds nothing on it but the per-id exemptions below
  (`person_named` on the role-descriptor entries: "Northwind's CEO" is what the public-copy scan
  is built to catch, and here it is the design — a role, never a name, in the headline);
* golden packages for every shipped series from records shaped like real data (GameStop's CEO
  $74.4M — the plan's own example —, a Berkshire Hathaway 13F quarter, a Costco money map), and
  the per-series variant tables (spotlight vs rows, ".of" twins, CFO / director, indirect /
  amended, 13F lead kinds, amended and newly listed, money-map losses, eliminations, cost bars);
* the rules: verb table, placement (A7 — the whole video role-only, owner decision 2026-10-09;
  mutation-checked), Congress refusals, slot rejection,
  the "%" rule, number / date formatting boundaries, script shape, the caption budget matrix
  (every sample × store state × allow_x_url through `check_composed(authorship="template")`),
  `revalidate` (untouched → []; tampering any public string, the facts or the version → a
  violation; JSONB round trips → []), and the name-free alt text.

Hermetic: pure functions over records built in the test. The Congress block-list is the real
committed roster (`company_news_rules` loads it from `backend/data/`).
"""

from __future__ import annotations

import copy
import json
import random
import re
import string
import subprocess
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from app.schemas.marketing import AUDIO_WORD_MAX_CHARS, image_post_problem
from app.services.marketing import company_news_rules as R
from app.services.marketing import news_templates as T
from app.services.marketing import post_copy, selection
from app.services.marketing import template_onscreen as onscreen
from app.services.marketing import writer_prompts as wp
from app.services.marketing.compliance import clean, fold, scan_text
from app.services.trillion_club import copy_rules

_BACKEND = Path(__file__).resolve().parents[1]
LEXICON_PATH = _BACKEND / "tests" / "data" / "marketing_news_lexicon_v1.json"

MON, TUE, THU = date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 19)


# ── records shaped like real data ─────────────────────────────────────────────

def co(sym, name):
    return R.CompanyRef(symbol=sym, name=name)


def buy(sym, name, role, person, amount, shares, k, filings, holding="direct", amended=False):
    return R.InsiderPurchase(company=co(sym, name), role=role, person_name=person, amount_usd=amount,
                             shares=shares, purchases=k, earliest_trade_date=filings[0],
                             latest_trade_date=filings[-1], filing_dates=tuple(filings), holding=holding,
                             amended=amended)


GME = buy("GME", "GameStop", "ceo", "Ryan Cohen", 74_400_000.0, 3_000_000.0, 2,
          [date(2026, 11, 10), date(2026, 11, 11)])
LOW = buy("LOW", "Lowe's", "ceo", None, 2_300_000.0, 9_812.0, 1, [date(2026, 11, 12)], holding="indirect")
SBUX = buy("SBUX", "Starbucks", "ceo", None, 410_000.0, 4_850.0, 1, [date(2026, 11, 13)], amended=True)


def week(*rows, series="ceo_buys", start=date(2026, 11, 9), end=date(2026, 11, 15)):
    return R.InsiderBuysWeek(series=series, window_start=start, window_end=end, rows=tuple(rows))


def ceo_roundup():
    return week(GME, LOW, SBUX)


def insider_mixed():
    return week(
        buy("NKE", "Nike", "cfo", None, 1_250_000.0, 18_500.0, 1, [date(2026, 11, 10)]),
        buy("CTSO", "Contoso", "director", "Anna Smith-Jones", 640_000.0, 2_600.0, 3,
            [date(2026, 11, 12), date(2026, 11, 13)], holding="mixed"),
        buy("HSY", "Hershey", "director", None, 150_000.0, 812.0, 1, [date(2026, 11, 14)]),
        series="insider_buys")


def move(sym, name, kind, **kw):
    base = dict(shares=None, prev_shares=None, value_usd=None, listed_on=None)
    base.update(kw)
    return R.ThirteenFMove(company=co(sym, name), move=kind, **base)


def berkshire(**kw):
    base = dict(
        series="thirteen_f", filer_name="Berkshire Hathaway", filer_cik="0001067983", filer_symbol="BRK-B",
        period="2026-Q3", period_end=date(2026, 9, 30), filed_on=date(2026, 11, 13), amended_on=None,
        total_value_usd=267_300_000_000.0, position_count=41,
        moves=(move("STZ", "Constellation Brands", "newly_reported", shares=5_624_324.0, prev_shares=0.0,
                    value_usd=1_240_000_000.0),
               move("DPZ", "Domino's Pizza", "newly_reported", shares=1_277_256.0, prev_shares=0.0,
                    value_usd=549_000_000.0),
               move("LEN", "Lennar", "newly_reported", shares=1_797_000.0, prev_shares=0.0, value_usd=250_100_000.0),
               move("C", "Citigroup", "no_longer_reported", shares=0.0, prev_shares=14_639_502.0),
               move("NU", "Nu Holdings", "no_longer_reported", shares=0.0, prev_shares=40_180_168.0)),
        counts=(("newly_reported", 3), ("increased", 2), ("decreased", 4), ("no_longer_reported", 2)))
    base.update(kw)
    return R.ThirteenFFiling(**base)


def pershing(**kw):
    """A registry filer with no listed symbol, an amended filing, a newly listed holding and only
    increase / decrease moves beside it."""
    base = dict(
        series="thirteen_f", filer_name="Pershing Square Capital Management", filer_cik="0001336528",
        filer_symbol=None, period="2026-Q3", period_end=date(2026, 9, 30), filed_on=date(2026, 11, 12),
        amended_on=date(2026, 11, 14), total_value_usd=14_900_000_000.0, position_count=11,
        moves=(move("CRWV", "CoreWeave", "newly_reported", shares=900_000.0, prev_shares=0.0,
                    value_usd=118_000_000.0, listed_on=date(2026, 8, 13)),
               move("UBER", "Uber", "increased", shares=40_000_000.0, prev_shares=30_200_000.0,
                    value_usd=3_100_000_000.0),
               move("CMG", "Chipotle Mexican Grill", "decreased", shares=20_000_000.0, prev_shares=26_000_000.0,
                    value_usd=1_000_000_000.0)),
        counts=(("newly_reported", 1), ("increased", 1), ("decreased", 1), ("no_longer_reported", 0)))
    base.update(kw)
    return R.ThirteenFFiling(**base)


def costco(**kw):
    base = dict(series="money_map", company=co("COST", "Costco"), fiscal_year="2025", period_end=date(2025, 8, 31),
                segments=(R.Segment("United States", 200_000_000_000.0), R.Segment("Canada", 38_500_000_000.0),
                          R.Segment("Other International", 36_700_000_000.0)),
                other_usd=None, eliminations_usd=None, revenue_usd=275_200_000_000.0,
                gross_profit_usd=35_400_000_000.0, operating_profit_usd=10_400_000_000.0,
                net_income_usd=8_100_000_000.0)
    base.update(kw)
    return R.MoneyMap(**base)


def rivian(**kw):
    """A loss year: every profit bar is a loss, drawn as an outline."""
    base = dict(series="money_map", company=co("RIVN", "Rivian Automotive"), fiscal_year="2025",
                period_end=date(2025, 12, 31),
                segments=(R.Segment("Automotive", 4_620_000_000.0), R.Segment("Software and services", 1_180_000_000.0)),
                other_usd=None, eliminations_usd=None, revenue_usd=5_800_000_000.0, gross_profit_usd=-80_000_000.0,
                operating_profit_usd=-3_960_000_000.0, net_income_usd=-3_850_000_000.0)
    base.update(kw)
    return R.MoneyMap(**base)


def comcast(**kw):
    """Intersegment eliminations (segment shares then read "of segment sales"), an Other bar, no
    gross-profit figure."""
    base = dict(series="money_map", company=co("CMCSA", "Comcast"), fiscal_year="2025", period_end=date(2025, 12, 31),
                segments=(R.Segment("Connectivity and Platforms", 82_000_000_000.0),
                          R.Segment("Content and Experiences", 43_400_000_000.0)),
                other_usd=2_600_000_000.0, eliminations_usd=-4_300_000_000.0, revenue_usd=123_700_000_000.0,
                gross_profit_usd=None, operating_profit_usd=23_300_000_000.0, net_income_usd=16_200_000_000.0)
    base.update(kw)
    return R.MoneyMap(**base)


# ── drop 2b records ───────────────────────────────────────────────────────────

CONGRESS_RUN = date(2026, 12, 8)     # Tue, day 8..14: the first Congress Count day (contract §0, 2b-1)
STAKES_RUN = date(2026, 12, 29)      # Tue: the first company_stakes day (2b-2)
EARNINGS_RUN = date(2026, 11, 12)    # Thu, inside the Q3 earnings season
THEME_RUN = date(2026, 11, 16)       # Mon (a theme is a fallback on any posting day)


def congress(**kw):
    """The scrubbed Congress feed (`~/.claude/plans/company-weekly-congress-probe/`, names removed at
    the source): among September 2026's disclosures, Accenture had stock purchases by 3 distinct
    members — that realistic count, read for November on the first Congress Count Tuesday."""
    base = dict(series="congress_count", company=co("ACN", "Accenture"), month="2026-11", members=3,
                fetched_on=CONGRESS_RUN)
    base.update(kw)
    return R.CongressCount(**base)


def stake(**kw):
    """A catalogue stake (`data/trillion_club_seed.json`, published / primary / named / a disclosed
    figure): NVIDIA → Nscale, the templates design's S2."""
    base = dict(series="company_stakes", stake_id="5f0c8a52-6a5e-4d4f-9a52-0d9c3e1b7a01", investor=co("NVDA", "NVIDIA"),
                investee_name="Nscale", investee=None, kind="private", value_usd=777_399_382.46, value_basis="invested",
                ownership_pct=None, as_of=date(2026, 3, 27), verified_on=date(2026, 9, 24),
                source_title="Nscale Form S-1 (Sep 18, 2026)",
                background="For Series C shares; NVIDIA also paid $400.0M for Series B shares on Oct 8, 2025.",
                listed_since=None, local_listing=None, is_new=False)
    base.update(kw)
    return R.CompanyStake(**base)


def nscale():
    return stake()


def anthropic_commitment():
    return stake(stake_id="0d3b6f1e-2a4c-4e8b-9f10-6c2d8a7e5b02", investee_name="Anthropic", kind="commitment",
                 value_usd=10_000_000_000.0, value_basis="committed_up_to", as_of=date(2025, 11, 18),
                 source_title="Microsoft, NVIDIA and Anthropic release (Nov 18, 2025)",
                 background="Anthropic said its Feb 12, 2026 Series G included a portion of this investment.")


def tesla_spacex():
    """A U.S.-listed investee with its own logo, at fair value; the background carries a "%" (dropped)."""
    return stake(stake_id="7a9e2c44-1b3d-4f5a-8c6e-9d0f1a2b3c03", investor=co("TSLA", "Tesla"), investee_name="SpaceX",
                 investee=co("SPCX", "SpaceX"), kind="us_listed_off_13f", value_usd=3_007_000_000.0,
                 value_basis="fair_value", as_of=date(2026, 6, 30), source_title="Tesla 10-Q (Q2 2026)",
                 background="Tesla invested $2.00B in March 2026 for under 1% (formerly xAI preferred).")


def berkadia():
    """A carrying value (the long hook steps down to "carried its … stake at"); an ownership share
    beside the figure is never said; the background names another company's 50% (dropped)."""
    return stake(stake_id="2c4e6a8b-0d1f-4a3c-9e5b-7d9f1b3d5f04", investor=co("BRK-B", "Berkshire Hathaway"),
                 investee_name="Berkadia Commercial Mortgage", kind="private", value_usd=461_000_000.0,
                 value_basis="carrying_value", ownership_pct=50.0, as_of=date(2026, 6, 30),
                 source_title="Berkshire Hathaway 10-Q (quarter ended Jun 30, 2026)",
                 background="Jefferies Financial Group owned the other 50%.")


def itochu():
    """Listed outside the U.S., with its local listing; a fair value from a shareholder letter."""
    return stake(stake_id="9b1d3f5a-7c9e-4b2d-8f0a-1c3e5a7b9d05", investor=co("BRK-B", "Berkshire Hathaway"),
                 investee_name="ITOCHU Corporation", kind="non_us_listed", value_usd=8_886_000_000.0,
                 value_basis="fair_value", ownership_pct=10.1, as_of=date(2025, 12, 31),
                 source_title="Berkshire Hathaway 2025 shareholder letter",
                 background="Cost basis was $4.17B, per the letter.", local_listing="Japan")


def intel_stake():
    """On the investor's 13F, with a verified U.S. listing (the investee's own CompanyRef and logo)."""
    return stake(stake_id="4e6a8c0e-2b4d-4c6e-8a0c-2e4a6c8e0a06", investee_name="Intel", investee=co("INTC", "Intel"),
                 kind="on_13f_note", value_usd=5_000_000_000.0, value_basis="invested", as_of=date(2025, 12, 26),
                 source_title="Intel 8-K (Dec 29, 2025)",
                 background="Agreed on Sep 15, 2025, alongside a product collaboration with NVIDIA.")


def earnings(**kw):
    """The templates design's S3 (fictional Northwind Fitness): a smaller loss than estimated."""
    base = dict(series="earnings", company=co("NWFT", "Northwind Fitness"), report_date=date(2026, 11, 5),
                period_end=date(2026, 9, 30), eps_actual=-0.05, eps_estimate=-0.12, revenue_actual=551_900_000.0,
                revenue_estimate=543_600_000.0)
    base.update(kw)
    return R.EarningsReport(**base)


def earnings_profit():
    """Fictional Fabrikam: earnings, no revenue estimate, no period end."""
    return earnings(company=co("FBKM", "Fabrikam"), report_date=date(2026, 11, 10), period_end=None, eps_actual=4.86,
                    eps_estimate=4.5, revenue_actual=None, revenue_estimate=None)


#: (symbol, name, largest segment, its share of revenue, fiscal year) — 14 members: 12 tiles and
#: "+2 more"; Arm without a share, Intel's fact stale (fiscal 2022) and Synopsys' segment too long
#: for a tile line — those members stay, without their fact.
AI_CHIPS = [
    ("NVDA", "NVIDIA", "Data Center", 0.88, "2026"), ("AVGO", "Broadcom", "Semiconductor Solutions", 0.58, "2025"),
    ("AMD", "AMD", "Data Center", 0.48, "2025"), ("MRVL", "Marvell Technology", "Data Center", 0.74, "2026"),
    ("QCOM", "Qualcomm", "QCT", 0.84, "2025"), ("ARM", "Arm Holdings", "Royalty", None, "2026"),
    ("MU", "Micron Technology", "Compute and Networking", 0.43, "2025"),
    ("INTC", "Intel", "Client Computing", 0.55, "2022"),
    ("TXN", "Texas Instruments", "Analog", 0.77, "2025"), ("ADI", "Analog Devices", "Industrial", 0.45, "2025"),
    ("SNPS", "Synopsys", "Electronic Design Automation Software", 0.81, "2025"),
    ("CDNS", "Cadence Design Systems", "Core EDA", 0.7, "2025"), ("LRCX", "Lam Research", "Systems", 0.66, "2025"),
    ("KLAC", "KLA", None, None, None),
]


def theme(rows=AI_CHIPS, **kw):
    base = dict(series="theme_explainer", slug="ai-chips", title="AI chips",
                members=tuple(R.ThemeMember(company=co(s, n), top_segment=g, top_segment_share=sh, fiscal_year=fy)
                              for s, n, g, sh, fy in rows),
                tickers_as_of=date(2026, 11, 1))
    base.update(kw)
    return R.ThemeExplainer(**base)


def ai_chips():
    """The whole theme: its `theme_size` (the canonical ticker count, the shared record contract) equals
    its 14 members, so the post may say "The 14 companies in AI chips"."""
    return theme(theme_size=14)


def ai_chips_of_more():
    """The adapter's gates refused two of the theme's 16 tickers (an OTC listing, a share-class twin): the
    record keeps 14 members and `theme_size` 16, and the post says "14 of its 16" wherever it counts."""
    return theme(theme_size=16)


#: The layouts as shipped in THIS checkout (pair and grid stay refused until their series ship).
REAL_SHIPPED_LAYOUTS = onscreen.SHIPPED_LAYOUTS


@pytest.fixture(autouse=True)
def _pair_and_grid_validate(monkeypatch):
    """The 2b `pair` (stakes) and `grid` (theme) images validate only once SHIPPED_LAYOUTS lists them
    (the plumbing step flips both sides with SHIPPED_SERIES). Every test here composes with them
    allowed IN MEMORY — the state that flip produces; the 2a layouts are untouched by it.
    `test_pair_and_grid_series_are_refused_until_their_layouts_ship` restores the real tuple."""
    monkeypatch.setattr(onscreen, "SHIPPED_LAYOUTS", onscreen.LAYOUTS)


#: (name, record factory, run date) — every sample the matrices walk.
SAMPLES = [
    ("ceo_roundup", ceo_roundup, MON),
    ("ceo_spotlight", lambda: week(GME), MON),
    ("ceo_spotlight_of_twin", lambda: week(LOW), MON),
    ("insider_mixed", insider_mixed, MON),
    ("berkshire_13f", berkshire, TUE),
    ("pershing_13f", pershing, TUE),
    ("costco_money_map", costco, THU),
    ("rivian_money_map", rivian, THU),
    ("comcast_money_map", comcast, THU),
    # drop 2b
    ("congress_accenture", congress, CONGRESS_RUN),
    ("stake_nscale", nscale, STAKES_RUN),
    ("stake_anthropic_commitment", anthropic_commitment, STAKES_RUN),
    ("stake_spacex_fair_value", tesla_spacex, STAKES_RUN),
    ("stake_berkadia_carrying", berkadia, STAKES_RUN),
    ("stake_itochu_non_us", itochu, STAKES_RUN),
    ("stake_intel_us", intel_stake, STAKES_RUN),
    ("earnings_loss", earnings, EARNINGS_RUN),
    ("earnings_profit", earnings_profit, EARNINGS_RUN),
    ("theme_ai_chips", ai_chips, THEME_RUN),
    ("theme_ai_chips_of_more", ai_chips_of_more, THEME_RUN),
]


def compose(rec, run_date, store_state="live", allow_x_url=False):
    return T.compose(rec, run_date=run_date, store_state=store_state, allow_x_url=allow_x_url)


def _parts(rec, run_date):
    """The builder's parts (every variant offered), before the assembler picks."""
    builder = T._BUILDERS[rec.series]
    return builder(rec, run_date, frozenset()) if builder is T._insider_parts else builder(rec, run_date)


def sheet(rec):
    return R.fact_sheet(rec, rejections={"already_posted": 1},
                        selection={"plan": "monday", "chain": [rec.series, "lesson"],
                                   "trail": [{"series": rec.series, "outcome": "chosen"}]})


# ── golden packages (pinned verbatim; regenerate only with a TEMPLATE_VERSION bump) ─────

GOLDEN = {
    'ceo_roundup': {'hook': "GameStop's chief executive disclosed buying $74.4 million of the company's stock.",
     'video_script': ["The filings are by the company's chief executive and were filed from November "
                      '10th to November 11th.',
                      'They report 2 purchases, 3 million shares in total, for $74.4 million.',
                      'Officers and directors must file a Form 4 within two business days of a trade.',
                      "At least 2 more chief executives disclosed buying their own company's stock in "
                      'the same week.'],
     'cards': [{'title': 'Who filed', 'body': 'The CEO of GameStop'},
               {'title': 'The purchases', 'body': '2 purchases · 3M shares · $74.4M'},
               {'title': 'What a Form 4 is',
                'body': "Filed within two business days of an insider's trade"},
               {'title': 'Same week',
                'body': "At least 2 more CEOs disclosed buying their own company's stock"}],
     'opening_card': {'kicker': 'FILED LAST WEEK · FORM 4',
                      'logos': ['GME'],
                      'chip': 'GME',
                      'figure': '$74.4M',
                      'headline': "GameStop's CEO disclosed buying GameStop stock"},
     'image_spec': {'layout': 'rows',
                    'version': 1,
                    'kicker': 'FILED LAST WEEK · FORM 4',
                    'title': "At least 3 CEOs disclosed buying their own company's stock",
                    'sections': [{'rows': [{'logo': 'GME', 'cells': ['GameStop', 'CEO', '$74.4M']},
                                           {'logo': 'LOW', 'cells': ["Lowe's", 'CEO', '$2.3M']},
                                           {'logo': 'SBUX', 'cells': ['Starbucks', 'CEO', '$410K']}]}],
                    'notes': ['Some purchases are reported as held indirectly.',
                              'The figures include an amended Form 4 (Form 4/A).'],
                    'footer': 'Educational only · not investment advice · Source: SEC Form 4 filings · '
                              'Filed Nov 9–15, 2026 · Caydex · Not affiliated with anyone named'},
     'image_post': {'title': "At least 3 CEOs disclosed buying their own company's stock",
                    'paragraphs': ['The image lists each purchase by company, role and amount: '
                                   "GameStop, CEO, $74.4M; Lowe's, CEO, $2.3M; Starbucks, CEO, $410K.",
                                   'Some purchases are reported as held indirectly. The figures '
                                   'include an amended Form 4 (Form 4/A).',
                                   'Source: SEC Form 4 filings. Filed Nov 9–15, 2026.']},
     'image_footer': 'Educational only · not investment advice · Source: SEC Form 4 filings · Filed '
                     'Nov 9–15, 2026 · Caydex · Not affiliated with anyone named',
     'persons': ['Ryan Cohen'],
     'logo_refs': [{'key': 'GME', 'name': 'GameStop'},
                   {'key': 'LOW', 'name': "Lowe's"},
                   {'key': 'SBUX', 'name': 'Starbucks'}],
     'source_ref': 'news:ceo_buys:2026-11-09',
     'x': "At least 3 CEOs disclosed buying their own company's stock in Form 4s filed Nov 9-15.",
     'linkedin': "At least 3 CEOs disclosed buying their own company's stock in Form 4s filed Nov "
                 '9-15. The largest here: GameStop, $74.4 million.\n'
                 '\n'
                 "The purchases shown: GameStop, CEO Ryan Cohen, $74.4 million; Lowe's, CEO, $2.3 "
                 'million; Starbucks, CEO, $410,000.\n'
                 '\n'
                 "Some of the shares are reported as held indirectly rather than in the buyer's own "
                 'name. The figures include an amended Form 4 (Form 4/A).\n'
                 '\n'
                 "A Form 4 is the report a company's officers and directors file within two business "
                 "days of a trade. Amounts are each filing's share count times the cost it reports for "
                 'each share.',
     'tiktok': "GameStop's CEO disclosed buying $74.4 million of GameStop stock in Form 4s filed Nov "
               '10-11.\n'
               '\n'
               'The filings name Ryan Cohen, the CEO of GameStop.',
     'youtube_title': "GameStop's CEO disclosed buying $74.4M of GameStop stock"},
    'ceo_spotlight': {'hook': "GameStop's chief executive disclosed buying $74.4 million of the company's stock.",
     'video_script': ["The filings are by the company's chief executive and were filed from November 10th "
                      'to November 11th.',
                      'They report 2 purchases, 3 million shares in total, for $74.4 million.',
                      'Officers and directors must file a Form 4 within two business days of a trade.',
                      "Amounts are the filing's share count times the cost it reports for each share."],
     'cards': [{'title': 'Who filed', 'body': 'The CEO of GameStop'},
               {'title': 'The purchases', 'body': '2 purchases · 3M shares · $74.4M'},
               {'title': 'What a Form 4 is',
                'body': "Filed within two business days of an insider's trade"},
               {'title': 'How the amount is counted',
                'body': 'Shares times the cost of each share, per the filing'}],
     'opening_card': {'kicker': 'FILED LAST WEEK · FORM 4',
                      'logos': ['GME'],
                      'chip': 'GME',
                      'figure': '$74.4M',
                      'headline': "GameStop's CEO disclosed buying GameStop stock"},
     'persons': ['Ryan Cohen'],
     'x': "GameStop's CEO disclosed buying $74.4 million of GameStop stock in Form 4s filed Nov 10-11.",
     'linkedin': "GameStop's CEO disclosed buying $74.4 million of GameStop stock in Form 4s filed Nov "
                 '10-11.\n'
                 '\n'
                 'The filings name Ryan Cohen, the CEO of GameStop.\n'
                 '\n'
                 "A Form 4 is the report a company's officers and directors file within two business "
                 "days of a trade. Amounts are each filing's share count times the cost it reports for "
                 'each share.',
     'tiktok': "GameStop's CEO disclosed buying $74.4 million of GameStop stock in Form 4s filed Nov "
               '10-11.\n'
               '\n'
               'The filings name Ryan Cohen, the CEO of GameStop.',
     'youtube_title': "GameStop's CEO disclosed buying $74.4M of GameStop stock"},
    'ceo_spotlight_of_twin': {'hook': "The chief executive of Lowe's disclosed buying $2.3 million of the company's stock.",
     'video_script': ["The filing is by the company's chief executive and was filed on November 12th.",
                      'It reports one purchase of 9,812 shares, for $2.3 million.',
                      "The shares are reported as held indirectly, not in the chief executive's own "
                      'name.',
                      'Officers and directors must file a Form 4 within two business days of a trade.'],
     'cards': [{'title': 'Who filed', 'body': "The CEO of Lowe's"},
               {'title': 'The purchase', 'body': '1 purchase · 9,812 shares · $2.3M'},
               {'title': 'Held indirectly', 'body': "Not in the CEO's own name, per the filing"},
               {'title': 'What a Form 4 is',
                'body': "Filed within two business days of an insider's trade"}],
     'opening_card': {'kicker': 'FILED LAST WEEK · FORM 4',
                      'logos': ['LOW'],
                      'chip': 'LOW',
                      'figure': '$2.3M',
                      'headline': "The CEO of Lowe's disclosed buying Lowe's stock"},
     'persons': [],
     'x': "The CEO of Lowe's disclosed buying $2.3 million of Lowe's stock in a Form 4 filed Nov 12.",
     # news-v11 (review round 11): the headline names the role and company, so no "The filing is by …"
     'linkedin': "The CEO of Lowe's disclosed buying $2.3 million of Lowe's stock in a Form 4 filed "
                 'Nov 12.\n'
                 '\n'
                 "The shares are reported as held indirectly rather than in the buyer's own name.\n"
                 '\n'
                 "A Form 4 is the report a company's officers and directors file within two business "
                 "days of a trade. Amounts are each filing's share count times the cost it reports for "
                 'each share.',
     'tiktok': "The CEO of Lowe's disclosed buying $2.3 million of Lowe's stock in a Form 4 filed Nov "
               '12.\n'
               '\n'
               "A Form 4 is the report a company's officers and directors file within two business "
               "days of a trade. Amounts are each filing's share count times the cost it reports for "
               'each share.',
     'youtube_title': "The CEO of Lowe's disclosed buying $2.3M of Lowe's stock"},
    'insider_mixed': {'hook': "Nike's chief financial officer disclosed buying $1.3 million of the company's stock.",
     'video_script': ["The filing is by the company's chief financial officer and was filed on "
                      'November 10th.',
                      'It reports one purchase of about 19,000 shares, for $1.3 million.',
                      'Officers and directors must file a Form 4 within two business days of a trade.',
                      "At least 2 more directors disclosed buying their own company's stock in the "
                      'same week.'],
     'cards': [{'title': 'Who filed', 'body': 'The CFO of Nike'},
               {'title': 'The purchase', 'body': '1 purchase · about 19K shares · $1.3M'},
               {'title': 'What a Form 4 is',
                'body': "Filed within two business days of an insider's trade"},
               {'title': 'Same week',
                'body': "At least 2 more directors disclosed buying their own company's stock"}],
     'opening_card': {'kicker': 'FILED LAST WEEK · FORM 4',
                      'logos': ['NKE'],
                      'chip': 'NKE',
                      'figure': '$1.3M',
                      'headline': "Nike's CFO disclosed buying Nike stock"},
     'image_spec': {'layout': 'rows',
                    'version': 1,
                    'kicker': 'FILED LAST WEEK · FORM 4',
                    'title': "At least 3 company insiders disclosed buying their own company's stock",
                    'sections': [{'rows': [{'logo': 'NKE', 'cells': ['Nike', 'CFO', '$1.3M']},
                                           {'logo': 'CTSO', 'cells': ['Contoso', 'Director', '$640K']},
                                           {'logo': 'HSY',
                                            'cells': ['Hershey', 'Director', '$150K']}]}],
                    'notes': ['Some purchases are reported as held indirectly.'],
                    'footer': 'Educational only · not investment advice · Source: SEC Form 4 filings · '
                              'Filed Nov 9–15, 2026 · Caydex · Not affiliated with anyone named'},
     'image_post': {'title': "At least 3 company insiders disclosed buying their own company's stock",
                    'paragraphs': ['The image lists each purchase by company, role and amount: Nike, '
                                   'CFO, $1.3M; Contoso, Director, $640K; Hershey, Director, $150K.',
                                   'Some purchases are reported as held indirectly.',
                                   'Source: SEC Form 4 filings. Filed Nov 9–15, 2026.']},
     'image_footer': 'Educational only · not investment advice · Source: SEC Form 4 filings · Filed '
                     'Nov 9–15, 2026 · Caydex · Not affiliated with anyone named',
     'persons': ['Anna Smith-Jones'],
     'logo_refs': [{'key': 'NKE', 'name': 'Nike'},
                   {'key': 'CTSO', 'name': 'Contoso'},
                   {'key': 'HSY', 'name': 'Hershey'}],
     'source_ref': 'news:insider_buys:2026-11-09',
     'x': "At least 3 company insiders disclosed buying their own company's stock in Form 4s filed Nov "
          '9-15.',
     'linkedin': "At least 3 company insiders disclosed buying their own company's stock in Form 4s "
                 'filed Nov 9-15. The largest here: Nike, $1.3 million.\n'
                 '\n'
                 'The purchases shown: Nike, CFO, $1.3 million; Contoso, director Anna Smith-Jones, '
                 '$640,000; Hershey, director, $150,000.\n'
                 '\n'
                 "Some of the shares are reported as held indirectly rather than in the buyer's own "
                 'name.\n'
                 '\n'
                 "A Form 4 is the report a company's officers and directors file within two business "
                 "days of a trade. Amounts are each filing's share count times the cost it reports for "
                 'each share.',
     'tiktok': "Nike's CFO disclosed buying $1.3 million of Nike stock in a Form 4 filed Nov 10.\n"
               '\n'
               "A Form 4 is the report a company's officers and directors file within two business "
               "days of a trade. Amounts are each filing's share count times the cost it reports for "
               'each share.',
     'youtube_title': "Nike's CFO disclosed buying $1.3M of Nike stock"},
    'berkshire_13f': {'hook': "Berkshire Hathaway's latest 13F lists 3 newly reported holdings.",
     'video_script': ['It covers the quarter that ended September 30th and was filed on November 13th.',
                      "The holdings newly reported in this filing are Constellation Brands, Domino's "
                      'Pizza and Lennar.',
                      'The holdings no longer reported in this filing are Citigroup and Nu Holdings.',
                      "A 13F lists U.S. stock holdings at a quarter's end and is filed up to 45 days "
                      'later.'],
     'cards': [{'title': 'Quarter ended', 'body': 'Sep 30, 2026 · filed Nov 13, 2026'},
               {'title': 'Newly reported', 'body': "Constellation Brands, Domino's Pizza and Lennar"},
               {'title': 'No longer reported', 'body': 'Citigroup and Nu Holdings'},
               {'title': 'About 13F filings',
                'body': 'U.S. stock holdings, filed up to 45 days after the quarter'}],
     'opening_card': {'kicker': '13F SEASON',
                      'logos': ['BRK-B'],
                      'figure': '3',
                      'headline': "newly reported holdings in Berkshire Hathaway's 13F",
                      'chip': 'BRK-B'},
     'image_spec': {'layout': 'rows',
                    'version': 1,
                    'kicker': '13F SEASON',
                    'title': "Berkshire Hathaway's latest 13F",
                    'sections': [{'heading': 'Newly reported',
                                  'rows': [{'logo': 'STZ', 'cells': ['Constellation Brands', '$1.2B']},
                                           {'logo': 'DPZ', 'cells': ["Domino's Pizza", '$549M']},
                                           {'logo': 'LEN', 'cells': ['Lennar', '$250.1M']}]},
                                 {'heading': 'No longer reported',
                                  'rows': [{'logo': 'C', 'cells': ['Citigroup']},
                                           {'logo': 'NU', 'cells': ['Nu Holdings']}]}],
                    # news-v5: every counted kind, the lead first (counted-only kinds after the
                    # kinds with moves, in display order)
                    'subtitle': '3 newly reported · 2 no longer reported · 2 with more shares · 4 with fewer '
                                'shares',
                    'footer': 'Educational only · not investment advice · Source: SEC Form 13F · '
                              'Quarter ended Sep 30, 2026 · filed Nov 13, 2026 · Caydex · Not '
                              'affiliated with anyone named'},
     'image_post': {'title': "Berkshire Hathaway's latest 13F",
                    'paragraphs': ["Newly reported: Constellation Brands, Domino's Pizza, Lennar.",
                                   'No longer reported: Citigroup, Nu Holdings.',
                                   'Source: SEC Form 13F. Quarter ended Sep 30, 2026 · filed Nov 13, '
                                   '2026.']},
     'image_footer': 'Educational only · not investment advice · Source: SEC Form 13F · Quarter ended '
                     'Sep 30, 2026 · filed Nov 13, 2026 · Caydex · Not affiliated with anyone named',
     'persons': [],
     'logo_refs': [{'key': 'STZ', 'name': 'Constellation Brands'},
                   {'key': 'DPZ', 'name': "Domino's Pizza"},
                   {'key': 'LEN', 'name': 'Lennar'},
                   {'key': 'C', 'name': 'Citigroup'},
                   {'key': 'NU', 'name': 'Nu Holdings'},
                   {'key': 'BRK-B', 'name': 'Berkshire Hathaway'}],
     'source_ref': 'news:thirteen_f:0001067983:2026-Q3',
     'x': "Berkshire Hathaway's latest 13F: 3 newly reported holdings.",
     'linkedin': "Berkshire Hathaway's 13F for the quarter ended Sep 30: 3 newly reported holdings, 2 "
                 'no longer reported, 2 with more shares, 4 with fewer shares.\n'
                 '\n'
                 "Newly reported, with the value the filing gives at the quarter's end: Constellation "
                 "Brands, $1.2 billion; Domino's Pizza, $549 million; Lennar, $250.1 million.\n"
                 '\n'
                 'No longer reported: Citigroup; Nu Holdings.\n'
                 '\n'
                 "A 13F lists U.S. stock holdings at a quarter's end and is filed up to 45 days later; "
                 'it does not show when shares changed hands.',
     'tiktok': "Berkshire Hathaway's 13F for the quarter ended Sep 30: 3 newly reported holdings, 2 no "
               'longer reported, 2 with more shares, 4 with fewer shares.\n'
               '\n'
               "Newly reported, with the value the filing gives at the quarter's end: Constellation "
               "Brands, $1.2 billion; Domino's Pizza, $549 million; Lennar, $250.1 million.",
     'youtube_title': "Berkshire Hathaway's 13F: 3 newly reported holdings"},
    # news-v5: Uber's increase (9.8M shares at $77.50 a share = $759.5M) is the largest move — above
    # Chipotle's decrease (6M at $50 = $300M) and CoreWeave's new $118M position — so it leads.
    'pershing_13f': {'hook': "Pershing Square Capital Management's latest 13F reports more shares of 1 holding.",
     'video_script': ['It covers the quarter that ended September 30th; an amended version was filed '
                      'on November 14th.',
                      'Compared with the quarter before, it reported more shares of Uber.',
                      'Compared with the quarter before, it also reported fewer shares of Chipotle Mexican '
                      'Grill.',
                      'CoreWeave first appears in this filing; it was listed in August 2026.'],
     'cards': [{'title': 'Quarter ended', 'body': 'Sep 30, 2026 · amended Nov 14, 2026'},
               {'title': 'Reported more shares', 'body': 'Uber'},
               {'title': 'Reported fewer shares', 'body': 'Chipotle Mexican Grill'},
               {'title': 'First appears', 'body': 'CoreWeave, listed Aug 2026'}],
     'opening_card': {'kicker': '13F SEASON',
                      'logos': ['UBER'],
                      'figure': '1',
                      'headline': "holding with more shares in Pershing Square Capital Management's 13F"},
     'persons': [],
     'x': "Pershing Square Capital Management's latest 13F: more shares of 1 holding.",
     'linkedin': "Pershing Square Capital Management's 13F for the quarter ended Sep 30: 1 holding with "
                 'more shares, 1 with fewer shares, 1 newly reported.\n'
                 '\n'
                 'Reported more shares of: Uber.\n'
                 '\n'
                 'Reported fewer shares of: Chipotle Mexican Grill.\n'
                 '\n'
                 "Newly reported, with the value the filing gives at the quarter's end: CoreWeave, "
                 '$118 million.\n'
                 '\n'
                 "A 13F lists U.S. stock holdings at a quarter's end and is filed up to 45 days later; "
                 'it does not show when shares changed hands.',
     'tiktok': "Pershing Square Capital Management's 13F for the quarter ended Sep 30: 1 holding with "
               'more shares, 1 with fewer shares, 1 newly reported.\n'
               '\n'
               'Reported more shares of: Uber.',
     'youtube_title': "Pershing Square Capital Management's 13F: more shares of 1 holding"},
    'costco_money_map': {'hook': 'For every $100 of revenue in fiscal 2025, Costco kept $2.94 as net income.',
     'video_script': ['Costco reported revenue of $275.2 billion in fiscal 2025.',
                      'Its United States segment made up 73% of revenue, and Canada made up 14% of '
                      'revenue.',
                      'After the cost of sales, gross profit was $35.4 billion; after operating costs, '
                      'operating profit was $10.4 billion.',
                      'After interest, taxes and everything else, net income was $8.1 billion.'],
     'cards': [{'title': 'Revenue', 'body': '$275.2B in fiscal 2025'},
               {'title': 'Segments', 'body': 'United States: 73% of revenue · Canada: 14% of revenue'},
               {'title': 'After costs', 'body': 'Gross profit $35.4B · operating profit $10.4B'},
               {'title': 'Net income', 'body': '$8.1B in fiscal 2025'}],
     'opening_card': {'kicker': 'MONEY MAP',
                      'logos': ['COST'],
                      'chip': 'COST',
                      'figure': '$2.94',
                      'headline': 'of every $100 of Costco revenue was net income'},
     'image_spec': {'layout': 'bars',
                    'version': 1,
                    'kicker': 'MONEY MAP',
                    'header': {'logo': 'COST', 'name': 'Costco'},
                    'title': 'How Costco makes money',
                    'subtitle': 'Fiscal 2025 · as reported',
                    'segments': [{'label': 'United States',
                                  'value': '$200B',
                                  'ratio': 1.0,
                                  'style': 'fill'},
                                 {'label': 'Canada',
                                  'value': '$38.5B',
                                  'ratio': 0.1925,
                                  'style': 'fill'},
                                 {'label': 'Other International',
                                  'value': '$36.7B',
                                  'ratio': 0.1835,
                                  'style': 'fill'}],
                    'flow': [{'label': 'Revenue', 'value': '$275.2B', 'ratio': 1.0, 'style': 'fill'},
                             {'label': 'Gross profit',
                              'value': '$35.4B',
                              'ratio': 0.1286,
                              'style': 'fill'},
                             {'label': 'Operating profit',
                              'value': '$10.4B',
                              'ratio': 0.0378,
                              'style': 'fill'},
                             {'label': 'Net income',
                              'value': '$8.1B',
                              'ratio': 0.0294,
                              'style': 'fill'}],
                    'callout': 'For every $100 of revenue, $2.94 was net income',
                    'footer': 'Educational only · not investment advice · Source: company financial '
                              'statements · Fiscal 2025, ended Aug 31, 2025 · Caydex · Not affiliated '
                              'with anyone named'},
     'image_post': {'title': 'How Costco makes money',
                    'paragraphs': ['Bars show revenue by segment: United States $200B; Canada $38.5B; '
                                   'Other International $36.7B.',
                                   'A second set of bars shows Revenue $275.2B; Gross profit $35.4B; '
                                   'Operating profit $10.4B; Net income $8.1B.',
                                   'For every $100 of revenue, $2.94 was net income.',
                                   'Source: company financial statements. Fiscal 2025, ended Aug 31, '
                                   '2025.']},
     'image_footer': 'Educational only · not investment advice · Source: company financial statements '
                     '· Fiscal 2025, ended Aug 31, 2025 · Caydex · Not affiliated with anyone named',
     'persons': [],
     'logo_refs': [{'key': 'COST', 'name': 'Costco'}],
     'source_ref': 'news:money_map:COST:2025',
     'x': 'How Costco makes money: $275.2 billion of revenue in fiscal 2025, and $2.94 of every $100 '
          'kept as net income.',
     'linkedin': 'How Costco makes money: $275.2 billion of revenue in fiscal 2025, and $2.94 of every '
                 '$100 kept as net income.\n'
                 '\n'
                 'Revenue by segment in fiscal 2025: United States, $200 billion; Canada, $38.5 '
                 'billion; Other International, $36.7 billion.\n'
                 '\n'
                 'After the cost of sales, gross profit was $35.4 billion. After operating costs, '
                 'operating profit was $10.4 billion.\n'
                 '\n'
                 'Net income was $8.1 billion: $2.94 of every $100 of revenue.\n'
                 '\n'
                 "Figures are as reported in the company's annual financial statements for fiscal "
                 '2025.',
     'tiktok': 'How Costco makes money: $275.2 billion of revenue in fiscal 2025, and $2.94 of every '
               '$100 kept as net income.\n'
               '\n'
               'Revenue by segment in fiscal 2025: United States, $200 billion; Canada, $38.5 billion; '
               'Other International, $36.7 billion.',
     'youtube_title': 'How Costco makes money: fiscal 2025 results'},
    'rivian_money_map': {'hook': 'Rivian Automotive had a net loss of $66.38 for every $100 of revenue.',
     'video_script': ['Rivian Automotive reported revenue of $5.8 billion in fiscal 2025.',
                      'Its Automotive segment made up 80% of revenue, and Software and services made '
                      'up 20% of revenue.',
                      'After the cost of sales, gross loss was $80 million; after operating costs, '
                      'operating loss was $4 billion.',
                      'After interest, taxes and everything else, the net loss was $3.9 billion.'],
     'cards': [{'title': 'Revenue', 'body': '$5.8B in fiscal 2025'},
               {'title': 'Segments',
                'body': 'Automotive: 80% of revenue · Software and services: 20% of revenue'},
               {'title': 'After costs', 'body': 'Gross loss $80M · operating loss $4B'},
               {'title': 'Net loss', 'body': '$3.9B in fiscal 2025'}],
     'opening_card': {'kicker': 'MONEY MAP',
                      'logos': ['RIVN'],
                      'chip': 'RIVN',
                      'figure': '$66.38',
                      'headline': 'net loss for every $100 of Rivian Automotive revenue'},
     'persons': [],
     'x': 'How Rivian Automotive makes money: $5.8 billion of revenue in fiscal 2025, and a net loss '
          'of $66.38 for every $100.',
     'linkedin': 'How Rivian Automotive makes money: $5.8 billion of revenue in fiscal 2025, and a net '
                 'loss of $66.38 for every $100.\n'
                 '\n'
                 'Revenue by segment in fiscal 2025: Automotive, $4.6 billion; Software and services, '
                 '$1.2 billion.\n'
                 '\n'
                 'After the cost of sales, gross loss was $80 million. After operating costs, '
                 'operating loss was $4 billion.\n'
                 '\n'
                 'The net loss was $3.9 billion: $66.38 for every $100 of revenue.\n'
                 '\n'
                 "Figures are as reported in the company's annual financial statements for fiscal "
                 '2025.',
     'tiktok': 'How Rivian Automotive makes money: $5.8 billion of revenue in fiscal 2025, and a net '
               'loss of $66.38 for every $100.\n'
               '\n'
               'Revenue by segment in fiscal 2025: Automotive, $4.6 billion; Software and services, '
               '$1.2 billion.',
     'youtube_title': 'How Rivian Automotive makes money: fiscal 2025 results'},
    'comcast_money_map': {'hook': 'For every $100 of revenue in fiscal 2025, Comcast kept $13.10 as net income.',
     'video_script': ['Comcast reported revenue of $123.7 billion in fiscal 2025.',
                      'Its Connectivity and Platforms segment made up 64% of segment sales that year.',
                      'After all operating costs, operating profit was $23.3 billion that year.',
                      'After interest, taxes and everything else, net income was $16.2 billion.'],
     'cards': [{'title': 'Revenue', 'body': '$123.7B in fiscal 2025'},
               {'title': 'Segments',
                'body': 'Connectivity and Platforms: 64% of segment sales · Content and Experiences: '
                        '34% of segment sales'},
               {'title': 'After costs', 'body': 'Operating profit $23.3B'},
               {'title': 'Net income', 'body': '$16.2B in fiscal 2025'}],
     'opening_card': {'kicker': 'MONEY MAP',
                      'logos': ['CMCSA'],
                      'chip': 'CMCSA',
                      'figure': '$13.10',
                      'headline': 'of every $100 of Comcast revenue was net income'},
     'persons': [],
     'x': 'How Comcast makes money: $123.7 billion of revenue in fiscal 2025, and $13.10 of every $100 '
          'kept as net income.',
     'linkedin': 'How Comcast makes money: $123.7 billion of revenue in fiscal 2025, and $13.10 of '
                 'every $100 kept as net income.\n'
                 '\n'
                 'Revenue by segment in fiscal 2025: Connectivity and Platforms, $82 billion; Content '
                 'and Experiences, $43.4 billion; other, $2.6 billion. Sales between its own segments, '
                 '$4.3 billion, are taken out of that total.\n'
                 '\n'
                 'After operating costs, operating profit was $23.3 billion.\n'
                 '\n'
                 'Net income was $16.2 billion: $13.10 of every $100 of revenue.\n'
                 '\n'
                 "Figures are as reported in the company's annual financial statements for fiscal "
                 '2025.',
     'tiktok': 'How Comcast makes money: $123.7 billion of revenue in fiscal 2025, and $13.10 of every '
               '$100 kept as net income.\n'
               '\n'
               'Revenue by segment in fiscal 2025: Connectivity and Platforms, $82 billion; Content '
               'and Experiences, $43.4 billion; other, $2.6 billion. Sales between its own segments, '
               '$4.3 billion, are taken out of that total.',
     'youtube_title': 'How Comcast makes money: fiscal 2025 results'},
}


#: Drop 2b goldens (news-v9; earnings_loss's LinkedIn / TikTok captions news-v10; the LinkedIn captions of
#: congress_accenture, every stake_* and both earnings_* news-v11): one per 2b sample,
#: pinned verbatim — regenerate only with a TEMPLATE_VERSION bump, after reading every string.
GOLDEN_2B = {
    'congress_accenture': {'hook': '3 members of Congress disclosed purchases of Accenture stock in November 2026.',
     'video_script': ['The count covers periodic transaction reports disclosed during November 2026, as of '
                      'December 8th.',
                      'Each member is counted once, however many reports they filed that month.',
                      "These reports can include a spouse's or dependent child's trades, and give amounts only as "
                      'ranges.',
                      'A trade can be disclosed up to 45 days after it happens, so the trades may be older.'],
     'cards': [{'title': 'Disclosed in', 'body': 'November 2026 · as of Dec 8, 2026'},
               {'title': 'Counted once', 'body': 'Each member, however many reports'},
               {'title': 'What the reports cover', 'body': 'Spouse and dependent-child trades; amounts as ranges'},
               {'title': 'Reporting delay', 'body': 'Up to 45 days after the trade'}],
     'opening_card': {'kicker': 'CONGRESS · DISCLOSED IN NOVEMBER',
                      'logos': ['ACN'],
                      'chip': 'ACN',
                      'figure': '3',
                      'headline': 'members of Congress disclosed purchases of Accenture stock'},
     'image_spec': {'layout': 'spotlight',
                    'version': 1,
                    'kicker': 'CONGRESS · DISCLOSED IN NOVEMBER',
                    'header': {'logo': 'ACN', 'name': 'Accenture', 'chip': 'ACN'},
                    'figure': '3',
                    'headline': 'members of Congress disclosed purchases of Accenture stock',
                    'lines': ['November 2026 · as of Dec 8, 2026', 'Each member counted once'],
                    'footer': 'Educational only · not investment advice · Source: congressional periodic '
                              'transaction reports · November 2026 · as of Dec 8, 2026 · Caydex · Not affiliated '
                              'with anyone named'},
     'image_post': {'title': 'Members of Congress disclosed purchases of Accenture stock',
                    'paragraphs': ['The image shows 3, the number of members of Congress who disclosed purchases '
                                   'of Accenture stock in November 2026, each counted once.',
                                   'Source: congressional periodic transaction reports. November 2026 · as of Dec '
                                   '8, 2026.']},
     'image_footer': 'Educational only · not investment advice · Source: congressional periodic transaction '
                     'reports · November 2026 · as of Dec 8, 2026 · Caydex · Not affiliated with anyone named',
     'persons': [],
     'logo_refs': [{'key': 'ACN', 'name': 'Accenture'}],
     'source_ref': 'news:congress_count:2026-11',
     'x': '3 members of Congress disclosed purchases of Accenture stock in November 2026, counting each member '
          'once.',
     'bluesky': '3 members of Congress disclosed purchases of Accenture stock in November 2026, counting each '
                'member once.',
     # news-v11 (review round 11): the headline counts each member once, so `cg.l2` is not said again
     'linkedin': '3 members of Congress disclosed purchases of Accenture stock in November 2026, counting each '
                 'member once.\n'
                 '\n'
                 'The count covers periodic transaction reports disclosed during November 2026, as of Dec 8.\n'
                 '\n'
                 "These reports can include a spouse's or dependent child's trades, and give amounts only as "
                 'ranges.\n'
                 '\n'
                 'A trade can be disclosed up to 45 days after it happens, so the trades may be older.',
     'tiktok': '3 members of Congress disclosed purchases of Accenture stock in November 2026, counting each '
               'member once.\n'
               '\n'
               'The count covers periodic transaction reports disclosed during November 2026, as of Dec 8.',
     'youtube_title': '3 members of Congress disclosed purchases of Accenture stock in November 2026'},
    'stake_nscale': {'hook': 'Nvidia invested $777.4 million in Nscale.',
     'video_script': ['The figure is as of March 27th, from the source named in this post.',
                      'That is the amount the source reports as invested, not a figure for today.',
                      'As of that date, Nscale was not listed on a public exchange.',
                      'Companies disclose stakes like this in filings and official releases.'],
     'cards': [{'title': 'As of', 'body': 'Mar 27, 2026'},
               {'title': 'Invested', 'body': 'The amount put in, not a figure for today'},
               {'title': 'Not listed', 'body': 'Not traded on a public exchange as of that date'},
               {'title': 'Source', 'body': 'Nscale Form S-1 (Sep 18, 2026)'}],
     'opening_card': {'kicker': 'COMPANY STAKES',
                      'logos': ['NVDA'],
                      'figure': '$777.4M',
                      'headline': 'NVIDIA invested in Nscale'},
     'image_spec': {'layout': 'pair',
                    'version': 1,
                    'kicker': 'COMPANY STAKES',
                    'left': {'logo': 'NVDA', 'name': 'NVIDIA'},
                    'right': {'name': 'Nscale'},
                    'figure': '$777.4M',
                    'label': 'invested',
                    'lines': ['As of Mar 27, 2026', 'Source: Nscale Form S-1 (Sep 18, 2026)'],
                    'footer': 'Educational only · not investment advice · Source: Nscale Form S-1 (Sep 18, 2026) · '
                              'As of Mar 27, 2026 · Caydex · Not affiliated with anyone named'},
     'image_post': {'title': 'NVIDIA invested $777.4M in Nscale',
                    'paragraphs': ['The image shows NVIDIA and Nscale, with $777.4M labelled invested.',
                                   'Source: Nscale Form S-1 (Sep 18, 2026). As of Mar 27, 2026.']},
     'image_footer': 'Educational only · not investment advice · Source: Nscale Form S-1 (Sep 18, 2026) · As of '
                     'Mar 27, 2026 · Caydex · Not affiliated with anyone named',
     'persons': [],
     'logo_refs': [{'key': 'NVDA', 'name': 'NVIDIA'}],
     'source_ref': 'news:company_stakes:5f0c8a52-6a5e-4d4f-9a52-0d9c3e1b7a01',
     'x': 'NVIDIA invested $777.4 million in Nscale, as of Mar 27. Source: Nscale Form S-1 (Sep 18, 2026).',
     'bluesky': 'NVIDIA invested $777.4 million in Nscale, as of Mar 27. Source: Nscale Form S-1 (Sep 18, 2026).',
     # news-v11 (review round 11): the headline cites the source, so `st.p.source` is not said again
     'linkedin': 'NVIDIA invested $777.4 million in Nscale, as of Mar 27. Source: Nscale Form S-1 (Sep 18, 2026).\n'
                 '\n'
                 'For Series C shares; NVIDIA also paid $400.0M for Series B shares on Oct 8, 2025.\n'
                 '\n'
                 'That is the amount the source reports as invested, not a figure for today.\n'
                 '\n'
                 'As of that date, Nscale was not listed on a public exchange.',
     'tiktok': 'NVIDIA invested $777.4 million in Nscale, as of Mar 27. Source: Nscale Form S-1 (Sep 18, 2026).\n'
               '\n'
               'For Series C shares; NVIDIA also paid $400.0M for Series B shares on Oct 8, 2025.',
     'youtube_title': 'NVIDIA invested $777.4M in Nscale'},
    'stake_anthropic_commitment': {'hook': 'Nvidia committed up to $10 billion to Anthropic.',
     'video_script': ['The figure is as of November 18th, 2025, from the source named in this post.',
                      'That is the most the agreement allows; the source says up to that amount.',
                      'A commitment is money agreed to be invested, which may be paid over time.',
                      'Companies disclose stakes like this in filings and official releases.'],
     'cards': [{'title': 'As of', 'body': 'Nov 18, 2025'},
               {'title': 'Committed up to', 'body': 'The most the agreement allows'},
               {'title': 'A commitment', 'body': 'Money agreed to be invested, which may be paid over time'},
               {'title': 'Source', 'body': 'Microsoft, NVIDIA and Anthropic release (Nov 18, 2025)'}],
     'opening_card': {'kicker': 'COMPANY STAKES',
                      'logos': ['NVDA'],
                      'figure': '$10B',
                      'headline': 'NVIDIA committed funds to Anthropic'},
     'image_spec': {'layout': 'pair',
                    'version': 1,
                    'kicker': 'COMPANY STAKES',
                    'left': {'logo': 'NVDA', 'name': 'NVIDIA'},
                    'right': {'name': 'Anthropic'},
                    'figure': '$10B',
                    'label': 'committed up to',
                    'lines': ['As of Nov 18, 2025',
                              'Source: Microsoft, NVIDIA and Anthropic release (Nov 18, 2025)'],
                    'footer': 'Educational only · not investment advice · Source: Microsoft, NVIDIA and Anthropic '
                              'release (Nov 18, 2025) · As of Nov 18, 2025 · Caydex · Not affiliated with anyone '
                              'named'},
     'image_post': {'title': 'NVIDIA committed up to $10B to Anthropic',
                    'paragraphs': ['The image shows NVIDIA and Anthropic, with $10B labelled committed up to.',
                                   'Source: Microsoft, NVIDIA and Anthropic release (Nov 18, 2025). As of Nov 18, '
                                   '2025.']},
     'image_footer': 'Educational only · not investment advice · Source: Microsoft, NVIDIA and Anthropic release '
                     '(Nov 18, 2025) · As of Nov 18, 2025 · Caydex · Not affiliated with anyone named',
     'persons': [],
     'logo_refs': [{'key': 'NVDA', 'name': 'NVIDIA'}],
     'source_ref': 'news:company_stakes:0d3b6f1e-2a4c-4e8b-9f10-6c2d8a7e5b02',
     'x': 'NVIDIA committed up to $10 billion to Anthropic, as of Nov 18, 2025.',
     'bluesky': 'NVIDIA committed up to $10 billion to Anthropic, as of Nov 18, 2025.',
     # news-v11 (review round 11): the headline cites the source, so `st.p.source` is not said again
     'linkedin': 'NVIDIA committed up to $10 billion to Anthropic, as of Nov 18, 2025. Source: Microsoft, NVIDIA '
                 'and Anthropic release (Nov 18, 2025).\n'
                 '\n'
                 'That is the most the agreement allows; the source says up to that amount.\n'
                 '\n'
                 'A commitment is money agreed to be invested, which may be paid over time.',
     'tiktok': 'NVIDIA committed up to $10 billion to Anthropic, as of Nov 18, 2025. Source: Microsoft, NVIDIA and '
               'Anthropic release (Nov 18, 2025).\n'
               '\n'
               'That is the most the agreement allows; the source says up to that amount.',
     'youtube_title': 'NVIDIA committed up to $10B to Anthropic'},
    'stake_spacex_fair_value': {'hook': 'Tesla reported its SpaceX stake at a fair value of $3 billion.',
     'video_script': ['The figure is as of June 30th, from the source named in this post.',
                      'Fair value is the amount the source says the stake was measured at on that date.',
                      'Shares of SpaceX are listed on a U.S. exchange.',
                      'Companies disclose stakes like this in filings and official releases.'],
     'cards': [{'title': 'As of', 'body': 'Jun 30, 2026'},
               {'title': 'Fair value', 'body': "The source's measure of the stake on that date"},
               {'title': 'Listed in the U.S.', 'body': 'Shares trade on a U.S. exchange'},
               {'title': 'Source', 'body': 'Tesla 10-Q (Q2 2026)'}],
     'opening_card': {'kicker': 'COMPANY STAKES',
                      'logos': ['TSLA'],
                      'figure': '$3B',
                      'headline': "Tesla's stake in SpaceX"},
     'image_spec': {'layout': 'pair',
                    'version': 1,
                    'kicker': 'COMPANY STAKES',
                    'left': {'logo': 'TSLA', 'name': 'Tesla'},
                    'right': {'name': 'SpaceX', 'logo': 'SPCX'},
                    'figure': '$3B',
                    'label': 'fair value',
                    'lines': ['As of Jun 30, 2026', 'Source: Tesla 10-Q (Q2 2026)'],
                    'footer': 'Educational only · not investment advice · Source: Tesla 10-Q (Q2 2026) · As of Jun '
                              '30, 2026 · Caydex · Not affiliated with anyone named'},
     'image_post': {'title': "Tesla's SpaceX stake had a fair value of $3B",
                    'paragraphs': ['The image shows Tesla and SpaceX, with $3B labelled fair value.',
                                   'Source: Tesla 10-Q (Q2 2026). As of Jun 30, 2026.']},
     'image_footer': 'Educational only · not investment advice · Source: Tesla 10-Q (Q2 2026) · As of Jun 30, 2026 '
                     '· Caydex · Not affiliated with anyone named',
     'persons': [],
     'logo_refs': [{'key': 'TSLA', 'name': 'Tesla'}, {'key': 'SPCX', 'name': 'SpaceX'}],
     'source_ref': 'news:company_stakes:7a9e2c44-1b3d-4f5a-8c6e-9d0f1a2b3c03',
     'x': 'Tesla reported its SpaceX stake at a fair value of $3 billion, as of Jun 30. Source: Tesla 10-Q (Q2 '
          '2026).',
     'bluesky': 'Tesla reported its SpaceX stake at a fair value of $3 billion, as of Jun 30. Source: Tesla 10-Q '
                '(Q2 2026).',
     # news-v11 (review round 11): the headline cites the source, so `st.p.source` is not said again
     'linkedin': 'Tesla reported its SpaceX stake at a fair value of $3 billion, as of Jun 30. Source: Tesla 10-Q '
                 '(Q2 2026).\n'
                 '\n'
                 'Fair value is the amount the source says the stake was measured at on that date.\n'
                 '\n'
                 'Shares of SpaceX are listed on a U.S. exchange.',
     'tiktok': 'Tesla reported its SpaceX stake at a fair value of $3 billion, as of Jun 30. Source: Tesla 10-Q '
               '(Q2 2026).\n'
               '\n'
               'Fair value is the amount the source says the stake was measured at on that date.',
     'youtube_title': 'Tesla reported its SpaceX stake at a fair value of $3B'},
    'stake_berkadia_carrying': {'hook': 'Berkshire Hathaway carried its Berkadia Commercial Mortgage stake at $461 million.',
     'video_script': ['The figure is as of June 30th, from the source named in this post.',
                      "Carrying value is the amount the stake is recorded at in the investor's accounts.",
                      'As of that date, Berkadia Commercial Mortgage was not listed on a public exchange.',
                      'Companies disclose stakes like this in filings and official releases.'],
     'cards': [{'title': 'As of', 'body': 'Jun 30, 2026'},
               {'title': 'Carrying value', 'body': 'What the stake is recorded at in the accounts'},
               {'title': 'Not listed', 'body': 'Not traded on a public exchange as of that date'},
               {'title': 'Source', 'body': 'Berkshire Hathaway 10-Q (quarter ended Jun 30, 2026)'}],
     'opening_card': {'kicker': 'COMPANY STAKES',
                      'logos': ['BRK-B'],
                      'figure': '$461M',
                      'headline': "Berkshire Hathaway's stake in Berkadia Commercial Mortgage"},
     'image_spec': {'layout': 'pair',
                    'version': 1,
                    'kicker': 'COMPANY STAKES',
                    'left': {'logo': 'BRK-B', 'name': 'Berkshire Hathaway'},
                    'right': {'name': 'Berkadia Commercial Mortgage'},
                    'figure': '$461M',
                    'label': 'carrying value',
                    'lines': ['As of Jun 30, 2026', 'Source: Berkshire Hathaway 10-Q (quarter ended Jun 30, 2026)'],
                    'footer': 'Educational only · not investment advice · Source: Berkshire Hathaway 10-Q (quarter '
                              'ended Jun 30, 2026) · As of Jun 30, 2026 · Caydex · Not affiliated with anyone '
                              'named'},
     'image_post': {'title': "Berkshire Hathaway's stake in Berkadia Commercial Mortgage",
                    'paragraphs': ['The image shows Berkshire Hathaway and Berkadia Commercial Mortgage, with '
                                   '$461M labelled carrying value.',
                                   'Source: Berkshire Hathaway 10-Q (quarter ended Jun 30, 2026). As of Jun 30, '
                                   '2026.']},
     'image_footer': 'Educational only · not investment advice · Source: Berkshire Hathaway 10-Q (quarter ended '
                     'Jun 30, 2026) · As of Jun 30, 2026 · Caydex · Not affiliated with anyone named',
     'persons': [],
     'logo_refs': [{'key': 'BRK-B', 'name': 'Berkshire Hathaway'}],
     'source_ref': 'news:company_stakes:2c4e6a8b-0d1f-4a3c-9e5b-7d9f1b3d5f04',
     'x': 'Berkshire Hathaway reported its Berkadia Commercial Mortgage stake at a carrying value of $461 million, '
          'as of Jun 30.',
     'bluesky': 'Berkshire Hathaway carried its Berkadia Commercial Mortgage stake at $461 million, as of Jun 30.',
     # news-v11 (review round 11): the headline cites the source, so `st.p.source` is not said again
     'linkedin': 'Berkshire Hathaway reported its Berkadia Commercial Mortgage stake at a carrying value of $461 '
                 'million, as of Jun 30. Source: Berkshire Hathaway 10-Q (quarter ended Jun 30, 2026).\n'
                 '\n'
                 "Carrying value is the amount the stake is recorded at in the investor's accounts.\n"
                 '\n'
                 'As of that date, Berkadia Commercial Mortgage was not listed on a public exchange.',
     'tiktok': 'Berkshire Hathaway reported its Berkadia Commercial Mortgage stake at a carrying value of $461 '
               'million, as of Jun 30. Source: Berkshire Hathaway 10-Q (quarter ended Jun 30, 2026).\n'
               '\n'
               "Carrying value is the amount the stake is recorded at in the investor's accounts.",
     'youtube_title': 'Berkshire Hathaway carried its Berkadia Commercial Mortgage stake at $461M'},
    'stake_itochu_non_us': {'hook': 'Berkshire Hathaway reported its Itochu Corporation stake at a fair value of $8.9 billion.',
     'video_script': ['The figure is as of December 31st, 2025, from the source named in this post.',
                      'Fair value is the amount the source says the stake was measured at on that date.',
                      'As of that date, shares of Itochu Corporation traded on an exchange outside the U.S.',
                      'Companies disclose stakes like this in filings and official releases.'],
     'cards': [{'title': 'As of', 'body': 'Dec 31, 2025'},
               {'title': 'Fair value', 'body': "The source's measure of the stake on that date"},
               {'title': 'Listed outside the U.S.', 'body': 'Japan'},
               {'title': 'Source', 'body': 'Berkshire Hathaway 2025 shareholder letter'}],
     'opening_card': {'kicker': 'COMPANY STAKES',
                      'logos': ['BRK-B'],
                      'figure': '$8.9B',
                      'headline': "Berkshire Hathaway's stake in ITOCHU Corporation"},
     'image_spec': {'layout': 'pair',
                    'version': 1,
                    'kicker': 'COMPANY STAKES',
                    'left': {'logo': 'BRK-B', 'name': 'Berkshire Hathaway'},
                    'right': {'name': 'ITOCHU Corporation'},
                    'figure': '$8.9B',
                    'label': 'fair value',
                    'lines': ['As of Dec 31, 2025', 'Source: Berkshire Hathaway 2025 shareholder letter'],
                    'footer': 'Educational only · not investment advice · Source: Berkshire Hathaway 2025 '
                              'shareholder letter · As of Dec 31, 2025 · Caydex · Not affiliated with anyone '
                              'named'},
     'image_post': {'title': "Berkshire Hathaway's stake in ITOCHU Corporation",
                    'paragraphs': ['The image shows Berkshire Hathaway and ITOCHU Corporation, with $8.9B labelled '
                                   'fair value.',
                                   'Source: Berkshire Hathaway 2025 shareholder letter. As of Dec 31, 2025.']},
     'image_footer': 'Educational only · not investment advice · Source: Berkshire Hathaway 2025 shareholder '
                     'letter · As of Dec 31, 2025 · Caydex · Not affiliated with anyone named',
     'persons': [],
     'logo_refs': [{'key': 'BRK-B', 'name': 'Berkshire Hathaway'}],
     'source_ref': 'news:company_stakes:9b1d3f5a-7c9e-4b2d-8f0a-1c3e5a7b9d05',
     'x': 'Berkshire Hathaway reported its ITOCHU Corporation stake at a fair value of $8.9 billion, as of Dec 31, '
          '2025.',
     'bluesky': "Berkshire Hathaway's ITOCHU Corporation stake had a fair value of $8.9 billion, as of Dec 31, "
                '2025.',
     # news-v11 (review round 11): the headline cites the source, so `st.p.source` is not said again
     'linkedin': 'Berkshire Hathaway reported its ITOCHU Corporation stake at a fair value of $8.9 billion, as of '
                 'Dec 31, 2025. Source: Berkshire Hathaway 2025 shareholder letter.\n'
                 '\n'
                 'Cost basis was $4.17B, per the letter.\n'
                 '\n'
                 'Fair value is the amount the source says the stake was measured at on that date.\n'
                 '\n'
                 'As of that date, shares of ITOCHU Corporation traded on an exchange outside the U.S.',
     'tiktok': 'Berkshire Hathaway reported its ITOCHU Corporation stake at a fair value of $8.9 billion, as of '
               'Dec 31, 2025. Source: Berkshire Hathaway 2025 shareholder letter.\n'
               '\n'
               'Cost basis was $4.17B, per the letter.',
     'youtube_title': 'Berkshire Hathaway reported its ITOCHU Corporation stake at a fair value of $8.9B'},
    'stake_intel_us': {'hook': 'Nvidia invested $5 billion in Intel.',
     'video_script': ['The figure is as of December 26th, 2025, from the source named in this post.',
                      'That is the amount the source reports as invested, not a figure for today.',
                      'Shares of Intel are listed on a U.S. exchange.',
                      'Companies disclose stakes like this in filings and official releases.'],
     'cards': [{'title': 'As of', 'body': 'Dec 26, 2025'},
               {'title': 'Invested', 'body': 'The amount put in, not a figure for today'},
               {'title': 'Listed in the U.S.', 'body': 'Shares trade on a U.S. exchange'},
               {'title': 'Source', 'body': 'Intel 8-K (Dec 29, 2025)'}],
     'opening_card': {'kicker': 'COMPANY STAKES',
                      'logos': ['NVDA'],
                      'figure': '$5B',
                      'headline': 'NVIDIA invested in Intel'},
     'image_spec': {'layout': 'pair',
                    'version': 1,
                    'kicker': 'COMPANY STAKES',
                    'left': {'logo': 'NVDA', 'name': 'NVIDIA'},
                    'right': {'name': 'Intel', 'logo': 'INTC'},
                    'figure': '$5B',
                    'label': 'invested',
                    'lines': ['As of Dec 26, 2025', 'Source: Intel 8-K (Dec 29, 2025)'],
                    'footer': 'Educational only · not investment advice · Source: Intel 8-K (Dec 29, 2025) · As of '
                              'Dec 26, 2025 · Caydex · Not affiliated with anyone named'},
     'image_post': {'title': 'NVIDIA invested $5B in Intel',
                    'paragraphs': ['The image shows NVIDIA and Intel, with $5B labelled invested.',
                                   'Source: Intel 8-K (Dec 29, 2025). As of Dec 26, 2025.']},
     'image_footer': 'Educational only · not investment advice · Source: Intel 8-K (Dec 29, 2025) · As of Dec 26, '
                     '2025 · Caydex · Not affiliated with anyone named',
     'persons': [],
     'logo_refs': [{'key': 'NVDA', 'name': 'NVIDIA'}, {'key': 'INTC', 'name': 'Intel'}],
     'source_ref': 'news:company_stakes:4e6a8c0e-2b4d-4c6e-8a0c-2e4a6c8e0a06',
     'x': 'NVIDIA invested $5 billion in Intel, as of Dec 26, 2025. Source: Intel 8-K (Dec 29, 2025).',
     'bluesky': 'NVIDIA invested $5 billion in Intel, as of Dec 26, 2025. Source: Intel 8-K (Dec 29, 2025).',
     # news-v11 (review round 11): the headline cites the source, so `st.p.source` is not said again
     'linkedin': 'NVIDIA invested $5 billion in Intel, as of Dec 26, 2025. Source: Intel 8-K (Dec 29, 2025).\n'
                 '\n'
                 'Agreed on Sep 15, 2025, alongside a product collaboration with NVIDIA.\n'
                 '\n'
                 'That is the amount the source reports as invested, not a figure for today.\n'
                 '\n'
                 'Shares of Intel are listed on a U.S. exchange.',
     'tiktok': 'NVIDIA invested $5 billion in Intel, as of Dec 26, 2025. Source: Intel 8-K (Dec 29, 2025).\n'
               '\n'
               'Agreed on Sep 15, 2025, alongside a product collaboration with NVIDIA.',
     'youtube_title': 'NVIDIA invested $5B in Intel'},
    'earnings_loss': {'hook': 'Northwind Fitness reported negative $0.05 a share; analysts estimated negative $0.12.',
     'video_script': ['These figures cover the quarter that ended September 30th, reported on November 5th.',
                      'Revenue was $551.9 million, against an analyst estimate of $543.6 million.',
                      'The estimate is the analyst consensus; the reported figure can be on a different basis '
                      'from the official accounting result.',
                      'An analyst estimate is the average figure analysts published before the report.'],
     'cards': [{'title': 'Quarter ended', 'body': 'Sep 30, 2026 · reported Nov 5, 2026'},
               {'title': 'Revenue', 'body': '$551.9M vs an estimate of $543.6M'},
               {'title': 'About the figures', 'body': 'The consensus basis can differ from the official accounts'},
               {'title': 'The estimate', 'body': 'The average analyst figure published before the report'}],
     'opening_card': {'kicker': 'EARNINGS VS ESTIMATES',
                      'logos': ['NWFT'],
                      'chip': 'NWFT',
                      'figure': '-$0.05',
                      'headline': 'EPS vs an analyst estimate of -$0.12'},
     'image_spec': {'layout': 'rows',
                    'version': 1,
                    'kicker': 'EARNINGS VS ESTIMATES',
                    'header': {'logo': 'NWFT', 'name': 'Northwind Fitness', 'chip': 'NWFT'},
                    'title': 'Reported vs analyst estimates',
                    'subtitle': 'Quarter ended Sep 30, 2026 · reported Nov 5, 2026',
                    'sections': [{'rows': [{'cells': ['EPS', 'vs -$0.12 estimate', '-$0.05']},
                                           {'cells': ['Revenue', 'vs $543.6M estimate', '$551.9M']}]}],
                    'notes': ['The estimate is the analyst consensus; its basis can differ from the official '
                              'accounts.'],
                    'footer': 'Educational only · not investment advice · Source: company results and analyst '
                              'consensus · Reported Nov 5, 2026 · Caydex · Not affiliated with anyone named'},
     'image_post': {'title': 'Northwind Fitness: reported vs analyst estimates',
                    'paragraphs': ['The image shows EPS of -$0.05 against an analyst estimate of -$0.12. It shows '
                                   'revenue of $551.9M against an estimate of $543.6M.',
                                   'Source: company results and analyst consensus. Reported Nov 5, 2026.']},
     'image_footer': 'Educational only · not investment advice · Source: company results and analyst consensus · '
                     'Reported Nov 5, 2026 · Caydex · Not affiliated with anyone named',
     'persons': [],
     'logo_refs': [{'key': 'NWFT', 'name': 'Northwind Fitness'}],
     'source_ref': 'news:earnings:NWFT:2026-11-05',
     'x': 'Northwind Fitness: EPS of -$0.05 vs an analyst estimate of -$0.12; its basis can differ from official '
          'accounts.',
     'bluesky': 'Northwind Fitness reported EPS of -$0.05 vs an analyst estimate of -$0.12.',
     # news-v10 (review round 10): the long headline states the revenue pair, so no paragraph repeats it;
     # news-v11 (review round 11): nor the basis note its ".basis" headline states (the definition stays)
     'linkedin': 'Northwind Fitness reported EPS of -$0.05 vs an analyst estimate of -$0.12, and revenue of '
                 '$551.9 million vs $543.6 million. The EPS basis can differ from the official accounts.\n'
                 '\n'
                 'These figures cover the quarter that ended Sep 30, reported on Nov 5.\n'
                 '\n'
                 'An analyst estimate is the average figure analysts published before the report.',
     'tiktok': 'Northwind Fitness reported EPS of -$0.05 vs an analyst estimate of -$0.12, and revenue of $551.9 '
               'million vs $543.6 million. The EPS basis can differ from the official accounts.\n'
               '\n'
               'These figures cover the quarter that ended Sep 30, reported on Nov 5.',
     'youtube_title': 'Northwind Fitness results vs estimates: EPS -$0.05 vs -$0.12'},
    'earnings_profit': {'hook': "Fabrikam reported $4.86 a share on the analysts' basis; the estimate was $4.50.",
     'video_script': ['The company reported these figures on November 10th, after analysts published their '
                      'estimate.',
                      'An analyst estimate is the average figure analysts published before the report.',
                      'The estimate is the analyst consensus; the reported figure can be on a different basis '
                      'from the official accounting result.',
                      'Companies report results each quarter, and analysts publish their estimates before each '
                      'report.'],
     'cards': [{'title': 'Reported', 'body': 'Nov 10, 2026'},
               {'title': 'The estimate', 'body': 'The average analyst figure published before the report'},
               {'title': 'About the figures', 'body': 'The consensus basis can differ from the official accounts'},
               {'title': 'Each quarter', 'body': 'Results are reported; analysts publish estimates first'}],
     'opening_card': {'kicker': 'EARNINGS VS ESTIMATES',
                      'logos': ['FBKM'],
                      'chip': 'FBKM',
                      'figure': '$4.86',
                      'headline': 'EPS vs an analyst estimate of $4.50'},
     'image_spec': {'layout': 'rows',
                    'version': 1,
                    'kicker': 'EARNINGS VS ESTIMATES',
                    'header': {'logo': 'FBKM', 'name': 'Fabrikam', 'chip': 'FBKM'},
                    'title': 'Reported vs analyst estimates',
                    'subtitle': 'Reported Nov 10, 2026',
                    'sections': [{'rows': [{'cells': ['EPS', 'vs $4.50 estimate', '$4.86']}]}],
                    'notes': ['The estimate is the analyst consensus; its basis can differ from the official '
                              'accounts.'],
                    'footer': 'Educational only · not investment advice · Source: company results and analyst '
                              'consensus · Reported Nov 10, 2026 · Caydex · Not affiliated with anyone named'},
     'image_post': {'title': 'Fabrikam: reported vs analyst estimates',
                    'paragraphs': ['The image shows EPS of $4.86 against an analyst estimate of $4.50.',
                                   'Source: company results and analyst consensus. Reported Nov 10, 2026.']},
     'image_footer': 'Educational only · not investment advice · Source: company results and analyst consensus · '
                     'Reported Nov 10, 2026 · Caydex · Not affiliated with anyone named',
     'persons': [],
     'logo_refs': [{'key': 'FBKM', 'name': 'Fabrikam'}],
     'source_ref': 'news:earnings:FBKM:2026-11-10',
     'x': 'Fabrikam reported EPS of $4.86 vs an analyst estimate of $4.50. The EPS basis can differ from the '
          'official accounts.',
     'bluesky': 'Fabrikam: EPS of $4.86 vs an analyst estimate of $4.50; its basis can differ from official '
                'accounts.',
     'linkedin': 'Fabrikam reported EPS of $4.86 vs an analyst estimate of $4.50. The EPS basis can differ from '
                 'the official accounts.\n'
                 '\n'
                 'The company reported these figures on Nov 10.\n'
                 '\n'
                 'An analyst estimate is the average figure analysts published before the report.',
     'tiktok': 'Fabrikam reported EPS of $4.86 vs an analyst estimate of $4.50. The EPS basis can differ from the '
               'official accounts.\n'
               '\n'
               'The company reported these figures on Nov 10.',
     'youtube_title': 'Fabrikam results vs estimates: EPS $4.86 vs $4.50'},
    'theme_ai_chips': {'hook': 'Where do 14 companies in AI chips get their revenue?',
     'video_script': ["Nvidia's largest revenue segment was Data Center, and Broadcom's was Semiconductor "
                      'Solutions.',
                      "AMD's largest revenue segment was Data Center, and Marvell Technology's was Data Center.",
                      "Qualcomm's largest revenue segment was QCT, and Arm Holdings' was Royalty.",
                      "This grouping is Caydex's own, based on each company's business."],
     'cards': [{'title': 'NVIDIA and Broadcom', 'body': 'Data Center · Semiconductor Solutions'},
               {'title': 'AMD and Marvell Technology', 'body': 'Data Center · Data Center'},
               {'title': 'Qualcomm and Arm Holdings', 'body': 'QCT · Royalty'},
               {'title': 'A Caydex grouping', 'body': "Based on each company's business"}],
     'opening_card': {'kicker': 'INSIDE A THEME',
                      'logos': ['NVDA'],
                      'figure': '14',
                      'headline': 'companies in AI chips, by largest revenue segment'},
     'image_spec': {'layout': 'grid',
                    'version': 1,
                    'kicker': 'INSIDE A THEME',
                    'title': 'AI chips',
                    'subtitle': '14 companies, by largest revenue segment',
                    'tiles': [{'logo': 'NVDA', 'name': 'NVIDIA', 'line': 'Data Center · 88%'},
                              {'logo': 'AVGO', 'name': 'Broadcom', 'line': 'Semiconductor Solutions · 58%'},
                              {'logo': 'AMD', 'name': 'AMD', 'line': 'Data Center · 48%'},
                              {'logo': 'MRVL', 'name': 'Marvell Technology', 'line': 'Data Center · 74%'},
                              {'logo': 'QCOM', 'name': 'Qualcomm', 'line': 'QCT · 84%'},
                              {'logo': 'ARM', 'name': 'Arm Holdings', 'line': 'Royalty'},
                              {'logo': 'MU', 'name': 'Micron Technology', 'line': 'Compute and Networking · 43%'},
                              {'logo': 'INTC', 'name': 'Intel'},
                              {'logo': 'TXN', 'name': 'Texas Instruments', 'line': 'Analog · 77%'},
                              {'logo': 'ADI', 'name': 'Analog Devices', 'line': 'Industrial · 45%'},
                              {'logo': 'SNPS', 'name': 'Synopsys'},
                              {'logo': 'CDNS', 'name': 'Cadence Design Systems', 'line': 'Core EDA · 70%'}],
                    'more': '+2 more',
                    'footer': 'Educational only · not investment advice · Source: company segment reporting; '
                              'grouping by Caydex · Members as of Nov 1, 2026 · Caydex · Not affiliated with '
                              'anyone named'},
     'image_post': {'title': 'AI chips',
                    'paragraphs': ['The image shows 12 of the 14 companies in AI chips. It gives the largest '
                                   'revenue segment for 10 of them.',
                                   'Shown: NVIDIA; Broadcom; AMD; Marvell Technology; Qualcomm; Arm Holdings; '
                                   'Micron Technology; Intel; Texas Instruments; Analog Devices; Synopsys; Cadence '
                                   'Design Systems.',
                                   'Source: company segment reporting; grouping by Caydex. Members as of Nov 1, '
                                   '2026.']},
     'image_footer': 'Educational only · not investment advice · Source: company segment reporting; grouping by '
                     'Caydex · Members as of Nov 1, 2026 · Caydex · Not affiliated with anyone named',
     'persons': [],
     'logo_refs': [{'key': 'NVDA', 'name': 'NVIDIA'},
                   {'key': 'AVGO', 'name': 'Broadcom'},
                   {'key': 'AMD', 'name': 'AMD'},
                   {'key': 'MRVL', 'name': 'Marvell Technology'},
                   {'key': 'QCOM', 'name': 'Qualcomm'},
                   {'key': 'ARM', 'name': 'Arm Holdings'},
                   {'key': 'MU', 'name': 'Micron Technology'},
                   {'key': 'INTC', 'name': 'Intel'},
                   {'key': 'TXN', 'name': 'Texas Instruments'},
                   {'key': 'ADI', 'name': 'Analog Devices'},
                   {'key': 'SNPS', 'name': 'Synopsys'},
                   {'key': 'CDNS', 'name': 'Cadence Design Systems'}],
     'source_ref': 'news:theme_explainer:ai-chips:2026-11-01',
     'x': 'AI chips: where 14 companies get their revenue, by largest reported segment.',
     'bluesky': 'AI chips: where 14 companies get their revenue, by largest reported segment.',
     'linkedin': 'AI chips: where 14 companies get their revenue, by largest reported segment.\n'
                 '\n'
                 'The 14 companies in AI chips: NVIDIA; Broadcom; AMD; Marvell Technology; Qualcomm; Arm Holdings; '
                 'Micron Technology; Intel; Texas Instruments; Analog Devices; Synopsys; Cadence Design Systems; '
                 'Lam Research; KLA.\n'
                 '\n'
                 'Largest revenue segments, as each company reports them: NVIDIA, Data Center, 88% of revenue; '
                 'Broadcom, Semiconductor Solutions, 58% of revenue; AMD, Data Center, 48% of revenue; Marvell '
                 'Technology, Data Center, 74% of revenue; Qualcomm, QCT, 84% of revenue; Arm Holdings, Royalty; '
                 'Micron Technology, Compute and Networking, 43% of revenue; Texas Instruments, Analog, 77% of '
                 'revenue; Analog Devices, Industrial, 45% of revenue; Cadence Design Systems, Core EDA, 70% of '
                 'revenue; Lam Research, Systems, 66% of revenue.\n'
                 '\n'
                 "This grouping is Caydex's own, based on each company's business.",
     'tiktok': 'AI chips: where 14 companies get their revenue, by largest reported segment.\n'
               '\n'
               'The 14 companies in AI chips: NVIDIA; Broadcom; AMD; Marvell Technology; Qualcomm; Arm Holdings; '
               'Micron Technology; Intel; Texas Instruments; Analog Devices; Synopsys; Cadence Design Systems; Lam '
               'Research; KLA.',
     'youtube_title': 'AI chips: where 14 companies get their revenue'},
    'theme_ai_chips_of_more': {'hook': 'Where do 14 of the 16 companies in AI chips get their revenue?',
     'video_script': ["Nvidia's largest revenue segment was Data Center, and Broadcom's was Semiconductor "
                      'Solutions.',
                      "AMD's largest revenue segment was Data Center, and Marvell Technology's was Data Center.",
                      "Qualcomm's largest revenue segment was QCT, and Arm Holdings' was Royalty.",
                      "This grouping is Caydex's own, based on each company's business."],
     'cards': [{'title': 'NVIDIA and Broadcom', 'body': 'Data Center · Semiconductor Solutions'},
               {'title': 'AMD and Marvell Technology', 'body': 'Data Center · Data Center'},
               {'title': 'Qualcomm and Arm Holdings', 'body': 'QCT · Royalty'},
               {'title': 'A Caydex grouping', 'body': "Based on each company's business"}],
     'opening_card': {'kicker': 'INSIDE A THEME',
                      'logos': ['NVDA'],
                      'figure': '14',
                      'headline': 'of the 16 companies in AI chips, by largest revenue segment'},
     'image_spec': {'layout': 'grid',
                    'version': 1,
                    'kicker': 'INSIDE A THEME',
                    'title': 'AI chips',
                    'subtitle': '14 of its 16 companies, by largest revenue segment',
                    'tiles': [{'logo': 'NVDA', 'name': 'NVIDIA', 'line': 'Data Center · 88%'},
                              {'logo': 'AVGO', 'name': 'Broadcom', 'line': 'Semiconductor Solutions · 58%'},
                              {'logo': 'AMD', 'name': 'AMD', 'line': 'Data Center · 48%'},
                              {'logo': 'MRVL', 'name': 'Marvell Technology', 'line': 'Data Center · 74%'},
                              {'logo': 'QCOM', 'name': 'Qualcomm', 'line': 'QCT · 84%'},
                              {'logo': 'ARM', 'name': 'Arm Holdings', 'line': 'Royalty'},
                              {'logo': 'MU', 'name': 'Micron Technology', 'line': 'Compute and Networking · 43%'},
                              {'logo': 'INTC', 'name': 'Intel'},
                              {'logo': 'TXN', 'name': 'Texas Instruments', 'line': 'Analog · 77%'},
                              {'logo': 'ADI', 'name': 'Analog Devices', 'line': 'Industrial · 45%'},
                              {'logo': 'SNPS', 'name': 'Synopsys'},
                              {'logo': 'CDNS', 'name': 'Cadence Design Systems', 'line': 'Core EDA · 70%'}],
                    'more': '+2 more',
                    'footer': 'Educational only · not investment advice · Source: company segment reporting; '
                              'grouping by Caydex · Members as of Nov 1, 2026 · Caydex · Not affiliated with '
                              'anyone named'},
     'image_post': {'title': 'AI chips',
                    'paragraphs': ['The image shows 12 of the 16 companies in AI chips. It gives the largest '
                                   'revenue segment for 10 of them.',
                                   'Shown: NVIDIA; Broadcom; AMD; Marvell Technology; Qualcomm; Arm Holdings; '
                                   'Micron Technology; Intel; Texas Instruments; Analog Devices; Synopsys; Cadence '
                                   'Design Systems.',
                                   'Source: company segment reporting; grouping by Caydex. Members as of Nov 1, '
                                   '2026.']},
     'image_footer': 'Educational only · not investment advice · Source: company segment reporting; grouping by '
                     'Caydex · Members as of Nov 1, 2026 · Caydex · Not affiliated with anyone named',
     'persons': [],
     'logo_refs': [{'key': 'NVDA', 'name': 'NVIDIA'},
                   {'key': 'AVGO', 'name': 'Broadcom'},
                   {'key': 'AMD', 'name': 'AMD'},
                   {'key': 'MRVL', 'name': 'Marvell Technology'},
                   {'key': 'QCOM', 'name': 'Qualcomm'},
                   {'key': 'ARM', 'name': 'Arm Holdings'},
                   {'key': 'MU', 'name': 'Micron Technology'},
                   {'key': 'INTC', 'name': 'Intel'},
                   {'key': 'TXN', 'name': 'Texas Instruments'},
                   {'key': 'ADI', 'name': 'Analog Devices'},
                   {'key': 'SNPS', 'name': 'Synopsys'},
                   {'key': 'CDNS', 'name': 'Cadence Design Systems'}],
     'source_ref': 'news:theme_explainer:ai-chips:2026-11-01',
     'x': 'AI chips: where 14 of its 16 companies get their revenue, by largest reported segment.',
     'bluesky': 'AI chips: where 14 of its 16 companies get their revenue, by largest reported segment.',
     'linkedin': 'AI chips: where 14 of its 16 companies get their revenue, by largest reported segment.\n'
                 '\n'
                 '14 of the 16 companies in AI chips: NVIDIA; Broadcom; AMD; Marvell Technology; Qualcomm; Arm '
                 'Holdings; Micron Technology; Intel; Texas Instruments; Analog Devices; Synopsys; Cadence Design '
                 'Systems; Lam Research; KLA.\n'
                 '\n'
                 'Largest revenue segments, as each company reports them: NVIDIA, Data Center, 88% of revenue; '
                 'Broadcom, Semiconductor Solutions, 58% of revenue; AMD, Data Center, 48% of revenue; Marvell '
                 'Technology, Data Center, 74% of revenue; Qualcomm, QCT, 84% of revenue; Arm Holdings, Royalty; '
                 'Micron Technology, Compute and Networking, 43% of revenue; Texas Instruments, Analog, 77% of '
                 'revenue; Analog Devices, Industrial, 45% of revenue; Cadence Design Systems, Core EDA, 70% of '
                 'revenue; Lam Research, Systems, 66% of revenue.\n'
                 '\n'
                 "This grouping is Caydex's own, based on each company's business.",
     'tiktok': 'AI chips: where 14 of its 16 companies get their revenue, by largest reported segment.\n'
               '\n'
               '14 of the 16 companies in AI chips: NVIDIA; Broadcom; AMD; Marvell Technology; Qualcomm; Arm '
               'Holdings; Micron Technology; Intel; Texas Instruments; Analog Devices; Synopsys; Cadence Design '
               'Systems; Lam Research; KLA.',
     'youtube_title': 'AI chips: where 14 of its 16 companies get their revenue'},
}


@pytest.mark.parametrize("name, factory, run_date", SAMPLES, ids=[s[0] for s in SAMPLES])
def test_golden_packages(name, factory, run_date):
    out = compose(factory(), run_date)
    golden = {**GOLDEN, **GOLDEN_2B}[name]
    for key, value in golden.items():
        got = out["captions"][key] if key in ("x", "bluesky", "linkedin", "tiktok", "youtube_title") else out[key]
        assert got == value, f"{name}.{key}"


def test_every_sample_has_exactly_one_golden():
    assert set(GOLDEN) | set(GOLDEN_2B) == {s[0] for s in SAMPLES} and not set(GOLDEN) & set(GOLDEN_2B)


@pytest.mark.parametrize("name, factory, run_date", SAMPLES, ids=[s[0] for s in SAMPLES])
def test_every_sample_composes_a_valid_deterministic_json_package(name, factory, run_date):
    a, b = compose(factory(), run_date), compose(factory(), run_date)
    assert a == b
    assert json.loads(json.dumps(a, allow_nan=False)) == a
    assert T.validate_package(a, record=factory()) == []
    assert T.validate_package(a) == []          # without the record: stricter, still clean
    assert a["authorship"] == "template" and a["video_layout"] == "per_line"
    assert a["template_version"] == T.TEMPLATE_VERSION == "news-v11"
    assert a["content_class"] == R.SERIES_CLASS[a["series"]] == selection.SERIES_BY_ID[a["series"]].content_class
    assert a["source_ref"] == R.ledger_key(factory())
    assert a["carousel_slides"] == [] and a["dropped_outlets"] == {}
    assert sorted(a["posts"]) == sorted(post_copy.PLATFORMS)
    assert a["disclaimer_card"] == post_copy.disclaimer_card(run_date, "template")


# ── the lexicon ───────────────────────────────────────────────────────────────


def test_the_lexicon_is_pinned_verbatim_and_versioned():
    doc = json.loads(LEXICON_PATH.read_text(encoding="utf-8"))
    assert set(doc) == {"_about", "version", "entries"}
    assert doc["version"] == T.TEMPLATE_VERSION == "news-v11"
    assert doc["entries"] == T.LEXICON
    assert list(doc["entries"]) == list(T.LEXICON)


#: A neutral value for every slot the lexicon uses (a test fails on a slot without one).
NEUTRAL = dict(
    a="Northwind", b="Fabrikam", c="Contoso", k=2, m=2, n=3, source="SEC Form 4 filings",
    as_of="Filed Nov 9–15, 2026", co="Northwind", co_s="Northwind's", co_sp="Northwind", co_sp_s="Northwind's",
    role="CEO", role_sp="chief executive", roles="CEOs", roles_sp="chief executives", role_np="the CEO",
    role_np_sp="the company's chief executive", person="Alex Morgan", subj="Northwind's CEO",
    subj_sp="Northwind's chief executive", amt="$2.5 million", amt_s="$2.5M", shares_w="about 48,000",
    shares_s="about 48K", share_noun="shares", k_txt="2 purchases", filed_sp="November 10th", first_sp="November 10th",
    last_sp="November 12th", window_upper="NOV 9–15", window_img="Nov 9–15, 2026", window_cap="Nov 9-15",
    filed_cap="Nov 10", filed_window_cap="Nov 10-12", filed_window_img="Nov 10–12, 2026",
    rows="Northwind, CEO, $2.5 million", filer="Northwind Capital", filer_s="Northwind Capital's",
    filer_sp="Northwind Capital", filer_sp_s="Northwind Capital's", holdings="holdings",
    period_img="Sep 30, 2026", filed_img="Nov 13, 2026", amended_img="Nov 20, 2026",
    period_sp="September 30th", amended_sp="November 20th", list="Northwind, Fabrikam and Contoso",
    month_sp="March 2025", month_img="Mar 2025", month_cap="March 2025", heading="Newly reported",
    items="Northwind; Fabrikam", counts="3 newly reported holdings, 2 no longer reported",
    lead="3 newly reported holdings", period_cap="Sep 30", value="$1.2 billion", fy="2025",
    per100="$2.94", rev="$275.2 billion", rev_s="$275.2B", s1="Retail", s2="Services", sh1="57%",
    sh2="14%", basis="revenue", gp_word="profit", gp="$35.4 billion", gp_s="$35.4B", op_word="profit",
    op="$10.4 billion", op_s="$10.4B", ni="$8.1 billion", ni_s="$8.1B", elim="$4.3 billion", name="Retail",
    label="Revenue", callout="For every $100 of revenue, $2.94 was net income",
    # drop 2b: congress_count, company_stakes, earnings, theme_explainer
    month_upper="SEPTEMBER", month_long="September 2026", month_name="September", month_short="Sep 2026",
    as_of_img="Oct 13, 2026", as_of_sp="October 13th", as_of_cap="Oct 13", inv="Northwind", inv_s="Northwind's",
    ee="Fabrikam", h="Northwind invested $2.5 million in Fabrikam", eps="$1.25", est="$1.10", eps_s="$1.25",
    est_s="$1.10", ra="$551.9 million", re="$543.6 million", ra_s="$551.9M", re_s="$543.6M",
    report_sp="November 5th", report_img="Nov 5, 2026", report_cap="Nov 5", x="$1.10", theme="Cloud software",
    m1="Northwind", m1_s="Northwind's", m2="Fabrikam", m2_s="Fabrikam's", g1="Retail", g2="Services",
    seg="Retail", share="57%", names="Northwind; Fabrikam; Contoso", tickers_img="Nov 1, 2026", j=2,
)
#: The 13F subjects are the filer's latest 13F, not a person (their own neutral fills).
NEUTRAL_13F = dict(NEUTRAL, subj="Northwind Capital's latest 13F", subj_sp="Northwind Capital's latest 13F")


def neutral(entry_id):
    fills = NEUTRAL_13F if entry_id.startswith("f13.") else NEUTRAL
    return T.LEXICON[entry_id].format_map(fills)


def slots_of(text):
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


def test_every_slot_has_a_neutral_fill_and_every_entry_renders():
    used = set().union(*(slots_of(v) for v in T.LEXICON.values()))
    assert used <= set(NEUTRAL), sorted(used - set(NEUTRAL))
    for entry_id in T.LEXICON:
        assert clean(neutral(entry_id)) == neutral(entry_id), entry_id


@pytest.mark.parametrize("entry_id", sorted(T.LEXICON))
def test_every_entry_passes_the_banned_forecast_news_and_structure_rules(entry_id):
    text = neutral(entry_id)
    assert not copy_rules.contains_banned_copy(text), entry_id
    assert not copy_rules.contains_forecast(text), entry_id
    assert not T.NEWS_BANNED_RE.search(text), entry_id
    assert not T.OFF_TABLE_VERBS_RE.search(text), entry_id
    assert "!" not in text and "#" not in text and "@" not in text
    # the one "%" that stands alone: a grid tile's share cell ("Retail · 57%", templates design §4.5)
    assert T._percent_ok(text) or (entry_id == "th.img.line.share" and T._SHARE_CELL_RE.fullmatch(text)), entry_id
    assert not T._CASHTAG_RE.search(text), entry_id
    assert R.congress_name_hits(text) == [], entry_id                     # the Congress roster scan
    # the 2b series' own word rules hold on every entry of the series
    family = entry_id.split(".")[0]
    if family == "cg":
        assert not T.CONGRESS_NARROWING_RE.search(text) and not T.CONGRESS_OFF_VERBS_RE.search(text), entry_id
        assert "$" not in text, entry_id
    if family == "er":
        assert not T.EARNINGS_BANNED_RE.search(text), entry_id
    if family == "st":
        assert not T.STAKES_BANNED_RE.search(text), entry_id


#: The ONLY codes `compliance.scan_text` may find on a neutral rendering, per entry: `person_named`
#: where the entry is (or carries) a role descriptor ("Northwind's CEO", "the chief executive") or a
#: person slot — the template's design is a role in the headline and a name only in the body, and
#: placement (A7) keeps the name out of the headline. Mutation-checked by hand (2026-10-09): adding
#: "a legendary investor" to "mm.l1" and "guaranteed" to "f13.l1" each turned this test red.
PERSON_NAMED_IDS = frozenset({
    "role.ceo.label", "role.ceo.word", "role.ceo.np", "role.ceo.np.sp", "role.ceo.sp",
    "role.cfo.label", "role.cfo.word", "role.cfo.np", "role.cfo.np.sp", "role.cfo.sp",
    "ins.subj.poss", "ins.subj.of", "ins.subj_sp.poss", "ins.subj_sp.of",
    "ins.hook", "ins.hook.short", "ins.hook.min",
    "ins.l1.role.one", "ins.l1.role.many", "ins.l1.role.short.one", "ins.l1.role.short.many",
    "ins.l3.indirect.all", "ins.l4.more.one",
    "ins.c1.noname.officer", "ins.c3.indirect.all.body", "ins.c4.more.one",
    "ins.open.head", "ins.open.head.min",
    "ins.ch.one.long.one", "ins.ch.one.long.many", "ins.ch.one.short", "ins.ch.one.min",
    "ins.p.rows", "ins.p.rows2", "ins.row.name", "ins.row.noname",
    "ins.p.one.name.one", "ins.p.one.name.many", "ins.p.one.noname.one", "ins.p.one.noname.many",
    "ins.vp.more.one", "ins.yt.long", "ins.yt.short", "ins.yt.min", "ins.alt.row", "ins.alt.rows",
})
#: `class_b_valuation` only on the stake's FAIR-VALUE entries (contract D7): the investor's own
#: accounting measure of its stake, as the source reports it — never Caydex's value of a stock.
CLASS_B_VALUATION_IDS = frozenset({"st.h.fair_value", "st.h.fair_value.short.poss", "st.h.fair_value.short.of",
                                   "st.l2.fair_value", "st.label.fair_value",
                                   "st.c2.fair_value.title"})
#: `brand_mention` only on the theme's grouping lines: the grouping IS Caydex's, and says so.
BRAND_MENTION_IDS = frozenset({"th.grouping", "th.c.grouping.title"})


@pytest.mark.parametrize("entry_id", sorted(T.LEXICON))
def test_the_public_copy_scan_finds_only_the_exempted_codes(entry_id):
    codes = {v.code for v in scan_text(entry_id, neutral(entry_id))}
    allowed = ({"person_named"} if entry_id in PERSON_NAMED_IDS else set()) | (
        {"class_b_valuation"} if entry_id in CLASS_B_VALUATION_IDS else set()) | (
        {"brand_mention"} if entry_id in BRAND_MENTION_IDS else set())
    assert codes <= allowed, (entry_id, codes)


def test_the_2b_exemption_tables_are_exact():
    """Every exempted 2b id really draws its code (no stale exemption that would hide a new hit).
    Mutation-checked by hand (2026-10-10, in memory): "worth" in `er.l2.revenue` turned the public-copy
    scan red; "a bargain" in `st.l4` turned the banned-word test red (scan_text has no row for it)."""
    for ids, code in ((CLASS_B_VALUATION_IDS, "class_b_valuation"), (BRAND_MENTION_IDS, "brand_mention")):
        for entry_id in ids:
            assert code in {v.code for v in scan_text(entry_id, neutral(entry_id))}, entry_id


def test_the_news_ban_and_the_verb_table_catch_what_they_name():
    for word in ("smart money", "followers", "copying", "signals", "shocker", "BREAKING", "just in", "alert",
                 "whale", "legendary", "massive", "soared", "plunged", "skyrocketing", "crushed", "beats",
                 "missed", "record", "all-time", "undervalued", "cheapest", "bargain", "upside", "target",
                 "rallied", "surged", "tanked", "price", "bought", "sold", "buys", "sells", "insider trading",
                 "exclusive", "premium", "act now", "don't miss", "opportunity"):
        assert T.NEWS_BANNED_RE.search(f"It {word} here"), word
    for clean_word in ("followed", "recorded", "Targeted", "pricey", "missions", "beaten"):
        assert not T.NEWS_BANNED_RE.search(f"It {clean_word} here"), clean_word
    for verb in ("bought", "buys", "purchased", "sold", "selling", "added", "trimmed", "cut", "exited", "dumped",
                 "loaded", "initiated", "acquired", "new position", "buying"):
        assert T.OFF_TABLE_VERBS_RE.search(f"The CEO {verb} shares"), verb
    assert not T.OFF_TABLE_VERBS_RE.search("The CEO disclosed buying shares in two purchases")


def test_a_form4_purchase_is_never_called_open_market():
    """compliance:F2 — SEC transaction code P is "open market OR private purchase" and the feed
    carries no footnote that tells them apart (a PIPE / private placement is code P too): no entry
    may claim "open-market", the narration says "purchase(s)" and the verb stays "disclosed
    buying". Mutation-checked by hand (2026-10-09): restoring "open-market" in `ins.l2.one.one`
    turns this red."""
    for entry_id, text in T.LEXICON.items():
        assert not re.search(r"open[\s-]*market", text, re.IGNORECASE), entry_id
    for name, factory, run_date in SAMPLES:
        if factory().series not in ("ceo_buys", "insider_buys"):
            continue
        out = compose(factory(), run_date)
        assert not re.search(r"open[\s-]*market", json.dumps(out), re.IGNORECASE), name
        assert re.search(r"\b(?:one purchase|\d+ purchases)\b", out["video_script"][1]), name
        assert "disclosed buying" in out["hook"], name


# ── the shipped series ────────────────────────────────────────────────────────


#: Contract D9 "Layout per series".
SERIES_LAYOUT = {"ceo_buys": "rows", "insider_buys": "rows", "thirteen_f": "rows", "congress_count": "spotlight",
                 "company_stakes": "pair", "earnings": "rows", "money_map": "bars", "theme_explainer": "grid"}


def test_series_specs_cover_every_series_and_every_shipped_one():
    """Every series is composed in code (drop 2b ships the four 2b templates); a run reaches one only
    once it is in SHIPPED_SERIES (and the per-series switch), so the shipped set is a subset — and
    a shipped series' layout is shipped (`test_marketing_layouts_2b` pins both sides of that)."""
    assert list(T.SERIES_SPECS) == list(R.NEWS_SERIES) == [s.id for s in selection.SERIES]
    assert set(selection.SHIPPED_SERIES) <= set(T.SERIES_SPECS)
    for sid, spec in T.SERIES_SPECS.items():
        assert spec.series == sid and spec.category == f"news:{sid}"
        assert spec.content_class == selection.SERIES_BY_ID[sid].content_class == R.SERIES_CLASS[sid]
        assert spec.layout == SERIES_LAYOUT[sid] and spec.layout in onscreen.LAYOUTS
        if sid in selection.SHIPPED_SERIES:
            assert spec.layout in REAL_SHIPPED_LAYOUTS
        assert post_copy.hashtags_for("x", spec.category) != ["#investing"]   # a news tag exists
        assert onscreen.drawable_problem(spec.kicker) is None
    assert set(T._BUILDERS) == set(T.SERIES_SPECS)


def test_refusal_codes_and_the_exception():
    assert T.REFUSAL_CODES == ("record_invalid", "stale_source", "too_few_rows", "implausible_figures",
                               "slot_rejected", "congress_name", "placement", "script_shape",
                               "too_few_outlets", "image_spec_invalid")
    e = T.NewsTemplateRefused("x")          # the classifier walk constructs every class with one arg
    assert e.code == "x" and e.detail == "" and isinstance(e, ValueError)
    assert set(T._REFUSAL_OF.values()) <= set(T.REFUSAL_CODES)


def test_the_shape_constants_fit_the_writer_and_worker_limits():
    assert T.HOOK_WORDS == (5, 14) and T.LINE_WORDS == (8, 20) and T.NARRATION_WORDS == (45, 75)
    assert T.HOOK_WORDS[1] <= wp.HOOK_MAX_WORDS
    assert T.LINE_WORDS[1] <= wp.SCRIPT_LINE_MAX_WORDS
    assert T.CARD_TITLE_MAX_WORDS <= wp.CARD_TITLE_MAX_WORDS and T.CARD_BODY_MAX_WORDS <= wp.CARD_BODY_MAX_WORDS
    assert T.NEWS_SCRIPT_LINES == 4 and T.NEWS_MIN_OUTLETS == 3


def test_the_module_is_fmp_free_in_a_fresh_interpreter():
    code = ("import sys; import app.services.marketing.news_templates; "
            "print([m for m in sys.modules if 'fmp' in m or m.startswith('app.integrations')])")
    out = subprocess.run([sys.executable, "-c", code], cwd=_BACKEND, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == "[]"


# ── numbers and dates ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("value, image, words", [
    (850, "$850", "$850"),
    (999.4, "$999", "$999"),
    (999.6, "$1K", "$1,000"),
    (100_000, "$100K", "$100,000"),
    (410_000, "$410K", "$410,000"),
    (123_456, "$123.5K", "$123,456"),
    (999_499, "$999.5K", "$999,499"),
    (999_950, "$1M", "$999,950"),
    (999_999.6, "$1M", "$1 million"),
    (1_000_000, "$1M", "$1 million"),
    (74_400_000, "$74.4M", "$74.4 million"),
    (74_450_000, "$74.5M", "$74.5 million"),          # half up, never banker's
    (777_399_382.46, "$777.4M", "$777.4 million"),
    (999_950_000, "$1B", "$1 billion"),
    (47_900_000_000, "$47.9B", "$47.9 billion"),
    (1_200_000_000_000, "$1.2T", "$1.2 trillion"),
    (-4_300_000_000, "-$4.3B", "-$4.3 billion"),
])
def test_money_formats(value, image, words):
    assert T.money_image(value) == image
    assert T.money_words(value) == words


@pytest.mark.parametrize("value, image, words", [
    (850, "850", "850"),
    (850.5, "about 851", "about 851"),
    (9_812, "9,812", "9,812"),
    (10_000, "10K", "10,000"),
    (48_321, "about 48K", "about 48,000"),
    (18_500, "about 19K", "about 19,000"),
    (999_600, "about 1M", "about 1 million"),
    (3_000_000, "3M", "3 million"),
    (2_987_654, "about 3M", "about 3 million"),
    (3_240_000, "about 3.2M", "about 3.2 million"),
    (1_500_000_000, "1.5B", "1.5 billion"),
])
def test_share_formats(value, image, words):
    assert T.shares_image(value) == image
    assert T.shares_words(value) == words


@pytest.mark.parametrize("share, out", [
    (0.0049, "less than 1%"), (0.0099, "less than 1%"), (0.01, "1%"), (0.043, "4.3%"), (0.0994, "9.9%"),
    (0.0995, "10%"), (0.5649, "56%"), (0.565, "57%"), (0.97, "97%"),
    (0.99, "99%"), (0.9949, "99%"), (0.995, "more than 99%"), (0.996, "more than 99%"),
    (0.9999, "more than 99%"), (1.0, "100%"),
])
def test_pct(share, out):
    assert T.pct(share) == out


def test_a_near_whole_segment_never_reads_100_percent_beside_less_than_1():
    """compliance:F4 — 99.6% / 0.4% used to narrate "made up 100% of revenue, and Licensing made up
    less than 1% of revenue": two figures that cannot both be true. Mutation-checked by hand
    (2026-10-09): without the "more than 99%" guard in `pct` this turns red."""
    rec = costco(segments=(R.Segment("Products", 274_100_000_000.0), R.Segment("Licensing", 1_100_000_000.0)))
    out = compose(rec, THU)
    for text in [out["video_script"][1], out["cards"][1]["body"]]:
        assert "100% of" not in text, text
        assert "more than 99% of revenue" in text and "less than 1% of revenue" in text, text
    assert out["video_script"][1] == ("Its Products segment made up more than 99% of revenue, and Licensing made up "
                                      "less than 1% of revenue.")
    assert T.validate_package(out, record=rec) == []


def test_money_cents():
    assert T.money_cents(2.943) == "$2.94" and T.money_cents(2.945) == "$2.95" and T.money_cents(-66.38) == "$66.38"
    assert T.money_cents(0) == "$0.00"


@pytest.mark.parametrize("value, out", [
    (0.0, "$0.00"), (-0.0, "$0.00"),                      # exactly zero: "$0.00" is then exact
    (1e-12, "under $0.01"), (0.004, "under $0.01"), (0.0049999, "under $0.01"),
    (-0.004, "under $0.01"),                              # magnitude only: a loss reads the same
    (0.005, "$0.01"), (0.0051, "$0.01"), (0.009, "$0.01"), (0.01, "$0.01"),
])
def test_money_cents_never_rounds_a_non_zero_amount_to_zero(value, out):
    """Review round 2 (low): a non-zero amount that rounds to $0.00 reads SUB_CENT — pct()'s rule.
    Review round 3: SUB_CENT is the two-word "under $0.01" (see the company-name matrix below).
    Mutation-checked by hand (2026-10-09): without the guard 0.004 reads "$0.00" and this fails."""
    assert T.SUB_CENT == "under $0.01"
    assert T.money_cents(value) == out


#: Costco's $275.2B revenue: 11.008M of net income is $0.004 per $100, 13.76M is exactly $0.005.
@pytest.mark.parametrize("ni, per100", [
    (4_000_000.0, "under $0.01"), (11_008_000.0, "under $0.01"), (-4_000_000.0, "under $0.01"),
    (-11_008_000.0, "under $0.01"), (13_760_000.0, "$0.01"), (-13_760_000.0, "$0.01"),
])
def test_money_map_near_break_even_never_says_zero_beside_a_non_zero_net_figure(ni, per100):
    """Review round 2 (low): $4M of net income on $275.2B of revenue used to read "kept $0.00 as
    net income" on the hook, the cover figure, the image callout and the captions while line 4 said
    "net income was $4 million" — two figures that cannot both be true. Every frame now says "under
    $0.01", still inside its limits (no outlet dropped, the hook inside HOOK_WORDS)."""
    rec = costco(net_income_usd=ni)
    out = compose(rec, THU)
    blob = json.dumps(out, ensure_ascii=False)
    assert "$0.00" not in blob and "less than $0.01" not in blob
    assert out["opening_card"]["figure"] == per100
    assert per100 in out["hook"] and per100 in out["image_spec"]["callout"]
    assert T.HOOK_WORDS[0] <= len(out["hook"].split()) <= T.HOOK_WORDS[1]
    with_figure = [k for k, text in out["captions"].items() if "$100" in text]
    assert with_figure and all(per100 in out["captions"][k] for k in with_figure), with_figure
    assert out["dropped_outlets"] == {} and sorted(out["posts"]) == sorted(post_copy.PLATFORMS)
    assert T.validate_package(out, record=rec) == [] and T.validate_package(out) == []
    assert T.revalidate(out, fact_sheet=sheet(rec), run_date=THU) == []


#: (symbol, display name, words the narration speaks for it). "&" is a word to `_words` (str.split).
#: Every name passes `company_news_rules.company_name_problem` (≤ 32 characters).
LONG_NAMES = [
    ("TPL", "Texas Pacific Land", 3),
    ("HPE", "Hewlett Packard Enterprise", 3),
    ("BBWI", "Bath & Body Works", 4),
    ("BK", "Bank of New York Mellon", 5),
    ("CBRL", "Cracker Barrel Old Country Store", 5),
    ("NTB", "Bank of N.T. Butterfield & Son", 6),
    ("FHLBB", "Federal Home Loan Bank of Boston", 6),       # 32 characters: company_name_problem's cap
]


@pytest.mark.parametrize("sign", [1.0, -1.0], ids=["profit", "loss"])
@pytest.mark.parametrize("sym, name, n_words", LONG_NAMES, ids=[n[1] for n in LONG_NAMES])
def test_money_map_sub_cent_year_composes_for_3_to_6_word_company_names(sym, name, n_words, sign):
    """Review round 3 (low): round 2's "less than $0.01" made the SHORTEST Money Map hook 11 + name
    words for a loss ("X: a net loss of less than $0.01 per $100 of revenue.") and 10 + name words for
    a profit, so a near-break-even year of "Bath & Body Works" (loss), "Bank of New York Mellon" or
    "Cracker Barrel Old Country Store" (both) was refused `script_shape` — a valid company dropped
    where round 2 meant to reword it. SUB_CENT ("under $0.01") plus the `mm.hook.*.tiny` variants
    (8 + name words) compose every 3- to 6-word name, profit and loss, at $0.004 per $100 — and no
    frame says "$0.00" beside the non-zero net figure.
    Mutation-checked by hand (2026-10-09, in memory): with money_cents' old "less than $0.01" the
    4-, 5- and 6-word cases are refused; without the tiny variants the 5-word loss and 6-word cases
    are refused."""
    assert len(T.spoken_company(name).split()) == n_words and R.company_name_problem(name) is None
    rev = 113_000_000_000.0
    rec = costco(company=co(sym, name), revenue_usd=rev, net_income_usd=sign * rev * 0.00004,
                 segments=(R.Segment("United States", 80_000_000_000.0), R.Segment("Canada", 20_000_000_000.0),
                           R.Segment("Other International", 13_000_000_000.0)),
                 gross_profit_usd=14_000_000_000.0, operating_profit_usd=3_000_000_000.0)
    out = compose(rec, THU)                                  # NewsTemplateRefused before the fix
    blob = json.dumps(out, ensure_ascii=False)
    assert "$0.00" not in blob and "less than $0.01" not in blob
    assert out["opening_card"]["figure"] == T.SUB_CENT
    assert T.SUB_CENT in out["hook"] and T.SUB_CENT in out["image_spec"]["callout"]
    assert T.HOOK_WORDS[0] <= len(out["hook"].split()) <= T.HOOK_WORDS[1]
    kind = "profit" if sign > 0 else "loss"
    variants = [T._t(f"mm.hook.{kind}.{v}", fy="2025", co_sp=T.spoken_company(name), per100=T.SUB_CENT)
                for v in ("long", "short", "min", "tiny")]
    assert out["hook"] in variants
    if n_words == 6:
        assert out["hook"] == variants[-1]                   # only the tight frame fits a 6-word name
    line4 = out["video_script"][3]
    assert line4.endswith(("net income was $4.5 million.", "the net loss was $4.5 million.")), line4
    with_figure = [k for k, text in out["captions"].items() if "$100" in text]
    assert with_figure and all(T.SUB_CENT in out["captions"][k] for k in with_figure), with_figure
    assert out["dropped_outlets"] == {} and sorted(out["posts"]) == sorted(post_copy.PLATFORMS)
    assert T.validate_package(out, record=rec) == [] and T.validate_package(out) == []
    assert T.revalidate(out, fact_sheet=sheet(rec), run_date=THU) == []


def test_the_tiny_hooks_never_displace_a_longer_variant_that_fits():
    """The tight frames are the LAST hook variant, offered only when no longer one fits HOOK_WORDS:
    a 1-word name still opens on the first frame that fits (Costco's $2.94 on the long one, its
    sub-cent year on the short one — the long one is 15 words), never on the tight one."""
    out = compose(costco(), THU)
    assert out["hook"] == "For every $100 of revenue in fiscal 2025, Costco kept $2.94 as net income."
    rec = costco(net_income_usd=11_008_000.0)
    out = compose(rec, THU)
    assert out["hook"] == "Costco kept under $0.01 of every $100 of revenue as net income."


@pytest.mark.parametrize("cents", [1, 300, 10_000], ids=["$0.01", "$3.00", "$100.00"])
@pytest.mark.parametrize("sign", [-1.0, 1.0], ids=["loss", "profit"])
@pytest.mark.parametrize("sym, name, n_words", LONG_NAMES, ids=[n[1] for n in LONG_NAMES])
def test_money_map_any_figure_composes_for_3_to_6_word_company_names(sym, name, n_words, sign, cents):
    """Review round 4 (low): the tight `mm.hook.*.tiny` frames were offered only for a sub-cent
    year, so an ORDINARY loss year of a 6-word name ("Bank of N.T. Butterfield & Son", $3.00 of
    loss per $100) had no hook of 5-14 words — the shortest, ".min", is name + 9 — and was refused
    `script_shape`. They are now the last variant for ANY figure, offered only when no longer frame
    fits: the hook is always the FIRST frame that fits (so a package that already composed keeps its
    hook), and the tight one only for a 6-word loss. Mutation-checked (2026-10-09, in memory): with
    the old `per100 == SUB_CENT` condition every 6-word loss case here is refused `script_shape`."""
    rev = 113_000_000_000.0
    rec = costco(company=co(sym, name), revenue_usd=rev, net_income_usd=sign * rev * cents / 10_000,
                 segments=(R.Segment("United States", 80_000_000_000.0), R.Segment("Canada", 20_000_000_000.0),
                           R.Segment("Other International", 13_000_000_000.0)),
                 gross_profit_usd=14_000_000_000.0, operating_profit_usd=3_000_000_000.0)
    out = compose(rec, THU)                                  # NewsTemplateRefused before the fix (6-word loss)
    figure = f"${cents / 100:,.2f}"
    assert out["opening_card"]["figure"] == figure and figure in out["hook"]
    assert T.HOOK_WORDS[0] <= len(out["hook"].split()) <= T.HOOK_WORDS[1]
    kind = "profit" if sign > 0 else "loss"
    variants = [T._t(f"mm.hook.{kind}.{v}", fy="2025", co_sp=T.spoken_company(name), per100=figure)
                for v in ("long", "short", "min", "tiny")]
    fits = [v for v in variants if T.HOOK_WORDS[0] <= len(v.split()) <= T.HOOK_WORDS[1]]
    assert out["hook"] == fits[0]                            # the first frame that fits, never a later one
    assert (out["hook"] == variants[-1]) == (n_words == 6 and kind == "loss")
    assert out["dropped_outlets"] == {} and sorted(out["posts"]) == sorted(post_copy.PLATFORMS)
    assert T.validate_package(out, record=rec) == [] and T.validate_package(out) == []
    assert T.revalidate(out, fact_sheet=sheet(rec), run_date=THU) == []
    assert T.revalidate(_jsonb(out), fact_sheet=_jsonb(sheet(rec)), run_date=THU) == []


def test_money_map_with_exactly_zero_net_income_keeps_its_exact_wording():
    """ni == 0: "$0.00" per $100 is exact beside "net income was $0", so it stays."""
    rec = costco(net_income_usd=0.0)
    out = compose(rec, THU)
    assert out["opening_card"]["figure"] == "$0.00"
    assert out["hook"] == "For every $100 of revenue in fiscal 2025, Costco kept $0.00 as net income."
    assert out["video_script"][3] == "After interest, taxes and everything else, net income was $0."
    blob = json.dumps(out, ensure_ascii=False)
    assert "less than" not in blob and T.SUB_CENT not in blob
    assert T.validate_package(out, record=rec) == []


@pytest.mark.parametrize("day, spoken", [
    (1, "October 1st"), (2, "October 2nd"), (3, "October 3rd"), (4, "October 4th"), (11, "October 11th"),
    (12, "October 12th"), (13, "October 13th"), (21, "October 21st"), (22, "October 22nd"),
    (23, "October 23rd"), (31, "October 31st"),
])
def test_spoken_ordinals(day, spoken):
    assert T.date_spoken(date(2026, 10, day), date(2026, 11, 16)) == spoken


def test_date_and_window_formats():
    run = date(2026, 11, 16)
    assert T.date_image(date(2026, 10, 7)) == "Oct 7, 2026"
    assert T.date_caption(date(2026, 10, 7), run) == "Oct 7"
    assert T.date_caption(date(2025, 12, 30), run) == "Dec 30, 2025"
    assert T.date_spoken(date(2025, 12, 30), run) == "December 30th, 2025"
    assert T.month_image(date(2025, 3, 28)) == "Mar 2025" and T.month_words(date(2025, 3, 28)) == "March 2025"
    assert T.window_image(date(2026, 10, 5), date(2026, 10, 9)) == "Oct 5–9, 2026"
    assert T.window_image(date(2026, 9, 28), date(2026, 10, 2)) == "Sep 28–Oct 2, 2026"
    assert T.window_image(date(2025, 12, 29), date(2026, 1, 4)) == "Dec 29, 2025–Jan 4, 2026"
    assert T.window_image(date(2026, 10, 5), date(2026, 10, 5)) == "Oct 5, 2026"
    assert T.window_caption(date(2026, 10, 5), date(2026, 10, 9), run) == "Oct 5-9"
    assert T.window_caption(date(2026, 9, 28), date(2026, 10, 2), run) == "Sep 28-Oct 2"
    assert T.window_caption(date(2025, 12, 1), date(2025, 12, 5), run) == "Dec 1-5, 2025"
    assert T.window_caption(date(2025, 12, 29), date(2026, 1, 4), run) == "Dec 29, 2025-Jan 4, 2026"
    assert T.window_upper(date(2026, 11, 9), date(2026, 11, 15)) == "NOV 9–15"
    assert T.window_upper(date(2026, 10, 28), date(2026, 11, 3)) == "OCT 28–NOV 3"


# ── names: possessive / .of twins, spoken company ─────────────────────────────


@pytest.mark.parametrize("name, out", [
    ("Costco", "Costco's"), ("NVIDIA", "NVIDIA's"), ("Fisher Investments", "Fisher Investments'"),
    ("Lowe's", None), ("Domino's", None), ("McDonald's", None), ("Investors'", None), ("", None),
])
def test_possessive(name, out):
    assert T.possessive(name) == out


@pytest.mark.parametrize("display, spoken", [
    ("NVIDIA", "Nvidia"), ("AMD", "AMD"), ("IBM", "IBM"), ("Procter & Gamble", "Procter & Gamble"),
    ("BLACKROCK TCP", "Blackrock TCP"), ("AT&T", "AT&T"), ("Coca-Cola", "Coca-Cola"),
])
def test_spoken_company(display, spoken):
    assert T.spoken_company(display) == spoken


def test_the_of_twins_replace_every_possessive():
    out = compose(week(LOW), MON)
    assert out["hook"].startswith("The chief executive of Lowe's ")
    assert out["opening_card"]["headline"] == "The CEO of Lowe's disclosed buying Lowe's stock"
    assert "Lowe's's" not in json.dumps(out) and "Lowe's'" not in json.dumps(out)
    f13 = compose(berkshire(filer_name="Moody's"), TUE)
    assert f13["hook"].startswith("The latest 13F of Moody's ")
    assert f13["image_spec"]["title"] == "The latest 13F of Moody's"
    assert f13["opening_card"]["headline"] == "newly reported holdings in the 13F of Moody's"
    assert f13["captions"]["youtube_title"].startswith("13F of Moody's: ")
    fisher = compose(berkshire(filer_name="Fisher Investments"), TUE)
    assert fisher["hook"].startswith("Fisher Investments' latest 13F ")


def test_narration_reads_well_for_kokoro():
    for name, factory, run_date in SAMPLES:
        out = compose(factory(), run_date)
        spoken = " ".join([out["hook"], *out["video_script"]])
        assert not re.search(r"\bUS\b|\bFY|\bQ[1-4]\b|\bGAAP\b|\bEPS\b", spoken), name
        assert "NVIDIA" not in spoken
        assert all(len(tok) <= AUDIO_WORD_MAX_CHARS for tok in spoken.split())
        for ref in out["logo_refs"]:          # tickers are never narrated
            if len(ref["key"]) > 1:
                assert not re.search(rf"\b{re.escape(ref['key'])}\b", spoken.replace(ref["name"], "")), (name, ref)
    out = compose(week(buy("NVDA", "NVIDIA", "ceo", None, 1_000_000.0, 5_000.0, 1, [date(2026, 11, 10)])), MON)
    assert out["hook"].startswith("Nvidia's chief executive ")
    assert out["opening_card"]["headline"].startswith("NVIDIA's CEO")


# ── script shape ──────────────────────────────────────────────────────────────


def test_every_sample_meets_the_script_shape():
    for name, factory, run_date in SAMPLES:
        out = compose(factory(), run_date)
        words = [len(s.split()) for s in [out["hook"], *out["video_script"]]]
        assert T.HOOK_WORDS[0] <= words[0] <= T.HOOK_WORDS[1], name
        assert all(T.LINE_WORDS[0] <= w <= T.LINE_WORDS[1] for w in words[1:]), name
        assert T.NARRATION_WORDS[0] <= sum(words) <= T.NARRATION_WORDS[1], name
        assert len(out["video_script"]) == len(out["cards"]) == 4
        assert len(set(out["video_script"])) == 4, name
        for c in out["cards"]:
            assert len(c["title"].split()) <= 8 and len(c["body"].split()) <= 28
            assert onscreen.drawable_problem(c["title"]) is None and onscreen.drawable_problem(c["body"]) is None


def test_pick_script_steps_down_the_longest_saving_and_refuses_what_cannot_fit():
    hook = ["one two three four five six seven eight nine ten eleven twelve thirteen fourteen",
            "one two three four five six"]
    lines = [["w " * 19 + "end"], ["w " * 19 + "end", "w " * 9 + "end"], ["w " * 19 + "end"], ["w " * 9 + "end"]]
    h, ls = T._pick_script(hook, lines)
    assert len(h.split()) + sum(len(s.split()) for s in ls) <= 75
    with pytest.raises(T.NewsTemplateRefused) as e:
        T._pick_script(["too short"], [["w " * 9 + "end"]] * 4)
    assert e.value.code == "script_shape"
    with pytest.raises(T.NewsTemplateRefused) as e:          # 14 + 4×20 = 94 and nothing to step down to
        T._pick_script([hook[0]], [["w " * 19 + "end"]] * 4)
    assert e.value.code == "script_shape"
    with pytest.raises(T.NewsTemplateRefused):                # 5 + 4×8 = 37 < 45
        T._pick_script(["one two three four five"], [["w " * 7 + "end"]] * 4)


# ── per-series variant tables ─────────────────────────────────────────────────


@pytest.mark.parametrize("holding, amended, l3, l4", [
    ("direct", False, "ins.l3.explainer", "ins.l4.amounts"),
    ("indirect", False, "ins.l3.indirect.all", "ins.l3.explainer"),
    ("mixed", False, "ins.l3.indirect.some", "ins.l3.explainer"),
    ("direct", True, "ins.l3.amended", "ins.l3.explainer"),
    ("indirect", True, "ins.l3.indirect.all", "ins.l3.amended"),
    ("mixed", True, "ins.l3.indirect.some", "ins.l3.amended"),
])
def test_form4_spotlight_line_three_and_four(holding, amended, l3, l4):
    row = buy("GME", "GameStop", "ceo", None, 500_000.0, 20_000.0, 1, [date(2026, 11, 10)], holding=holding,
              amended=amended)
    out = compose(week(row), MON)
    assert out["video_script"][2] == T.LEXICON[l3].format(role_sp="chief executive")
    assert out["video_script"][3] == T.LEXICON[l4]
    assert out["image_spec"]["layout"] == "spotlight"
    flags = out["image_spec"]["lines"][1]
    assert ("indirectly" in flags) == (holding != "direct")
    assert ("Form 4/A" in flags) == amended


def test_an_amended_filing_is_only_ever_said_to_be_included():
    """The record carries only `amended: bool` — it cannot prove a FULL restatement (a partial 4/A
    keeps the original lines beside it), so no line says an amendment "replaces" the earlier
    report: the figures "include an amended Form 4". Mutation-checked by hand (2026-10-09): the
    old `ins.l3.amended` ("…, which replaces the earlier report.") turns this red."""
    for entry_id, text in T.LEXICON.items():
        assert not re.search(r"\breplac", text, re.IGNORECASE), entry_id
    row = buy("GME", "GameStop", "ceo", None, 500_000.0, 20_000.0, 1, [date(2026, 11, 10)], amended=True)
    out = compose(week(row), MON)
    assert out["video_script"][2] == "The figures shown here include an amended Form 4."
    assert out["cards"][2] == {"title": "Amended filing", "body": "The figures include an amended Form 4 (Form 4/A)"}
    roundup = compose(ceo_roundup(), MON)
    assert "The figures include an amended Form 4 (Form 4/A)." in roundup["image_spec"]["notes"]
    for o in (out, roundup):
        assert "replac" not in json.dumps(o).lower()


@pytest.mark.parametrize("holdings, note", [
    (("indirect", "indirect", "indirect"), "The purchases are reported as held indirectly."),
    (("indirect", "mixed", "indirect"), "Some purchases are reported as held indirectly."),
    (("direct", "indirect", "direct"), "Some purchases are reported as held indirectly."),
    (("direct", "direct", "direct"), None),
])
def test_the_roundup_indirect_note_never_understates_an_all_indirect_week(holdings, note):
    """compliance:F5 — a week where EVERY purchase is held indirectly says so on the image and in
    its alt text; "Some" only when some are. Mutation-checked by hand (2026-10-09): the old single
    "Some purchases …" note turns the all-indirect case red."""
    rows = [replace(r, holding=h) for r, h in zip((GME, LOW, SBUX), holdings)]
    out = compose(week(*rows), MON)
    notes = out["image_spec"].get("notes", [])
    indirect_notes = [n for n in notes if "indirectly" in n]
    assert indirect_notes == ([note] if note else [])
    alt = " ".join(out["image_post"]["paragraphs"])
    if note:
        assert note in alt
        other = ("Some purchases are reported as held indirectly." if note.startswith("The ")
                 else "The purchases are reported as held indirectly.")
        assert other not in alt and other not in notes
    else:
        assert "indirectly" not in alt


def test_form4_roles_subjects_and_plurals():
    cfo = buy("NKE", "Nike", "cfo", None, 1_000_000.0, 10_000.0, 1, [date(2026, 11, 10)])
    director = buy("HSY", "Hershey", "director", None, 300_000.0, 1_500.0, 1, [date(2026, 11, 11)])
    out = compose(week(cfo, series="insider_buys"), MON)
    assert out["hook"] == "Nike's chief financial officer disclosed buying $1 million of the company's stock."
    assert out["opening_card"]["headline"] == "Nike's CFO disclosed buying Nike stock"
    out = compose(week(director, series="insider_buys"), MON)
    assert out["hook"] == "A director of Hershey disclosed buying $300,000 of the company's stock."
    assert out["cards"][0] == {"title": "Who filed", "body": "A director of Hershey"}
    out = compose(week(cfo, director, series="insider_buys"), MON)
    assert out["image_spec"]["title"] == "At least 2 company insiders disclosed buying their own company's stock"
    assert out["video_script"][3] == ("At least 1 more director disclosed buying their own company's stock in the "
                                      "same week.")
    two_dirs = compose(week(director, replace(director, company=co("CTSO", "Contoso"), amount_usd=200_000.0),
                            series="insider_buys"), MON)
    assert two_dirs["image_spec"]["title"] == "At least 2 directors disclosed buying their own company's stock"


#: A count of filers stated WITHOUT its lower-bound qualifier ("5 CEOs disclosed buying", "4 more chief
#: executives disclosed buying"): the record holds at most INSIDER_MAX_ROWS rows, after the adapter's
#: dollar floor, listing, cap, role and amendment gates — never the week's total.
_UNQUALIFIED_COUNT_RE = re.compile(
    r"(?<!At least )(?<!at least )\b[0-9]+ (?:more )?(?:CEOs?|CFOs?|directors?|company insiders?|chief executives?|"
    r"chief financial officers?) disclosed\b")


def _roundup(series, roles, n=5):
    syms = ("AAPL", "MSFT", "NVDA", "COST", "NKE")
    names = ("Apple", "Microsoft", "NVIDIA", "Costco", "Nike")
    return week(*(buy(syms[i], names[i], roles[i], None, 9_000_000.0 - i * 1_000_000.0, 50_000.0, 1,
                      [date(2026, 11, 10)]) for i in range(n)), series=series)


@pytest.mark.parametrize("series, roles", [("ceo_buys", ("ceo",) * 5),
                                            ("insider_buys", ("cfo", "director", "director", "cfo", "director"))])
def test_a_roundup_count_is_a_lower_bound_never_the_weeks_total(series, roles):
    """Review round 9 (medium + low): the adapter keeps at most five rows (seven qualifying CEO
    purchases became "5 CEOs disclosed buying …" and "4 more chief executives … in the same week"),
    so every place a roundup counts its filers says "At least {n}" / "At least {m} more" — the image
    title and alt title, every caption headline, narration line 4, card 4 and the video paragraph — in
    the composed post AND every variant the builder offers. Mutation-checked (2026-10-10, in memory):
    restoring the bare count in `ins.img.title.many`, `ins.ch.many.*`, `ins.l4.more.many`,
    `ins.c4.more.many` or `ins.vp.more.many` each turned this red."""
    rec = _roundup(series, roles)
    out = compose(rec, MON)
    p = _parts(rec, MON)
    offered = [*p.image_headlines, *p.video_headlines, *p.image_paragraphs, *p.video_paragraphs, p.alt_title,
               p.image_spec["title"], *(v for g in p.lines for v in g), *(t for c in p.cards for t in c)]
    for s in [*_strings(out), *offered]:
        assert not _UNQUALIFIED_COUNT_RE.search(s), s
    assert out["image_spec"]["title"].startswith("At least 5 ") and out["image_post"]["title"].startswith("At least 5 ")
    assert out["video_script"][3].startswith("At least 4 more ") and out["cards"][3]["body"].startswith("At least 4 more ")
    for f in ("x", "bluesky", "threads", "facebook", "linkedin"):
        assert out["captions"][f].startswith("At least 5 "), f
    assert any(para.startswith("At least 4 more ") for para in p.video_paragraphs)
    # the guard reads the bare form it exists to catch
    assert _UNQUALIFIED_COUNT_RE.search("5 CEOs disclosed buying their own company's stock")
    assert _UNQUALIFIED_COUNT_RE.search("4 more chief executives disclosed buying their own company's stock")


#: The ONLY "a" / "an" that may stand directly before a slot (review round 9): a role word, whose first
#: sound is fixed — every value it can take is below, with the article it needs. A company name or a
#: figure never follows an article: its first sound decides it ("an Oracle", "a Unilever", "an $8.1B").
_ARTICLE_SLOTS = frozenset({"role", "role_sp"})
_ROLE_ARTICLE = {"CEO": "a", "CFO": "a", "director": "a", "chief executive": "a", "chief financial officer": "a"}


def test_no_article_stands_before_a_name_or_a_figure_slot():
    """Mutation-checked (2026-10-10, in memory): the old "A {co} director" subject, or "vs a {est_s}
    analyst estimate", turned this red."""
    found = []
    for entry_id, text in T.LEXICON.items():
        for m in re.finditer(r"\b([Aa]n?)\s+\{(\w+)\}", text):
            found.append(entry_id)
            assert m.group(2) in _ARTICLE_SLOTS, (entry_id, m.group(0))
            assert m.group(1).lower() == "a", (entry_id, m.group(0))
    assert found                                   # the role slots are still there, so the scan is live
    single_roles = {T.LEXICON[f"role.{r}.{form}"] for r in ("ceo", "cfo", "director") for form in ("word", "sp")}
    assert single_roles == set(_ROLE_ARTICLE) and set(_ROLE_ARTICLE.values()) == {"a"}


@pytest.mark.parametrize("sym, name", [("ORCL", "Oracle"), ("AMD", "AMD"), ("UBER", "Uber"), ("LLY", "Eli Lilly"),
                                       ("UL", "Unilever"), ("INTC", "Intel"), ("XOM", "Exxon Mobil"),
                                       ("HSY", "Hershey")])
def test_a_director_is_a_director_of_the_company_whatever_its_first_sound(sym, name):
    """Review round 9 (low): "A Oracle director disclosed buying …" in the hook, the cover, the YouTube
    title and every caption's first line. The subject is now "A director of {company}" — the article
    always stands before "director", so it never depends on how a name is read."""
    rec = week(buy(sym, name, "director", None, 1_000_000.0, 5_000.0, 1, [date(2026, 11, 11)]), series="insider_buys")
    out = compose(rec, MON)
    assert out["hook"] == f"A director of {name} disclosed buying $1 million of the company's stock."
    assert out["captions"]["x"].startswith(f"A director of {name} disclosed buying $1 million of {name} stock")
    assert out["opening_card"]["headline"].startswith(f"A director of {name} disclosed buying")
    assert out["captions"]["youtube_title"].startswith(("A director of ", "A director disclosed"))
    for s in _strings(out):
        assert not re.search(rf"\b[Aa]n? {re.escape(name)}\b", s), s


@pytest.mark.parametrize("shares, words, image", [
    (1.0, "1 share", "1 share"), (1.3, "about 1 share", "about 1 share"), (0.6, "about 1 share", "about 1 share"),
    (2.0, "2 shares", "2 shares"), (1.5, "about 2 shares", "about 2 shares"), (12_000.0, "12,000 shares", "12K shares"),
], ids=["one", "about_one", "rounds_up_to_one", "two", "rounds_up_to_two", "thousands"])
def test_the_share_noun_agrees_with_the_count_as_written(shares, words, image):
    """Review round 9 (low): a one-share Form 4 purchase (a $700,000 class A share) read "1 shares" in
    narration, card 2, the spotlight line and the alt text. Mutation-checked (2026-10-10, in memory):
    with the noun always "shares" the one-share cases turned red."""
    row = buy("BRK-A", "Berkshire Hathaway", "director", None, 700_000.0, shares, 1, [date(2026, 11, 12)])
    out = compose(week(row, series="insider_buys"), MON)
    assert out["video_script"][1] == f"It reports one purchase of {words}, for $700,000."
    assert out["cards"][1]["body"] == f"1 purchase · {image} · $700K"
    assert out["image_spec"]["lines"][0] == f"1 purchase · {image}"
    assert out["image_post"]["paragraphs"][0] == f"It shows $700K in 1 purchase of {image}."
    assert not re.search(r"\b1 shares\b|\b1K share\b", json.dumps(out)), out["video_script"][1]


def test_a_share_count_that_would_read_about_0_is_refused():
    row = buy("BRK-A", "Berkshire Hathaway", "director", None, 700_000.0, 0.3, 1, [date(2026, 11, 12)])
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(week(row, series="insider_buys"), MON)
    assert e.value.code == "implausible_figures"


def test_form4_kicker_is_last_week_only_for_last_weeks_window():
    assert compose(ceo_roundup(), MON)["opening_card"]["kicker"] == "FILED LAST WEEK · FORM 4"
    tue = date(2026, 11, 17)
    rec = week(GME, start=date(2026, 11, 10), end=date(2026, 11, 16))
    out = compose(rec, tue)
    assert out["opening_card"]["kicker"] == "FILED NOV 10–16 · FORM 4"
    assert out["image_spec"]["kicker"] == out["opening_card"]["kicker"]


@pytest.mark.parametrize("factory", [ceo_roundup, lambda: week(GME),
                                     lambda: week(replace(GME, role="director"), series="insider_buys"),
                                     lambda: week(replace(GME, role="cfo"), series="insider_buys")],
                         ids=["roundup", "spotlight", "director", "cfo"])
def test_form4_names_a_person_only_where_placement_allows(factory):
    """compliance:F6 + owner decision 2026-10-09 "Role-only video" — every card is drawn on a video
    frame, every narrated word is burned as a caption on one, and YouTube Shorts picks its own cover
    frame, so the whole VIDEO is role-only (hook, narration, cards, opening card); the name stays in
    the caption paragraphs after the headline and in the YouTube description. Mutation-checked by
    hand (2026-10-09): with card 1 back on the name, or narration L1 back on it, this test goes red."""
    out = compose(factory(), MON)
    assert out["persons"] == ["Ryan Cohen"]
    for text in [out["hook"], *out["video_script"]]:
        assert "Cohen" not in text and "Ryan" not in text, text
    assert out["video_script"][0].startswith(("The filings are by the company's chief executive ",
                                              "The filings are by the company's chief financial officer ",
                                              "The filings are by a director of the company "))
    assert all("Cohen" not in c["title"] and "Cohen" not in c["body"] for c in out["cards"])
    assert out["cards"][0]["body"] in ("The CEO of GameStop", "The CFO of GameStop", "A director of GameStop")
    assert "Ryan Cohen" in out["captions"]["linkedin"].split("\n\n", 1)[1]
    assert "Ryan Cohen" in out["captions"]["youtube_description"].split("\n\n", 1)[1]
    # the post as a whole names someone: every template caption keeps the code-owned line
    for platform in ("linkedin", "youtube", "tiktok", "instagram"):
        assert post_copy.NON_AFFILIATION in out["posts"][platform]["caption"], platform
    assert post_copy.NON_AFFILIATION in out["disclaimer_card"]
    for platform in ("x", "bluesky", "threads"):
        assert "Cohen" not in out["posts"][platform]["caption"]
    for f in ("hook", "opening_card", "image_spec", "image_post", "image_footer"):
        assert "Cohen" not in json.dumps(out[f]), f
    assert all("Cohen" not in c["title"] for c in out["cards"])
    assert all("Cohen" not in p["caption"].split("\n", 1)[0] for p in out["posts"].values())
    assert "Cohen" not in out["posts"]["youtube"]["title"]


def test_person_slots_go_role_only_while_the_block_list_is_unusable(monkeypatch):
    named = compose(ceo_roundup(), MON)
    monkeypatch.setattr(R, "person_names_allowed", lambda: False)
    out = compose(ceo_roundup(), MON)
    assert out["persons"] == [] and "Cohen" not in json.dumps(out)
    # the video never carried the name: it is the same either way
    assert out["video_script"][0].startswith("The filings are by the company's chief executive ")
    assert (out["hook"], out["video_script"], out["cards"]) == (named["hook"], named["video_script"], named["cards"])


def test_a_person_whose_surname_collides_with_restricted_text_is_rendered_role_only():
    """A surname that is also a template word ("Stock") would put a 'name' in the headline: that
    person is rendered role-only, the post survives. Mutation-checked by hand (2026-10-09): with
    the suppression loop removed from `compose`, this record is refused as `placement`."""
    rec = week(buy("GME", "GameStop", "ceo", "Ryan Stock", 2_000_000.0, 80_000.0, 1, [date(2026, 11, 10)]))
    out = compose(rec, MON)
    assert out["persons"] == []
    assert "Ryan" not in json.dumps(out)
    assert out["video_script"][0] == "The filing is by the company's chief executive and was filed on November 10th."


_VIDEO_FIELDS = ("hook", "video_script", "cards", "opening_card")


@pytest.mark.parametrize("factory", [
    ceo_roundup, lambda: week(GME), insider_mixed,
    lambda: week(replace(GME, role="director"), series="insider_buys"),
    lambda: week(buy("NKE", "Nike", "cfo", "Anna Smith-Jones", 1_000_000.0, 10_000.0, 1, [date(2026, 11, 10)]),
                 series="insider_buys"),
], ids=["roundup", "spotlight", "insider_mixed", "director", "cfo_one_filing"])
def test_the_video_is_role_only_and_the_same_with_or_without_a_name(factory):
    """Owner decision 2026-10-09 "Role-only video": every narrated word is burned as a caption on a
    frame and YouTube Shorts picks its own cover, so a template VIDEO never carries a person's name —
    the role stands in, possessed by the company. The video of a named record (hook, narration,
    cards, opening card — and the image) is byte-equal to the same record with every name removed;
    only the written captions differ, and they still name the person after the headline.
    Mutation-checked (2026-10-09, in memory): with the person put back into narration L1 the named
    record either loses its name everywhere (the placement loop renders it role-only) or, with
    `_restricted_strings` blind to narration, narrates it — both turn this red."""
    named = factory()
    anon = replace(named, rows=tuple(replace(r, person_name=None) for r in named.rows))
    a, b = compose(named, MON), compose(anon, MON)
    assert a["persons"] and b["persons"] == []
    for key in _VIDEO_FIELDS + ("image_spec", "image_post"):
        assert a[key] == b[key], key
    video = json.dumps([a[k] for k in _VIDEO_FIELDS], ensure_ascii=False)
    for person in a["persons"]:
        for token in person.replace("-", " ").split():
            assert not re.search(rf"\b{re.escape(token)}\b", video), (person, token)
        assert any(person in body.split("\n\n", 1)[-1] for f, body in a["captions"].items()
                   if f in ("facebook", "linkedin", "instagram", "youtube_description")), person
    assert a["captions"] != b["captions"]


def test_a_congress_name_in_a_narration_line_or_a_card_is_a_violation():
    """The video's drawn fields carry the Congress block-list scan too (every public string)."""
    base = compose(ceo_roundup(), MON)
    for field_, mutate in [
        ("video_script[1]", lambda o: o["video_script"].__setitem__(1, o["video_script"][1] + " Nancy Pelosi.")),
        ("cards[2].body", lambda o: o["cards"][2].update(body="Nancy Pelosi")),
    ]:
        out = copy.deepcopy(base)
        mutate(out)
        found = {(v["field"], v["code"]) for v in T.validate_package(out)}
        assert (field_, "congress_name") in found, (field_, found)


def test_placement_violations_are_reported_in_every_restricted_field():
    base = compose(ceo_roundup(), MON)
    tamper = [
        ("hook", lambda o: o.update(hook="Ryan Cohen " + o["hook"])),
        ("opening_card.headline", lambda o: o["opening_card"].update(headline="Ryan Cohen bought")),
        ("image_spec.title", lambda o: o["image_spec"].update(title="Cohen and 2 more CEOs disclosed buying")),
        ("image_post.title", lambda o: o["image_post"].update(title="Ryan Cohen disclosed buying")),
        ("cards[0].title", lambda o: o["cards"][0].update(title="Cohen")),
        ("cards[0].body", lambda o: o["cards"][0].update(body="Ryan Cohen, CEO of GameStop")),
        ("cards[3].body", lambda o: o["cards"][3].update(body="Cohen and 2 more CEOs")),
        ("captions.x", lambda o: o["captions"].update(x=o["captions"]["x"] + " Cohen.")),
        # role-only video (owner decision 2026-10-09): every narration line is a drawn field
        ("video_script[0]", lambda o: o["video_script"].__setitem__(0, o["video_script"][0].replace(
            "by the company's", "by Ryan Cohen, the company's"))),
        ("video_script[3]", lambda o: o["video_script"].__setitem__(3, o["video_script"][3] + " Cohen was first.")),
    ]
    for field_, mutate in tamper:
        out = copy.deepcopy(base)
        mutate(out)
        found = {(v["field"], v["code"]) for v in T.validate_package(out)}
        assert (field_, "placement") in found, (field_, found)      # the EXACT field, never folded
    # the body may name the person: the untouched package is clean
    assert T.validate_package(base) == []


def _prefix_name(o, caption_field, platform):
    """A person at the head of a long caption AND of its composed post, kept consistent so the
    post still equals its caption composed by post_copy and the `posts.{p}.suffix` check (the
    code-owned tail) stays clean: only the first-line checks can see it."""
    o["captions"][caption_field] = "Ryan Cohen " + o["captions"][caption_field]
    o["posts"][platform]["caption"] = "Ryan Cohen " + o["posts"][platform]["caption"]


@pytest.mark.parametrize("mutate, expected", [
    (lambda o: _prefix_name(o, "facebook", "facebook"), {"captions.facebook[0]", "posts.facebook[0]"}),
    (lambda o: _prefix_name(o, "linkedin", "linkedin"), {"captions.linkedin[0]", "posts.linkedin[0]"}),
    (lambda o: _prefix_name(o, "instagram", "instagram"), {"captions.instagram[0]", "posts.instagram[0]"}),
    (lambda o: _prefix_name(o, "tiktok", "tiktok"), {"captions.tiktok[0]", "posts.tiktok[0]"}),
    (lambda o: _prefix_name(o, "youtube_description", "youtube"),
     {"captions.youtube_description[0]", "posts.youtube[0]"}),
    (lambda o: o["captions"].update(youtube_title="Ryan Cohen " + o["captions"]["youtube_title"]),
     {"captions.youtube_title"}),
    (lambda o: o["posts"]["youtube"].update(title="Ryan Cohen " + o["posts"]["youtube"]["title"]),
     {"posts.youtube.title"}),
], ids=["facebook", "linkedin", "instagram", "tiktok", "youtube_description", "youtube_title", "posts_title"])
def test_a7_the_first_line_of_every_long_caption_is_restricted(mutate, expected):
    """tests:F4 — the A7 rule for the FIRST LINE of the long captions (Facebook, LinkedIn,
    Instagram, TikTok, the YouTube description) and for both YouTube titles, asserted on the exact
    field. Mutation-checked by hand (2026-10-09): dropping the `captions.{f}[0]` or the
    `posts.{p}[0]` line from `_restricted_strings` turns the matching cases red."""
    out = compose(ceo_roundup(), MON)
    assert out["persons"] == ["Ryan Cohen"]
    # the same name AFTER the headline is allowed: the untouched long captions name it, cleanly
    assert "Ryan Cohen" in out["captions"]["linkedin"].split("\n", 1)[1]
    assert T.validate_package(out) == []
    mutate(out)
    found = {v["field"] for v in T.validate_package(out) if v["code"] == "placement"}
    assert expected <= found, (expected, found)


# ── 13F variants ──────────────────────────────────────────────────────────────


def test_thirteen_f_lead_kinds_and_their_hooks():
    gone_only = berkshire(moves=(move("C", "Citigroup", "no_longer_reported", shares=0.0, prev_shares=5.0),),
                          counts=(("newly_reported", 0), ("no_longer_reported", 1)))
    out = compose(gone_only, TUE)
    assert out["hook"] == "Berkshire Hathaway's latest 13F no longer reports 1 holding from the quarter before."
    # news-v5: line 2 narrates the lead kind; with no other kind counted, line 3 explains the filing
    assert out["video_script"][1] == "The holding no longer reported in this filing is Citigroup."
    assert out["video_script"][2] == T.LEXICON["f13.l.explainer2"]
    assert out["opening_card"]["headline"] == "holding no longer reported in Berkshire Hathaway's 13F"
    assert out["image_spec"]["subtitle"] == "1 no longer reported"
    more_only = berkshire(moves=(move("OXY", "Occidental Petroleum", "increased", shares=12.0, prev_shares=10.0),
                                 move("AAPL", "Apple", "decreased", shares=8.0, prev_shares=10.0)),
                          counts=(("increased", 1), ("decreased", 1)))
    out = compose(more_only, TUE)
    # no value in the record: the display order decides (more before fewer)
    assert out["hook"] == ("Berkshire Hathaway's latest 13F reports more shares of 1 holding than the quarter "
                           "before.")
    assert out["video_script"][1] == "Compared with the quarter before, it reported more shares of Occidental Petroleum."
    assert out["video_script"][2] == "Compared with the quarter before, it also reported fewer shares of Apple."
    # newly / gone counts are unknown (absent): nothing claims "none" for them
    assert not any(w in " ".join(out["video_script"] + [c["body"] for c in out["cards"]])
                   for w in ("No holdings", "Every holding", "None this quarter", "newly", "no longer"))
    assert [s["heading"] for s in out["image_spec"]["sections"]] == ["Reported more shares", "Reported fewer shares"]
    # the same moves, valued: Apple's decrease is the larger move, so fewer shares leads
    valued = berkshire(moves=(move("OXY", "Occidental Petroleum", "increased", shares=12.0, prev_shares=10.0,
                                   value_usd=1_200.0),
                              move("AAPL", "Apple", "decreased", shares=8.0, prev_shares=10.0, value_usd=8_000.0)),
                       counts=(("increased", 1), ("decreased", 1)))
    out = compose(valued, TUE)
    assert out["hook"] == ("Berkshire Hathaway's latest 13F reports fewer shares of 1 holding than the quarter "
                           "before.")
    assert out["video_script"][1] == "Compared with the quarter before, it reported fewer shares of Apple."
    assert out["video_script"][2] == ("Compared with the quarter before, it also reported more shares of Occidental "
                                      "Petroleum.")
    # the image keeps its display order whatever leads
    assert [s["heading"] for s in out["image_spec"]["sections"]] == ["Reported more shares", "Reported fewer shares"]
    assert out["image_spec"]["subtitle"] == "1 with fewer shares · 1 with more shares"


def test_thirteen_f_long_lists_shorten_and_count_the_rest():
    moves = tuple(move(sym, name, "newly_reported", shares=1.0, prev_shares=0.0, value_usd=1e6)
                  for sym, name in (("STZ", "Constellation Brands"), ("DPZ", "Domino's Pizza"), ("LEN", "Lennar"),
                                    ("OXY", "Occidental Petroleum"), ("CB", "Chubb")))
    out = compose(berkshire(moves=moves, counts=(("newly_reported", 9),)), TUE)
    assert out["video_script"][1].endswith("and 6 more.")
    sec = out["image_spec"]["sections"][0]
    # news-v5: at most F13_ROWS_PER_KIND (3) rows of one kind
    assert T.F13_ROWS_PER_KIND == 3
    assert len(sec["rows"]) == 3 and sec["more"] == "+6 more"
    assert out["image_spec"]["subtitle"] == "9 newly reported"


def test_thirteen_f_without_a_filer_symbol_opens_on_its_holdings():
    out = compose(pershing(), TUE)
    # only the lead kind's holding: Uber (increased, the largest move) never sits beside CoreWeave
    # (newly reported) on a "holding with more shares" cover
    assert out["opening_card"]["logos"] == ["UBER"] and "chip" not in out["opening_card"]
    assert out["image_footer"].startswith("Educational only · not investment advice · Source: SEC Form 13F-HR/A · ")
    assert out["video_script"][0].endswith("an amended version was filed on November 14th.")
    assert out["video_script"][3] == "CoreWeave first appears in this filing; it was listed in August 2026."


_F13_HEADLINE_KIND = {"newly_reported": "newly reported", "no_longer_reported": "no longer reported",
                      "increased": "with more shares", "decreased": "with fewer shares"}


def test_the_13f_cover_shows_only_holdings_of_its_headline_kind():
    """compliance:F1 — the cover (the video's first frame) must never pair "N newly reported
    holdings" with the logos of exited or increased holdings. Without a filer symbol the lead kind
    is one with rows SHOWN and the cover opens on those rows only. Mutation-checked by hand
    (2026-10-09): with the old lead (any counted kind) and the old logo order (lead kind first,
    then every other kind), the probe below opens on "2 newly reported" beside two exits."""
    # the review's probe: 2 newly reported holdings whose rows all failed the adapter's gates
    soros = berkshire(filer_name="Soros Fund Management", filer_cik="0001029160", filer_symbol=None,
                      moves=(move("C", "Citigroup", "no_longer_reported", shares=0.0, prev_shares=5.0),
                             move("NU", "Nu Holdings", "no_longer_reported", shares=0.0, prev_shares=7.0)),
                      counts=(("newly_reported", 2), ("no_longer_reported", 2)))
    out = compose(soros, TUE)
    oc = out["opening_card"]
    assert oc["logos"] == ["C", "NU"] and oc["figure"] == "2"
    assert oc["headline"] == "holdings no longer reported in Soros Fund Management's 13F"
    assert out["hook"] == "Soros Fund Management's latest 13F no longer reports 2 holdings from the quarter before."
    assert "newly" not in oc["headline"] + out["hook"] + out["captions"]["youtube_title"]
    # news-v5: line 2 is the lead kind; the counted-only kind is still stated, on line 3
    assert out["video_script"][1] == "The holdings no longer reported in this filing are Citigroup and Nu Holdings."
    assert out["video_script"][2] == "2 holdings were newly reported in this filing."   # counts still stated
    # news-v5: a counted kind with no move in the record never leads — not even beside the filer's
    # own logo (none of its moves passed the adapter's gates, materiality among them)
    brk = compose(replace(soros, filer_name="Berkshire Hathaway", filer_symbol="BRK-B"), TUE)
    assert brk["opening_card"]["logos"] == ["BRK-B"] and brk["opening_card"]["figure"] == "2"
    assert brk["opening_card"]["headline"] == "holdings no longer reported in Berkshire Hathaway's 13F"
    assert brk["hook"].startswith("Berkshire Hathaway's latest 13F no longer reports 2 holdings")
    # the invariant over seeded records (every kind mix, counts at or above the rows shown): every
    # holding logo on a symbol-less cover is a shown move of the kind its headline names
    rng = random.Random(1182)
    names = [("STZ", "Constellation Brands"), ("DPZ", "Domino's Pizza"), ("LEN", "Lennar"), ("C", "Citigroup"),
             ("NU", "Nu Holdings"), ("UBER", "Uber"), ("CMG", "Chipotle Mexican Grill"), ("OXY", "Occidental")]
    kw = {"newly_reported": dict(shares=1.0, prev_shares=0.0), "increased": dict(shares=12.0, prev_shares=10.0),
          "decreased": dict(shares=8.0, prev_shares=10.0), "no_longer_reported": dict(shares=0.0, prev_shares=5.0)}
    for _ in range(200):
        picked = rng.sample(names, rng.randint(1, 8))
        moves = tuple(move(sym, nm, k, **kw[k]) for (sym, nm), k in
                      ((p, rng.choice(R.THIRTEEN_F_MOVES)) for p in picked))
        counts = tuple((k, sum(m.move == k for m in moves) + rng.choice([0, 0, 2]))
                       for k in R.THIRTEEN_F_MOVES if rng.random() < 0.8 or any(m.move == k for m in moves))
        cover = compose(pershing(amended_on=None, moves=moves, counts=counts), TUE)["opening_card"]
        shown = [m for m in moves if m.company.symbol in cover["logos"]]
        assert len(shown) == len(cover["logos"]) >= 1
        assert len({m.move for m in shown}) == 1, (cover, moves, counts)
        assert _F13_HEADLINE_KIND[shown[0].move] in cover["headline"], (cover, moves, counts)
        assert cover["figure"] == str(dict(counts)[shown[0].move])


def test_a_filer_symbol_that_is_also_a_holding_is_refused():
    rec = berkshire(filer_symbol="STZ")
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(rec, TUE)
    assert e.value.code == "record_invalid"


# ── 13F: lead with what matters (live-data finding L1(b), news-v5) ────────────
#
# Two REAL records (`record_to_dict` JSON from the 2026-10-09 live preview run, copied inline: the
# preview folder is gitignored). They carry no person's name — a 13F's subject is the filing ENTITY.

LIVE_RUN = date(2026, 8, 18)


def _live_move(sym, name, kind, shares, prev, value, listed=None):
    return {"company": {"symbol": sym, "name": name}, "move": kind, "shares": shares, "prev_shares": prev,
            "value_usd": value, "listed_on": listed}


LIVE_BERKSHIRE_2026_Q2 = {
    "schema": 1, "series": "thirteen_f", "filer_name": "Berkshire Hathaway", "filer_cik": "0001067983",
    "filer_symbol": "BRK-A", "period": "2026-Q2", "period_end": "2026-06-30", "filed_on": "2026-08-14",
    "amended_on": None, "total_value_usd": 299253556246.0, "position_count": 29,
    "moves": [_live_move("DHI", "D.R. Horton", "newly_reported", 3564.0, None, 580504.0),
              _live_move("STZ", "Constellation Brands", "no_longer_reported", None, 632890.0, None),
              _live_move("DAL", "Delta Air Lines", "increased", 57320000.0, 39809456.0, 5368591200.0),
              _live_move("M", "Macy's", "increased", 7347426.0, 3038355.0, 173031882.0),
              _live_move("COF", "Capital One Financial", "decreased", 3000000.0, 7150000.0, 601860000.0),
              _live_move("KR", "The Kroger", "decreased", 39000000.0, 50000000.0, 2165670000.0),
              _live_move("NUE", "Nucor", "decreased", 1857752.0, 3907075.0, 413814258.0)],
    "counts": [["newly_reported", 1], ["increased", 7], ["decreased", 6], ["no_longer_reported", 1]],
}
LIVE_ARK_2026_Q2 = {
    "schema": 1, "series": "thirteen_f", "filer_name": "ARK Invest", "filer_cik": "0001697748",
    "filer_symbol": None, "period": "2026-Q2", "period_end": "2026-06-30", "filed_on": "2026-08-14",
    "amended_on": None, "total_value_usd": 15402068542.0, "position_count": 189,
    "moves": [_live_move("SPCX", "Space Exploration Technologies", "newly_reported", 4478013.0, None, 765113281.0,
                         "2026-06-12"),
              _live_move("CBRS", "Cerebras Systems", "newly_reported", 764613.0, None, 168979473.0, "2026-05-14"),
              _live_move("XE", "X-Energy", "newly_reported", 5411846.0, None, 99361493.0, "2026-04-24"),
              _live_move("SNOW", "Snowflake", "newly_reported", 269539.0, None, 68597731.0),
              _live_move("ALMR", "Alamar Biosciences", "newly_reported", 1373959.0, None, 37220549.0, "2026-04-17"),
              _live_move("HONA", "Honeywell Aerospace", "newly_reported", 23262.0, None, 5142763.0, "2026-06-29"),
              _live_move("OCTV", "Octave Intelligence", "newly_reported", 8897.0, None, 145021.0, "2026-05-28"),
              _live_move("SLMT", "Brera Holdings", "no_longer_reported", None, 9687877.0, None)],
    "counts": [["newly_reported", 15], ["increased", 78], ["decreased", 94], ["no_longer_reported", 6]],
}


def after_floor(raw):
    """The record as the adapter's materiality floor leaves it (finding L1(a), the ADAPTER's job): a
    newly reported / no longer reported move whose value the record carries is dropped below
    max($5M, 0.05% of the filer's total). Berkshire loses D.R. Horton ($580,504 in a $299B book);
    ARK loses Honeywell Aerospace ($5.1M) and Octave Intelligence ($145,021) under its $7.7M floor."""
    floor = max(5e6, 0.0005 * raw["total_value_usd"])
    return dict(raw, moves=[m for m in raw["moves"]
                            if not (m["move"] in ("newly_reported", "no_longer_reported")
                                    and m["value_usd"] is not None and m["value_usd"] < floor)])


def live(raw):
    return R.record_from_dict(json.loads(json.dumps(raw)))


def test_the_live_fixtures_are_the_floor_the_finding_names():
    assert [m["company"]["symbol"] for m in LIVE_BERKSHIRE_2026_Q2["moves"]
            if m not in after_floor(LIVE_BERKSHIRE_2026_Q2)["moves"]] == ["DHI"]
    assert [m["company"]["symbol"] for m in LIVE_ARK_2026_Q2["moves"]
            if m not in after_floor(LIVE_ARK_2026_Q2)["moves"]] == ["HONA", "OCTV"]


LIVE_GOLDEN = {
    "berkshire": {
        "hook": "Berkshire Hathaway's latest 13F reports more shares of 7 holdings than the quarter before.",
        "video_script": ["This filing covers the quarter that ended June 30th.",
                         "Compared with the quarter before, it reported more shares of Delta Air Lines, Macy's and "
                         "5 more.",
                         "Compared with the quarter before, it also reported fewer shares of Capital One Financial, "
                         "The Kroger, Nucor and 3 more.",
                         "A 13F is filed up to 45 days after a quarter's end."],
        "cards": [{"title": "Quarter ended", "body": "Jun 30, 2026 · filed Aug 14, 2026"},
                  {"title": "Reported more shares", "body": "Delta Air Lines, Macy's and 5 more"},
                  {"title": "Reported fewer shares", "body": "Capital One Financial, The Kroger, Nucor and 3 more"},
                  {"title": "About 13F filings", "body": "U.S. stock holdings, filed up to 45 days after the quarter"}],
        "opening_card": {"kicker": "13F SEASON", "logos": ["BRK-A"], "figure": "7",
                         "headline": "holdings with more shares in Berkshire Hathaway's 13F", "chip": "BRK-A"},
        "image_spec": {
            "layout": "rows", "version": 1, "kicker": "13F SEASON", "title": "Berkshire Hathaway's latest 13F",
            "subtitle": "7 with more shares · 6 with fewer shares · 1 no longer reported · 1 newly reported",
            "sections": [{"heading": "Reported more shares",
                          "rows": [{"logo": "DAL", "cells": ["Delta Air Lines"]},
                                   {"logo": "M", "cells": ["Macy's"]}],
                          "more": "+5 more"},
                         {"heading": "Reported fewer shares",
                          "rows": [{"logo": "COF", "cells": ["Capital One Financial"]},
                                   {"logo": "KR", "cells": ["The Kroger"]},
                                   {"logo": "NUE", "cells": ["Nucor"]}],
                          "more": "+3 more"},
                         {"heading": "No longer reported",
                          "rows": [{"logo": "STZ", "cells": ["Constellation Brands"]}]}],
            "footer": "Educational only · not investment advice · Source: SEC Form 13F · Quarter ended Jun 30, "
                      "2026 · filed Aug 14, 2026 · Caydex · Not affiliated with anyone named"},
        "image_post": {"title": "Berkshire Hathaway's latest 13F",
                       "paragraphs": ["Reported more shares: Delta Air Lines, Macy's.",
                                      "Reported fewer shares: Capital One Financial, The Kroger, Nucor.",
                                      "No longer reported: Constellation Brands.",
                                      "Source: SEC Form 13F. Quarter ended Jun 30, 2026 · filed Aug 14, 2026."]},
        "x": "Berkshire Hathaway's latest 13F: more shares of 7 holdings.",
        "linkedin": "Berkshire Hathaway's 13F for the quarter ended Jun 30: 7 holdings with more shares, 6 with "
                    "fewer shares, 1 no longer reported, 1 newly reported.\n\n"
                    "Reported more shares of: Delta Air Lines; Macy's.\n\n"
                    "Reported fewer shares of: Capital One Financial; The Kroger; Nucor.\n\n"
                    "No longer reported: Constellation Brands.\n\n"
                    "A 13F lists U.S. stock holdings at a quarter's end and is filed up to 45 days later; it does "
                    "not show when shares changed hands.",
        "tiktok": "Berkshire Hathaway's 13F for the quarter ended Jun 30: 7 holdings with more shares, 6 with fewer "
                  "shares, 1 no longer reported, 1 newly reported.\n\n"
                  "Reported more shares of: Delta Air Lines; Macy's.",
        "youtube_title": "Berkshire Hathaway's 13F: more shares of 7 holdings",
    },
    "ark": {
        "hook": "ARK Invest's latest 13F lists 15 newly reported holdings.",
        "video_script": ["It covers the quarter that ended June 30th and was filed on August 14th.",
                         "The holdings newly reported in this filing are Space Exploration Technologies, Cerebras "
                         "Systems, X-Energy and 12 more.",
                         "The holdings no longer reported in this filing are Brera Holdings and 5 more.",
                         "Space Exploration Technologies first appears in this filing; it was listed in June 2026."],
        "cards": [{"title": "Quarter ended", "body": "Jun 30, 2026 · filed Aug 14, 2026"},
                  {"title": "Newly reported",
                   "body": "Space Exploration Technologies, Cerebras Systems, X-Energy and 12 more"},
                  {"title": "No longer reported", "body": "Brera Holdings and 5 more"},
                  {"title": "First appears", "body": "Space Exploration Technologies, listed Jun 2026"}],
        "opening_card": {"kicker": "13F SEASON", "logos": ["SPCX", "CBRS"], "figure": "15",
                         "headline": "newly reported holdings in ARK Invest's 13F"},
        "image_spec": {
            "layout": "rows", "version": 1, "kicker": "13F SEASON", "title": "ARK Invest's latest 13F",
            "subtitle": "15 newly reported · 6 no longer reported · 78 with more shares · 94 with fewer shares",
            "sections": [{"heading": "Newly reported",
                          "rows": [{"logo": "SPCX",
                                    "cells": ["Space Exploration Technologies", "$765.1M", "listed Jun 2026"]},
                                   {"logo": "CBRS", "cells": ["Cerebras Systems", "$169M", "listed May 2026"]},
                                   {"logo": "XE", "cells": ["X-Energy", "$99.4M", "listed Apr 2026"]}],
                          "more": "+12 more"},
                         {"heading": "No longer reported",
                          "rows": [{"logo": "SLMT", "cells": ["Brera Holdings"]}],
                          "more": "+5 more"}],
            "footer": "Educational only · not investment advice · Source: SEC Form 13F · Quarter ended Jun 30, "
                      "2026 · filed Aug 14, 2026 · Caydex · Not affiliated with anyone named"},
        "image_post": {"title": "ARK Invest's latest 13F",
                       "paragraphs": ["Newly reported: Space Exploration Technologies, Cerebras Systems, X-Energy.",
                                      "No longer reported: Brera Holdings.",
                                      "Source: SEC Form 13F. Quarter ended Jun 30, 2026 · filed Aug 14, 2026."]},
        "x": "ARK Invest's latest 13F: 15 newly reported holdings.",
        "linkedin": "ARK Invest's 13F for the quarter ended Jun 30: 15 newly reported holdings, 6 no longer "
                    "reported, 78 with more shares, 94 with fewer shares.\n\n"
                    "Newly reported, with the value the filing gives at the quarter's end: Space Exploration "
                    "Technologies, $765.1 million; Cerebras Systems, $169 million; X-Energy, $99.4 million; "
                    "Snowflake, $68.6 million; Alamar Biosciences, $37.2 million.\n\n"
                    "No longer reported: Brera Holdings.\n\n"
                    "A 13F lists U.S. stock holdings at a quarter's end and is filed up to 45 days later; it does "
                    "not show when shares changed hands.",
        "tiktok": "ARK Invest's 13F for the quarter ended Jun 30: 15 newly reported holdings, 6 no longer reported, "
                  "78 with more shares, 94 with fewer shares.\n\n"
                  "Newly reported, with the value the filing gives at the quarter's end: Space Exploration "
                  "Technologies, $765.1 million; Cerebras Systems, $169 million; X-Energy, $99.4 million; "
                  "Snowflake, $68.6 million; Alamar Biosciences, $37.2 million.",
        "youtube_title": "ARK Invest's 13F: 15 newly reported holdings",
    },
}
LIVE_RECORDS = {"berkshire": LIVE_BERKSHIRE_2026_Q2, "ark": LIVE_ARK_2026_Q2}


@pytest.mark.parametrize("name", sorted(LIVE_GOLDEN))
def test_the_live_13f_goldens(name):
    """Berkshire's 2026-Q2 13F leads with its largest move — Delta's increase (17.5M more shares,
    $1.6B at the filing's value per share) — never with the $0.6M new position the floor drops; the
    image draws every kind with moves (more → fewer → no longer reported here) so Capital One and
    Kroger are never hidden; narration covers the lead kind and the next. ARK's newly listed
    holdings lead (Space Exploration Technologies, $765.1M)."""
    rec = live(after_floor(LIVE_RECORDS[name]))
    out = compose(rec, LIVE_RUN)
    for key, value in LIVE_GOLDEN[name].items():
        got = out["captions"][key] if key in ("x", "linkedin", "tiktok", "youtube_title") else out[key]
        assert got == value, f"{name}.{key}"
    assert T.validate_package(out, record=rec) == []
    assert T.revalidate(_jsonb(out), fact_sheet=_jsonb(sheet(rec)), run_date=LIVE_RUN) == []


def test_a_tiny_new_position_never_leads_a_large_increase():
    """The finding itself, on the record AS IT CAME (D.R. Horton still in it): the $580,504 new
    position is the first kind in display order, yet the post leads with Delta's increase — and
    the image still draws the more / fewer sections Delta, Capital One and Kroger sit in."""
    out = compose(live(LIVE_BERKSHIRE_2026_Q2), LIVE_RUN)
    assert out["hook"] == LIVE_GOLDEN["berkshire"]["hook"]
    assert out["opening_card"]["figure"] == "7"
    assert out["opening_card"]["headline"] == "holdings with more shares in Berkshire Hathaway's 13F"
    assert out["video_script"][1].startswith("Compared with the quarter before, it reported more shares of Delta")
    assert out["captions"]["x"] == "Berkshire Hathaway's latest 13F: more shares of 7 holdings."
    heads = [s["heading"] for s in out["image_spec"]["sections"]]
    assert "Reported more shares" in heads and "Reported fewer shares" in heads
    assert out["image_spec"]["sections"][heads.index("Reported more shares")]["rows"][0]["logo"] == "DAL"
    # four kinds have moves, the image holds three sections: every counted kind is still named
    assert out["image_spec"]["subtitle"] == ("7 with more shares · 6 with fewer shares · 1 newly reported · "
                                             "1 no longer reported")


def _valued(sym, name, kind, *, value, shares=None, prev=None, listed=None):
    """A move whose dollar size is `value`: a new position's value, an exited position's value on
    the PREVIOUS quarter's book (`prev_value_usd`, the shape the adapter builds since review round
    8 — its `value_usd` is None), or for more / fewer the value of the shares that changed (10 → 20
    shares and back; the position value is 2× the move)."""
    if kind == "newly_reported":
        return move(sym, name, kind, shares=shares or 1_000.0, prev_shares=None, value_usd=value, listed_on=listed)
    if kind == "no_longer_reported":
        return move(sym, name, kind, shares=None, prev_shares=prev or 1_000.0, value_usd=None, prev_value_usd=value)
    if kind == "increased":
        return move(sym, name, kind, shares=20.0, prev_shares=10.0, value_usd=2 * value)
    return move(sym, name, kind, shares=10.0, prev_shares=20.0, value_usd=value)


def f13(*moves, counts=None, symbol="BRK-B"):
    counts = counts or tuple((k, sum(m.move == k for m in moves)) for k in R.THIRTEEN_F_MOVES
                             if any(m.move == k for m in moves))
    return berkshire(moves=tuple(moves), counts=tuple(counts), filer_symbol=symbol)


def test_the_lead_is_the_kind_holding_the_largest_move_by_value():
    """A move's value is its dollar SIZE at the filing's quarter-end values: a new position's value,
    the CHANGED shares of an increase / decrease (never the whole position: a $10B holding grown
    by 10% is a $0.9B move, below a new $2B one), an exit's carried value; unknown ranks last."""
    def lead(*moves):
        return compose(f13(*moves), TUE)["hook"]

    big_holding_small_add = move("AAPL", "Apple", "increased", shares=11.0, prev_shares=10.0,
                                 value_usd=10_000_000_000.0)                       # +1 share of 11: $0.91B
    new_2b = _valued("STZ", "Constellation Brands", "newly_reported", value=2_000_000_000.0)
    assert "lists 1 newly reported holding" in lead(big_holding_small_add, new_2b)
    # a decrease's size is the shares that LEFT: 20 → 10 shares of a now-$1.5B position = $1.5B
    big_cut = _valued("KR", "Kroger", "decreased", value=1_500_000_000.0)
    assert "reports fewer shares of 1 holding" in lead(replace(new_2b, value_usd=1_000_000_000.0), big_cut)
    # an exit with no value in the record never outranks a valued move …
    exit_unknown = move("C", "Citigroup", "no_longer_reported", shares=None, prev_shares=5.0)
    assert "lists 1 newly reported holding" in lead(exit_unknown, _valued("LEN", "Lennar", "newly_reported",
                                                                          value=6_000_000.0))
    # … and one that carries its value on the quarter before's book (`prev_value_usd`, review
    # round 8) ranks by it: a $9B exit leads a $2B new position
    exit_valued = _valued("C", "Citigroup", "no_longer_reported", value=9_000_000_000.0)
    assert exit_valued.prev_value_usd == 9_000_000_000.0 and exit_valued.value_usd is None
    assert "no longer reports 1 holding" in lead(exit_valued, new_2b)
    assert "lists 1 newly reported holding" in lead(replace(exit_valued, prev_value_usd=1_999_999_999.0), new_2b)
    # an exit's `value_usd` (a quarter-end value it cannot have) is never its size
    stray = move("C", "Citigroup", "no_longer_reported", shares=None, prev_shares=5.0, value_usd=9_000_000_000.0)
    assert T._f13_move_value(stray) is None
    assert "lists 1 newly reported holding" in lead(stray, new_2b)
    # a tie keeps the display order: newly reported, more, fewer, no longer reported
    tie = 500_000_000.0
    assert "reports more shares" in lead(_valued("OXY", "Occidental", "decreased", value=tie),
                                         _valued("CB", "Chubb", "increased", value=tie))



def test_a_large_exit_leads_a_small_increase_review_round_8():
    """Review round 8 (both lenses): the adapter valued every exit for its floor and then dropped the
    value, so a $3B exit came LAST behind a $7.5M increase. With the shared contract's
    `prev_value_usd` (the exit's value on the previous quarter's book) the exit leads: hook, cover
    and narration line 2 — and the value is never drawn, said or written anywhere in the package."""
    oxy = move("OXY", "Occidental", "increased", shares=1_150_000.0, prev_shares=1_000_000.0,
               value_usd=57_500_000.0)                                            # +150K shares: a $7.5M move
    citi = move("C", "Citigroup", "no_longer_reported", shares=None, prev_shares=40_000_000.0,
                prev_value_usd=3_000_000_000.0)
    rec = berkshire(total_value_usd=299_000_000_000.0, filer_symbol=None, moves=(oxy, citi),
                    counts=(("increased", 1), ("no_longer_reported", 1)))
    assert [T._f13_move_value(m) for m in rec.moves] == [7_500_000.0, 3_000_000_000.0]
    out = compose(rec, TUE)
    assert out["hook"] == "Berkshire Hathaway's latest 13F no longer reports 1 holding from the quarter before."
    assert out["opening_card"]["logos"] == ["C"] and out["opening_card"]["figure"] == "1"
    assert out["opening_card"]["headline"] == "holding no longer reported in Berkshire Hathaway's 13F"
    assert out["video_script"][1] == "The holding no longer reported in this filing is Citigroup."
    assert out["video_script"][2] == "Compared with the quarter before, it also reported more shares of Occidental."
    assert out["captions"]["x"] == ("Berkshire Hathaway's 13F for the quarter ended Sep 30: 1 holding no longer "
                                    "reported, 1 with more shares.")
    assert [s["heading"] for s in out["image_spec"]["sections"]] == ["Reported more shares", "No longer reported"]
    assert out["image_spec"]["sections"][1]["rows"] == [{"logo": "C", "cells": ["Citigroup"]}]
    public = json.dumps({k: out[k] for k in T.COMPARED_FIELDS if k in out} | {"captions": out["captions"]})
    assert not re.search(r"\$3(\.0)?\s?(B|billion)|3,000,000,000", public)
    assert T.validate_package(out, record=rec) == []
    # the fact sheet carries the value: revalidate re-composes from it, and a tampered value that
    # changes the lead is caught (the record, not the output, decides the post)
    good = _jsonb(sheet(rec))
    assert good["record"]["moves"][1]["prev_value_usd"] == 3_000_000_000.0
    assert T.revalidate(_jsonb(out), fact_sheet=good, run_date=TUE) == []
    bad = copy.deepcopy(good)
    bad["record"]["moves"][1]["prev_value_usd"] = 1_000_000.0
    assert T.revalidate(_jsonb(out), fact_sheet=bad, run_date=TUE) != []
    # a fact sheet stored before the field existed reads it as None: the exit ranks last again
    legacy = copy.deepcopy(good)
    del legacy["record"]["moves"][1]["prev_value_usd"]
    assert compose(R.record_from_dict(legacy["record"]), TUE)["hook"].startswith(
        "Berkshire Hathaway's latest 13F reports more shares of 1 holding")


def test_a_long_title_draws_the_lead_and_the_next_kind_only():
    """Review round 8: under a title longer than F13_FULL_TITLE_CHARS (31) the image draws only the
    lead kind and the next — the kinds narration lines 2 and 3 cover — so the worker's `rows` layout
    always fits (proved in tests/test_marketing_news_layouts.py); the subtitle still counts every
    kind. At the threshold itself (Berkshire Hathaway's 31-character title) all three are drawn."""
    assert T.F13_FULL_TITLE_CHARS == len("Berkshire Hathaway's latest 13F") == 31
    out = compose(pershing(), TUE)                                  # a 47-character title
    assert out["image_spec"]["title"] == "Pershing Square Capital Management's latest 13F"
    assert out["image_spec"]["sections"] == [
        {"heading": "Reported more shares", "rows": [{"logo": "UBER", "cells": ["Uber"]}]},
        {"heading": "Reported fewer shares", "rows": [{"logo": "CMG", "cells": ["Chipotle Mexican Grill"]}]}]
    assert out["image_spec"]["subtitle"] == "1 with more shares · 1 with fewer shares · 1 newly reported"
    assert out["image_post"]["paragraphs"][:2] == ["Reported more shares: Uber.",
                                                   "Reported fewer shares: Chipotle Mexican Grill."]
    assert T.validate_package(out, record=pershing()) == []

    def headings(filer):
        moves = (_valued("CB", "Chubb", "newly_reported", value=6_000_000.0),
                 _valued("DAL", "Delta Air Lines", "increased", value=1_600_000_000.0),
                 _valued("COF", "Capital One", "decreased", value=800_000_000.0))
        o = compose(replace(f13(*moves), filer_name=filer), TUE)
        return len(o["image_spec"]["title"]), [s["heading"] for s in o["image_spec"]["sections"]]

    three = ["Newly reported", "Reported more shares", "Reported fewer shares"]
    assert headings("Abcdefghij Abcdefg") == (31, three)                    # possessive, at the threshold
    assert headings("Abcdefghij Abcdefgh") == (32, ["Reported more shares", "Reported fewer shares"])
    assert headings("Abcde Fghij's") == (31, three)                         # the ".of" twin, at it
    assert headings("Abcde Fghijk's") == (32, ["Reported more shares", "Reported fewer shares"])


@pytest.mark.parametrize("where", ["filer", "drawn_holding", "listed_only_in_captions"])
def test_a_name_word_too_long_to_draw_whole_is_refused(where):
    """Review round 8: a word longer than F13_MAX_WORD_CHARS (20) in the filer's or a holding's name
    is wider than the cover headline's column at the worker's floor in wide glyphs (a CardOverflow
    skips the DAY at render, after the build was accepted): the record is refused `slot_rejected` —
    one post lost. Every move is checked, drawn or not (it is narrated or listed in the captions).
    A 20-character word composes."""
    word = "Biopharmaceuticalsxyz"                                  # 21 characters
    assert len(word) == T.F13_MAX_WORD_CHARS + 1
    base = [_valued("STZ", "Constellation Brands", "newly_reported", value=9e8),
            _valued("DPZ", "Domino's Pizza", "newly_reported", value=8e8),
            _valued("LEN", "Lennar", "newly_reported", value=7e8),
            _valued("CB", "Chubb", "newly_reported", value=6e8)]
    filer = "Berkshire Hathaway"
    if where == "filer":
        filer = f"{word} Capital"
    elif where == "drawn_holding":
        base[0] = _valued("STZ", f"{word} Inc", "newly_reported", value=9e8)
    else:                                                           # the 4th new move: "+1 more" on the image
        base[3] = _valued("CB", f"{word} Group", "newly_reported", value=6e8)
    rec = replace(f13(*base), filer_name=filer)
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(rec, TUE)
    assert e.value.code == "slot_rejected" and "20 characters" in e.value.detail
    fits = word[:-1]
    ok = {"filer": replace(rec, filer_name=f"{fits} Capital"),
          "drawn_holding": replace(rec, moves=(replace(rec.moves[0], company=co("STZ", f"{fits} Inc")),
                                               *rec.moves[1:])),
          "listed_only_in_captions": replace(rec, moves=(*rec.moves[:3], replace(rec.moves[3],
                                                                                  company=co("CB", f"{fits} Group"))))}
    assert T.validate_package(compose(ok[where], TUE), record=ok[where]) == []


def test_rows_and_the_cover_go_largest_first_whatever_the_record_order():
    """Within a kind the image rows, narration lists, caption items and a symbol-less cover's logos
    run largest move first — the record's order is not trusted (a stable sort keeps it on a tie)."""
    moves = (_valued("LEN", "Lennar", "newly_reported", value=30_000_000.0),
             _valued("STZ", "Constellation Brands", "newly_reported", value=900_000_000.0),
             _valued("DPZ", "Domino's Pizza", "newly_reported", value=200_000_000.0),
             _valued("CB", "Chubb", "newly_reported", value=40_000_000.0))
    out = compose(f13(*moves, symbol=None), TUE)
    assert [r["logo"] for r in out["image_spec"]["sections"][0]["rows"]] == ["STZ", "DPZ", "CB"]
    assert out["image_spec"]["sections"][0]["more"] == "+1 more"
    assert out["opening_card"]["logos"] == ["STZ", "DPZ"]
    assert out["video_script"][1] == ("The holdings newly reported in this filing are Constellation Brands, "
                                      "Domino's Pizza, Chubb and 1 more.")
    assert "Constellation Brands, $900 million; Domino's Pizza, $200 million; Chubb, $40 million; Lennar, " \
           "$30 million." in out["captions"]["linkedin"]
    # more / fewer rank by the shares that changed, never by the remaining position
    fewer = (move("NUE", "Nucor", "decreased", shares=19.0, prev_shares=20.0, value_usd=1_900.0),   # $100 left
             move("KR", "Kroger", "decreased", shares=1.0, prev_shares=20.0, value_usd=50.0))       # $950 left
    out = compose(f13(*fewer), TUE)
    assert [r["logo"] for r in out["image_spec"]["sections"][0]["rows"]] == ["KR", "NUE"]


def test_the_image_draws_every_kind_with_moves_in_display_order():
    """new → more → fewer → no longer reported, whatever leads; ≤ 3 rows a kind; no value beside a
    more / fewer row (a 13F value is the position's, not the size of the change)."""
    moves = (_valued("CB", "Chubb", "newly_reported", value=6_000_000.0),
             _valued("DAL", "Delta Air Lines", "increased", value=1_600_000_000.0),
             _valued("COF", "Capital One", "decreased", value=800_000_000.0))
    out = compose(f13(*moves), TUE)
    secs = out["image_spec"]["sections"]
    assert [s["heading"] for s in secs] == ["Newly reported", "Reported more shares", "Reported fewer shares"]
    assert secs[0]["rows"][0]["cells"] == ["Chubb", "$6M"]
    assert secs[1]["rows"][0]["cells"] == ["Delta Air Lines"] and secs[2]["rows"][0]["cells"] == ["Capital One"]
    assert out["hook"].startswith("Berkshire Hathaway's latest 13F reports more shares of 1 holding")
    assert out["image_spec"]["subtitle"] == "1 with more shares · 1 with fewer shares · 1 newly reported"


def test_four_kinds_draw_the_three_largest_and_name_the_fourth():
    """template_onscreen.MAX_SECTIONS is 3 (the worker mirrors it): with moves of all four kinds the
    image draws the three kinds holding the largest moves — the lead always — in display order, and
    the subtitle still counts the fourth (a known limit, reported: the image cannot draw a 4th)."""
    assert onscreen.MAX_SECTIONS == 3

    def kind_moves(kind, base, names):
        return [_valued(sym, name, kind, value=base - i) for i, (sym, name) in enumerate(names)]

    moves = (kind_moves("newly_reported", 10_000_000.0, [("A", "Alpha")])
             + kind_moves("increased", 3_000_000_000.0, [("CC", "Charlie"), ("D", "Delta"), ("E", "Echo")])
             + kind_moves("decreased", 2_000_000_000.0, [("F", "Foxtrot"), ("G", "Golf"), ("H", "Hotel")])
             + [_valued("NU", "Nu Holdings", "no_longer_reported", value=1_000_000_000.0)])
    rec = f13(*moves, counts=(("newly_reported", 2), ("increased", 5), ("decreased", 4), ("no_longer_reported", 1)))
    out = compose(rec, TUE)
    secs = out["image_spec"]["sections"]
    # ranked: more ($3B), fewer ($2B), no longer reported ($1B), newly reported ($10M) — the last one off
    assert [s["heading"] for s in secs] == ["Reported more shares", "Reported fewer shares", "No longer reported"]
    assert [len(s["rows"]) for s in secs] == [3, 3, 1]
    assert out["image_spec"]["subtitle"] == ("5 with more shares · 4 with fewer shares · 1 no longer reported · "
                                             "2 newly reported")
    assert T.validate_package(out, record=rec) == []


def test_rows_over_the_image_budget_come_off_the_lowest_ranked_kind():
    """The image holds at most F13_IMAGE_ROWS (7, one under template_onscreen.MAX_ROWS: room to
    spare at the worker's floor, review round 8) rows: 3 kinds × 3 moves (a record of 3 + 3 + 2 is
    possible within THIRTEEN_F_MAX_MOVES) lose rows off the LOWEST-ranked kind still holding more
    than one (its "+k more" grows), never off the lead. Under a long title only the lead and the
    next kind are drawn (≤ 2 × 3 rows: nothing to trim)."""
    assert T.F13_IMAGE_ROWS == 7 < onscreen.MAX_ROWS == 8
    assert T.F13_LONG_TITLE_SECTIONS == 2 < onscreen.MAX_SECTIONS == 3
    by = {"newly_reported": [None] * 3, "increased": [None] * 3, "decreased": [None] * 3,
          "no_longer_reported": [None] * 3}
    ranked = ["decreased", "newly_reported", "no_longer_reported", "increased"]
    assert T._f13_image_rows(ranked, by, long_title=False) == {"decreased": 3, "newly_reported": 3,
                                                               "no_longer_reported": 1}
    by["no_longer_reported"] = [None]
    assert T._f13_image_rows(ranked, by, long_title=False) == {"decreased": 3, "newly_reported": 3,
                                                               "no_longer_reported": 1}
    by["newly_reported"] = [None] * 2
    assert T._f13_image_rows(ranked, by, long_title=False) == {"decreased": 3, "newly_reported": 2,
                                                               "no_longer_reported": 1}
    assert T._f13_image_rows(ranked, by, long_title=True) == {"decreased": 3, "newly_reported": 2}
    assert T._f13_image_rows(["increased"], {"increased": [None] * 7}, long_title=False) == {
        "increased": T.F13_ROWS_PER_KIND}
    # a record that fills the old 8-row image (3 + 3 + 2 moves) now draws 3 + 3 + 1
    moves = ([_valued(f"N{c}", f"New {c}", "newly_reported", value=9e8 - i) for i, c in enumerate("ABC")]
             + [_valued(f"I{c}", f"More {c}", "increased", value=8e8 - i) for i, c in enumerate("DEF")]
             + [_valued(f"D{c}", f"Fewer {c}", "decreased", value=7e8 - i) for i, c in enumerate("GH")])
    out = compose(f13(*moves), TUE)
    assert [len(s["rows"]) for s in out["image_spec"]["sections"]] == [3, 3, 1]
    assert out["image_spec"]["sections"][2]["more"] == "+1 more"


def test_narration_covers_the_lead_kind_then_the_next_largest():
    """Line 2 is the lead kind, line 3 the kind holding the next-largest move (not the next in
    display order); with one kind of moves, a counted-only kind by its count; else the explainer."""
    moves = (_valued("STZ", "Constellation Brands", "newly_reported", value=1_000_000_000.0),
             _valued("OXY", "Occidental Petroleum", "increased", value=10_000_000.0),
             _valued("KR", "Kroger", "decreased", value=500_000_000.0))
    lines = compose(f13(*moves), TUE)["video_script"]
    assert lines[1] == "The holding newly reported in this filing is Constellation Brands."
    assert lines[2] == "Compared with the quarter before, it also reported fewer shares of Kroger."
    one_kind = f13(_valued("STZ", "Constellation Brands", "newly_reported", value=1_000_000_000.0),
                   counts=(("newly_reported", 1), ("increased", 12), ("decreased", 3)))
    assert compose(one_kind, TUE)["video_script"][2] == "It reported more shares of 12 holdings than the quarter before."
    alone = f13(_valued("STZ", "Constellation Brands", "newly_reported", value=1_000_000_000.0))
    assert compose(alone, TUE)["video_script"][2] == T.LEXICON["f13.l.explainer2"]


def test_the_lead_and_image_rules_hold_over_seeded_records():
    """Seeded fuzz (≤ 8 moves of mixed kinds, values known or not, counts at or above the rows, a
    short and a long filer title): every record composes a valid package or refuses with a known
    code, and when it composes — the cover names the kind holding the largest known move (display
    order on a tie / no value), sized INDEPENDENTLY here (new: value; more / fewer: the changed
    shares' value; exit: `prev_value_usd`, never a stray `value_usd`); the image draws the
    min(3, kinds) highest-ranked kinds — only min(2, kinds) under a title longer than
    F13_FULL_TITLE_CHARS — in display order, each ≤ 3 rows, largest first, ≤ F13_IMAGE_ROWS rows in
    all; the subtitle names every counted kind, the lead first."""
    rng = random.Random(20261009)
    pool = [("STZ", "Constellation Brands"), ("DPZ", "Domino's Pizza"), ("LEN", "Lennar"), ("C", "Citigroup"),
            ("NU", "Nu Holdings"), ("UBER", "Uber"), ("CMG", "Chipotle Mexican Grill"), ("OXY", "Occidental"),
            ("KR", "Kroger"), ("DAL", "Delta Air Lines")]
    display = ["newly_reported", "increased", "decreased", "no_longer_reported"]
    filers = {"Berkshire Hathaway": False, "Pershing Square Capital Management": True}
    assert [len(f + "'s latest 13F") > T.F13_FULL_TITLE_CHARS for f in filers] == [False, True]

    def size(m):
        if m.move == "newly_reported":
            return m.value_usd
        if m.move == "no_longer_reported":
            return m.prev_value_usd
        return None if m.value_usd is None else m.value_usd * abs(m.shares - m.prev_shares) / m.shares

    composed, exits_led, long_seen = 0, 0, 0
    for _ in range(300):
        moves = []
        for sym, nm in rng.sample(pool, rng.randint(1, 8)):
            kind = rng.choice(display)
            value = rng.choice([None, rng.choice([1e6, 5e6, 2e8, 5e9]) * rng.random() + 1.0])
            if kind == "newly_reported":
                moves.append(move(sym, nm, kind, shares=1_000.0, value_usd=value))
            elif kind == "no_longer_reported":
                stray = rng.choice([None, None, 9e9])          # a value an exit cannot have: never its size
                moves.append(move(sym, nm, kind, prev_shares=1_000.0, value_usd=stray, prev_value_usd=value))
            else:
                s, p = (rng.randint(11, 40), 10) if kind == "increased" else (rng.randint(1, 9), 10)
                moves.append(move(sym, nm, kind, shares=float(s), prev_shares=float(p), value_usd=value))
        counts = tuple((k, sum(m.move == k for m in moves) + rng.choice([0, 0, 3]))
                       for k in R.THIRTEEN_F_MOVES if any(m.move == k for m in moves) or rng.random() < 0.3)
        filer = rng.choice(sorted(filers))
        rec = f13(*moves, counts=counts, symbol=rng.choice([None, "BRK-B"]))
        rec = replace(rec, filer_name=filer)
        try:
            out = compose(rec, TUE)
        except T.NewsTemplateRefused as e:
            assert e.code in T.REFUSAL_CODES
            continue
        composed += 1
        assert T.validate_package(out, record=rec) == []
        value = {m.company.symbol: size(m) for m in moves}
        assert value == {m.company.symbol: T._f13_move_value(m) for m in moves}
        kinds = [k for k in display if any(m.move == k for m in moves)]

        def top(kind):
            known = [value[m.company.symbol] for m in moves if m.move == kind and value[m.company.symbol] is not None]
            return max(known) if known else None

        ranked = sorted(kinds, key=lambda k: (top(k) is None, -(top(k) or 0.0), display.index(k)))
        best = ranked[0]
        exits_led += best == "no_longer_reported"
        assert _F13_HEADLINE_KIND[best] in out["opening_card"]["headline"], (moves, out["opening_card"])
        secs = out["image_spec"]["sections"]
        heading_kind = {T.LEXICON[f"f13.head.{T._F13_SHORT[k]}"]: k for k in display}
        drawn = [heading_kind[s["heading"]] for s in secs]
        n_sections = T.F13_LONG_TITLE_SECTIONS if filers[filer] else onscreen.MAX_SECTIONS
        long_seen += filers[filer] and len(kinds) > n_sections
        assert len(drawn) == min(n_sections, len(kinds)) and set(drawn) == set(ranked[:n_sections])
        assert drawn == [k for k in display if k in drawn]
        assert sum(len(s["rows"]) for s in secs) <= T.F13_IMAGE_ROWS
        for s in secs:
            vals = [value[r["logo"]] for r in s["rows"]]
            known = [v for v in vals if v is not None]
            assert len(s["rows"]) <= T.F13_ROWS_PER_KIND
            assert known == sorted(known, reverse=True) and vals[:len(known)] == known
        counted = {k for k, n in counts if n > 0}
        sub = out["image_spec"]["subtitle"].split(" · ")
        assert len(sub) == len(counted) and _F13_HEADLINE_KIND[best].split()[-2] in sub[0]
    assert composed >= 250 and exits_led >= 20 and long_seen >= 20, (composed, exits_led, long_seen)


@pytest.mark.parametrize("name", ["berkshire", "berkshire_full", "ark", "worst", "worst_long_title"])
def test_the_13f_image_and_cover_fit_the_workers_layout(name):
    """The image the templates compose lays out whole in the worker's own `rows` layout (no
    CardOverflow, which would skip the day) — every logo a wordmark, the widest case — and so does
    the opening card. `worst`: three kinds filling the F13_IMAGE_ROWS budget, 32-character names,
    three cells on every newly reported row, a "+k more" on each section, four 3-digit counts;
    `worst_long_title`: the same under a 39-character filer, whose title draws the lead and the
    next kind only. The adversarial sweep (60-character filers in the widest glyphs, the worst word
    packing) is in tests/test_marketing_news_layouts.py."""
    pytest.importorskip("PIL")
    from marketing import cards
    from marketing import news_layouts as nl

    if name.startswith("worst"):
        long = "Wwwwwwwww Wwwwwwwww Wwwwwwwwww"                     # + " N" = 32 characters
        mv = ([_valued(f"N{c}", f"{long} {c}", "newly_reported", value=999_900_000.0 - i,
                       listed=date(2026, 8, 1)) for i, c in enumerate("ABC")]
              + [_valued(f"I{c}", f"{long} {c}", "increased", value=900_000_000.0 - i) for i, c in enumerate("DEF")]
              + [_valued(f"D{c}", f"{long} {c}", "decreased", value=800_000_000.0 - i) for i, c in enumerate("GH")])
        filer = "Bill and Melinda Gates Foundation Trust" if name == "worst_long_title" else "Berkshire Hathaway"
        rec = berkshire(filer_name=filer, filer_symbol=None, moves=tuple(mv),
                        amended_on=date(2026, 11, 15), total_value_usd=5e12, position_count=900,
                        counts=(("newly_reported", 315), ("increased", 444), ("decreased", 333),
                                ("no_longer_reported", 222)))
        run = TUE
    else:
        raw = {"berkshire": after_floor(LIVE_BERKSHIRE_2026_Q2), "berkshire_full": LIVE_BERKSHIRE_2026_Q2,
               "ark": after_floor(LIVE_ARK_2026_Q2)}[name]
        rec, run = live(raw), LIVE_RUN
    out = compose(rec, run)
    if name.startswith("worst"):
        rows = [len(s["rows"]) for s in out["image_spec"]["sections"]]
        assert rows == ([3, 3] if name == "worst_long_title" else [3, 3, 1])
        assert sum(rows) <= T.F13_IMAGE_ROWS
        assert all(len(c) == 32 for s in out["image_spec"]["sections"] for r in s["rows"] for c in r["cells"][:1])
    font = str(_BACKEND / "marketing" / "assets" / "fonts" / "Inter-Bold.ttf")
    entries = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None, "bytes": None, "width": None,
                "height": None} for r in out["logo_refs"]]
    script = dict(out, logos=entries)
    table = cards.logo_table(script, {})
    image = nl.template_image_for_script(script, table)
    assert nl.layout_template(image, font_path=font, layout_engine="basic").step <= cards.RAMP_STEPS
    opening = cards.layout_card(cards.opening_spec(out["opening_card"], table), font_path=font,
                                layout_engine="basic")
    assert opening.step <= cards.RAMP_STEPS


# ── Money Map variants ────────────────────────────────────────────────────────


def test_money_map_loss_year_reads_as_losses_and_outlines_its_bars():
    out = compose(rivian(), THU)
    assert out["hook"] == "Rivian Automotive had a net loss of $66.38 for every $100 of revenue."
    assert out["video_script"][3] == "After interest, taxes and everything else, the net loss was $3.9 billion."
    flow = out["image_spec"]["flow"]
    assert [b["label"] for b in flow] == ["Revenue", "Gross loss", "Operating loss", "Net loss"]
    assert [b["style"] for b in flow] == ["fill", "outline", "outline", "outline"]
    assert out["opening_card"]["headline"] == "net loss for every $100 of Rivian Automotive revenue"
    assert out["image_spec"]["callout"] == "For every $100 of revenue, the net loss was $66.38"


def test_money_map_eliminations_change_the_basis_and_draw_an_outline_bar():
    out = compose(comcast(), THU)
    assert "of segment sales" in out["video_script"][1]
    bars = out["image_spec"]["segments"]
    assert bars[-2]["label"] == "Other" and bars[-1] == {
        "label": "Intersegment eliminations", "value": "-$4.3B", "ratio": 0.0524, "style": "outline"}
    assert out["video_script"][2] == "After all operating costs, operating profit was $23.3 billion that year."
    assert all(0 <= b["ratio"] <= 1 for b in bars + out["image_spec"]["flow"])
    # eliminations None (the adapter publishes none it cannot verify): no string claims any
    plain = json.dumps(compose(costco(), THU)).lower()
    assert "elimination" not in plain and "between its own segments" not in plain


@pytest.mark.parametrize("gp, op, l3", [
    (35.4e9, 10.4e9, "After the cost of sales, gross profit was $35.4 billion; after operating costs, operating "
                     "profit was $10.4 billion."),
    (35.4e9, None, "After the cost of sales, gross profit was $35.4 billion that year."),
    (None, 10.4e9, "After all operating costs, operating profit was $10.4 billion that year."),
    (None, None, "Revenue is the total the company reported from sales over the year."),
])
def test_money_map_cost_lines_never_guess_a_missing_bar(gp, op, l3):
    out = compose(costco(gross_profit_usd=gp, operating_profit_usd=op), THU)
    assert out["video_script"][2] == l3
    labels = [b["label"] for b in out["image_spec"]["flow"]]
    assert ("Gross profit" in labels) == (gp is not None)
    assert ("Operating profit" in labels) == (op is not None)


def test_money_map_implausible_figures_are_refused():
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(rivian(net_income_usd=-6_000_000_000.0), THU)       # a loss larger than revenue
    assert e.value.code == "implausible_figures"


# ── refusals ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("rec, run_date", [
    (ceo_roundup(), date(2026, 11, 24)),              # the window ended 9 days before the run
    (ceo_roundup(), date(2026, 11, 15)),              # the window has not ended
    (berkshire(), date(2026, 11, 12)),                # filed after the run date
    (berkshire(), date(2027, 3, 15)),                 # 122 days after filing
    (costco(), date(2027, 3, 5)),                     # 551 days after the fiscal year ended
], ids=["insider_old", "insider_future", "13f_future", "13f_old", "money_map_old"])
def test_stale_sources_are_refused(rec, run_date):
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(rec, run_date)
    assert e.value.code == "stale_source"


def test_non_records_and_unknown_series_are_refused(monkeypatch):
    for rec in ({"series": "ceo_buys"}, None, "news:ceo_buys:2026-11-09"):
        with pytest.raises(T.NewsTemplateRefused) as e:
            compose(rec, MON)
        assert e.value.code == "record_invalid"
    # a record whose series has no template (a series added to the rules before its template)
    monkeypatch.delitem(T.SERIES_SPECS, "congress_count")
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(congress(), CONGRESS_RUN)
    assert e.value.code == "record_invalid"


def test_pair_and_grid_series_are_refused_until_their_layouts_ship(monkeypatch):
    """Fail closed while `pair` / `grid` are not in SHIPPED_LAYOUTS (the worker would refuse them): a
    stake or a theme is refused `image_spec_invalid` — the day falls back, nothing reaches the worker.
    The plumbing step flips SHIPPED_LAYOUTS with SHIPPED_SERIES; this test then has nothing to prove."""
    monkeypatch.setattr(onscreen, "SHIPPED_LAYOUTS", REAL_SHIPPED_LAYOUTS)
    if {"pair", "grid"} <= set(REAL_SHIPPED_LAYOUTS):
        pytest.skip("pair and grid have shipped")
    for rec, run_date in ((nscale(), STAKES_RUN), (ai_chips(), THEME_RUN)):
        with pytest.raises(T.NewsTemplateRefused) as e:
            compose(rec, run_date)
        assert e.value.code == "image_spec_invalid"


def test_caller_bugs_raise_type_errors():
    with pytest.raises(TypeError):
        T.compose(ceo_roundup(), run_date="2026-11-16", store_state="live", allow_x_url=False)
    with pytest.raises(TypeError):
        T.compose(ceo_roundup(), run_date=MON, store_state="live", allow_x_url="no")


def test_congress_rows_are_dropped_and_a_week_of_them_is_refused():
    """Defence in depth: the adapter drops a member's raw reporting name first; a rendered name that
    is a member is dropped here too (never shown role-only — the role would still point at them)."""
    member = buy("GE", "General Electric", "director", "Josh Gottheimer", 500_000.0, 2_000.0, 1, [date(2026, 11, 10)])
    member_ceo = replace(member, role="ceo")
    assert R.is_congress_name("Josh Gottheimer")
    out = compose(week(GME, member_ceo), MON)
    assert "Gottheimer" not in json.dumps(out) and "General Electric" not in json.dumps(out)
    assert out["image_spec"]["layout"] == "spotlight" and out["logo_refs"] == [{"key": "GME", "name": "GameStop"}]
    mixed = compose(week(buy("NKE", "Nike", "cfo", None, 900_000.0, 9_000.0, 1, [date(2026, 11, 10)]), member,
                         series="insider_buys"), MON)
    assert "Gottheimer" not in json.dumps(mixed) and len(mixed["logo_refs"]) == 1
    assert mixed["image_spec"]["layout"] == "spotlight"
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(week(member, series="insider_buys"), MON)
    assert e.value.code == "too_few_rows"


def test_a_congress_name_anywhere_in_the_output_is_a_violation():
    out = compose(costco(), THU)
    out["image_post"]["paragraphs"][0] += " Nancy Pelosi."
    assert any(v["code"] == "congress_name" for v in T.validate_package(out))


@pytest.mark.parametrize("segment", ["50% Owned", "Signal Processing", "Followers", "Store❤", "Web http",
                                     "Copy Centers", "Whale Watching", "Cloud #1"])
def test_bad_slot_values_are_refused(segment):
    rec = costco(segments=(R.Segment("United States", 200e9), R.Segment(segment, 38.5e9),
                           R.Segment("Other International", 36.7e9)))
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(rec, THU)
    assert e.value.code == "slot_rejected"


def _unchecked(rec, **changes):
    """A copy of a frozen record with `changes` applied WITHOUT its own validation: a record that
    slipped past a gate (an older rule set, a future adapter path). Test-only, for the backstop."""
    obj = object.__new__(type(rec))
    for f in rec.__slots__:
        object.__setattr__(obj, f, changes.get(f, getattr(rec, f)))
    return obj


@pytest.mark.parametrize("name", ["C3.ai", "BigBear.ai Holdings", "1-800-FLOWERS.COM", "Amazon.com"])
def test_a_domain_shaped_name_is_refused_before_it_reaches_a_caption(name):
    """compliance:F3 (the template backstop) — a domain-shaped name is a bare link that X / Threads /
    Facebook autolink, and X refuses the post at publish time AFTER the owner approved it.
    `company_news_rules` refuses such a company or filer name when the record is built; a SEGMENT
    name is not checked there, and the template refuses every slot by the publisher's own
    definition (`post_copy.x_link_tokens`) whatever slipped past. Mutation-checked by hand
    (2026-10-09): without the check in `_slot_problem` the first assert goes red; without it in
    `_validate_words` the tamper cases below do."""
    assert T._slot_problem(name) == "a bare domain"
    segs = (R.Segment("United States", 200e9), R.Segment(name, 38.5e9), R.Segment("Other International", 36.7e9))
    cases = [
        (costco(segments=segs), THU),                                        # reachable: a segment name
        (week(_unchecked(GME, company=_unchecked(GME.company, name=name))), MON),
        (_unchecked(berkshire(), filer_name=name), TUE),
        (costco(company=_unchecked(co("COST", "Costco"), name=name)), THU),
    ]
    for rec, run_date in cases:
        with pytest.raises(T.NewsTemplateRefused) as e:
            compose(rec, run_date)
        assert e.value.code == "slot_rejected", (rec.series, e.value)
    # the abbreviations X never links stay usable names
    for ok in ("U.S. Bancorp", "D.R. Horton", "e.l.f. Beauty", "Philips N.V.", "Acme Inc.", "Lowe's"):
        assert T._slot_problem(ok) is None, ok


@pytest.mark.parametrize("mutate, field_", [
    (lambda o: o["image_post"]["paragraphs"].__setitem__(0, o["image_post"]["paragraphs"][0] + " See c3.ai."),
     "image_post.paragraphs[0]"),
    (lambda o: o["cards"][3].update(body=o["cards"][3]["body"] + " at costco.com"), "cards[3].body"),
    (lambda o: o["captions"].update(linkedin=o["captions"]["linkedin"] + "\n\nMore at bigbear.ai"),
     "captions.linkedin"),
    (lambda o: o.update(hook=o["hook"].replace("Costco", "Costco.com")), "hook"),
])
def test_a_bare_domain_in_any_body_is_a_violation(mutate, field_):
    out = compose(costco(), THU)
    mutate(out)
    found = {(v["field"], v["code"]) for v in T.validate_package(out)}
    assert (field_, "structure") in found, found
    """"Target" is a company, not a price target: names are masked before NEWS_BANNED_RE runs."""
    out = compose(costco(company=co("TGT", "Target")), THU)
    assert out["image_spec"]["title"] == "How Target makes money"
    out["hook"] = out["hook"].replace("Target", "the price target")
    assert any(v["code"] == "banned_word" for v in T.validate_package(out, record=costco(company=co("TGT", "Target"))))


def test_the_percent_rule():
    assert T._percent_ok("57% of revenue") and T._percent_ok("no percent sign")
    assert T._percent_ok("less than 1% of revenue.")
    assert not T._percent_ok("shares rose 5%") and not T._percent_ok("57% often") and not T._percent_ok("57%")
    out = compose(costco(), THU)
    out["cards"][1]["body"] = "United States 73%"
    assert any(v["code"] == "structure" for v in T.validate_package(out))


@pytest.mark.parametrize("mutate, code", [
    (lambda o: o.update(hook=o["hook"] + "!"), "structure"),
    (lambda o: o["captions"].update(x=o["captions"]["x"] + " $COST"), "structure"),
    (lambda o: o["cards"][0].update(body="Revenue \U0001F4C8"), "structure"),
    (lambda o: o["captions"].update(linkedin=o["captions"]["linkedin"] + " #costco"), "structure"),
    (lambda o: o["video_script"].__setitem__(0, "Costco will keep growing its revenue every single year ahead."),
     "banned_word"),
    (lambda o: o.update(hook="Costco shares soared after the record quarter this year."), "banned_word"),
])
def test_structure_and_banned_word_violations(mutate, code):
    out = compose(costco(), THU)
    mutate(out)
    assert code in {v["code"] for v in T.validate_package(out)}


def test_class_c_speaks_only_the_verb_table():
    out = compose(berkshire(), TUE)
    out["video_script"][1] = "Berkshire Hathaway bought Constellation Brands, Domino's Pizza and Lennar this time."
    assert "off_table_verb" in {v["code"] for v in T.validate_package(out)}
    out = compose(ceo_roundup(), MON)
    out["video_script"][3] = "2 more chief executives purchased their own company's stock in the same week."
    assert "off_table_verb" in {v["code"] for v in T.validate_package(out)}
    for name, factory, run_date in SAMPLES:
        text = json.dumps(compose(factory(), run_date))
        if factory().series in ("ceo_buys", "insider_buys"):
            assert "disclosed buying" in text
        if factory().series == "thirteen_f":
            assert "newly reported" in text or "no longer reported" in text


def test_too_few_outlets_is_refused(monkeypatch):
    monkeypatch.setattr(post_copy, "body_budget", lambda *a, **k: 20)
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(costco(), THU)
    assert e.value.code == "too_few_outlets"


# ── captions: the budget matrix ───────────────────────────────────────────────


@pytest.mark.parametrize("store_state", post_copy.STORE_STATES)
@pytest.mark.parametrize("allow_x_url", [False, True])
@pytest.mark.parametrize("name, factory, run_date", SAMPLES, ids=[s[0] for s in SAMPLES])
def test_the_caption_budget_matrix(name, factory, run_date, store_state, allow_x_url):
    out = compose(factory(), run_date, store_state, allow_x_url)
    assert len(out["posts"]) >= T.NEWS_MIN_OUTLETS
    assert "threads" in out["posts"], out["dropped_outlets"]
    if factory().series in ("ceo_buys", "insider_buys", "thirteen_f", "money_map", "congress_count", "earnings"):
        assert {"x", "bluesky"} <= set(out["posts"]), out["dropped_outlets"]
    # X / Bluesky drop only when NO headline variant fits (a long stake: two long names and a
    # dated figure) — never sliced, never undated, never by mistake
    heads = _parts(factory(), run_date).image_headlines
    for field_ in ("x", "bluesky"):
        if field_ not in out["posts"]:
            assert [d["code"] for d in out["dropped_outlets"][field_]] == ["over_budget"], name
            budget = post_copy.body_budget(field_, f"news:{out['series']}", run_date, allow_x_url=allow_x_url,
                                           store_state=store_state, authorship="template")
            assert all(post_copy.measured_length(field_, h) > budget for h in heads), (name, field_)
    for platform, post in out["posts"].items():
        composed = post_copy.ComposedPost(post["platform"], post["title"], post["caption"])
        assert post_copy.check_composed(composed, run_date, "template") == [], (name, platform)
        assert "Written with AI assistance" not in post["caption"] and "AI-assisted" not in post["caption"]
        field_ = "youtube_description" if platform == "youtube" else platform
        body = out["captions"][field_]
        assert post["caption"].startswith(body)
        assert post_copy.measured_length(field_, body) <= post_copy.body_budget(
            field_, f"news:{out['series']}", run_date, allow_x_url=allow_x_url, store_state=store_state,
            authorship="template")
        if platform in ("x", "bluesky", "threads"):
            assert "\n\n" not in body                                          # the headline only
    assert out["captions"]["youtube_title"] == out["posts"]["youtube"]["title"]


def test_variants_go_long_then_short_then_drop_paragraphs():
    out = compose(comcast(), THU, "preorder", True)
    assert out["captions"]["x"] == "How Comcast makes money, from its fiscal 2025 results."      # long is 113
    assert out["captions"]["threads"].startswith("How Comcast makes money: $123.7 billion")
    roomy = compose(comcast(), THU, "live", False)
    assert roomy["captions"]["x"].startswith("How Comcast makes money: $123.7 billion")


def test_paragraphs_are_dropped_from_the_end_before_an_outlet_is(monkeypatch):
    real = post_copy.body_budget

    def tight(field_, *a, **k):
        return 260 if field_ == "linkedin" else real(field_, *a, **k)

    full = compose(costco(), THU)["captions"]["linkedin"].split("\n\n")
    monkeypatch.setattr(post_copy, "body_budget", tight)
    out = compose(costco(), THU)
    parts = out["captions"]["linkedin"].split("\n\n")
    assert len("\n\n".join(parts)) <= 260
    assert 1 < len(parts) < len(full)
    assert parts[1:] == full[1:len(parts)]               # trailing paragraphs dropped, never sliced
    assert "linkedin" in out["posts"]


# ── image post (alt text), image spec, opening card, logos ────────────────────


def test_image_post_is_name_free_alt_text_that_passes_the_schema():
    for name, factory, run_date in SAMPLES:
        out = compose(factory(), run_date)
        ip = out["image_post"]
        assert image_post_problem(ip) is None, name
        assert 2 <= len(ip["paragraphs"]) <= 4 and ip["paragraphs"][-1].startswith("Source: ")
        text = json.dumps(ip)
        for person in out["persons"]:
            assert person.split()[-1] not in text, name
        assert "Cohen" not in text and "Smith-Jones" not in text


def test_image_spec_and_opening_card_validate_against_the_records_logos():
    for name, factory, run_date in SAMPLES:
        out = compose(factory(), run_date)
        keys = {r["key"] for r in out["logo_refs"]}
        assert onscreen.validate_image_spec(out["image_spec"], keys, footer=out["image_footer"]) is None, name
        assert onscreen.validate_opening_card(out["opening_card"], keys) is None, name
        assert out["image_spec"]["footer"] == out["image_footer"]
        assert len(out["logo_refs"]) <= onscreen.MAX_LOGOS
        assert T.logo_refs(out) == out["logo_refs"]
        strings = onscreen.image_strings(out["image_spec"], out["logo_refs"])
        assert out["image_footer"] in strings and strings[0] == out["image_spec"]["kicker"]


def test_logos_attached_later_must_carry_the_records_names():
    out = compose(ceo_roundup(), MON)
    logos = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None, "bytes": None, "width": None,
              "height": None} for r in out["logo_refs"]]
    assert T.validate_package({**out, "logos": logos}) == []
    renamed = copy.deepcopy(logos)
    renamed[0]["name"] = "Buy GameStop now"
    assert "logo_mismatch" in {v["code"] for v in T.validate_package({**out, "logos": renamed})}
    assert "logo_mismatch" in {v["code"] for v in T.validate_package({**out, "logos": logos[:1]})}
    extra = logos + [{"key": "TSLA", "name": "Tesla"}]
    assert "logo_mismatch" in {v["code"] for v in T.validate_package({**out, "logos": extra})}


# ── revalidate ────────────────────────────────────────────────────────────────


def _jsonb(value):
    """What JSONB hands back: keys reordered, integral floats re-serialised as ints."""
    def shuffle(v):
        if isinstance(v, dict):
            items = list(v.items())
            random.Random(7).shuffle(items)
            return {k: shuffle(x) for k, x in items}
        if isinstance(v, list):
            return [shuffle(x) for x in v]
        if isinstance(v, float) and v.is_integer():
            return int(v)
        return v
    return shuffle(json.loads(json.dumps(value)))


@pytest.mark.parametrize("name, factory, run_date", SAMPLES, ids=[s[0] for s in SAMPLES])
def test_revalidate_passes_an_untouched_package_after_a_jsonb_round_trip(name, factory, run_date):
    rec = factory()
    out = compose(rec, run_date, "preorder", True)
    assert T.revalidate(out, fact_sheet=sheet(rec), run_date=run_date) == []
    stored = _jsonb(out)
    # what script_service adds before the INSERT (logos, frozen formats) does not disturb it
    stored["logos"] = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None} for r in out["logo_refs"]]
    stored["post_formats"] = {p: "image" for p in stored["posts"]}
    assert T.revalidate(stored, fact_sheet=_jsonb(sheet(rec)), run_date=run_date) == []


@pytest.mark.parametrize("mutate, code", [
    (lambda o: o.update(hook=o["hook"].replace("$74.4", "$75.4")), "field_mismatch"),
    (lambda o: o["video_script"].__setitem__(1, o["video_script"][1].replace("2 ", "3 ", 1)), "field_mismatch"),
    (lambda o: o["cards"][1].update(body="2 purchases · 3M shares · $80M"), "field_mismatch"),
    (lambda o: o["opening_card"].update(figure="$80M"), "field_mismatch"),
    (lambda o: o["image_spec"]["sections"][0]["rows"][0]["cells"].__setitem__(2, "$80M"), "field_mismatch"),
    (lambda o: o["image_post"]["paragraphs"].__setitem__(0, "GameStop, CEO, $80M."), "field_mismatch"),
    # (news-v9: the roundup's X headline is the short "At least 3 CEOs …" variant — tamper its count)
    (lambda o: o["posts"]["x"].update(caption=o["posts"]["x"]["caption"].replace("At least 3", "At least 4")),
     "field_mismatch"),
    (lambda o: o["captions"].update(x=o["captions"]["x"].replace("At least 3", "At least 4")), "field_mismatch"),
    (lambda o: o.update(image_footer=o["image_footer"].replace("Nov 9", "Nov 8")), "field_mismatch"),
    (lambda o: o.update(disclaimer_card=o["disclaimer_card"].replace("Nov 16", "Nov 17")), "field_mismatch"),
    (lambda o: o.update(persons=[]), "field_mismatch"),
    (lambda o: o.update(template_version="news-v0"), "template_changed"),
    (lambda o: o.update(run_date="2026-11-17"), "provenance"),
    (lambda o: o.update(store_state="soon"), "provenance"),
])
def test_revalidate_refuses_any_tampered_public_field(mutate, code):
    rec = ceo_roundup()
    out = compose(rec, MON)
    before = copy.deepcopy(out)
    mutate(out)
    assert out != before          # every tamper really changes the package (never a no-op replace)
    got = {v["code"] for v in T.revalidate(out, fact_sheet=sheet(rec), run_date=MON)}
    assert code in got, got


def test_revalidate_refuses_tampered_facts():
    rec = ceo_roundup()
    out = compose(rec, MON)
    facts = sheet(rec)
    facts["record"]["rows"][0]["amount_usd"] = 80_000_000.0
    assert "field_mismatch" in {v["code"] for v in T.revalidate(out, fact_sheet=facts, run_date=MON)}
    facts = sheet(rec)
    facts["record"]["rows"][0]["person_name"] = "Josh Gottheimer"
    got = {v["code"] for v in T.revalidate(out, fact_sheet=facts, run_date=MON)}
    assert got & {"field_mismatch", "facts_invalid"}
    facts = sheet(rec)
    facts["source_ref"] = "news:ceo_buys:2026-11-02"
    assert {v["code"] for v in T.revalidate(out, fact_sheet=facts, run_date=MON)} == {"facts_invalid"}
    facts = sheet(rec)
    facts["record"]["schema"] = 2
    assert {v["code"] for v in T.revalidate(out, fact_sheet=facts, run_date=MON)} == {"facts_invalid"}
    other = sheet(costco())
    assert T.revalidate(out, fact_sheet=other, run_date=MON) != []


def test_revalidate_refuses_a_run_date_it_cannot_recompose():
    rec = ceo_roundup()
    out = compose(rec, MON)
    got = {v["code"] for v in T.revalidate(out, fact_sheet=sheet(rec), run_date=date(2026, 11, 30))}
    assert "recompose_refused" in got or "provenance" in got


@pytest.mark.parametrize("junk", [None, {}, [], "text", {"template_version": "news-v1"},
                                  {**{k: None for k in T._REQUIRED}}])
def test_validate_and_revalidate_never_raise_on_junk(junk):
    assert T.validate_package(junk) != []
    assert T.revalidate(junk, fact_sheet=sheet(ceo_roundup()), run_date=MON) != []
    assert T.revalidate(compose(ceo_roundup(), MON), fact_sheet=junk, run_date=MON) != []


def test_compared_fields_cover_every_public_field():
    out = compose(ceo_roundup(), MON)
    inputs = {"run_date", "store_state", "allow_x_url"}
    assert set(T.COMPARED_FIELDS) | inputs == set(out)


def test_the_narration_is_what_the_server_will_check_the_voice_against():
    from app.services.marketing.run_service import narration_words
    out = compose(ceo_roundup(), MON)
    words = narration_words(out)
    assert words[:3] == ["gamestop's", "chief", "executive"] and "$74.4" in words


# ── outliers: long names, extreme amounts, year-crossing windows ──────────────


def test_long_names_fall_to_the_shorter_variants_instead_of_refusing():
    gates = berkshire(filer_name="Bill and Melinda Gates Foundation Trust", filer_symbol=None,
                      moves=(move("OXY", "Occidental Petroleum", "increased", shares=12.0, prev_shares=10.0),),
                      counts=(("increased", 3),))
    out = compose(gates, TUE)
    # the long form is 18 words; its ".short" twin is exactly 14 (the boundary)
    assert out["hook"] == "Bill and Melinda Gates Foundation Trust's latest 13F reports more shares of 3 holdings."
    assert len(out["hook"].split()) == T.HOOK_WORDS[1]
    four = compose(costco(company=co("WMS", "Advanced Drainage Systems Corp")), THU)     # 4 words: ".short"
    assert four["hook"] == "Advanced Drainage Systems Corp kept $2.94 of every $100 of revenue as net income."
    five = co("NWCS", "Northwind Cold Storage of Ohio")                                # 5 words: ".min"
    assert compose(costco(company=five), THU)["hook"] == (
        "Northwind Cold Storage of Ohio: $2.94 of net income per $100 of revenue.")
    assert compose(rivian(company=five), THU)["hook"] == (
        "Northwind Cold Storage of Ohio: a net loss of $66.38 per $100 of revenue.")


def test_a_year_crossing_form4_window():
    run = date(2027, 1, 4)                                      # a Monday; the window is Dec 28 - Jan 3
    rec = week(buy("GME", "GameStop", "ceo", "Ryan Cohen", 1_000_000.0, 40_000.0, 2,
                   [date(2026, 12, 30), date(2027, 1, 2)]), start=date(2026, 12, 28), end=date(2027, 1, 3))
    out = compose(rec, run)
    assert out["opening_card"]["kicker"] == "FILED LAST WEEK · FORM 4"
    assert "Dec 28, 2026–Jan 3, 2027" in out["image_footer"]
    assert out["video_script"][0] == ("The filings are by the company's chief executive and were filed from "
                                      "December 30th, 2026 to January 2nd.")
    assert "Dec 30, 2026-Jan 2, 2027" in out["captions"]["x"]


def _outlier_records(seed, n):
    """Records at the edges the adapter can hand over: 32-character names, possessive twins, five rows,
    eight moves, unknown counts, fractional shares, $100K and $5B amounts, losses, eliminations."""
    rng = random.Random(seed)
    names = ["GameStop", "Lowe's", "Procter & Gamble", "Constellation Software Holdings", "AT&T", "NVIDIA",
             "International Flavors Fragrance", "Domino's Pizza", "Target", "Dick's Sporting Goods", "Uber"]
    people = [None, "Ryan Cohen", "Anna Smith-Jones", "Michael D. Sicilia", "Ryan Stock", "Josh Gottheimer"]
    segs = ["iPhone", "Services", "Wearables, Home and Accessories", "Connectivity and Platforms", "Data Center"]
    out = []
    for i in range(n):
        kind = i % 3
        if kind == 0:
            series = rng.choice(["ceo_buys", "insider_buys"])
            rows = []
            for j, name in enumerate(rng.sample(names, rng.randint(1, 5))):
                d1 = date(2026, 11, 9) + __import__("datetime").timedelta(days=rng.randrange(0, 7))
                role = "ceo" if series == "ceo_buys" else rng.choice(["cfo", "director"])
                rows.append(buy("ABCDE"[j] + "QZ"[j % 2], name, role, rng.choice(people),
                                rng.choice([100_000.0, 999_950.0, 74_400_000.0, 4_999_999_999.0]),
                                rng.choice([1.0, 850.5, 48_321.0, 2_987_654.0]), rng.randint(1, 40), [d1],
                                holding=rng.choice(["direct", "mixed", "indirect"]), amended=rng.random() < .3))
            rows.sort(key=lambda r: (-r.amount_usd, r.company.name.casefold(), r.company.symbol))
            out.append((week(*rows, series=series), MON))
        elif kind == 1:
            kinds = R.THIRTEEN_F_MOVES
            moves = []
            for j, name in enumerate(rng.sample(names, rng.randint(1, 8))):
                k = rng.choice(kinds)
                kw = {"newly_reported": dict(shares=1.0, prev_shares=0.0, value_usd=rng.choice([None, 1e6]),
                                             listed_on=rng.choice([None, date(2026, 8, 13)])),
                      "increased": dict(shares=12.0, prev_shares=10.0), "decreased": dict(shares=8.0, prev_shares=10.0),
                      "no_longer_reported": {}}[k]
                moves.append(move("ABCDEFGH"[j] + "Q", name, k, **kw))
            present = [k for k in kinds if any(m.move == k for m in moves)]
            counts = tuple((k, sum(m.move == k for m in moves) + rng.choice([0, 4])) for k in kinds if k in present)
            out.append((berkshire(filer_name=rng.choice(["Berkshire Hathaway", "Moody's", "Fisher Investments",
                                                         "Pershing Square Capital Management"]),
                                  filer_symbol=rng.choice([None, "BRK-B"]), moves=tuple(moves), counts=counts,
                                  amended_on=rng.choice([None, date(2026, 11, 14)])), TUE))
        else:
            picked = rng.sample(segs, rng.randint(2, 5))
            vals = [rng.choice([1e6, 3.3e9, 47.9e9]) for _ in picked]
            elim = rng.choice([None, -1e6, -4.3e9])
            rev = sum(vals) + (elim or 0.0)
            out.append((costco(company=co("ZQ", rng.choice(names)),
                               segments=tuple(R.Segment(s, v) for s, v in zip(picked, vals)),
                               eliminations_usd=elim, revenue_usd=rev,
                               gross_profit_usd=rng.choice([None, 0.4 * rev, -0.01 * rev]),
                               operating_profit_usd=rng.choice([None, 0.01 * rev, -0.7 * rev]) if False else None,
                               net_income_usd=rng.choice([0.0, 0.03 * rev, -0.6 * rev, rev])), THU))
    return out


def test_outlier_records_compose_cleanly_or_refuse_with_a_known_code():
    """Seeded fuzz (the full 700-record run is in the 2026-10-09 hardening notes): every record either
    composes a package that validates and survives a JSONB round trip through `revalidate`, or is
    refused with one of REFUSAL_CODES — never another exception."""
    outcomes = {}
    for rec, run_date in _outlier_records(20261009, 45):
        # the generator draws people WITH repetition; a week naming one person on two rows (which the
        # adapter never hands over) is refused `record_invalid` since review round 10 — and only it is
        repeated = isinstance(rec, R.InsiderBuysWeek) and _repeats_a_person(rec)
        try:
            out = compose(rec, run_date, "preorder", True)
        except T.NewsTemplateRefused as e:
            assert e.code in T.REFUSAL_CODES
            assert (e.code == "record_invalid") == repeated, (e.code, repeated)
            outcomes[e.code] = outcomes.get(e.code, 0) + 1
            continue
        assert not repeated
        assert T.validate_package(out, record=rec) == []
        assert T.revalidate(_jsonb(out), fact_sheet=_jsonb(sheet(rec)), run_date=run_date) == []
        outcomes["ok"] = outcomes.get("ok", 0) + 1
    assert outcomes.get("ok", 0) >= 35, outcomes
    # only all-Congress weeks and weeks naming one person twice are refused
    assert set(outcomes) <= {"ok", "too_few_rows", "record_invalid"}, outcomes


def _repeats_a_person(rec):
    named = [r.person_name for r in rec.rows if r.person_name]
    return len(set(named)) != len(named)


def test_a_segment_share_never_exceeds_the_whole():
    """Small eliminations keep the revenue basis — unless one segment alone exceeds revenue."""
    rec = costco(segments=(R.Segment("Warehouses", 276_000_000_000.0), R.Segment("Online", 3_000_000_000.0)),
                 eliminations_usd=-3_800_000_000.0)                   # 1.4% of revenue, segment > revenue
    out = compose(rec, THU)
    assert "of segment sales" in out["video_script"][1]
    shares = [int(x) for x in re.findall(r"(\d+)% of", out["video_script"][1])]
    assert shares and max(shares) <= 100
    small = compose(costco(eliminations_usd=-1_000_000_000.0, other_usd=1_000_000_000.0), THU)
    assert "of revenue" in small["video_script"][1]


def test_validation_stays_fast_on_a_huge_tampered_package():
    """A stored row tampered with 200,000-character strings is refused quickly, never a hang on the
    single web worker: an over-long string is refused (MAX_STRING_CHARS) without being scanned, and
    every other check is linear (measured 2026-10-09: ~0.6 s on 400,000 characters before the cap)."""
    import time
    out = compose(ceo_roundup(), MON)
    big = "a · " * 50_000
    out.update(image_footer="Educational only · not investment advice · Source: " + big
               + " · Caydex · Not affiliated with anyone named", hook=big)
    out["captions"]["x"] = "%" * 200_000
    t = time.perf_counter()
    found = T.validate_package(out)
    assert time.perf_counter() - t < 2.0
    codes = {v["code"] for v in found}
    assert {"disclaimer_invalid", "script_shape", "schema"} <= codes
    assert {"hook", "captions.x"} <= {v["field"] for v in found if v["code"] == "schema"}


# ══ drop 2b: Congress Count, Company Stakes, Earnings vs Estimates, Theme Explainer ══════════════════
#
# The 2b templates are composed in code; a run reaches them only once SHIPPED_SERIES and the
# per-series switch (MARKETING_NEWS_SERIES) list them, and the stake / theme images validate only
# with `pair` / `grid` shipped — every test here runs with both allowed IN MEMORY (the autouse
# fixture above), the state the plumbing step's flip produces.

SAMPLES_2B = [s for s in SAMPLES if s[0] in GOLDEN_2B]


def _codes(out, record=None):
    return {v["code"] for v in T.validate_package(out, record=record)}


@pytest.mark.parametrize("name, factory, run_date", SAMPLES_2B, ids=[s[0] for s in SAMPLES_2B])
def test_2b_samples_name_companies_only_and_carry_their_class(name, factory, run_date):
    rec = factory()
    out = compose(rec, run_date)
    assert out["persons"] == [] and out["series"] == rec.series
    assert out["content_class"] == R.SERIES_CLASS[rec.series] == ("C" if rec.series == "congress_count" else "F")
    assert out["image_spec"]["layout"] == SERIES_LAYOUT[rec.series]
    head = f"Educational only · not investment advice · Source: {R.source_label(rec)} · "
    assert out["image_footer"].startswith(head)
    assert "sell" not in json.dumps(out).lower().split()                    # NEWS_BANNED_RE's "sells?"


@pytest.mark.parametrize("name, factory, run_date", SAMPLES_2B, ids=[s[0] for s in SAMPLES_2B])
def test_2b_revalidate_refuses_a_tampered_figure_or_fact(name, factory, run_date):
    rec = factory()
    out = compose(rec, run_date)
    tampered = copy.deepcopy(out)
    tampered["hook"] = tampered["hook"].replace(tampered["hook"].split()[0], "Contoso", 1)
    assert "field_mismatch" in {v["code"] for v in T.revalidate(tampered, fact_sheet=sheet(rec), run_date=run_date)}
    facts = sheet(rec)
    body = facts["record"]
    if rec.series == "congress_count":
        body["members"] = 4
    elif rec.series == "company_stakes":
        body["value_usd"] = body["value_usd"] * 2
    elif rec.series == "earnings":
        body["eps_actual"] = body["eps_actual"] + 0.01
    else:
        body["members"][0]["top_segment"] = "Gaming"
    assert "field_mismatch" in {v["code"] for v in T.revalidate(out, fact_sheet=facts, run_date=run_date)}


def test_the_2b_age_bounds_are_the_adapters():
    """The template refuses as stale exactly what the adapter's gates already drop — never a candidate
    the adapter kept for being one day younger than the template allows."""
    assert T.STAKE_MAX_VERIFIED_AGE_DAYS == R.STAKE_STALE_DAYS
    assert T.STAKE_MAX_AS_OF_AGE_DAYS == R.STAKE_MAX_AGE_DAYS
    assert T.THEME_MAX_AGE_DAYS == R.THEME_STALE_DAYS
    # a count read on ANY Congress Count Tuesday (day 8..14 of the next month) is settled: month end +
    # CONGRESS_SETTLE_DAYS is never after the earliest such day
    assert T.CONGRESS_SETTLE_DAYS <= selection.CONGRESS_DAYS[0]
    for month in ("2026-02", "2026-04", "2026-11", "2027-02", "2028-02"):
        end = R.month_end_of(month)
        first = (end + __import__("datetime").timedelta(days=1)).replace(day=selection.CONGRESS_DAYS[0])
        assert (first - end).days >= T.CONGRESS_SETTLE_DAYS
        assert compose(congress(month=month, fetched_on=first), first)["source_ref"] == f"news:congress_count:{month}"
    assert T.THEME_MIN_FACTS <= R.THEME_MIN_FACTS and R.CONGRESS_MIN_MEMBERS == 2


# ── Congress Count ────────────────────────────────────────────────────────────


def test_congress_states_a_disclosed_count_dated_by_its_month_and_no_dollars():
    out = compose(congress(), CONGRESS_RUN)
    assert out["hook"] == "3 members of Congress disclosed purchases of Accenture stock in November 2026."
    assert out["opening_card"]["kicker"] == out["image_spec"]["kicker"] == "CONGRESS · DISCLOSED IN NOVEMBER"
    assert out["opening_card"]["figure"] == out["image_spec"]["figure"] == "3"
    for f, body in out["captions"].items():
        head = body.split("\n", 1)[0]
        assert "disclosed purchases of Accenture stock in November 2026" in head or (
            "disclosed purchases of Accenture stock in Nov 2026" in head), f
    assert "$" not in json.dumps(out)                                       # posts and suffixes included
    assert out["source_ref"] == "news:congress_count:2026-11"
    # every headline variant is dated by the disclosure month (an undated count is never offered)
    for head in _parts(congress(), CONGRESS_RUN).image_headlines + _parts(congress(), CONGRESS_RUN).youtube_titles:
        assert "November 2026" in head or "Nov 2026" in head, head


@pytest.mark.parametrize("name, hook", [
    ("Booz Allen Hamilton", "3 members of Congress disclosed purchases of Booz Allen Hamilton stock in November "
                            "2026."),
    ("Fair Isaac Rollins Cintas", "3 members of Congress disclosed purchases of Fair Isaac Rollins Cintas stock "
                                  "in November."),
    ("Fair Isaac Rollins Cintas Ecolab", "3 members of Congress disclosed Fair Isaac Rollins Cintas Ecolab stock "
                                         "purchases in November."),
])
def test_congress_hooks_fall_to_shorter_variants_for_longer_names(name, hook):
    out = compose(congress(company=co("ACN", name)), CONGRESS_RUN)
    assert out["hook"] == hook and len(hook.split()) <= T.HOOK_WORDS[1]
    with pytest.raises(T.NewsTemplateRefused) as e:                        # six words: no hook fits
        compose(congress(company=co("ACN", "Fair Isaac Rollins Cintas Eco Co")), CONGRESS_RUN)
    assert e.value.code == "script_shape"


@pytest.mark.parametrize("rec, run_date, code", [
    (_unchecked(congress(), members=1), CONGRESS_RUN, "too_few_rows"),       # "1 member" points at a person
    (_unchecked(congress(), members=0), CONGRESS_RUN, "too_few_rows"),
    (_unchecked(congress(), members=True), CONGRESS_RUN, "too_few_rows"),
    (_unchecked(congress(), members=536), CONGRESS_RUN, "too_few_rows"),
    (congress(month="2026-10", fetched_on=date(2026, 11, 10)), CONGRESS_RUN, "stale_source"),   # not last month
    (congress(), date(2027, 1, 12), "stale_source"),                       # a month later
    (congress(fetched_on=date(2026, 12, 6)), CONGRESS_RUN, "stale_source"),  # read < 7 days after the month
    (congress(fetched_on=date(2026, 12, 9)), CONGRESS_RUN, "stale_source"),  # read after the run
    (congress(fetched_on=date(2026, 12, 7)), date(2026, 12, 15), "stale_source"),  # read 8 days before the run
    (congress(company=_unchecked(co("ACN", "Accenture"), name="Signal Hill")), CONGRESS_RUN, "slot_rejected"),
    (congress(company=co("ACN", "Supercalifragilisticex Co")), CONGRESS_RUN, "slot_rejected"),  # a 21-char word
], ids=["one_member", "zero", "bool", "over_535", "older_month", "month_later", "read_too_early", "read_after_run",
        "read_too_long_ago", "slot_word", "word_too_long"])
def test_congress_refusals(rec, run_date, code):
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(rec, run_date)
    assert e.value.code == code


#: Every kind of word that narrows a count to a member (rules §1: no party, state, district,
#: committee, chamber or title) — matched wherever it lands, the company name aside.
NARROWING = ["senator", "Senators", "the Senate", "a representative", "Rep. Smith", "congressman", "congresswomen",
             "lawmakers", "Republican", "Democrats", "Democratic", "GOP", "party", "House", "chamber", "committee",
             "subcommittee", "district", "state", "states", "Texas", "New York", "caucus", "Speaker", "chairman",
             "majority", "incumbents", "delegation", "bipartisan"]
_NARROW_FIELDS = {
    "hook": lambda o, w: o.update(hook=o["hook"].replace("Congress", f"Congress ({w})")),
    "video_script[1]": lambda o, w: o["video_script"].__setitem__(1, o["video_script"][1] + f" One is {w}."),
    "cards[2].body": lambda o, w: o["cards"][2].update(body=o["cards"][2]["body"] + f" · {w}"),
    "image_spec.lines[1]": lambda o, w: o["image_spec"]["lines"].__setitem__(1, f"{w} members"),
    "image_post.paragraphs[0]": lambda o, w: o["image_post"]["paragraphs"].__setitem__(0, f"All {w}."),
    "captions.linkedin": lambda o, w: o["captions"].update(linkedin=o["captions"]["linkedin"] + f"\n\nBy {w}."),
}


@pytest.mark.parametrize("field_", sorted(_NARROW_FIELDS))
@pytest.mark.parametrize("word", NARROWING)
def test_congress_narrowing_words_are_refused_in_every_field(word, field_):
    """Mutation-checked by hand (2026-10-10, in memory): with CONGRESS_NARROWING_RE emptied, every case
    here (and the two tests below) turned red."""
    out = compose(congress(), CONGRESS_RUN)
    assert "congress_narrowing" not in _codes(out, congress())
    _NARROW_FIELDS[field_](out, word)
    found = {(v["field"], v["code"]) for v in T.validate_package(out, record=congress())}
    assert (field_, "congress_narrowing") in found, found


@pytest.mark.parametrize("word", NARROWING)
def test_the_narrowing_rule_reads_every_word_by_itself(word):
    assert T.CONGRESS_NARROWING_RE.search(fold(f"It says {word} here"))


def test_every_state_name_narrows_and_a_company_named_after_one_does_not():
    for state in T._US_STATES:
        name = state.replace(r"\s+", " ")
        assert T.CONGRESS_NARROWING_RE.search(f"members from {name}"), state
    for sym, name in (("STT", "State Street"), ("TXN", "Texas Instruments"), ("PARR", "Par Pacific")):
        out = compose(congress(company=co(sym, name)), CONGRESS_RUN)       # the company slot is masked
        assert name in out["hook"] and _codes(out, congress(company=co(sym, name))) == set()
    # …but only the company's own name is masked: the same word anywhere else still narrows
    out = compose(congress(company=co("STT", "State Street")), CONGRESS_RUN)
    out["captions"]["linkedin"] += "\n\nOne member is from the state of the company."
    assert "congress_narrowing" in _codes(out, congress(company=co("STT", "State Street")))


@pytest.mark.parametrize("mutate, code", [
    (lambda o: o.update(hook=o["hook"].replace("disclosed purchases of", "bought")), "congress_wording"),
    (lambda o: o.update(hook=o["hook"].replace("disclosed purchases of", "disclosed that they purchased")),
     "congress_wording"),
    (lambda o: o["captions"].update(x=o["captions"]["x"].replace("disclosed purchases", "disclosed sales")),
     "congress_wording"),
    (lambda o: o["video_script"].__setitem__(3, "A trade can be disclosed up to 45 days after members traded the "
                                                "stock."), "congress_wording"),
    (lambda o: o["image_post"]["paragraphs"].__setitem__(0, "Purchases of $1,001 - $15,000 each, disclosed."),
     "congress_wording"),
    (lambda o: o["captions"].update(x=o["captions"]["x"].replace("disclosed purchases", "made purchases")),
     "congress_wording"),
    (lambda o: o["opening_card"].update(headline="members of Congress and Accenture stock"), "congress_wording"),
    (lambda o: o.update(hook=o["hook"].replace("3 members", "1 members")), "congress_wording"),
], ids=["bought", "purchased", "sales", "traded", "dollar_range", "no_disclosed_caption", "no_disclosed_cover",
        "one_member"])
def test_congress_wording_is_disclosed_purchases_counted_never_bought_or_dollars(mutate, code):
    out = compose(congress(), CONGRESS_RUN)
    mutate(out)
    assert code in _codes(out, congress())


def test_congress_revalidate_refuses_a_count_below_two_in_the_facts():
    rec = congress()
    out = compose(rec, CONGRESS_RUN)
    facts = sheet(rec)
    facts["record"]["members"] = 1
    assert {v["code"] for v in T.revalidate(out, fact_sheet=facts, run_date=CONGRESS_RUN)} == {"facts_invalid"}


# ── Company Stakes ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("factory, hook, label, l3", [
    (nscale, "Nvidia invested $777.4 million in Nscale.", "invested",
     "As of that date, Nscale was not listed on a public exchange."),
    (anthropic_commitment, "Nvidia committed up to $10 billion to Anthropic.", "committed up to",
     "A commitment is money agreed to be invested, which may be paid over time."),
    (tesla_spacex, "Tesla reported its SpaceX stake at a fair value of $3 billion.", "fair value",
     "Shares of SpaceX are listed on a U.S. exchange."),
    (berkadia, "Berkshire Hathaway carried its Berkadia Commercial Mortgage stake at $461 million.", "carrying value",
     "As of that date, Berkadia Commercial Mortgage was not listed on a public exchange."),
    (itochu, "Berkshire Hathaway reported its Itochu Corporation stake at a fair value of $8.9 billion.", "fair value",
     "As of that date, shares of Itochu Corporation traded on an exchange outside the U.S."),
    (intel_stake, "Nvidia invested $5 billion in Intel.", "invested", "Shares of Intel are listed on a U.S. exchange."),
], ids=["invested_private", "committed", "fair_value_us", "carrying_value", "fair_value_non_us", "invested_us"])
def test_stake_verb_table(factory, hook, label, l3):
    out = compose(factory(), STAKES_RUN)
    assert out["hook"] == hook and out["image_spec"]["label"] == label and out["video_script"][2] == l3
    assert out["opening_card"]["kicker"] == out["image_spec"]["kicker"] == "COMPANY STAKES"
    assert out["image_spec"]["figure"] == out["opening_card"]["figure"]
    text = json.dumps(out)
    assert not T.STAKES_BANNED_RE.search(fold(text)) and not re.search(r"\b(?:bet|bets|worth|bought)\b", text, re.I)
    assert out["image_spec"]["lines"][0].startswith("As of ")                # the figure's own date, always


def test_a_stake_never_says_its_ownership_share_and_drops_a_bad_background():
    out = compose(berkadia(), STAKES_RUN)
    text = json.dumps(out)
    assert "50%" not in text and "50.0" not in text and " owns" not in text
    assert "Jefferies" not in text                                         # its "50%" background is dropped
    spacex = json.dumps(compose(tesla_spacex(), STAKES_RUN))
    assert "under 1%" not in spacex and "xAI" not in spacex


def test_the_stake_of_twin_and_the_short_fair_value_for_a_possessive_investor():
    rec = stake(investor=co("MCO", "Moody's"), investee_name="Fabrikam Analytics", value_usd=3_200_000_000.0,
                value_basis="fair_value", kind="private")
    parts = _parts(rec, STAKES_RUN)
    assert parts.opening["headline"] == "The stake of Moody's in Fabrikam Analytics"
    assert "The Fabrikam Analytics stake of Moody's had a fair value of $3.2 billion, as of Mar 27." in (
        parts.image_headlines)
    out = compose(rec, STAKES_RUN)
    assert "Moody's's" not in json.dumps(out) and "Moody's'" not in json.dumps(out)


@pytest.mark.parametrize("rec, run_date, code", [
    (stake(value_usd=None, value_basis=None, ownership_pct=10.7), STAKES_RUN, "record_invalid"),   # a share alone
    (_unchecked(nscale(), investee_name="Private companies (not named)"), STAKES_RUN, "record_invalid"),
    (_unchecked(nscale(), investee_name="Investment commitments not named"), STAKES_RUN, "record_invalid"),
    (stake(kind="commitment", value_basis="invested"), STAKES_RUN, "record_invalid"),
    (stake(kind="private", value_basis="committed_up_to"), STAKES_RUN, "record_invalid"),
    (stake(investee=co("NVDA", "NVIDIA")), STAKES_RUN, "record_invalid"),   # a stake in itself
    (stake(investee_name="NVIDIA"), STAKES_RUN, "record_invalid"),
    (stake(listed_since=date(2027, 1, 4)), STAKES_RUN, "record_invalid"),
    (nscale(), date(2027, 1, 23), "stale_source"),                         # verified 121 days before the run
    (stake(as_of=date(2023, 12, 1), verified_on=date(2026, 12, 1)), STAKES_RUN, "stale_source"),   # > 3 years
    (stake(as_of=date(2026, 12, 30), verified_on=date(2026, 12, 30)), STAKES_RUN, "stale_source"),
    (stake(investee_name="Nanofabricationsystems"), STAKES_RUN, "slot_rejected"),    # a 22-char word
    (stake(investee_name="Optoelectronics Holdings"), STAKES_RUN, None),               # 15: draws whole
    (stake(investee_name="Telecommunications Holdings"), STAKES_RUN, "slot_rejected"),  # 18 > 15 (a half column)
    (stake(investee_name="Northwind Data Center Partners Holdings Co"), STAKES_RUN, "slot_rejected"),   # 41 chars
    (stake(source_title=("Northwind 10-K " + "(fiscal year ended Dec 31, 2025) " * 3).strip()), STAKES_RUN,
     "slot_rejected"),
    (stake(source_title="See https://example.com/filing"), STAKES_RUN, "slot_rejected"),
    (stake(source_title="Northwind 10-K Supercalifragilisticexpialidocious"), STAKES_RUN, "slot_rejected"),
    (stake(kind="non_us_listed", local_listing="Japan 100%"), STAKES_RUN, "slot_rejected"),
], ids=["pct_only", "aggregate", "not_named", "commitment_invested", "committed_private", "self_symbol", "self_name",
        "listed_after_run", "verified_stale", "as_of_old", "dated_after_run", "word_22", "word_15_ok", "word_18",
        "name_41", "source_long", "source_link", "source_word", "local_percent"])
def test_stake_refusals(rec, run_date, code):
    if code is None:
        assert compose(rec, run_date)["image_spec"]["right"]["name"] == rec.investee_name
        return
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(rec, run_date)
    assert e.value.code == code


#: (background, kept?) — hand-written free text: kept only when every rule passes, else dropped
#: (never fixed, never a reason to refuse the stake).
BACKGROUNDS = [
    ("For Series C shares; NVIDIA also paid $400.0M for Series B shares on Oct 8, 2025.", True),
    ("Agreed on Sep 15, 2025, alongside a product collaboration with NVIDIA.", True),
    ("Purchased Dec 1, 2025 at $414.79 a share, alongside a strategic partnership.", False),   # a per-share price
    ("Acquired 4,000,000 shares at $20.00 each alongside a Dec 2021 collaboration.", False),
    ("Agreed in Jul 2020 to invest about ₹33,737 crore for a stake.", False),               # non-ASCII
    ("Jefferies Financial Group owned the other 50%.", False),                                  # a "%"
    ("Jensen Huang said the deal closed in 2025.", False),                                      # a person
    ("Nancy Pelosi disclosed a similar trade.", False),                                         # a member of Congress
    ("The stake is worth far more today.", False),                                              # banned copy
    ("It will double by 2027.", False),                                                         # a forecast
    ("A record deal for the company.", False),                                                  # NEWS_BANNED_RE
    ("NVIDIA bet early on the company.", False),                                                # the stake's verb
    ("Details at nvidia.com for investors.", False),                                            # a bare domain
    ("alongside a cloud partnership.", False),                                                  # not a sentence
    ("Alongside a cloud partnership", False),
]


@pytest.mark.parametrize("background, kept", BACKGROUNDS, ids=[str(i) for i in range(len(BACKGROUNDS))])
def test_a_stake_background_is_caption_text_only_and_only_when_every_rule_passes(background, kept):
    """A3: the background (hand-written, unnormalised) appears only in long-caption paragraphs —
    never drawn, narrated, carded or used as alt text. Mutation-checked by hand (2026-10-10, in memory):
    without `_PER_SHARE_RE` in `_stake_background` case 2 ("at $414.79 a share") turned red — case 3
    is refused by its "Acquired" as well."""
    rec = stake(background=background)
    out = compose(rec, STAKES_RUN)
    assert T.validate_package(out, record=rec) == []
    in_captions = [f for f, body in out["captions"].items() if background in body]
    if kept:
        assert set(in_captions) >= {"linkedin", "facebook", "instagram", "youtube_description"}
        assert not set(in_captions) & {"x", "bluesky", "threads", "youtube_title"}
    else:
        assert in_captions == []
    video_and_image = json.dumps([out["hook"], out["video_script"], out["cards"], out["opening_card"],
                                  out["image_spec"], out["image_post"]])
    assert background not in video_and_image


@pytest.mark.parametrize("kind, investee, local, listed, l3, c3", [
    ("private", None, None, None, "As of that date, Nscale was not listed on a public exchange.",
     ("Not listed", "Not traded on a public exchange as of that date")),
    ("non_us_listed", None, "Korea (KOSDAQ)", None,
     "As of that date, shares of Nscale traded on an exchange outside the U.S.",
     ("Listed outside the U.S.", "Korea (KOSDAQ)")),
    ("non_us_listed", None, None, None, "As of that date, shares of Nscale traded on an exchange outside the U.S.",
     ("Listed outside the U.S.", "On an exchange outside the U.S.")),
    ("us_listed_off_13f", co("NSCL", "Nscale"), None, None, "Shares of Nscale are listed on a U.S. exchange.",
     ("Listed in the U.S.", "Shares trade on a U.S. exchange")),
    ("on_13f_note", None, None, None, "The figure comes from a company filing or an official company release.",
     ("Where it comes from", "A company filing or an official release")),           # no verified listing
    ("private", None, None, date(2026, 10, 1), "Nscale has been listed on an exchange since October 2026.",
     ("Listed since", "Oct 2026")),                                                # listed_since overrides
], ids=["private", "non_us_local", "non_us", "us_verified", "us_unverified", "listed_since"])
def test_stake_listing_lines_say_only_what_the_record_carries(kind, investee, local, listed, l3, c3):
    out = compose(stake(kind=kind, investee=investee, local_listing=local, listed_since=listed), STAKES_RUN)
    assert out["video_script"][2] == l3
    assert (out["cards"][2]["title"], out["cards"][2]["body"]) == c3
    assert ("logo" in out["image_spec"]["right"]) == (investee is not None)
    assert [r["key"] for r in out["logo_refs"]] == ["NVDA"] + ([investee.symbol] if investee else [])


def test_a_listed_since_stake_says_since_and_never_first_listed():
    """Review round 8 (lens 2): `listed_since` is the CURRENT listing's start (iOS "Listed since …"); a
    relisted company was listed before it (Arm: London 1998-2016, then Nasdaq in September 2023), so
    the post says "has been listed … since", never "first listed". Mutation-checked (2026-10-10, in
    memory): with the news-v7 wording restored, this test and the "listed_since" listing case turned red."""
    rec = stake(investee_name="Arm Holdings", investee=co("ARM", "Arm Holdings"), kind="us_listed_off_13f",
                listed_since=date(2023, 9, 14))
    out = compose(rec, STAKES_RUN)
    assert out["video_script"][2] == "Arm Holdings has been listed on an exchange since September 2023."
    assert out["cards"][2] == {"title": "Listed since", "body": "Sep 2023"}
    assert "Arm Holdings has been listed on an exchange since September 2023." in out["captions"]["linkedin"]
    for s in _strings(out):
        assert not re.search(r"(?i)\bfirst listed\b|\bwas listed in\b|went public|\bIPO\b", s), s


def test_stake_banned_words_are_refused_wherever_they_land():
    for word in ("bet", "bets", "betting", "worth", "bought", "backed", "pledged", "controls", "acquired", "owns"):
        out = compose(nscale(), STAKES_RUN)
        out["video_script"][3] = f"Companies {word} stakes like this in filings and official releases."
        assert {"series_word", "banned_word", "off_table_verb"} & _codes(out, nscale()), word


# ── Earnings vs Estimates ─────────────────────────────────────────────────────


#: A profit or loss stated as the company's (review round 9): the calendar's EPS is the figure on the
#: basis analysts estimate — often not the official accounting result — so no line may say the
#: company "earned" / "lost" or had "earnings of" / "a loss of" / "in earnings" (nor any "profit").
_PROFIT_LOSS_RE = re.compile(r"(?i)\bearned\b|\blost\b|\bloss(?:es)?\b|\bearnings\s+(?:of|per)\b|\bin\s+earnings\b|"
                             r"\bprofit")


@pytest.mark.parametrize("a, e, hook", [
    (4.86, 4.5, "Fabrikam reported $4.86 a share on the analysts' basis; the estimate was $4.50."),
    (-0.05, -0.12, "Fabrikam reported negative $0.05 a share; analysts estimated negative $0.12."),
    (-0.05, 0.12, "Fabrikam reported negative $0.05 a share on the analysts' basis; the estimate was $0.12."),
    (0.31, -0.12, "Fabrikam reported $0.31 a share on the analysts' basis; the estimate was negative $0.12."),
    (0.0, -0.12, "Fabrikam reported $0.00 a share on the analysts' basis; the estimate was negative $0.12."),
], ids=["positive_positive", "negative_negative", "negative_vs_positive", "positive_vs_negative", "zero"])
def test_earnings_state_the_signed_figure_on_the_analysts_basis_never_a_profit_or_loss(a, e, hook):
    """Review round 9 (medium): the hooks said "Snowflake reported earnings of $0.35 a share" /
    "earned" / "lost" — the calendar's adjusted figure stated as the company's profit or loss (a GAAP
    loss with positive adjusted EPS is common). Every surface now says the figure with its sign
    ("negative $0.05" spoken, "-$0.05" written), "EPS" where it is written, the analysts' basis in the
    long hook and the basis note in the headline where it fits (always in narration line 3), and no
    caption paragraph restates the hook. Mutation-checked (2026-10-10, in memory): the old
    `er.hook.*` profit/loss entries, the headline without its `.basis` variants, or the hook restated
    as the first caption paragraph each turned this red."""
    rec = earnings(company=co("FBKM", "Fabrikam"), eps_actual=a, eps_estimate=e)
    out = compose(rec, EARNINGS_RUN)
    assert out["hook"] == hook
    parts = _parts(rec, EARNINGS_RUN)
    # the template's own words (never the code-owned disclaimer: "including loss of principal")
    own = {k: v for k, v in out.items() if k not in ("posts", "disclaimer_card")}
    for s in [*_strings(own), *_offered(rec, EARNINGS_RUN), *(v for g in parts.lines for v in g),
              *(t for c in parts.cards for t in c)]:
        assert not _PROFIT_LOSS_RE.search(s), s
    a_s, e_s = T.eps_image(a), T.eps_image(e)
    assert out["opening_card"]["figure"] == a_s
    assert out["opening_card"]["headline"] == f"EPS vs an analyst estimate of {e_s}"
    # the REPORTED figure is the last (accent) cell, the estimate the muted one; no green/red word anywhere
    assert out["image_spec"]["sections"][0]["rows"][0]["cells"] == ["EPS", f"vs {e_s} estimate", a_s]
    assert out["captions"]["x"] == (f"Fabrikam reported EPS of {a_s} vs an analyst estimate of {e_s}. The EPS basis "
                                    "can differ from the official accounts.")
    assert out["video_script"][2] == T.LEXICON["er.l3.basis"]                   # the basis note, always narrated
    for f, body in out["captions"].items():
        assert "a share" not in body and hook not in body, f                    # no paragraph restates the hook
        if f not in ("youtube_title",):
            assert "basis" in body.split("\n\n")[0], f                         # the headline carries the note
    assert not T.EARNINGS_BANNED_RE.search(fold(json.dumps(out)))
    assert "-" not in " ".join([out["hook"], *out["video_script"]])         # narration: magnitudes in words


def test_earnings_revenue_row_and_lines_only_when_present():
    with_rev = compose(earnings(), EARNINGS_RUN)
    assert [r["cells"][0] for r in with_rev["image_spec"]["sections"][0]["rows"]] == ["EPS", "Revenue"]
    assert with_rev["video_script"][1] == "Revenue was $551.9 million, against an analyst estimate of $543.6 million."
    without = compose(earnings_profit(), EARNINGS_RUN)
    assert [r["cells"][0] for r in without["image_spec"]["sections"][0]["rows"]] == ["EPS"]
    assert "revenue" not in json.dumps({k: without[k] for k in ("hook", "video_script", "cards", "image_spec",
                                                                 "captions", "image_post")}).lower()


@pytest.mark.parametrize("rec, run_date, code", [
    (earnings(), date(2026, 11, 13), "stale_source"),                     # reported 8 days before the run
    (earnings(), date(2026, 11, 5), "stale_source"),                      # reported on the run day
    (earnings(eps_actual=45.0, eps_estimate=4.5), EARNINGS_RUN, "implausible_figures"),          # a digit shift
    (earnings(eps_actual=-5.0, eps_estimate=0.4), EARNINGS_RUN, "implausible_figures"),          # gap > 10x
    (earnings(eps_actual=0.004, eps_estimate=0.12), EARNINGS_RUN, "implausible_figures"),        # reads $0.00
    (_unchecked(earnings(), eps_estimate=0.05), EARNINGS_RUN, "implausible_figures"),
    (earnings(period_end=date(2026, 7, 7)), EARNINGS_RUN, "implausible_figures"),              # 121 days
    (_unchecked(earnings(), revenue_actual=900e6), EARNINGS_RUN, "implausible_figures"),       # outside the band
    (_unchecked(earnings(), revenue_estimate=None), EARNINGS_RUN, "implausible_figures"),
    (earnings(company=_unchecked(co("NWFT", "Northwind"), name="Shocking Fitness")), EARNINGS_RUN, "slot_rejected"),
], ids=["old", "same_day", "digit_shift", "gap", "sub_cent", "tiny_estimate", "period_old", "revenue_band",
        "revenue_one_sided", "slot_word"])
def test_earnings_refusals(rec, run_date, code):
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(rec, run_date)
    assert e.value.code == code


@pytest.mark.parametrize("word", ["beat", "missed", "expected", "surprise", "topped", "exceeded", "fell short",
                                  "disappointing", "strong", "weak", "green", "red", "better", "worse", "crushed"])
def test_earnings_never_puts_a_verdict_beside_the_figures(word):
    """Mutation-checked by hand (2026-10-10, in memory): with EARNINGS_BANNED_RE emptied, every word
    no other list holds turned red (surprise, topped, exceeded, fell short, disappointing, strong,
    weak, green, red, better, worse); beat, missed, expected and crushed are also banned elsewhere."""
    for mutate in (lambda o: o.update(hook=o["hook"].replace("; analysts", f", a {word} quarter; analysts")),
                   lambda o: o["captions"].update(x=o["captions"]["x"] + f" A {word} result.")):
        out = compose(earnings(), EARNINGS_RUN)
        mutate(out)
        assert {"series_word", "banned_word"} & _codes(out, earnings()), word


@pytest.mark.parametrize("ra, rv, words, image", [
    # a 0.09% shortfall printed "$1.1 billion vs $1.2 billion" (about 8%) when each was rounded alone
    (1_149_000_000.0, 1_150_000_000.0, ("$1.149 billion", "$1.150 billion"), ("$1.149B", "$1.150B")),
    (24_949_000_000.0, 24_950_000_000.0, ("$24.949 billion", "$24.950 billion"), ("$24.949B", "$24.950B")),
    # a 2.6% gap printed "$1.2 billion vs $1.2 billion" (hidden)
    (1_180_000_000.0, 1_150_000_000.0, ("$1.18 billion", "$1.15 billion"), ("$1.18B", "$1.15B")),
    # equal figures read equal
    (1_150_000_000.0, 1_150_000_000.0, ("$1.2 billion", "$1.2 billion"), ("$1.2B", "$1.2B")),
    # the golden's exact pair is unchanged (one decimal, the single-figure form)
    (551_900_000.0, 543_600_000.0, ("$551.9 million", "$543.6 million"), ("$551.9M", "$543.6M")),
    # 999.96M rounds to $1B at one place, beside $1B: the pair stays in millions, two places
    (999_960_000.0, 990_000_000.0, ("$999.96 million", "$990.00 million"), ("$999.96M", "$990.00M")),
    # one unit, the larger figure's (never "$1.2 billion vs $900 million")
    (1_200_000_000.0, 900_000_000.0, ("$1.2 billion", "$0.9 billion"), ("$1.2B", "$0.9B")),
], ids=["shortfall_0.09pct", "shortfall_large_co", "gap_2.6pct", "equal", "golden", "rollup_edge", "one_unit"])
def test_the_revenue_pair_is_written_at_one_shared_precision(ra, rv, words, image):
    """Review round 9 (medium): revenue_actual and revenue_estimate were rounded on their own, so a
    near-match read as a miss or a beat for a named company. They are now written as ONE pair: the
    larger figure's unit, decimals grown from one to REVENUE_MAX_PLACES until the shown gap is within
    REVENUE_GAP_TOLERANCE of the true gap — the same strings in narration, card, image, captions and
    alt text. Mutation-checked (2026-10-10, in memory): with each figure formatted alone again
    (`money_words` / `money_image`), the shortfall, gap and roll-up cases turned red."""
    pair = T.revenue_pair(ra, rv)
    assert (pair["ra"], pair["re"]) == words and (pair["ra_s"], pair["re_s"]) == image
    out = compose(earnings(revenue_actual=ra, revenue_estimate=rv), EARNINGS_RUN)
    assert out["video_script"][1] == f"Revenue was {words[0]}, against an analyst estimate of {words[1]}."
    assert out["image_spec"]["sections"][0]["rows"][1]["cells"] == ["Revenue", f"vs {image[1]} estimate", image[0]]
    assert out["cards"][1]["body"] == f"{image[0]} vs an estimate of {image[1]}"
    assert f"and revenue of {words[0]} vs {words[1]}." in out["captions"]["linkedin"].split("\n\n")[0]
    assert f"It shows revenue of {image[0]} against an estimate of {image[1]}." in out["image_post"]["paragraphs"][0]


@pytest.mark.parametrize("ra, rv", [
    (1_149_400_000.0, 1_150_000_000.0),       # a 0.05% gap: even three places show it 67% too large
    (5_000_000_001.0, 5_000_000_000.0),       # a one-dollar gap: no shared precision can show it
    (900_000.0, 800_000.0),                   # under $1 million: never written as a pair
])
def test_a_revenue_gap_no_shared_precision_can_show_is_left_out(ra, rv):
    """Fail closed: when no precision up to REVENUE_MAX_PLACES shows the gap within the tolerance, the
    post leaves revenue out (the no-revenue lines) — never a pair that reads equal, or the wrong size."""
    assert T.revenue_pair(ra, rv) is None
    out = compose(earnings(revenue_actual=ra, revenue_estimate=rv), EARNINGS_RUN)
    assert [r["cells"][0] for r in out["image_spec"]["sections"][0]["rows"]] == ["EPS"]
    assert "revenue" not in json.dumps({k: out[k] for k in ("hook", "video_script", "cards", "image_spec",
                                                             "captions", "image_post")}).lower()


def _figure(text: str) -> float:
    """"$1.149 billion" / "$1.149B" → 1.149e9 (the pair's own two forms)."""
    m = re.fullmatch(r"\$([0-9,]+(?:\.[0-9]+)?)\s?(M|B|T|million|billion|trillion)", text)
    return float(m.group(1).replace(",", "")) * {"M": 1e6, "B": 1e9, "T": 1e12, "million": 1e6, "billion": 1e9,
                                                  "trillion": 1e12}[m.group(2)]


def test_a_shown_revenue_pair_always_keeps_the_true_gap_sign_and_size():
    """Seeded property sweep (4,000 pairs inside the revenue band, most of them near-matches): a pair
    that is written keeps the true gap's sign, reads equal only when the figures are equal, uses one
    unit and one precision, and shows the gap within REVENUE_GAP_TOLERANCE (plus float slack) — in its
    word form and its image form alike."""
    rnd = random.Random(20261010)
    shown = 0
    for _ in range(4000):
        est = 10 ** rnd.uniform(6, 12.5)
        act = est * (1 + rnd.choice((rnd.uniform(-0.002, 0.002), rnd.uniform(-0.05, 0.05), rnd.uniform(-0.3, 0.4))))
        act = round(act, rnd.choice((0, -3, -6)))
        pair = T.revenue_pair(act, est)
        if pair is None:
            continue
        shown += 1
        true_gap = act - est
        for a_s, e_s in ((pair["ra"], pair["re"]), (pair["ra_s"], pair["re_s"])):
            gap = _figure(a_s) - _figure(e_s)
            assert abs(gap - true_gap) <= float(T.REVENUE_GAP_TOLERANCE) * abs(true_gap) + 1e-6 * est, (act, est, pair)
            assert (gap == 0) == (true_gap == 0) and (gap > 0) == (true_gap > 0), (act, est, pair)
            unit = re.compile(r"(M|B|T|million|billion|trillion)$")
            assert unit.search(a_s).group(1) == unit.search(e_s).group(1), pair                 # one unit
            places = [len(re.search(r"(?:\.([0-9]+))?\s?[A-Za-z]+$", x).group(1) or "") for x in (a_s, e_s)]
            # one precision (the one-place form drops ".0": "$25 billion" beside "$24.9 billion")
            assert places[0] == places[1] or max(places) == 1, pair
    assert shown > 3000


def test_no_article_stands_before_an_earnings_figure():
    """An "a" before a figure would depend on how it is read ("an $8.1B estimate"): every earnings
    string says "an analyst estimate of {figure}" / "an estimate of {figure}" instead."""
    for factory in (earnings, earnings_profit,
                    lambda: earnings(eps_actual=8.1, eps_estimate=8.05, revenue_actual=18e9, revenue_estimate=18.2e9)):
        out = compose(factory(), EARNINGS_RUN)
        for s in _strings(out):
            assert not re.search(r"\b[Aa]n?\s+-?\$", s), s


# ── Theme Explainer ───────────────────────────────────────────────────────────


def _members(n, *, name=lambda i: f"Company {i:02d}", seg=lambda i: "Cloud", share=lambda i: 0.5):
    syms = [f"{a}{b}" for a in "ABCDEFGHJK" for b in "ABCDEFGHJK"][:n]
    return [(syms[i], name(i), seg(i), share(i), "2026") for i in range(n)]


def test_theme_lists_every_member_in_the_long_captions_and_draws_twelve():
    rows = _members(24)
    out = compose(theme(rows, theme_size=24), THEME_RUN)
    names = [r[1] for r in rows]
    for f in T.THEME_LIST_FIELDS:
        assert all(n in out["captions"][f] for n in names), f
    assert len(out["image_spec"]["tiles"]) == 12 and out["image_spec"]["more"] == "+12 more"
    assert out["image_spec"]["subtitle"] == "24 companies, by largest revenue segment"
    assert out["hook"] == "Where do 24 companies in AI chips get their revenue?"
    assert out["opening_card"]["figure"] == "24" and out["opening_card"]["logos"] == [rows[0][0]]
    narrated = [n for n in names if n in " ".join(out["video_script"])]
    assert 1 <= len(narrated) <= T.THEME_NARRATED_MAX
    assert out["image_post"]["paragraphs"][0].startswith("The image shows 12 of the 24 companies")


def _strings(x):
    """Every string value of a composed output, recursively (captions, posts, image, cards, …)."""
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from _strings(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _strings(v)


def _offered(rec, run_date=THEME_RUN):
    """Every variant the theme builder OFFERS (hooks, headlines, titles, paragraphs, alt), picked or not."""
    p = _parts(rec, run_date)
    return [*p.hook, *p.image_headlines, *p.video_headlines, *p.youtube_titles, *p.image_paragraphs,
            *p.video_paragraphs, *p.alt_paragraphs, p.opening["headline"], p.image_spec["subtitle"]]


#: A whole-theme claim about 14 companies ("The 14 companies in", "12 of the 14", "AI chips: 14
#: companies") or a bare "14 companies" — wording a 14-member record may use only when its theme_size is 14.
_WHOLE_14_RE = re.compile(r"(?i)\bthe 14 companies\b|\bof the 14\b|\b14 companies\b")


def test_theme_says_n_of_its_m_wherever_it_counts_when_the_adapter_gated_members():
    """Review round 8 (lens 2): the adapter's company gate can drop real members of a theme (an OTC
    listing, an unusable name) — the record then carries `theme_size` (the theme's own count, the
    shared contract) above its members, and every place the post gives the count says "14 of its 16"
    (the hook, the cover, the image subtitle and alt text, every caption headline, the member-list
    paragraph and the YouTube title), never a whole-theme claim. Mutation-checked (2026-10-10, in
    memory, 14 mutants all killed): with `part` forced False or the `.part` suffix dropped this test
    turned red; with `th.hook.part.min` removed, the seven-word theme was refused `script_shape`."""
    out = compose(ai_chips_of_more(), THEME_RUN)
    assert out["hook"] == "Where do 14 of the 16 companies in AI chips get their revenue?"
    assert (out["opening_card"]["figure"], out["opening_card"]["headline"]) == (
        "14", "of the 16 companies in AI chips, by largest revenue segment")
    assert out["image_spec"]["subtitle"] == "14 of its 16 companies, by largest revenue segment"
    assert out["image_spec"]["more"] == "+2 more"          # the LISTED members not drawn, never the gated ones
    assert out["image_post"]["paragraphs"][0].startswith("The image shows 12 of the 16 companies in AI chips.")
    for f, body in out["captions"].items():
        assert "14 of its 16 companies" in body.split("\n\n")[0], f
    for f in T.THEME_LIST_FIELDS:
        assert "\n\n14 of the 16 companies in AI chips: NVIDIA; Broadcom; " in out["captions"][f], f
    for s in [*_strings(out), *_offered(ai_chips_of_more())]:
        assert not _WHOLE_14_RE.search(s), s
    # a seven-word theme: the longer hooks run past HOOK_WORDS, the tight one still gives "of its"
    title = "Space and Satellite Launch and Service Providers"
    out = compose(theme(title=title, theme_size=16), THEME_RUN)
    assert out["hook"] == f"{title}: revenue at 14 of its 16 companies."
    assert compose(theme(title=title, theme_size=14), THEME_RUN)["hook"] == (
        f"{title}: where 14 companies get their revenue.")


@pytest.mark.parametrize("size, members_line, whole", [
    (14, "\n\nThe 14 companies in AI chips: NVIDIA; ", True),     # the record says the list is the theme
    (None, "\n\n14 companies in AI chips: NVIDIA; ", False),      # size not recorded: no size claim at all
], ids=["whole", "unknown"])
def test_theme_claims_the_whole_theme_only_when_the_record_says_so(size, members_line, whole):
    """"The 14 companies in AI chips", "12 of the 14" and "AI chips: 14 companies" state the theme's
    size: offered only when `theme_size` equals the members, never when it is not recorded.
    Mutation-checked (2026-10-10, in memory): reading an unrecorded size as whole in the member list,
    the bare "{theme}: {n} companies." headline, the short YouTube title or the alt text each turned the
    "unknown" case red."""
    rec = theme(theme_size=size)
    out = compose(rec, THEME_RUN)
    for f in T.THEME_LIST_FIELDS:
        assert members_line in out["captions"][f], f
    offered = _offered(rec)
    assert ("AI chips: 14 companies." in offered) is whole and ("AI chips: 14 companies" in offered) is whole
    assert out["image_post"]["paragraphs"][0].startswith(
        "The image shows 12 of the 14 companies in AI chips." if whole else "The image shows 12 companies in AI chips.")
    if not whole:
        for s in [*_strings(out), *offered]:
            assert not re.search(r"(?i)\bthe 14 companies\b|\bof the 14\b|AI chips: 14 companies", s), s
    # every member drawn and the whole theme: "the 8 companies"; unknown: "8 companies"
    eight = compose(theme(_members(8), theme_size=8 if whole else None), THEME_RUN)
    assert eight["image_post"]["paragraphs"][0] == (
        ("The image shows the 8 companies in AI chips." if whole else "The image shows 8 companies in AI chips.")
        + " It gives each one's largest revenue segment.")


def test_theme_says_each_ones_segment_only_when_every_member_has_one():
    """"by each one's largest reported segment" (the caption headline) only when every LISTED member has
    its fact in the post, and the alt text's "each one's" only when every drawn tile carries its segment
    (else how many do). Mutation-checked (2026-10-10, in memory): with ".each" always chosen, the AI
    chips lines turned red; with it never chosen, the all-facts lines did."""
    for rec in (ai_chips(), ai_chips_of_more(), theme()):       # Intel stale, Synopsys too long, KLA none
        out = compose(rec, THEME_RUN)
        assert not any("each one's" in s for s in [*_strings(out), *_offered(rec)]), rec.theme_size
        assert out["image_post"]["paragraphs"][0].endswith(" It gives the largest revenue segment for 10 of them.")
    out = compose(theme(_members(8), theme_size=8), THEME_RUN)
    assert out["captions"]["x"] == "AI chips: where 8 companies get their revenue, by each one's largest reported segment."
    out = compose(theme(_members(8), theme_size=10), THEME_RUN)
    assert out["captions"]["x"] == ("AI chips: where 8 of its 10 companies get their revenue, by each one's largest "
                                    "reported segment.")
    # no drawn tile with a fact (the three facts sit past the twelfth tile): no segment sentence at all
    rows = _members(15, seg=lambda i: "Cloud" if i >= 12 else None, share=lambda i: 0.5 if i >= 12 else None)
    out = compose(theme(rows, theme_size=15), THEME_RUN)
    assert not any("line" in t for t in out["image_spec"]["tiles"])
    assert out["image_post"]["paragraphs"][0] == "The image shows 12 of the 15 companies in AI chips."


@pytest.mark.parametrize("bad", [13, 0, -1, True, 16.0, "16"])
def test_theme_refuses_a_size_below_its_members_or_not_a_count(bad):
    """The template's own guard behind the record's check (set past it here): a theme_size under the
    member count, a bool, a float or a string is `record_invalid` — never "14 of its 13"."""
    rec = ai_chips()
    object.__setattr__(rec, "theme_size", bad)
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(rec, THEME_RUN)
    assert e.value.code == "record_invalid"


def test_theme_drops_an_outlet_whose_budget_cannot_hold_the_member_list(monkeypatch):
    """A4: every member is listed in the four long captions — an outlet that cannot hold the list is
    dropped, never posted without it. Mutation-checked by hand (2026-10-10, in memory): with the
    requirement removed (THEME_LIST_FIELDS emptied), linkedin was posted without the member list."""
    real = post_copy.body_budget

    def tight(field_, *a, **k):
        return 150 if field_ == "linkedin" else real(field_, *a, **k)

    monkeypatch.setattr(post_copy, "body_budget", tight)
    out = compose(theme(_members(24)), THEME_RUN)
    assert "linkedin" not in out["posts"] and out["dropped_outlets"]["linkedin"][0]["code"] == "over_budget"
    assert "facebook" in out["posts"]


def test_theme_member_facts_and_tiles_never_guess():
    out = compose(ai_chips(), THEME_RUN)
    tiles = {t["name"]: t.get("line") for t in out["image_spec"]["tiles"]}
    assert tiles["Intel"] is None                      # fiscal 2022: stale, no fact
    assert tiles["Synopsys"] is None                   # 37-char segment: no fact
    assert tiles["Arm Holdings"] == "Royalty"          # no share: the segment alone
    assert tiles["NVIDIA"] == "Data Center · 88%"
    assert "KLA" not in tiles and "Lam Research" not in tiles                  # 13th and 14th: "+2 more"
    linkedin = out["captions"]["linkedin"]
    assert "KLA" in linkedin and "Intel, Client" not in linkedin and "Synopsys, " not in linkedin
    # a name that wraps past two tile lines is left off the grid, counted in "+k more", still listed
    rows = _members(8)
    rows[1] = (rows[1][0], "Wwwwwwwwwwww Wwwwwwwwwwww Wwwwww", "Cloud", 0.5, "2026")
    out = compose(theme(rows), THEME_RUN)
    assert "Wwwwwwwwwwww Wwwwwwwwwwww Wwwwww" not in [t["name"] for t in out["image_spec"]["tiles"]]
    assert out["image_spec"]["more"] == "+1 more" and "Wwwwwwwwwwww Wwwwwwwwwwww Wwwwww" in out["captions"]["linkedin"]
    # a share that would push its line past two tile lines is left off the tile only
    rows = _members(8, seg=lambda i: "Wwwwwwwwwwwwwwww Wwwwwwwwwwwwwwww", share=lambda i: 0.995)
    out = compose(theme(rows), THEME_RUN)
    assert out["image_spec"]["tiles"][0]["line"] == "Wwwwwwwwwwwwwwww Wwwwwwwwwwwwwwww"
    assert "more than 99% of revenue" in out["captions"]["linkedin"]


@pytest.mark.parametrize("rec, run_date, code", [
    (ai_chips(), date(2027, 1, 11), "stale_source"),                      # 71 days after the member list
    (theme(tickers_as_of=date(2026, 11, 17)), THEME_RUN, "stale_source"),
    (theme(_members(8, seg=lambda i: None if i > 1 else "Cloud", share=lambda i: None)), THEME_RUN, "too_few_rows"),
    (theme(title="Supercalifragilistics Robotics"), THEME_RUN, "slot_rejected"),    # a 21-char word
    (theme(title="Followers of AI"), THEME_RUN, "slot_rejected"),
    (theme(title="Semiconductors Biotechnology Infrastructure Pharmaceutical"), THEME_RUN, "slot_rejected"),
    (theme(_members(8, name=lambda i: f"Wwwwwwwwwwwwwww Co{i}")), THEME_RUN, "slot_rejected"),   # no tile fits
], ids=["stale", "after_run", "few_facts", "title_word", "title_word_banned", "title_four_lines",
        "no_drawable_tile"])
def test_theme_refusals(rec, run_date, code):
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(rec, run_date)
    assert e.value.code == code


@pytest.mark.parametrize("title", ["Undervalued Chips", "Soaring Robotics", "Cheap Biotech", "Record Profits",
                                   "Upside Energy", "Bargain Banks", "Overvalued Tech", "Huge Movers",
                                   "Plunging Retail", "Momentum Plays", "Outperforming Chips", "Best Robotics",
                                   "Market Leaders", "Stellar Software"])
def test_a_theme_title_with_a_news_banned_or_verdict_word_is_refused(title):
    """Review round 9 (low): the title is Caydex's own wording (the post says "This grouping is
    Caydex's own"), yet it passed only the slot rules — "Undervalued Chips" composed into the hook, the
    image title and every caption. It now passes NEWS_BANNED_RE and THEME_TITLE_BANNED_RE too.
    Mutation-checked (2026-10-10, in memory): with the `_theme_parts` check removed, every title here
    composed."""
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(theme(title=title, theme_size=14), THEME_RUN)
    assert e.value.code == "slot_rejected"


@pytest.mark.parametrize("title", ["Silicon Rush", "Modern Battlefield", "The New Oil", "Robot Workforce",
                                   "Hack Human Health", "Cyber Wars", "Power the Machine", "The Final Frontier",
                                   "AI chips", "Green Energy", "Cloud Software"])
def test_todays_theme_titles_still_compose(title):
    """The curated Emerging Frontiers titles (migrations 081 / 083 / 099) and plain sector names pass."""
    assert compose(theme(title=title, theme_size=14), THEME_RUN)["image_spec"]["title"] == title


def test_the_theme_title_is_scanned_unmasked_by_the_package_check():
    """The backstop behind the compose-time refusal: `_record_names` no longer masks the theme's title
    (it is not a name), so `validate_package` / `revalidate` read it wherever it lands. Mutation-checked
    (2026-10-10, in memory): with the title back in the mask, the tampered package below was clean."""
    good = theme(title="AI chips", theme_size=14)
    out = compose(good, THEME_RUN)
    bad = replace(good, title="Undervalued Chips")
    tampered = json.loads(json.dumps(out).replace("AI chips", "Undervalued Chips"))
    found = {(v["field"], v["code"]) for v in T.validate_package(tampered, record=bad)}
    assert ("hook", "banned_word") in found and ("image_spec.title", "banned_word") in found, found


def test_the_share_cell_is_the_only_bare_percent_and_only_on_a_tile_line():
    """Mutation-checked by hand (2026-10-10, in memory): with the tile-line exemption removed from
    `_validate_words`, the theme sample (and its golden) turned red."""
    out = compose(ai_chips(), THEME_RUN)
    assert T.validate_package(out, record=ai_chips()) == []
    for mutate, field_ in (
            (lambda o: o["image_spec"]["tiles"][0].update(line="Data Center · 88% growth"), "image_spec.tiles[0].line"),
            (lambda o: o["image_spec"].update(subtitle="14 companies · 88%"), "image_spec.subtitle"),
            (lambda o: o["cards"][0].update(body="Data Center · 88%"), "cards[0].body"),
            (lambda o: o["captions"].update(x=o["captions"]["x"] + " NVIDIA 88%"), "captions.x")):
        bad = copy.deepcopy(out)
        mutate(bad)
        assert (field_, "structure") in {(v["field"], v["code"]) for v in T.validate_package(bad)}, field_


def test_theme_narration_pairs_facts_then_falls_back_to_single_lines_and_the_explainer():
    out = compose(ai_chips(), THEME_RUN)
    assert out["video_script"][0] == ("Nvidia's largest revenue segment was Data Center, and Broadcom's was "
                                      "Semiconductor Solutions.")
    assert out["cards"][0] == {"title": "NVIDIA and Broadcom", "body": "Data Center · Semiconductor Solutions"}
    # four-word names: a pair no longer fits 20 words, so each fact gets its own line
    long = _members(6, name=lambda i: f"Northwind Data Systems {i:02d}", seg=lambda i: "Cloud and Data Infrastructure")
    out = compose(theme(long), THEME_RUN)
    assert out["video_script"][0] == ("Northwind Data Systems 00's largest revenue segment in its latest reported year "
                                      "was Cloud and Data Infrastructure.")
    assert out["cards"][0] == {"title": "Northwind Data Systems 00",
                               "body": "Largest segment: Cloud and Data Infrastructure"}
    # a member name word too long for a card TITLE (CARD_TITLE_WORD_MAX_CHARS): the generic title,
    # the names in the body
    wide = _members(6, name=lambda i: f"Northwindcorporation {i:02d}", seg=lambda i: "Cloud") + [
        (f"S{a}", f"Fabrikam {a}", "Cloud", 0.5, "2026") for a in "ABC"]          # the grid draws these
    out = compose(theme(wide), THEME_RUN)
    assert out["cards"][0] == {"title": "Largest segments",
                               "body": "Northwindcorporation 00: Cloud · Northwindcorporation 01: Cloud"}
    one = T._theme_parts(theme(wide), THEME_RUN)
    assert all(len(w) <= T.CARD_TITLE_WORD_MAX_CHARS for t, _b in one.cards for w in t.split())
    # three facts: [pair, single] + the segment explainer + the grouping line
    three = compose(theme(_members(6, seg=lambda i: "Cloud" if i < 3 else None,
                                   share=lambda i: 0.5 if i < 3 else None)), THEME_RUN)
    assert three["video_script"][2] == T.LEXICON["th.explainer"]
    assert three["video_script"][3] == T.LEXICON["th.grouping"]


# ── review round 10 (news-v10) ────────────────────────────────────────────────

#: A money figure as the templates write it ("-$0.05", "$551.9 million", "$4.50").
_FIGURE_RE = re.compile(r"-?\$[0-9][0-9,]*(?:\.[0-9]+)?(?: (?:thousand|million|billion|trillion))?")


_PER_100_RE = re.compile(r"\b(?:of|for) every \$100\b")


def _figures(text):
    """The figures a caption sentence states ("of / for every $100" is the Money Map's base, not a
    figure: "$2.94 of every $100" is one figure, said in the headline and again beside the net income)."""
    return set(_FIGURE_RE.findall(_PER_100_RE.sub("", text)))


def _repeated_pairs(body):
    """(paragraph, the figures it shares with the headline) for every paragraph after the headline
    that states two or more of the headline's figures — a pair said twice."""
    head, *paras = body.split("\n\n")
    hf = _figures(head)
    return [(p, sorted(hf & _figures(p))) for p in paras if len(hf & _figures(p)) >= 2]


@pytest.mark.parametrize("name, factory, run_date", SAMPLES, ids=[s[0] for s in SAMPLES])
def test_no_caption_paragraph_repeats_a_figure_pair_its_headline_states(name, factory, run_date):
    """Review round 10 (low): the long earnings headline states the revenue pair ("… and revenue of $551.9
    million vs $543.6 million"), and the first paragraph said it again ("Revenue was $551.9 million,
    against an analyst estimate of $543.6 million") on TikTok, Instagram, Facebook, LinkedIn and the
    YouTube description. The paragraphs are now assembled after the headline is picked
    (`_Parts.stated_by_headline`). Every sample, store state and URL setting. Mutation-checked
    (2026-10-10, in memory): with `stated_by_headline` emptied for earnings, earnings_loss turned red on
    all five long captions."""
    for store_state in post_copy.STORE_STATES:
        for allow in (False, True):
            out = compose(factory(), run_date, store_state, allow)
            for f, body in out["captions"].items():
                assert _repeated_pairs(body) == [], (store_state, allow, f)


def test_the_earnings_revenue_pair_is_said_once_in_every_long_caption():
    """…and the revenue pair is not lost: said exactly once — in the headline or in a paragraph."""
    out = compose(earnings(), EARNINGS_RUN)
    pair = {"$551.9 million", "$543.6 million"}
    for f in ("tiktok", "instagram", "facebook", "linkedin", "youtube_description"):
        chunks = out["captions"][f].split("\n\n")
        assert sum(1 for c in chunks if pair <= _figures(c)) == 1, f
    # the next paragraph moved up into TikTok's one slot (the period line), never left empty
    assert out["captions"]["tiktok"].split("\n\n")[1] == ("These figures cover the quarter that ended Sep 30, "
                                                          "reported on Nov 5.")


def test_a_headline_without_the_revenue_pair_keeps_the_revenue_paragraph_at_every_budget(monkeypatch):
    """The fit sweep (every caption field, budgets 40..1,200): no body ever repeats the headline's pair;
    a headline that states revenue never has the revenue paragraph under it; and a shorter headline
    without revenue still takes the revenue paragraph where it fits."""
    rec = earnings()
    parts = _parts(rec, EARNINGS_RUN)
    rev_para = "Revenue was $551.9 million, against an analyst estimate of $543.6 million."
    long_heads = {h for h in parts.image_headlines if "revenue of" in h}
    # (news-v11: the ".basis" one also states the basis note — `test_every_headline_paragraph_pair_…`)
    assert len(long_heads) == 2 and all(rev_para in parts.stated_by_headline[h] for h in long_heads)
    spec = T.SERIES_SPECS["earnings"]
    seen_short_with_rev = set()
    for budget in range(40, 1201, 7):
        monkeypatch.setattr(T.post_copy, "body_budget", lambda *a, _b=budget, **k: _b)
        bodies, _dropped = T._caption_bodies(parts, spec, EARNINGS_RUN, "live", False)
        for f, body in bodies.items():
            head, *paras = body.split("\n\n")
            assert _repeated_pairs(body) == [], (budget, f)
            if head in long_heads:
                assert rev_para not in paras, (budget, f)
            elif rev_para in paras:
                seen_short_with_rev.add(f)
    # TikTok's one paragraph slot: where the long headline plus the period line does not fit, a shorter
    # headline takes the revenue paragraph (the multi-paragraph captions keep revenue in a headline)
    assert "tiktok" in seen_short_with_rev, seen_short_with_rev


#: One must-reject sample per THEME_TITLE_BANNED_ROWS row: each matches its own row and no other, and
#: every one but the news-banned "Surging Chips" is refused by the theme rule alone.
THEME_TITLE_ROW_SAMPLES = {
    r"outperform\w*": "Outperforming Chips", r"underperform\w*": "Underperformers", r"exceed\w*": "Exceeding Banks",
    r"disappoint\w*": "Disappointing Retail", r"stellar": "Stellar Software", r"winn(?:er|ers|ing)": "AI Winners",
    r"losers?": "Big Losers", r"momentum": "Momentum Chips", r"best": "Best Robotics", r"worst": "Worst Banks",
    r"strong(?:er|est)?": "Strongest Banks", r"weak(?:er|est)?": "Weak Retail", r"hot(?:test)?": "Hottest Chips",
    r"leaders?": "Market Leaders", r"laggards?": "Retail Laggards",
    # review round 10
    r"perform\w*": "Performance Chips", r"top": "Top Chips", r"gain(?:s|er|ers|ing)?": "Chip Gainers",
    r"mov(?:er|ers)": "Big Movers", r"returns?": "Highest Returns", r"ris(?:e|es|ers?|ing)": "Rising Stars",
    r"breakout\w*": "Breakout Biotech", r"high[- ]?fl(?:y|i)ers?": "Tech High-Flyers",
    r"beat(?:s|er|ers|ing)?": "Market Beaters", r"rall(?:y|ies|ied|ying)": "Rallying Banks",
    r"surg(?:e|es|ed|ing)": "Surging Chips", r"soar\w*": "Soaraway Chips",
}


def _row_re(row):
    return re.compile(r"\b(?:" + row + r")\b", re.IGNORECASE)


def test_every_theme_title_row_has_a_must_reject_sample_only_it_matches():
    assert set(THEME_TITLE_ROW_SAMPLES) == set(T.THEME_TITLE_BANNED_ROWS)
    for row, sample in THEME_TITLE_ROW_SAMPLES.items():
        hits = [r for r in T.THEME_TITLE_BANNED_ROWS if _row_re(r).search(fold(sample))]
        assert hits == [row], (sample, hits)
        assert T._slot_problem(sample) is None, sample          # refused for the word, not the slot rules


@pytest.mark.parametrize("row", list(THEME_TITLE_ROW_SAMPLES), ids=list(THEME_TITLE_ROW_SAMPLES.values()))
def test_a_theme_title_in_each_performance_or_ranking_family_is_refused(row):
    """Review round 10 (low): "Top Performers", "Top Gainers", "Big Movers", "Highest Returns", "Market
    Beaters" and "Rising Stars" all composed — the hook, the image title and every caption — with the
    post saying "This grouping is Caydex's own" (rules §1 decision 4: never a theme's performance).
    Mutation-checked (2026-10-10, in memory): with the round-10 rows removed, the round-10 samples but
    the news-banned "Surging Chips" composed."""
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(theme(title=THEME_TITLE_ROW_SAMPLES[row], theme_size=14), THEME_RUN)
    assert e.value.code == "slot_rejected"


@pytest.mark.parametrize("title", ["Top Performers", "Top Gainers", "Big Movers", "Highest Returns",
                                   "Market Beaters", "Rising Stars", "Breakout Names", "High Flyers",
                                   "Highflyers", "AI Rally", "Soaring Chips", "Big Gains", "Top 10 Chips",
                                   "Top-Tier Banks", "Chip Risers", "Gaining Ground"])
def test_the_reviewers_performance_titles_are_refused(title):
    assert T.THEME_TITLE_BANNED_RE.search(fold(title)), title
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(theme(title=title, theme_size=14), THEME_RUN)
    assert e.value.code == "slot_rejected"


def test_the_theme_rule_alone_is_what_refuses_each_sample(monkeypatch):
    """Row weight: with THEME_TITLE_BANNED_RE disabled, every sample the news ban does not already
    cover composes — so each refusal above is the theme rule's, not another rule's."""
    monkeypatch.setattr(T, "THEME_TITLE_BANNED_RE", re.compile(r"(?!x)x"))
    covered = [s for s in THEME_TITLE_ROW_SAMPLES.values() if T.NEWS_BANNED_RE.search(fold(s))]
    assert covered == ["Surging Chips"]
    for sample in THEME_TITLE_ROW_SAMPLES.values():
        if sample in covered:
            continue
        assert compose(theme(title=sample, theme_size=14), THEME_RUN)["image_spec"]["title"] == sample


def _curated_theme_titles():
    """Every title a migration ever gave a theme: the 081 seed's INSERT and each later `SET title`."""
    titles = set()
    for path in sorted((_BACKEND / "database" / "migrations").glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        if "trending_themes" not in sql:
            continue
        titles |= set(re.findall(r"\bSET\s+title\s*=\s*'((?:[^']|'')*)'", sql, re.IGNORECASE))
        for block in re.findall(r"INSERT INTO public\.trending_themes \(slug, category, title[^)]*\)\s*VALUES(.*?);",
                                sql, re.DOTALL):
            titles |= set(re.findall(r"\(\s*'[^']*',\s*'[^']*',\s*'((?:[^']|'')*)'", block))
    return sorted(t.replace("''", "'") for t in titles)


def test_every_curated_theme_title_from_the_migrations_still_composes():
    """The widened rule keeps every curated Emerging Frontiers title (migrations 081 / 083 / 099, read
    from the SQL so a new curated title is covered when it lands)."""
    titles = _curated_theme_titles()
    assert {"The Silicon Rush", "Silicon Rush", "The Final Frontier", "Hacking Human Health",
            "Hack Human Health", "Powering the Machine", "Power the Machine", "The New Oil"} <= set(titles)
    assert len(titles) >= 14, titles
    for title in titles:
        assert not T.THEME_TITLE_BANNED_RE.search(fold(title)), title
        assert compose(theme(title=title, theme_size=14), THEME_RUN)["image_spec"]["title"] == title


def _avery(sym, name, amount, person="Jordan Avery", role="ceo"):
    return buy(sym, name, role, person, amount, 20_000.0, 1, [date(2026, 11, 12)])


@pytest.mark.parametrize("first, second", [
    ("Jordan Avery", "Jordan Avery"),
    ("Jordan T. Avery", "Jordan Avery"),          # one filing carries the middle initial, the next not
    ("JORDAN AVERY", "Jordan Avery"),
], ids=["same", "middle_initial", "case"])
def test_a_roundup_naming_one_person_on_two_rows_is_refused(first, second):
    """Review round 10 (medium): a row is one ISSUER, and "At least {n} CEOs" counts people. One person
    who bought at two affiliates sharing a CEO (externally managed funds, a shared board) composed "At
    least 2 CEOs disclosed buying …" while the caption named that one person on both rows. The adapter
    dedupes by reporter; the template now refuses such a record `record_invalid`, never counting a
    person twice. Mutation-checked (2026-10-10, in memory): without the `_insider_parts` check, each
    case composed "At least 2 CEOs …"."""
    rec = week(_avery("CTSH", "Contoso Hotels", 900_000.0, first),
               _avery("FBRS", "Fabrikam Resorts", 400_000.0, second))
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(rec, MON)
    assert e.value.code == "record_invalid"


def test_the_same_person_among_three_rows_is_refused_and_directors_too():
    three = week(_avery("CTSH", "Contoso Hotels", 900_000.0), _avery("NWND", "Northwind", 600_000.0, "Sam Lee"),
                 _avery("FBRS", "Fabrikam Resorts", 400_000.0))
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(three, MON)
    assert e.value.code == "record_invalid"
    directors = week(_avery("CTSH", "Contoso Hotels", 900_000.0, role="director"),
                     _avery("FBRS", "Fabrikam Resorts", 400_000.0, role="director"), series="insider_buys")
    with pytest.raises(T.NewsTemplateRefused) as e:
        compose(directors, MON)
    assert e.value.code == "record_invalid"


def test_distinct_people_and_unnamed_rows_still_compose_with_their_count():
    """Two different people → "At least 2 CEOs"; unnamed rows cannot be matched to anyone and each
    counts once (the adapter's reporter dedupe is the guard there)."""
    named = compose(week(_avery("CTSH", "Contoso Hotels", 900_000.0),
                         _avery("FBRS", "Fabrikam Resorts", 400_000.0, "Sam Lee")), MON)
    assert named["image_spec"]["title"].startswith("At least 2 CEOs")
    assert named["persons"] == ["Jordan Avery", "Sam Lee"]
    unnamed = compose(week(_avery("CTSH", "Contoso Hotels", 900_000.0, None),
                           _avery("FBRS", "Fabrikam Resorts", 400_000.0, None)), MON)
    assert unnamed["image_spec"]["title"].startswith("At least 2 CEOs") and unnamed["persons"] == []
    assert T._distinct_people(week(_avery("CTSH", "Contoso Hotels", 900_000.0),
                                   _avery("FBRS", "Fabrikam Resorts", 400_000.0, None)).rows) == 2
    assert T._distinct_people(week(_avery("CTSH", "Contoso Hotels", 900_000.0, "Jordan T. Avery"),
                                   _avery("FBRS", "Fabrikam Resorts", 400_000.0)).rows) == 1


def test_revalidate_refuses_a_fact_sheet_whose_rows_name_one_person_twice():
    """create_posts' path: a stored package whose fact sheet repeats a person never revalidates."""
    good = week(_avery("CTSH", "Contoso Hotels", 900_000.0), _avery("FBRS", "Fabrikam Resorts", 400_000.0, "Sam Lee"))
    out = compose(good, MON)
    assert T.revalidate(_jsonb(out), fact_sheet=_jsonb(sheet(good)), run_date=MON) == []
    bad = replace(good, rows=(good.rows[0], replace(good.rows[1], person_name="Jordan Avery")))
    found = T.revalidate(_jsonb(out), fact_sheet=_jsonb(sheet(bad)), run_date=MON)
    assert found == [{"field": "package", "code": "recompose_refused", "detail": "record_invalid"}], found


# ── review round 11 (news-v11) ────────────────────────────────────────────────

#: Words that state nothing of their own.
_FUNCTION_WORDS = frozenset(
    "a an the and or nor but of for in on at to by as is are was were be been being it its their his her "
    "this that these those with from per than then each every can".split())
_TOKEN_RE = re.compile(r"\$?[A-Za-z0-9][A-Za-z0-9.,$%&'’/-]*")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9$])")


def _stem(word):
    w = word.lower().replace("’", "'").rstrip(".,;:")
    w = (w[:-2] if w.endswith("'s") else w).rstrip("'")
    if w[:1].isalpha():
        for suffix in ("ings", "ing", "ed", "s"):
            if w.endswith(suffix) and len(w) - len(suffix) >= 3:
                return w[:-len(suffix)]
    return w


def _content(text):
    return {_stem(w) for w in _TOKEN_RE.findall(text)} - _FUNCTION_WORDS - {""}


#: Statements one headline variant carries and a paragraph also makes in OTHER words, where no word
#: test can see the repeat (each pattern matches both wordings).
_HEADLINE_CLAIMS = {
    # "The EPS basis can differ …" / "… can be on a different basis from the official accounting result"
    "basis_note": re.compile(r"\b(?:basis can differ|different basis)\b", re.IGNORECASE),
    # "…, counting each member once." / "Each member is counted once, however many reports …"
    "counted_once": re.compile(r"\bcount(?:ing|ed)\W+each\W+member\W+once\b"
                               r"|\beach\W+member\W+(?:is\W+)?counted\W+once\b", re.IGNORECASE),
}


def _restatements(head, para):
    """Why the caption paragraph `para` restates its headline `head`, or []: a sentence of it whose every
    content word the headline already says (a stake's source line under a headline citing the source; the
    role-only Form 4 line under a headline naming the role and company), a headline claim made again in
    other words (`_HEADLINE_CLAIMS`), or a figure pair said twice (review round 10)."""
    found = []
    words = _content(head)
    found += [("adds_nothing", s) for s in _SENTENCE_RE.split(para) if _content(s) and _content(s) <= words]
    found += [(claim, para) for claim, rx in _HEADLINE_CLAIMS.items() if rx.search(head) and rx.search(para)]
    if len(_figures(head) & _figures(para)) >= 2:
        found.append(("figure_pair", para))
    return found


def _headline_paragraph_pairs(parts):
    """Every (headline, paragraph) a caption can hold: a caption is one headline and a prefix of that
    headline's OWN paragraphs (its list less the ones it states), so these pairs cover every body."""
    for heads, paras in ((parts.image_headlines, parts.image_paragraphs),
                         (parts.video_headlines, parts.video_paragraphs)):
        for h in heads:
            for p in paras:
                if p not in parts.stated_by_headline.get(h, frozenset()):
                    yield h, p


@pytest.mark.parametrize("name, factory, run_date", SAMPLES, ids=[s[0] for s in SAMPLES])
def test_no_headline_paragraph_pair_says_one_thing_twice(name, factory, run_date):
    """Review round 11 (low): the earnings ".basis" headlines end "The EPS basis can differ from the official
    accounts." and the last paragraph began "The estimate is the analyst consensus; the reported figure can
    be on a different basis from the official accounting result" on Facebook, LinkedIn, Instagram and the
    YouTube description. The round-10 figure-pair test, generalised to every series and every headline /
    paragraph pair a caption can hold, also found a stake's ".long" headline citing its source above the
    "Source: …, as of …" paragraph, a Congress ".long" headline "counting each member once" above "Each
    member is counted once, …", and a Form 4 single-row headline "The CEO of Lowe's disclosed buying …"
    above "The filing is by the CEO of Lowe's." Each is now left out under the headlines that state it
    (`_Parts.stated_by_headline`). Mutation-checked (2026-10-10, in memory): with each builder's stated
    map emptied, or the earnings basis note and estimate definition joined into one paragraph again, the
    samples of that series turned red."""
    parts = _parts(factory(), run_date)
    found = [(h, r) for h, p in _headline_paragraph_pairs(parts) for r in _restatements(h, p)]
    assert found == [], found


@pytest.mark.parametrize("name, factory, run_date", SAMPLES, ids=[s[0] for s in SAMPLES])
def test_no_composed_caption_paragraph_restates_its_headline(name, factory, run_date):
    """The same over the assembled captions: every store state, URL setting and caption field."""
    for store_state in post_copy.STORE_STATES:
        for allow in (False, True):
            out = compose(factory(), run_date, store_state, allow)
            for f, body in out["captions"].items():
                head, *paras = body.split("\n\n")
                assert [r for p in paras for r in _restatements(head, p)] == [], (store_state, allow, f)


def test_the_restatement_check_sees_each_shape_it_names():
    """The check is not vacuous: the four pre-news-v11 bodies (verbatim from the news-v10 goldens) each
    trip it, and an honest paragraph beside them does not."""
    assert [r for r, _p in _restatements(
        "Fabrikam reported EPS of $4.86 vs an analyst estimate of $4.50. The EPS basis can differ from the "
        "official accounts.",
        "The estimate is the analyst consensus; the reported figure can be on a different basis from the "
        "official accounting result. An analyst estimate is the average figure analysts published before the "
        "report.")] == ["basis_note"]
    assert [r for r, _p in _restatements(
        "NVIDIA invested $5 billion in Intel, as of Dec 26, 2025. Source: Intel 8-K (Dec 29, 2025).",
        "Source: Intel 8-K (Dec 29, 2025), as of Dec 26, 2025.")] == ["adds_nothing"]
    assert [r for r, _p in _restatements(
        "3 members of Congress disclosed purchases of Accenture stock in November 2026, counting each member "
        "once.", "Each member is counted once, however many reports they filed that month.")] == ["counted_once"]
    assert [r for r, _p in _restatements(
        "The CEO of Lowe's disclosed buying $2.3 million of Lowe's stock in a Form 4 filed Nov 12.",
        "The filing is by the CEO of Lowe's.")] == ["adds_nothing"]
    # honest: a definition, a name the headline lacks, the next fact, a single figure beside a new one
    for head, para in [
        ("Fabrikam reported EPS of $4.86 vs an analyst estimate of $4.50. The EPS basis can differ from the "
         "official accounts.", "An analyst estimate is the average figure analysts published before the report."),
        ("GameStop's CEO disclosed buying $74.4 million of GameStop stock in Form 4s filed Nov 10-11.",
         "The filings name Ryan Cohen, the CEO of GameStop."),
        ("3 members of Congress disclosed purchases of Accenture stock in November 2026.",
         "Each member is counted once, however many reports they filed that month."),
        ("How Costco makes money: $275.2 billion of revenue in fiscal 2025, and $2.94 of every $100 kept as net "
         "income.", "Net income was $8.1 billion: $2.94 of every $100 of revenue."),
    ]:
        assert _restatements(head, para) == [], para


def test_each_headline_leaves_out_exactly_the_paragraphs_it_states():
    """No more and no less: a paragraph is left out only under a headline that states it, and stays
    offered under every headline that does not (the plain earnings headlines keep the basis note, a
    stake's ".short" headline keeps the source, the Congress ".short" / ".min" keep "counted once", the
    Form 4 ".min" headline — which names no company — keeps the role-and-company line)."""
    basis, estimate = T.LEXICON["er.l3.basis"], T.LEXICON["er.estimate"]
    for rec in (earnings(), earnings_profit()):
        p = _parts(rec, EARNINGS_RUN)
        assert p.image_paragraphs[-2:] == [basis, estimate] and p.video_paragraphs == p.image_paragraphs
        rev = [x for x in p.image_paragraphs if x.startswith("Revenue was")]
        for h in p.image_headlines:
            want = set(rev if "revenue of" in h else []) | ({basis} if "basis can differ" in h else set())
            assert p.stated_by_headline.get(h, frozenset()) == want, h
        assert any("basis" not in h for h in p.image_headlines)        # the plain fallbacks exist
    for factory in (nscale, anthropic_commitment, tesla_spacex, berkadia, itochu, intel_stake):
        p = _parts(factory(), STAKES_RUN)
        source = [x for x in p.image_paragraphs if x.startswith("Source: ")]
        assert len(source) == 1
        for h in p.image_headlines:
            assert p.stated_by_headline.get(h, frozenset()) == (set(source) if "Source: " in h else set()), h
        assert any("Source: " not in h for h in p.image_headlines)
    p = _parts(congress(), CONGRESS_RUN)
    once = T.LEXICON["cg.l2"]
    assert once in p.image_paragraphs
    for h in p.image_headlines:
        assert p.stated_by_headline.get(h, frozenset()) == ({once} if "counting each member once" in h else set())
    assert sum(1 for h in p.image_headlines if once not in p.stated_by_headline.get(h, ())) == 2
    # Form 4: the role-only line under the long and short headlines; never the named line
    for rec, line in [(week(LOW), "The filing is by the CEO of Lowe's."),
                      (week(buy("HSY", "Hershey", "director", None, 150_000.0, 812.0, 1, [date(2026, 11, 14)]),
                            series="insider_buys"), "The filing is by a director of Hershey.")]:
        p = _parts(rec, MON)
        long_h, short_h, min_h = p.image_headlines
        assert p.image_paragraphs[0] == line
        assert p.stated_by_headline == {long_h: {line}, short_h: {line}}, p.stated_by_headline
        assert "company's stock" in min_h and line not in p.stated_by_headline.get(min_h, ())
    assert _parts(week(GME), MON).stated_by_headline == {}
    # a roundup's paragraphs are its rows, never the role-only line (its lead row's unnamed headlines carry
    # the map only for the video side, whose paragraphs never hold that line)
    for rec in (ceo_roundup(), insider_mixed()):
        p = _parts(rec, MON)
        offered = set(p.image_paragraphs) | set(p.video_paragraphs)
        assert all(not (said & offered) for said in p.stated_by_headline.values()), p.stated_by_headline
    # the series whose headlines never state a paragraph are untouched
    for factory, run_date in ((berkshire, TUE), (pershing, TUE),
                              (costco, THU), (rivian, THU), (comcast, THU), (ai_chips, THEME_RUN)):
        assert _parts(factory(), run_date).stated_by_headline == {}


@pytest.mark.parametrize("factory", [earnings, earnings_profit], ids=["loss", "profit"])
def test_every_long_earnings_caption_says_the_basis_note_once_and_keeps_the_estimate_definition(factory):
    """The finding's captions, composed: the basis note is said exactly once (in the ".basis" headline) and
    the estimate definition — the half of the old joined paragraph the headline does not state — stays."""
    for store_state in post_copy.STORE_STATES:
        for allow in (False, True):
            out = compose(factory(), EARNINGS_RUN, store_state, allow)
            for f in ("facebook", "linkedin", "instagram", "youtube_description"):
                chunks = out["captions"][f].split("\n\n")
                assert sum(1 for c in chunks if _HEADLINE_CLAIMS["basis_note"].search(c)) == 1, (store_state, f)
                assert "basis can differ" in chunks[0], (store_state, f)
                assert chunks[-1] == T.LEXICON["er.estimate"], (store_state, f)


@pytest.mark.parametrize("name, factory, run_date", SAMPLES, ids=[s[0] for s in SAMPLES])
def test_no_caption_restates_its_headline_at_any_budget(name, factory, run_date, monkeypatch):
    """The fit sweep (every caption field, budgets 40..1,200, every sample): whatever headline a budget
    picks, its body never restates it — the assembler honours `stated_by_headline` at every budget, not
    only at the outlets' real ones."""
    rec = factory()
    parts = _parts(rec, run_date)
    spec = T.SERIES_SPECS[rec.series]
    for budget in range(40, 1201, 7):
        monkeypatch.setattr(T.post_copy, "body_budget", lambda *a, _b=budget, **k: _b)
        bodies, _dropped = T._caption_bodies(parts, spec, run_date, "live", False)
        for f, body in bodies.items():
            head, *paras = body.split("\n\n")
            assert [r for p in paras for r in _restatements(head, p)] == [], (budget, f)
